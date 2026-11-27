# Tasks and scheduling

A task is Anokos's only unit of execution: a thread with its own stack, heaps and symbol table,
described by a `CTask` record. Each core keeps its tasks in a ring and switches between them
cooperatively: a task runs until it calls `Yield`. This page covers the task record, the
scheduler, creating and ending tasks, and the job and message queues tasks use to talk to each
other.

## What a task is

There are no processes and no address spaces. Every task sees the same identity-mapped memory
and can call any function. What makes a task a task is:

- a `CTask` record, which holds the saved registers while the task isn't running;
- a stack (`CTask.stk`, 256 KiB by default, `MEM_DFT_STK`);
- its own heaps (`code_heap` and `data_heap`), so everything it allocates can be freed at once
  when it dies (see [Memory](memory.md));
- its own symbol table (`hash_table`), chained to its parent's, so code a task compiles is
  visible to it and its children but not to its siblings (see [Symbols](symbols.md));
- its job queue (`srv_ctrl`), through which other tasks send it work and messages.

Switching tasks only stores and restores registers. There's no page table change and no disk
I/O, so a switch is cheap.

## CTask: the task record

`CTask` is defined in
[KernelA.TH](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KernelA.TH)
(search for `public class CTask`). It's large because it also carries window, document and
debugger state. The fields the scheduler and task code use:

| Field | Meaning |
| --- | --- |
| `addr` | Pointer to itself. `Fs` reads this (see below). |
| `task_signature` | `TASK_SIGNATURE_VAL` (`'TskS'`) while the task is alive; `TaskValidate` checks it. |
| `wake_jiffy` | Don't run before `cnts.jiffies` reaches this. Set by `Sleep`. |
| `task_flags` | `TASKf_` bits, see [the flags](#task-flags). |
| `code_heap`, `data_heap` | The task's heaps. Often the same heap. |
| `parent_task` | Who spawned it. |
| `next_task`, `last_task` | Links in the core's run ring. |
| `next_sibling_task`, ...`last_child_task` | The parent/child tree. |
| `rip`, `rflags`, `rsp`, `rsi`, `rax` ... `r15` | Saved registers. The order is fixed: the switch code pushes and pops them in place. |
| `gs` | The `CCPU` of the core the task runs on. |
| `fpu_mmx` | Area for `FXSAVE`/`FXRSTOR` of the FPU/SSE state. |
| `swap_cnter` | How many times it has been switched in. |
| `stk` | Its stack (`CTaskStk`). |
| `next_except`, `except_ch`, `catch_except` | `try`/`catch` state, see [Exceptions](exceptions.md). |
| `bpt_lst` | Its debugger breakpoints. |
| `hash_table` | Its symbol table. |
| `srv_ctrl` | Its job queue (`CJobCtrl`): jobs waiting and jobs done. |
| `task_end_cb` | Optional function to run instead of dying, once. |
| `popup_task` | A pop-up task it's waiting on, if any. |
| `task_num` | Serial number, from `sys_num_spawned_tasks`. |

## Fs and Gs

Two names are everywhere in the code: `Fs` is the current task (`CTask *`) and `Gs` is the
current core (`CCPU *`). They're compiler intrinsics, declared in
[KernelB.TH](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KernelB.TH) as
`_intern IC_FS CTask *Fs()` and `_intern IC_GS CCPU *Gs()`.

On x86-64 the names are literal. The FS segment base points at the current `CTask` and the GS
base at the current `CCPU`. Both records start with a pointer to themselves (`addr`), so `Fs`
compiles to `XOR EAX,EAX` and `MOV RAX,FS:[RAX]`: one load, no global variable, and different on
every core for free. The scheduler changes FS with `SET_FS_BASE` (a `WRMSR` to `IA32_FS_BASE`,
in [KUtils.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KUtils.TC)) each
time it picks a task. The asm often reads fields directly, as in `MOV RSI,FS:CTask.addr[RSI]`.

**On ARM64**, `Fs` is kept in the system register `TPIDR_EL1` and `Gs` in `TPIDRRO_EL0`. The
ARM64 back end compiles `Fs` to `MRS X0,TPIDR_EL1` (see `IC_FS` in
[BackArm64.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Compiler/BackArm64.TC)),
and `SET_FS_BASE`/`SET_GS_BASE` in
[Arm64/KUtils.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Arm64/KUtils.TC)
write those registers.

## The task ring

Each core has a circular, doubly linked list of its runnable tasks, through `next_task` and
`last_task`. The head is the core's Seth task (`Gs->seth_task`). On core 0 the Seth task is the
Adam task, `adam_task`, the first task, created at boot. The comment at the top of
[Sched.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Sched.TC) adds that on
core 0 the ring order is also the window stacking order. A ring on core 0 might look like this:

```
        Gs->seth_task
             |
             v
   +----> [ Adam ] --> [ WinMgr ] --> [ Terminal ] --> [ task X ] --+
   |                                                                |
   +---------------------------- next_task -------------------------+
```

`TaskQueIns(task,pred)` links a task in just before `pred` (default `Fs`), and `TaskQueRem`
unlinks it. Both run with interrupts off (`PUSHFD`, `CLI`, ..., `POPFD`), which is the usual way
to make a few statements atomic on one core. Tasks never move to another core.

## The scheduler

The scheduler is round-robin and lives in the asm block of
[Sched.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Sched.TC). A task gives
up the CPU by calling `Yield` (label `_YIELD`):

1. If single-user mode is on (`sys_semas[SEMA_SINGLE_USER]`), return at once. The debugger uses
   this to stop other tasks from running underneath it.
2. `CLI`, then `TASK_CONTEXT_SAVE` stores the registers into the current `CTask`, `FXSAVE`s the
   FPU state and takes this task's breakpoints out of the code. The saved `rip` is `_RET`, so the
   task resumes by returning from `Yield`.
3. Walk the ring from `Fs->next_task` (label `RESTORE_RSI_TASK`). For each task:
   - if `TASKf_KILL_TASK` is set, end it now (`TaskEnd`, below);
   - if `TASKf_AWAITING_MSG` or `TASKf_SUSPENDED` is set, skip it;
   - if `wake_jiffy > cnts.jiffies`, skip it;
   - otherwise make it `Fs` and jump to `_TASK_CONTEXT_RESTORE`.
4. `_TASK_CONTEXT_RESTORE` puts the task's breakpoints back, `FXRSTOR`s, pops the registers and
   `IRET`s to the saved `rip`, `rsp` and `rflags`.

A switch therefore costs one register save and one restore. A task waiting for I/O usually
loops, checking a status and calling `Yield`. That's simple and costs little real performance,
but it makes the CPU load look high.

### Task flags

The `TASKf_` bits in `CTask.task_flags` (defined in `KernelA.TH`):

| Flag | Meaning |
| --- | --- |
| `TASKf_TASK_LOCK` | Spin lock for changing the task's state. |
| `TASKf_KILL_TASK` | End this task the next time the scheduler reaches it. |
| `TASKf_SUSPENDED` | Skip it. Set with `Suspend(task)`, cleared with `Suspend(task,FALSE)`. |
| `TASKf_IDLE` | The task says it's waiting. Used by `TaskWait` and for the CPU load count; the scheduler doesn't test it. |
| `TASKf_AWAITING_MSG` | Skip it until someone sends a job or message (`TaskRstAwaitingMsg` clears it). |
| `TASKf_DISABLE_BPTS` | Don't insert breakpoints when switching in (single-stepping). |
| `TASKf_BREAK_LOCKED`, `TASKf_PENDING_BREAK` | Ctrl+Alt+C is held off; one is pending. See `BreakLock`. |
| `TASKf_BREAK_TO_SHIFT_ESC` | Ctrl+Alt+C sends Shift+Esc instead of throwing. |
| `TASKf_KILL_AFTER_DBG` | `Exit` was called while debugging; die when the debugger resumes. |
| `TASKf_INPUT_FILTER_TASK`, `TASKf_FILTER_INPUT` | Input filter tasks, see [Messages](#messages). |

### Sleeping

`Sleep(mS)` and `SleepUntil(wake_jiffy)` in
[KMisc.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KMisc.TC) just set
`Fs->wake_jiffy`, set `TASKf_IDLE`, and `Yield`. The scheduler skips the task until
`cnts.jiffies` catches up. A jiffy is 1 ms (`JIFFY_FREQ` is 1000); see
[Interrupts and timers](interrupts.md#jiffies). `Sleep(0)` is just `Yield`.

### When nothing is ready

Each core also has an idle task, `Gs->idle_task`, made by `CPUStructInit` in
[MultiProc.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/MultiProc.TC). It
is never in the ring; it only lends its stack. When the walk gets back to the Seth task, the
scheduler looks at `CPUf_RAN_A_TASK` in `Gs->cpu_flags`. That bit is set whenever a task is
restored with interrupts enabled.

- If it's set, some task ran with IRQs on since the last lap, so timers had a chance to tick.
  Clear it and walk again.
- If it's clear, nothing could run. Switch to the idle task's stack, `STI`, `HLT`. The next
  interrupt wakes the core and the walk starts again at Seth (`SYS_IDLE_PT`,
  `RESTORE_SETH_TASK_IF_READY`).

Without this, a core whose tasks were all asleep would spin with interrupts off, and
`cnts.jiffies` would never move.

## Is it preemptive?

No. The timer interrupt (`IRQ_TIMER` in
[KInts.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KInts.TC)) saves the
interrupted task's full context, counts the tick, and then jumps to `RESTORE_RSI_TASK` with the
*interrupted task* as the starting point. That task is normally still ready, so it is restored
straight away and carries on. A task that never yields keeps its core.

The timer path matters for two things anyway. It's how `cnts.jiffies` advances, which wakes
sleeping tasks at their next chance. And because it goes through `RESTORE_RSI_TASK`, it checks
the Ctrl+Alt key flags (below), which is how Ctrl+Alt+C can break a task stuck in a loop. If the
interrupted task was the idle task, it starts at Seth instead. Details are in
[Interrupts and timers](interrupts.md#the-timer-interrupt).

## Creating a task: Spawn

`Spawn` in [KTask.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KTask.TC):

```c
CTask *Spawn(U0 (*fp_start_addr)(U8 *data),U8 *data=NULL,U8 *task_name=NULL,
	I64 target_cpu=-1,	//-1 for current CPU
	CTask *parent=NULL,	//NULL means the core's Seth task
	I64 stk_size=0,		//0=default
	I64 flags=1<<JOBf_ADD_TO_QUE)
```

What it does:

1. If `target_cpu>=0`, hand the job to that core with `SpawnQue` (see [Multicore](multicore.md))
   and return what it made.
2. `CAlloc` the `CTask` from Adam's code heap and set `task_signature`.
3. Default the parent to `Gs->seth_task`. Copy `gs` from the parent.
4. Make the task's own heaps with `HeapCtrlInit` on the system block pools.
5. `TaskInit`: self-links (`addr`, ring and filter links), `task_num`, `rflags=RFLAGG_NORMAL`
   (interrupts on), empty job, exception and compiler queues, the FPU area (copied from a clean
   initial state), a new hash table, the stack (`TaskStkNew`), and the current drive and
   directory.
6. Build the first frame on the new stack: push `data`, then `&Exit` as a return address, and
   set `rip` to the start function. When the function returns, it "returns" into `Exit`.
7. Chain the hash table to the parent's, copy the name, and (with `JOBf_ADD_TO_QUE`) link the
   task into the parent's child list (`TaskQueInsChild`) and into the ring (`TaskQueIns`).

The new task first runs the next time the scheduler reaches it. Nothing is called directly.
`BirthWait` and `DeathWait` are small helpers that `Yield` until a task is valid, or gone.

**On ARM64** the return address must be in X30, not on the stack. `Spawn` stores the start
address in the `rcx` slot (X1) and sets `rip` to `ARM64_TASK_START`, which pops `&Exit` into X30
and branches to X1. See
[Arm64/Sched.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Arm64/Sched.TC).

## Parents, children and Seth

Every task has a parent. Tasks spawned without one get the core's Seth task, so on core 0 Adam is
the ancestor of everything. The child list matters in two places: a child inherits the parent's
symbols (through `hash_table->next`), and when a parent ends, its children are killed first.

## Ending a task

- `Exit()` ends the current task. It records where it is (`rsp`, `rbp`, `rip`), sets
  `TASKf_KILL_TASK` and jumps into the scheduler (`TaskEndNow`). If it's called in the debugger on the focus task, it sets
  `TASKf_KILL_AFTER_DBG` and resumes instead.
- `Kill(task)` sets `TASKf_KILL_TASK` on another task and, by default, yields until it's gone. It
  refuses to kill the window manager or any Seth task. `Kill(task,,TRUE)` only breaks the task.

The scheduler calls `TaskEnd` (with interrupts off) when it meets a task with `TASKf_KILL_TASK`.
`TaskEnd` runs on the dying task's `Fs` and returns the task to run next:

1. If `task_end_cb` is set, clear the kill flag and resume the task at the callback, once.
2. If the task is a pop-up its parent is waiting on, kill the parent too.
3. Release locked drives (`DrvsRelease`, `BlkDevsRelease`).
4. If it still has children, mark them all `TASKf_KILL_TASK` and move on. The parent stays in the
   ring, flagged, and is ended on a later lap once the children are gone.
5. Delete its job queues, drop the focus if it had it, clear `task_signature`, and unlink it from
   the input filter chain, its siblings and the ring.
6. Put it on the core's dying queue (`Gs->next_dying`) with `wake_jiffy` set
   `DYING_JIFFIES` (200 ms) in the future.

`TaskKillDying` frees tasks that have been dead long enough: `TaskDel` frees both heaps with
`HeapCtrlDel` (which returns every block the task ever allocated) and then the `CTask`. The delay
exists so a stale pointer to a just-dead task still finds a record with a cleared signature,
rather than memory already reused. The window manager calls `TaskKillDying` on core 0
([WinMgr.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Anokos/WinMgr.TC)) and
`CoreAPSethTask` calls it on the other cores.

## Adam, servant tasks and jobs

A task's `srv_ctrl` (`CJobCtrl`) holds two queues of `CJob` records: `next_waiting` and
`next_done`. Jobs are how one task asks another to do something. They're in
[Job.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Job.TC).

| Job type | Made by | The servant does |
| --- | --- | --- |
| `JOBT_EXE_STR` | `TaskExe` | Compile and run a string of source (`ExePrint`). |
| `JOBT_TEXT_INPUT` | `TaskText` | Feed text as if typed (via an input filter task). |
| `JOBT_MSG` | `TaskMsg` | Deliver a message (key, mouse ...). |
| `JOBT_CALL` | `JobQue` | Call a function with an argument. |
| `JOBT_SPAWN_TASK` | `SpawnQue` | `Spawn` a task on its own core. |

Queuing a job takes the queue's spin lock (`LBts(&ctrl->flags,JOBCf_LOCKED)`), appends the job,
and clears the servant's `TASKf_IDLE` and `TASKf_AWAITING_MSG` so the scheduler runs it again.
With `JOBf_WAKE_MASTER`, the sender suspends itself until the job is done. The result goes on
the master's `next_done` queue; `JobResScan` and `JobResGet` collect it.

A servant task loops in `SrvTaskCont` (in `KTask.TC`): run all waiting jobs (`JobsHndlr`), set
`TASKf_IDLE` and `TASKf_AWAITING_MSG`, `Yield`. Because of `TASKf_AWAITING_MSG`, an idle servant
costs nothing until a job arrives. Adam ends its boot in `KMain` by calling `SrvTaskCont`, so
Adam is a servant: `Adam("...")` makes it run code, and `AdamLog`/`AdamErr` print in its window.
`PopUp` spawns a temporary servant (`SrvCmdLine`), runs code in it and kills it.

A user task (`User()`, which spawns `UserCmdLine`) runs `UserTaskCont` instead: read a line,
compile and run it (`ExeCmdLine`), repeat, with a `try`/`catch` around each line so an exception
prints and returns to the prompt.

## Messages

Messages are `JOBT_MSG` jobs carrying a `msg_code` (`MSG_KEY_DOWN`, `MSG_MS_MOVE` ...) and two
arguments. They're defined in `KernelA.TH`, and the reading side is in
[SerialDev/Message.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/SerialDev/Message.TC).

- `TaskMsg(srv,master,code,arg1,arg2,flags)` queues one. A negative code means "down then up"
  and posts two. `PostMsg` and `Msg` (to yourself) are the usual wrappers.
- `ScanMsg(&a1,&a2,mask)` returns the first waiting message whose code has its bit set in
  `mask`, or `MSG_NULL` at once if there is none. Messages not in the mask are thrown away.
  When called on itself, it first runs any other waiting jobs. A pop-up gets its parent's
  messages.
- `GetMsg` loops `ScanMsg` and `Yield` until something arrives. `ScanKey`, `ScanChar` and
  `GetKey` are built on these.

Text input can pass through input filter tasks (`InputFilterTask`), which sit in a separate
chain (`next_input_filter_task`) in front of a task and take its messages first. `InStr` and
`In` use them to feed a task text from itself.

## Ctrl+Alt keys and breaking a task

The keyboard handler doesn't act on Ctrl+Alt+C, X, N or Del itself. It sets a bit in
`sys_ctrl_alt_flags` (`CtrlAltC` and friends in
[KeyDev.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KeyDev.TC)).
`RESTORE_RSI_TASK` checks those bits on every task switch and every timer tick, on core 0 only
(`HANDLE_SYSF_KEY_EVENT`):

- **Del**: `Reboot`.
- **N** (`CTRL_ALT_TAB`): `TaskFocusNext`, the next task that takes focus.
- **X**: kill the focus task.
- **C**: break the focus task. Its saved `rip` is replaced with `&Break`, and its `wake_jiffy`
  and awaiting-message state are cleared, so the next time it runs it calls `Break`, which
  throws `'Break'`. If the task holds `BreakLock`, the break is only marked pending until
  `BreakUnlock`.

Because the timer goes through the same path, Ctrl+Alt+C works even on a task that never yields.

## On ARM64

The scheduler is the same algorithm, rewritten in
[Arm64/Sched.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Arm64/Sched.TC):

- The `CTask` register slots hold ARM64 registers: `rax`..`r15` are X0..X15 (`rsi` is X6, `rdi`
  X7), `rbp` is X29, `rsp` is SP, `rip` is the resume address. `rflags` keeps the x86 format, so
  bit 9 means "interrupts on"; the asm converts it to and from DAIF.
- `Yield` saves only what TerryC callees must keep (X6, X7, X10-X15, X29, SP) and the return
  address. It saves no FP/SIMD registers, because TerryC keeps no FP values in registers across
  a call.
- X6 plays the part of RSI (the task being looked at), and X7 holds the jiffy count while
  choosing. `ArmJiffiesUpdate` refreshes `cnts.jiffies` from the generic timer counter if the
  timer IRQ isn't running.
- Idle is `ArmIdle`: `WFI` with IRQs on, or `PAUSE` if there's no timer IRQ.
- An interrupted task gets `rip=ARM64_IRQ_RESUME` and `rsp` pointing at its IRQ frame, so a
  later `_TASK_CONTEXT_RESTORE` lands in code that unwinds the frame with `ERET`. See
  [Interrupts and timers](interrupts.md#on-arm64).
- Not done yet: the Ctrl+Alt key events, breakpoints and FP state.
- Adam doesn't run `SrvTaskCont` (that needs the compiler); `KMainArm64B` ends in a `Sleep`
  loop. So jobs sent to Adam aren't handled on ARM64 yet, and nothing calls `TaskKillDying`, so
  dead tasks stay on the dying queue.

## Where to look

- [Sched.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Sched.TC): `Yield`,
  the context switch, the Ctrl+Alt keys, `TaskQueIns`.
- [KTask.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KTask.TC): `Spawn`,
  `TaskInit`, `Exit`, `Kill`, `TaskEnd`, `SrvTaskCont`, `UserTaskCont`.
- [Job.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Job.TC): jobs,
  `TaskMsg`, `TaskWait`, `Adam`, `PopUp`, `XTalk`.
- [SerialDev/Message.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/SerialDev/Message.TC):
  `ScanMsg`, `GetMsg`, input filters.
- [MultiProc.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/MultiProc.TC):
  Seth tasks, `JobQue`, `SpawnQue`.
