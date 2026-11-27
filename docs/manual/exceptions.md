# Exceptions and the debugger

TerryC has `try`, `catch` and `throw`, implemented in a few dozen lines of kernel code with one
list of catch frames per task. CPU faults take a different road: they stop the system in a small
built-in debugger, which gives you a TerryC command line inside the task that crashed. This page
explains both, plus breakpoints, stack traces and the raw screen mode the debugger prints in.

## try, catch and throw

```c
try {
  ExeFile("Foo.TC");
} catch {
  if (Fs->except_ch=='Break')
    "Interrupted\n";
  PutExcept;	//Print the code and callers, and mark it caught
}
```

- `throw(ch)` raises an exception. `ch` is an `I64` that's normally a character constant of up to
  8 letters, like `'DivZero'` or `'Compiler'`. There are no exception objects.
- A `catch` block reads the code from `Fs->except_ch`. Up to eight return addresses from where it
  was thrown are in `Fs->except_callers`.
- A `catch` block must set `Fs->catch_except=TRUE` to stop the exception. `PutExcept` prints
  `Except:` with the code and the callers, and sets it for you. If the catch block doesn't set it,
  the exception keeps going to the next `try` further out.
- If nothing catches it, `throw` prints the exception and calls `Panic("Unhandled Exception")`.

Codes you'll meet in the kernel and compiler include `'Compiler'`, `'Drv'`, `'BlkDev'`,
`'StrPrint'`, `'Scan'`, `'File'`, `'OutMem'`, `'MultCore'`, `'DivZero'` and `'Break'`.

The terminal relies on this. `UserTaskCont` in
[KTask.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KTask.TC) wraps each
command line in `try`/`catch` with `PutExcept`, so an error in what you typed prints and returns
you to the prompt instead of killing the terminal. Jobs run by servant tasks (`JobRunOne` in
[Job.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Job.TC)) are wrapped in a
`catch` that just sets `Fs->catch_except=TRUE`, so a failing job doesn't take the servant down.

## How try and catch work

The kernel side is
[KExcept.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KExcept.TC); the
compiler side is `PrsTryBlk` in
[PrsStmt.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Compiler/PrsStmt.TC).

The compiler turns a `try`/`catch` into this shape:

```
        SysTry(&catch_label,&untry_label)   push a catch frame
        ...try body...
  untry_label:
        SysUntry()                          pop the catch frame
        JMP done_label
  catch_label:
        ...catch body...
        RET                                 back into throw()
  done_label:
```

`SysTry` allocates a `CExcept` and appends it to the current task's list,
`Fs->next_except`/`last_except`. The frame records the two labels, the caller's `rbp` and `rsp`,
the flags, and the registers TerryC callees must preserve (`SaveExceptRegs`: RSI, RDI, R10-R15).
Because the list is in the `CTask`, every task has its own, and nesting works by pushing more
frames. A `return` from inside a `try` calls `SysUntry` once for each level it leaves.

`throw` then does this:

1. Store `ch` in `Fs->except_ch`, the callers in `except_callers`, and its own `rbp` in
   `except_rbp`. Clear `catch_except`. Log the exception with `AdamLog`, unless `no_log` was
   passed.
2. While there are frames, take the innermost one:
   - `TestExcept` reloads the frame's registers, `rbp` and flags, and jumps to its catch label.
     The catch body runs as a subroutine called from `throw`, on top of the throwing stack, but
     with `rbp` set to the function that has the `try`, so its local variables work.
   - When the catch body returns, if it set `catch_except`, `TakeExcept` resets `rsp` to the
     frame's saved value and jumps to the untry label. `SysUntry` drops the frame and execution
     carries on after the `try`/`catch`. The throwing stack is simply abandoned.
   - Otherwise restore `throw`'s own `rbp`, free the frame, and try the next one out.
3. With no frames left, print the exception and `Panic`.

Two things follow from this. The catch block runs *before* the stack is unwound, so a catch can
look at the state where the exception happened. And nothing runs destructors or frees memory
along the way: whatever the abandoned code allocated stays allocated until the task's heap is
freed.

The compiler also turns off register variables in any function that contains a `try`
(`CCF_NO_REG_OPT`). That way locals live in the stack frame and are correct when the catch block
reads them through `rbp`.

## Break and Ctrl+Alt+C

`Break()` is how a running task is interrupted from outside. It clears the task's sleep and
awaiting-message state, releases locked drives, flushes its messages and does `throw('Break')`.
If the task has `TASKf_BREAK_TO_SHIFT_ESC` it sends itself Shift+Esc instead.

Ctrl+Alt+C doesn't call `Break` directly. The scheduler sees the key flag on core 0 and rewrites
the focus task's saved `rip` to `&Break`, so the next time that task runs it calls `Break`
([Tasks and scheduling](tasks.md#ctrlalt-keys-and-breaking-a-task)). Code that mustn't be broken
in the middle wraps itself in `BreakLock`/`BreakUnlock`. A break that arrives in between is
marked pending and delivered by `BreakUnlock`.

## CPU faults

A CPU exception (page fault, general protection, invalid opcode, a breakpoint `INT3`, a single
step ...) goes through a per-vector stub to `INT_FAULT` in
[KInts.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KInts.TC), which saves
the whole task context into `Fs` and calls `Fault2(fault_num,fault_err_code)`. See
[Interrupts and timers](interrupts.md#cpu-exceptions). Divide by zero is the one fault that's
turned into a `throw` instead (`IntDivZero`).

`Fault2` in [KDbg.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KDbg.TC):

1. Turns on single-user mode (`SingleUser(ON)`). From now on `Yield` returns at once, so no
   other task runs while you're in the debugger, and nothing disturbs the saved context.
2. Remembers the focus task in `dbg.focus_task`. For `I_BPT` it backs `rip` up by one byte so it
   points at the breakpoint.
3. Switches to raw output (below) and prints the task's title, the fault number and name, the
   error code, `RIP` and `RSP`, and for a page fault the faulting address from CR2.
4. Sets debug mode (`DbgMode(ON)`) and calls `Fault3`.

`Fault3` prints the debugger banner, beeps, prints the `Dbg`/`Panic` message if there is one,
prints a `CallerRep` stack trace for a panic, and calls `Dbg2`. `Dbg2` makes the task the focus
and runs `UserTaskCont`: a normal TerryC command line, in the task that faulted.

When you leave with `G`, everything unwinds back up to `INT_FAULT`, which resumes the task from
its saved context through the scheduler (`RESTORE_FS_TASK`). If the task called `Exit` while you
were debugging, `TASKf_KILL_AFTER_DBG` makes it exit now.

## The debugger

`Dbg(msg,num)` enters the debugger on purpose; `Panic(msg,num)` does the same for an error and
adds a stack trace. Both store the message in `dbg.msg`/`dbg.msg_num` and execute `INT I_DBG`,
so they arrive through exactly the same `Fault2` path as a real fault.

`DbgMode(val)` and `IsDbgMode()` set and test `sys_semas[SEMA_DBG_MODE]`. Code checks it to
behave differently while debugging: `Exit` defers itself, `G` ends the debug command line, and
Ctrl+Alt+D won't set a new breakpoint.

Because the debugger is just the command line, any function can be called from it. `DbgHelp`
(type `Help;`) lists the common ones:

| Command | Does |
| --- | --- |
| `G;` | Go: resume the task (`G(addr)` resumes elsewhere). |
| `G2;` | Clear all breakpoints, restore focus and the screen, then go. |
| `S;` | Single-step one instruction. |
| `B(addr);` | Toggle a breakpoint. `B2;` clears all. |
| `Dr;` | Dump the saved registers. |
| `CallerRep;` | Stack trace. `StkRep;` shows the raw stack. |
| `D(addr);`, `Dm(addr);` | Dump memory. |
| `U(addr);`, `Uf("Fun");` | Unassemble. |
| `_RIP`, `_RAX` ... | The task's saved registers, readable and writable (macros for `Fs->rip` ...). |
| `Exit;` | Kill the task. |

## Breakpoints

`BptS(addr,task)` adds a `CBpt` (address plus the original byte) to `task->bpt_lst` and, if the
task is the current one, writes the `INT3` opcode (`OC_BPT`) at `addr`. `BptR` removes one, `B`
toggles, `B2` removes all.

All tasks share the same code, so a breakpoint byte would stop every task that ran over it. The
context switch prevents that: `TASK_CONTEXT_SAVE` puts the original bytes back when a task is
switched out, and `_TASK_CONTEXT_RESTORE` writes `INT3` again when it's switched in, unless the
task has `TASKf_DISABLE_BPTS`. So a breakpoint only fires in the task that set it.

`S` sets the CPU's trap flag (`RFLAGf_TRAP`) and `TASKf_DISABLE_BPTS` in the saved context and
resumes, so the CPU faults again after one instruction. `G` clears both. Ctrl+Alt+D (`CtrlAltD`
in [KeyDev.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KeyDev.TC)) sets a
breakpoint at the address the keyboard interrupt will return to, which drops you into the
debugger in whatever task was running.

## Stack traces

TerryC functions keep a standard `RBP` frame chain, and the trace functions walk it:

- `Caller(n)` returns the return address `n` frames up. `throw` uses it to fill
  `except_callers`.
- `CallerRep(rbp,task)` prints each return address with its symbol (the `%P` format looks up the
  nearest symbol), stopping when a frame leaves the task's stack (`ChkOnStk`) or the chain stops
  going up.
- `StkRep(task)` prints 32 raw stack words, highlighting the ones that are return addresses.
- `TaskCaller(task,n)` does the same as `Caller` for another task's saved context.
- `UnusedStk(task)` reports how much of the stack is still free.

## Raw output

While debugging, normal output can't be trusted: the window manager isn't running (single-user
mode) and the document code may be what crashed. So the debugger prints in raw mode.

`Raw(ON)` clears the current task's `DISPLAYf_NOT_RAW` flag, and `IsRaw()` tests it. In raw
mode the raw put-key handlers in the KeyDev chain (`KDRawPutKey`, `KDRawPutS` in `KeyDev.TC`)
send every character to `RawPutChar` in
[Display.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Display.TC), which
draws straight into the framebuffer (or VGA text memory), bypassing windows and DolDoc.
`text.raw_flags` controls the details. `RWF_SHOW_DOLLAR`, which the debugger sets, prints DolDoc
`$` codes as plain text instead of hiding them.

`RawPrint(mS,fmt,...)` prints in raw mode and then busy-waits, so a message can be seen even when
nothing else works. `RawD` and `RawDm` do the same for memory dumps. They're useful when
debugging the kernel itself: a comment in `KMain` says to debug with `Snd` and `Busy` before
`RLf_RAW` is set early in boot, and with `RawPrint` after.

## Faults on other cores

There's only one debugger, and it runs on core 0. `Fault3` on any other core fills in
`dbg.mp_crash`, sends `I_MP_CRASH` to core 0 and halts. `IntMPCrash` on core 0 prints the core,
task, address and message in raw mode and calls `Panic`. See [Multicore](multicore.md#core-0-only).

## On ARM64

**try, catch and throw work.** `throw`, `SysTry` and `SysUntry` are shared. Only the three asm
routines are new, in
[Arm64/KExcept.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Arm64/KExcept.TC):

- `_SAVE_EXCEPT_REGS` saves X6, X7 and X10-X15, the ARM64 registers in the x86 RSI, RDI and
  R10-R15 slots of `CExcept`.
- `_TEST_EXCEPT` first pushes its return address (X30) on the stack, because a catch block ends
  with a stack-popping return like x86 `RET`. Then it reloads the registers and X29 (`rbp`),
  converts the saved x86-style flags back into DAIF, and branches to the catch label.
- `_TAKE_EXCEPT` resets SP and branches to the untry label.

`ArmExceptTest` in
[Arm64/KMain.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Arm64/KMain.TC)
checks this at every boot.

**Faults don't reach the debugger yet.** Every ARM64 exception except the kernel IRQ goes from
`ARM64_VECTORS` to `ARM64_EXCEPT` in
[Arm64/KStart.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Arm64/KStart.TC).
It saves X0-X30, ELR, SPSR and ESR in a frame and calls `ArmExcept(kind,regs)` in
[Arm64/Fault.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Arm64/Fault.TC).
That prints a crash report on the serial console (the exception kind, the ESR class decoded by
`ArmEsrClass` such as "Data abort", ELR as an image offset, FAR, SPSR, SP and all registers) and
halts with `WFI`.

`Panic` and `Dbg` call `ArmPanic` instead of `INT I_DBG`. It prints the message and the caller's
image offset on serial and halts. So an unhandled `throw` also ends there. `Fault2` itself is
already partly converted: for a page fault it reads `ArmFar` instead of CR2.

Other differences:

- ARM64 integer division by zero doesn't fault (the result is 0), so `IntDivZero` only runs if
  something calls it.
- Breakpoints and single-stepping aren't implemented in the ARM64 context switch.
- `ArmImportTrap` is a related crash stop: the ARM64 compiler calls it in place of functions that
  are only provided by modules that don't load on ARM64 yet.

## Where to look

- [KExcept.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KExcept.TC):
  `SysTry`, `SysUntry`, `throw`, `PutExcept`, `Break`.
- [KDbg.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KDbg.TC): `Fault2`,
  `Fault3`, `Dbg2`, `Panic`, `Dbg`, breakpoints, `CallerRep`, `StkRep`, `RawPrint`.
- [KInts.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KInts.TC):
  `INT_FAULT` and the fault stubs.
- [KMisc.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KMisc.TC): `Raw`,
  `DbgMode`, `SingleUser`.
- [Arm64/KExcept.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Arm64/KExcept.TC)
  and [Arm64/Fault.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Arm64/Fault.TC).
