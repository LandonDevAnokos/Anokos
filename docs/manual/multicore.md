# Multicore

Anokos uses the other cores of a PC in a master/slave way, as TempleOS did. Core 0 runs the
system: windows, devices and the clock. The other cores run work that core 0 gives them, and the
scheduler never moves a task from one core to another. This page covers the per-core `CCPU`
record, how the other cores are started, how work is sent to them, inter-processor interrupts,
and locking. The ARM64 port runs on one core for now.

## The model

The original [MultiCore.DD](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Doc/MultiCore.DD)
sums it up: core 0 is the master, and its programs explicitly hand computing jobs to other
cores. It isn't SMP. Each core has its own task ring and its own scheduler loop, and a task stays
on the core it was spawned on for its whole life.

In the code, that means:

- Each core runs the same scheduler ([Tasks and scheduling](tasks.md#the-scheduler)) on its own
  ring, headed by its own Seth task.
- Shared data (heaps, file system caches, job queues) is protected by spin locks, so any core can
  allocate memory or read files. In that sense the system is still symmetrical.
- Some things only core 0 does; see [Core 0 only](#core-0-only).

## CCPU: the per-core record

`CCPU` is defined in
[KernelA.TH](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KernelA.TH). There is
one per core in the array `cpu_structs`, and `Gs` points at the current core's (see
[Fs and Gs](tasks.md#fs-and-gs)). `mp_cnt` is the number of cores in use.

| Field | Meaning |
| --- | --- |
| `addr` | Pointer to itself, so `Gs` is one load. |
| `num` | Core number, 0 for the master. |
| `cpu_flags` | `CPUf_RAN_A_TASK` (for idling), `CPUf_DYING_TASK_QUE` (lock for the dying queue). |
| `seth_task` | Head of this core's task ring. Adam on core 0. |
| `idle_task` | Lends its stack while the core halts with nothing to do. |
| `total_jiffies`, `idle_pt_hits` | Timer ticks taken, and how many hit an idle task (CPU load). |
| `swap_cnter` | Task switches on this core. |
| `profiler_timer_irq` | Optional hook called on every timer tick. |
| `next_dying`, `last_dying`, `kill_jiffy` | Dead tasks waiting to be freed. |
| `tss` | This core's task state segment (x86). |
| `start_stk` | A small stack used while the core starts. |

`CPUStructInit(num,c,seth_task)` in
[MultiProc.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/MultiProc.TC)
clears one, spawns its idle task and TSS, and stores `seth_task` last. The last part matters: a
starting core waits for that field to become non-zero.

## Starting the other cores

Core 0 sets itself up in `Core0Init`: it allocates `cpu_structs` (from Adam's code heap, because
the 32-bit start-up code needs a 32-bit address), fills in entry 0 with Adam as its Seth task,
sets the GS base, and initializes its local APIC with `MPAPICInit`.

Later in boot, `KMain` calls `Core0StartMP` unless the kernel was configured without
multicore (`CFG_NO_MP`), then sets `RLf_MP` in `sys_run_level`. `Core0StartMP`:

1. Returns at once if CPUID says there is no APIC.
2. If cores were already running (it can be called again), halts them with `MPHalt` and empties
   their job queues.
3. Copies the start-up code, `COREAP_16BIT_INIT`, to `MP_VECT_ADDR` (page `MPN_VECT`, 0x97000),
   with a pointer to the GDT next to it.
4. Sends an INIT inter-processor interrupt to all other cores, waits 10 ms with `Busy`, then sends
   two STARTUP interrupts with vector `MPN_VECT`. That makes every other core start in 16-bit
   real mode at 0x97000.
5. Waits 100 ms, then grabs `mp_cnt_lock` so no late core can take a number. `mp_cnt_initial` now
   holds how many cores checked in.
6. For each new core: `Spawn` its Seth task (`CoreAPSethInit`, stack `MEM_SETH_STK`, not added to
   any ring), then `CPUStructInit` it, which publishes the task in `seth_task`.
7. Waits until every Seth task has set `TASKf_AWAITING_MSG`, meaning it reached its main loop,
   and sets `mp_cnt`.

Meanwhile each new core runs the trampoline:

```
  0x97000, 16-bit  COREAP_16BIT_INIT   load GDT, turn on protected mode, far jump
           32-bit  AP_32BIT_INIT       take SYS_MP_CNT_LOCK, my number = mp_cnt_initial++
                                       stack = cpu_structs[n].start_stk
                                       SYS_ENTER_LONG_MODE
           64-bit                      GS base = &cpu_structs[n]
                                       spin until cpu_structs[n].seth_task != 0
                                       task->gs = this CCPU, FS base = task
                                       _TASK_CONTEXT_RESTORE   (runs the Seth task)
  CoreAPSethInit     MPAPICInit; then restart itself at CoreAPSethTask
```

`MPAPICInit` enables the core's local APIC, records its APIC ID in `dev.mp_apic_ids[Gs->num]`
(needed to address it later), sets up logical addressing, loads the task register from the
core's TSS, and on cores other than 0 loads the shared IDT with `IntInit1` and enables
interrupts.

A core whose number would be `MP_PROCESSORS_NUM` (128) or more halts in the trampoline.

## Seth tasks

On cores other than 0, the Seth task runs `CoreAPSethTask` forever:

```c
while (TRUE) {
  STI
  do {
    TaskKillDying;
    do PAUSE
    while (LBts(&ctrl->flags,JOBCf_LOCKED));
  } while (ctrl->next_waiting!=ctrl && JobRunOne(GetRFlags,ctrl));
  CLI
  LBts(&Fs->task_flags,TASKf_AWAITING_MSG);
  LBtr(&ctrl->flags,JOBCf_LOCKED);
  LBts(&Fs->task_flags,TASKf_IDLE);
  Yield;
  LBtr(&Fs->task_flags,TASKf_IDLE);
}
```

It runs every waiting job in its queue (`ctrl` is `&Fs->srv_ctrl`), frees dead tasks, then marks
itself awaiting a message and yields. If no other task on the core is ready, the core halts until
an interrupt. On core 0 the same role is played by Adam, which runs `SrvTaskCont`.

## Sending work to a core

All of this is in `MultiProc.TC` and uses the job queues described in
[Tasks and scheduling](tasks.md#adam-servant-tasks-and-jobs).

- `JobQue(fp,data,target_cpu=1,flags=1<<JOBf_FREE_ON_COMPLETE)` queues a `JOBT_CALL` on the
  target core's Seth task, which calls `fp(data)`. If the queue was empty and the Seth task was
  waiting, it also sends `I_WAKE` to that core so a halted core wakes up. With `flags=0` the job
  is kept, and `JobResGet(job)` waits for it and returns the function's result.
- `Spawn(fp,data,name,target_cpu)` with `target_cpu>=0` calls `SpawnQue`, which queues a
  `JOBT_SPAWN_TASK`. The target's Seth task calls `Spawn` locally, so the new task lands in that
  core's ring, and `SpawnQue` yields until it's done and returns the new `CTask`.

From [Demo/MultiCore/Lock.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Demo/MultiCore/Lock.TC):

```c
tmpm0=JobQue(&MPLockedInc,NULL,0,0);	//Run on core 0
tmpm1=JobQue(&MPLockedInc,NULL,1,0);	//Run on core 1
JobResGet(tmpm0);
JobResGet(tmpm1);
```

`JobQue` throws `'MultCore'` if `target_cpu` isn't below `mp_cnt`. More examples are in
`src/Demo/MultiCore/` (`LoadTest.TC`, `MPPrint.TC`, `Primes.TC` ...).

## Inter-processor interrupts

Cores signal each other through the local APIC's interrupt command register:

- `MPInt(vector,cpu)` sends one interrupt to one core, addressed by `dev.mp_apic_ids[cpu]`. Before
  `RLf_MP` it silently does nothing for a core that doesn't exist; after, it throws
  `'MultCore'`.
- `MPIntAll(vector)` sends to every core but this one.
- `MPNMInt` sends a non-maskable interrupt to every other core. Vector `I_NMI` is `_SYS_HLT`.
- `MPHalt` sets `mp_cnt=1` and uses `MPNMInt` to halt the others. `Reboot` does this first.

The vectors used between cores:

| Vector | Sent by | Effect |
| --- | --- | --- |
| `I_TIMER` | `IntCore0TimerHndlr`, every tick | The other cores' timer tick. |
| `I_WAKE` | `JobQue` | Wakes a halted core; the handler only acknowledges the APIC. |
| `I_MP_CRASH` | a crashing core | `IntMPCrash` on core 0 reports it. |

Only core 0 has a timer, the PIT. Each tick it forwards `I_TIMER` to every other core, so they
run the same `IRQ_TIMER` code and acknowledge their APIC instead of the PIC. See
[Interrupts and timers](interrupts.md#the-timer-interrupt).

## Core 0 only

- **The clock.** Only core 0 increments `cnts.jiffies` (`IntCore0TimerHndlr`).
- **The Ctrl+Alt keys.** The scheduler's `HANDLE_SYSF_KEY_EVENT` does nothing on other cores.
- **Interrupt setup.** `IntInit2` runs on core 0 only. The IDT is shared, so its handlers apply to
  every core.
- **Windows.** According to `MultiCore.DD`, only tasks on core 0 can have windows, though other
  cores can help render them.
- **The debugger.** A fault or `Dbg` on another core doesn't open a debugger there. `Fault3` (and
  `IntDivZero`) fill in `dbg.mp_crash` with the core, task, address and message, set `mp_cnt=1`,
  send `I_MP_CRASH` to core 0 and halt. `IntMPCrash` on core 0 prints the report in raw mode and
  calls `Panic`. See [Exceptions and the debugger](exceptions.md).

## Locking

There are three tools, from weakest to strongest.

**Interrupts off.** `PUSHFD`, `CLI`, ..., `POPFD` stops the current core from switching tasks or
taking an interrupt. It does nothing about other cores, so it's only enough for data a single
core owns, like its task ring (`TaskQueIns`). It's also used around spin locks, so the holder
can't be interrupted while it holds one.

**Locked bit operations.** `LBts`, `LBtr` and `LBtc` (declared in
[KernelB.TH](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KernelB.TH)) are
compiler intrinsics that become x86 `LOCK BTS`, `LOCK BTR` and `LOCK BTC`, which are atomic
across cores. `LBEqu` (set a bit to a value) is a small asm routine in `KUtils.TC` doing the same. They return the old bit, so a spin
lock is two lines:

```c
while (LBts(&task->srv_ctrl.flags,JOBCf_LOCKED))
  PAUSE
...change the job queue...
LBtr(&task->srv_ctrl.flags,JOBCf_LOCKED);
```

The kernel uses this pattern for job queues (`JOBCf_LOCKED`), task state (`TASKf_TASK_LOCK`),
dying queues (`CPUf_DYING_TASK_QUE`), heaps (`HClf_LOCKED`) and the PIT (`SEMA_SYS_TIMER`). Some
waiters `Yield` instead of `PAUSE`, which lets other tasks on the same core run.

**System semaphores.** `sys_semas[SEMA_SEMAS_NUM]` is an array of `CSema`, each a `Bool` padded
to `DFT_CACHE_LINE_WIDTH` (128) bytes so that no two share a cache line. Each `SEMA_` index is
either a lock or a system-wide flag: `SEMA_SYS_TIMER` and `SEMA_SND` are locks, while
`SEMA_SINGLE_USER` and `SEMA_DBG_MODE` are flags read with `Bt` and set with `LBEqu`.

**The `lock` keyword.** `lock stmt` makes the compiler put a `LOCK` prefix on the
read-modify-write instructions in that statement, as in `lock cnts.jiffies++` in
`IntCore0TimerHndlr`. The comment in `Lock.TC` warns to use it only on read-modify-write
statements, since misuse produces invalid instructions.

## On ARM64

ARM64 runs on one core for now. `MultiProc.TC` has `#ifdef ARCH_ARM64` versions:

- `Core0Init` sets `mp_cnt=1`, allocates `cpu_structs`, initializes entry 0 with Adam and sets
  `Gs` with `ArmSetGs`. There's no APIC.
- `Core0StartMP`, `MPIntAll`, `MPNMInt` and `MPAPICInit` are empty, `MPHalt` only sets
  `mp_cnt=1`, and `MPInt` only does the range check. `TSSNew` returns `NULL`.
- `CPUStructInit`, `CoreAPSethTask`, `JobQue` and `SpawnQue` are shared, so the code is ready
  for more cores. The comment there says they will be started through PSCI on QEMU virt or the
  spin tables on the Raspberry Pi, with the GIC sending inter-processor interrupts.
- `JobQue` to core 0 queues on Adam, but on ARM64 Adam doesn't run `SrvTaskCont` yet, so such a
  job isn't run.
- `LBts`, `LBtr` and `LBtc` compile to exclusive load/store loops (`LDAXR`/`STLXR`) in
  [BackArm64.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Compiler/BackArm64.TC),
  so spin locks are already atomic. The ARM64 back end doesn't act on the `lock` keyword: it
  never checks the `ICF_LOCK` flag. That's harmless on one core, but will need doing before
  more cores are started.

## Where to look

- [MultiProc.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/MultiProc.TC):
  everything on this page.
- [KInts.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KInts.TC):
  `INT_WAKE`, the timer IRQ on other cores.
- [KMisc.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KMisc.TC):
  `IntCore0TimerHndlr`.
- [src/Doc/MultiCore.DD](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Doc/MultiCore.DD)
  and `src/Demo/MultiCore/`.
