# Interrupts and timers

This page covers how hardware interrupts reach TerryC code: the x86 interrupt descriptor table
(IDT), the 8259 PIC, `interrupt` functions, and the 1000 Hz timer that drives `cnts.jiffies`.
It also covers the other clocks (PIT count, HPET, TSC) and the CPU exception stubs. The ARM64
port does the same jobs with a GIC and the generic timer; see [On ARM64](#on-arm64).

## The interrupt descriptor table

The x86 code is in
[KInts.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KInts.TC). There is
one IDT for all cores, `dev.idt`: 256 entries of 16 bytes.

- `IntInit1` runs on every core. On core 0 it allocates `dev.idt` and points all 256 vectors at
  `IntNop`. On every core it loads the table with `LIDT`.
- `IntInit2` runs on core 0 only and installs the real handlers (table below).
- `IntEntrySet(irq,handler,type=IDTET_IRQ,dpl=0)` writes one gate and returns the old handler,
  so you can put it back later. `IDTET_IRQ` (0x0E) is an interrupt gate, which clears IF on
  entry; `IDTET_TRAP` (0x0F) is a trap gate, which doesn't.
- `IntEntryGet(irq)` reads a handler address back out of the gate.

At boot, `KMain` in [KMain.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KMain.TC)
calls these in order: `Core0Init`, `IntInit1`, `TimersInit` (PIT and HPET), `IntsInit` (the
PIC), `SetRFlags(RFLAGG_NORMAL)` to enable interrupts, `IntInit2`, then sets `RLf_INTERRUPTS` in
`sys_run_level` and calibrates with `TimeCal`. The keyboard and mouse handlers come later, from
`KbdInit` and `MsHardDrvrInstall`.

### Vector numbers

The `I_` constants are in
[KernelA.TH](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KernelA.TH).

| Vector | Name | Handler | Notes |
| --- | --- | --- | --- |
| 0x00 | `I_DIV_ZERO` | `IntDivZero` | Throws `'DivZero'`, so it can be caught. |
| 0x01-0x1F | CPU exceptions | fault stubs | Go to `Fault2` and the debugger. `I_SINGLE_STEP` 1, `I_BPT` 3, `I_PAGE_FAULT` 0x0E. |
| 0x02 | `I_NMI` | `_SYS_HLT` | Halts the core. `MPHalt` uses this. |
| 0x20 | `I_TIMER` | `IRQ_TIMER` | IRQ 0, the timer. |
| 0x21 | IRQ 1 | `IRQKbd` | Keyboard, set in `KbdInit`. |
| 0x2C | IRQ 12 | `IRQMsHard` | PS/2 mouse, set in `MsHardDrvrInstall`. |
| 0x30 | `I_MP_CRASH` | `IntMPCrash` | Another core crashed; see [Multicore](multicore.md). |
| 0x31 | `I_WAKE` | `INT_WAKE` | Wakes a halted core; just acknowledges the APIC. |
| 0x32 | `I_DBG` | fault stub | `Panic` and `Dbg` do `INT I_DBG` to enter the debugger. |
| 0x40 | `I_USER` | | First vector free for your own use. |

Everything else stays at `IntNop`, which acknowledges both PICs and the local APIC so a stray
interrupt can't jam the controller. A comment in `IntInit2` records that this was needed for an
unexplained vector 0x27.

## The 8259 PIC

`IntsInit` programs the two legacy 8259 interrupt controllers. It moves IRQs 0-7 to vectors
0x20-0x27 and IRQs 8-15 to 0x28-0x2F, out of the range Intel reserves for exceptions, and masks
everything except IRQ 0 (the timer) and IRQ 2 (the link to the second PIC). Drivers unmask their
own line when they install a handler, for example `KbdInit` does `OutU8(0x21,InU8(0x21)&~2)`.

The local APIC is used too, but for different jobs: inter-processor interrupts, and the
end-of-interrupt write on the other cores (`LAPIC_EOI`). See [Multicore](multicore.md).

## Interrupt functions

TerryC has an `interrupt` keyword for writing handlers without asm:

```c
interrupt U0 IRQKbd()
{
  CLD
  OutU8(0x20,0x20);	//Acknowledge the PIC
  kbd.irqs_working=TRUE;
  ...
  KbdPktRead;
}
```

For an `interrupt` function the x86 back end saves and restores every register the function may
clobber, not just the callee-saved ones, and ends with `IRETQ` instead of `RET` (see `IC_ENTER`
and `IC_LEAVE` in
[OptPass789A.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Compiler/OptPass789A.TC)).
`haserrcode` is a companion keyword for exceptions that push an error code; it pops the code
before `IRETQ`. The function must acknowledge the interrupt itself: the PIC with
`OutU8(0x20,0x20)` (and `OutU8(0xA0,0x20)` for IRQs 8-15), or the local APIC.

An interrupt function runs on whatever stack and task it interrupted, with interrupts off. Keep
it short: read the device, put data in a FIFO, return. The keyboard handler stores bytes in
`kbd.fifo` and the work happens later in a task.

The timer handler is different. It's written in asm because it must save the whole task context
and then enter the scheduler.

## The timer interrupt

`TimersInit` (in `KMain.TC`) programs PIT channel 0 in rate-generator mode with the divisor
`SYS_TIMER0_PERIOD`, which gives `JIFFY_FREQ` (1000) interrupts a second. Each one runs
`IRQ_TIMER`:

1. `TASK_CONTEXT_SAVE` stores all registers and the FPU state in `Fs`, then the interrupted
   `rip`, `rflags` and `rsp` are copied from the interrupt frame. The interrupted task now looks
   exactly as if it had called `Yield`.
2. `lock` increment `Gs->total_jiffies`. If the task has `TASKf_IDLE`, also count
   `Gs->idle_pt_hits` (that's how CPU load is measured).
3. If `Gs->profiler_timer_irq` is set, call it with the task (the profiler samples `rip` here).
4. On core 0, call `IntCore0TimerHndlr`. On other cores, write the local APIC EOI.
5. Jump to the scheduler at `RESTORE_RSI_TASK`, starting with the interrupted task itself, or at
   the Seth task if the idle task was interrupted.

`IntCore0TimerHndlr` in
[KMisc.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KMisc.TC) increments
`cnts.jiffies`, adds a period's worth of PIT ticks to `cnts.timer`, sends `I_TIMER` to every
other core with `MPInt`, and acknowledges the PIC. Only core 0 has the PIT; the others get their
tick as an inter-processor interrupt.

Step 5 is why this doesn't make the system preemptive. The scheduler starts its walk at the task
that was interrupted, which is normally still ready, so it resumes right away. But the walk
does check the Ctrl+Alt key flags and wakes the idle core, which is what makes Ctrl+Alt+C and
`Sleep` work. See [Tasks and scheduling](tasks.md#is-it-preemptive).

## Jiffies

A jiffy is one timer tick, 1 ms.

- `cnts.jiffies` is the global count, kept by core 0. It's what `Sleep` and the scheduler's
  `wake_jiffy` test use.
- `Gs->total_jiffies` is a per-core count of timer interrupts that core has taken.
- `JIFFY_FREQ` converts: `Sleep(mS)` sets `wake_jiffy=cnts.jiffies+mS*JIFFY_FREQ/1000`.

`Sleep` only promises "not before". A sleeping task runs again the first time the scheduler
reaches it after the jiffy has passed. Since scheduling is cooperative, that depends on the other
tasks yielding. `Sleep` is for pausing without hogging the CPU, not for precise timing.

## Clocks

There are several time sources, each with its own use:

| Function | Source | Unit | Use |
| --- | --- | --- | --- |
| `cnts.jiffies` | timer IRQ count | 1 ms | Sleeping, timeouts, `Blink`. |
| `SysTimerRead` | PIT count + `cnts.timer` | `SYS_TIMER_FREQ` Hz | Finer time without HPET. |
| `HPET` | HPET main counter | `cnts.HPET_freq` Hz | Precise time, if present. |
| `GetTSC` | `RDTSC` | `cnts.time_stamp_freq` Hz | Fastest; cycle counts. |
| `tS` | HPET, else `SysTimerRead` | seconds, `F64` | Time since boot. |

- `SysTimerRead` latches PIT channel 0 and adds the elapsed part of the current period to
  `cnts.timer`. If the counter has just wrapped but the tick hasn't been counted yet (the PIC has
  IRQ 0 pending), it allows for that.
- `TimersInit` turns the HPET on (on Intel chipsets it first enables it through the LPC bridge)
  and reads its period from `HPET_GCAP_ID`. If the period isn't plausible, `cnts.HPET_freq` stays
  0 and everything falls back to the PIT.
- `TimeCal` measures the TSC frequency against the HPET (or PIT) between two calls and stores it
  in `cnts.time_stamp_freq`. `KMain` calls it once after interrupts are on and again after the
  compiler loads, saving `cnts.time_stamp_freq_initial`.
- `Busy(uS)` spins without yielding: on the HPET if there is one, otherwise by calling `PortNop`
  (one ISA port read, about 1-2 us) that many times. It's used where `Sleep` can't be, such as
  before interrupts work or while starting other cores.

## CPU exceptions

Vectors 1 to 0x1F, and `I_DBG`, point into a block of tiny stubs made by `IntFaultHndlrsNew` and
kept in `dbg.int_fault_code`. Each stub is 7 bytes, `PUSH vector` then `JMP INT_FAULT`, so one
common handler knows which exception happened.

`INT_FAULT` stores the vector in `Fs->fault_num`, saves the task context like the timer does,
and pops the CPU's error code into `Fs->fault_err_code` for the vectors that push one
(`INT_FAULT_ERR_CODE_BITMAP`: 8, 10-14 and 17). Then it calls `Fault2(fault_num,err_code)` and
resumes the task with `RESTORE_FS_TASK`, so a debugger command that changes `rip` takes effect.
What `Fault2` does is in [Exceptions and the debugger](exceptions.md#cpu-faults).

Division by zero is the exception: vector 0 goes to `IntDivZero`, which turns it into a TerryC
`throw('DivZero')` on core 0.

## On ARM64

The ARM64 version is
[Arm64/KInts.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Arm64/KInts.TC)
plus the vectors in
[Arm64/KStart.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Arm64/KStart.TC).
The function names are kept so shared code works unchanged.

### One vector, a dispatch table

ARM64 has no IDT. `VBAR_EL1` points at `ARM64_VECTORS`, 16 entries of 128 bytes. The IRQ entry
for the kernel's own level (entry 5) branches to `ARM64_IRQ`; all others go to `ARM64_EXCEPT`
and a crash report (see [Exceptions](exceptions.md#on-arm64)).

`IntEntrySet(irq,handler)` stores the handler in `arm_irq_hndlrs[irq]` and unmasks that IRQ at
the controller; passing `NULL` masks it again. Handlers are ordinary TerryC functions: they run
with IRQs masked and needn't acknowledge anything, because `ArmIrq` does it. The `interrupt`
keyword is rejected by the ARM64 back end. IRQ numbers are GIC numbers: 0-15 are software
interrupts, 16-31 are per-core (the generic timer is `ARM_IRQ_TIMER`, 30), 32 and up are
devices.

### Interrupt controllers

- **GICv2** (QEMU virt, Raspberry Pi 4 and 5). `IntsInit` calls `ArmGicInit`: disable and clear
  every line, give them all the same priority, route device IRQs to CPU 0, then turn the
  distributor and CPU interface on. `ArmIrq` reads `GICC_IAR`, dispatches, writes `GICC_EOIR`,
  and loops until the GIC reports nothing pending.
- **BCM2836 local controller** (Raspberry Pi 3). It only carries the per-core timer here:
  `ArmIrq` checks the timer bit in `LOCAL_IRQ_SRC0`.

Which one is used, and its address, comes from `arm_board`. `ArmBoardInit` in
[Arm64/Board.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Arm64/Board.TC)
fills it in from a per-board table, chosen by the memory layout the boot code detected.

### The IRQ path

```
  IRQ --> ARM64_VECTORS[5] --> ARM64_IRQ
            save X0-X30, ELR, SPSR in a 272-byte frame on the current stack
            BL ArmIrq            (find the IRQ, call its handler, EOI)
            Fs->rip = ARM64_IRQ_RESUME, Fs->rsp = frame, Fs->rflags = 2
            B RESTORE_RSI_TASK   (the scheduler, starting at Fs, or Seth if Fs is idle)

  later, when the task is chosen:
  _TASK_CONTEXT_RESTORE --> ARM64_IRQ_RESUME: reload the frame, ERET
```

This mirrors x86 `IRQ_TIMER`, with one difference: every ARM64 IRQ goes through the scheduler
afterwards, while on x86 only the timer does. If `Fs` is still 0 (an IRQ before the first task
exists), `ARM64_IRQ` just returns.

### The generic timer

`IntInit2` programs the physical generic timer to fire every `ArmCntFrq/JIFFY_FREQ` counts,
through the compare register (`ArmCntpCval`), and installs `ArmTimerIrq`. That handler moves the
compare value on by one interval (or to "now plus one interval" if it fell behind), then does
what `IRQ_TIMER` does: `Gs->total_jiffies`, `idle_pt_hits`, the profiler hook, and
`IntCore0TimerHndlr` on core 0.

`IntInit2` then waits 20 ms and checks that the timer really ticked. If it didn't,
`arm_timer_irq_on` stays `FALSE`, the IRQ is masked again, and `ArmJiffiesUpdate` (called on
every task switch, in
[Arm64/Sched.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Arm64/Sched.TC))
computes `cnts.jiffies` from the counter instead. The system still works, it just idles with
`PAUSE` instead of `WFI`.

### Time functions

The generic timer counter stands in for the PIT, HPET and TSC. `cnts.HPET_freq` is set to
`ArmCntFrq` at boot, `HPET` returns `ArmCntPct`, `SysTimerRead` scales the counter to
`SYS_TIMER_FREQ`, and `GetTSC` compiles to a read of `CNTVCT_EL0`. So `tS`, `Busy` and `TimeCal`
work unchanged.

## Where to look

- [KInts.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KInts.TC): IDT,
  PIC, `IRQ_TIMER`, `INT_FAULT`, fault stubs.
- [KMisc.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KMisc.TC):
  `IntCore0TimerHndlr`, `SysTimerRead`, `HPET`, `TimeCal`, `tS`, `Busy`, `Sleep`.
- [KMain.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KMain.TC):
  `TimersInit` and the boot order.
- [Arm64/KInts.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Arm64/KInts.TC)
  and [Arm64/KStart.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Arm64/KStart.TC):
  the ARM64 versions.
