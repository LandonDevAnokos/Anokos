# The ARM64 port

The ARM64 (AArch64) kernel isn't a rewrite: the same TerryC kernel sources are compiled for
ARM64 by the Anokos compiler, and only the x86-specific parts have ARM64 versions. It runs on
QEMU's `virt` machine and the emulated Raspberry Pi 3 and 4. This page explains how the port
works inside. Status and the per-file checklist are in
[ARM64.md](https://github.com/LandonDevAnokos/Anokos/blob/main/ARM64.md).

## How a shared file is converted

`src/Kernel/KernelArm64.PRJ` lists the same files in the same order as `Kernel.PRJ`, starts with
`#define ARCH_ARM64 1`, and sets `Option(OPTf_ARM64,ON)` so the compiler emits ARM64 code. In a
shared file, an x86-only part is wrapped like this:

```c
#ifdef ARCH_ARM64
#include "/Kernel/Arm64/Sched"
#else
  ...x86 asm, port I/O or x86 registers...
#endif
```

The ARM64 version keeps the x86 labels and function names, so callers and `_extern`
declarations don't change. Small differences are inline `#ifdef` blocks instead (for example
`RawPutChar` in `src/Kernel/Display.TC` also writes to the serial port). Files with nothing
x86-specific are compiled unchanged; that's most of the kernel.

## The compiler side

`Cmp()` with `OPTf_ARM64` sends module code through the ARM64 back end
(`src/Compiler/BackArm64.TC`) and `asm {}` blocks through the ARM64 assembler
(`src/Compiler/AsmArm64.TC`). Expressions the compiler evaluates while compiling (`#exe`,
constant folding) still run as x86 code on the build machine. See [The compiler](compiler.md).

The back end maps the x86 register model onto ARM64 so the rest of the compiler needn't change:

| x86-64 | ARM64 | Notes |
| --- | --- | --- |
| RAX ... R15 | X0 ... X15 | same numbering; RSP and RBP are special |
| RSP | SP | |
| RBP | X29 | frame pointer |
| return address | X30 | pushed by the prologue |
| scratch | X16, X17 (IP0/IP1), X19-X22 | used inside generated sequences |
| FS base (`Fs`) | TPIDR_EL1 | current task |
| GS base (`Gs`) | TPIDRRO_EL0 | current CPU |

The calling convention matches x86 TempleOS: arguments on the stack, the result in X0, and a
function with a fixed argument count pops its own arguments. The prologue is
`STP X29,X30,[SP,-16]!` and `MOV X29,SP`, so the first argument is at `[X29+16]`, as at
`[RBP+16]` on x86. Asm routines must pop their arguments (`ADD SP,SP,8*n`) before `RET`.

`PUSHFD`/`POPFD`/`CLI`/`STI` exist as pseudo-instructions in ARM64 asm and in the code
generator. `GetRFlags`/`SetRFlags` translate DAIF to the x86 RFLAGS layout (bit 9 is "interrupts
on"), so shared code that saves and restores flags works.

## The boot image

The output is a Linux arm64 `Image` (`kernel8.img`): firmware and QEMU's `-kernel` load it at any
2 MiB-aligned address and jump to it. `src/Kernel/Arm64/KStart.TC` is the first code in it:

1. the Image header (magic `ARM\x64`, image size);
2. drop from EL3 or EL2 to EL1 if needed;
3. apply the relocation table the compiler appended (`ARM64_RELOCS`): absolute addresses in the
   image are fixed up for where it was loaded;
4. turn on the MMU with an identity map (4 KiB granule, 39-bit addresses, 1 GiB blocks, 2 MiB
   blocks where RAM and devices share a GiB), choosing a layout from the load address and CPU type;
5. install the exception vectors, set up a stack, run the module's top-level code and call
   `KMainArm64`.

`KMainArm64` and `KMainArm64B` (`src/Kernel/Arm64/KMain.TC`) do what `KStart64` and `KMain` do on
x86: CPU struct, Adam's heap and task, block pools, interrupts, the timer, keyboard, then
self tests and the terminal. See [Booting](boot.md#on-arm64).

The image keeps no symbol table and there's no compiler on ARM64 yet, so calls to code that
isn't in the image go to `ArmImportTrap`, which prints the caller's offset in the image on the
serial console and halts.

## Boards

One image runs on every board. `ArmBoardInit` (`src/Kernel/Arm64/Board.TC`) fills in
`arm_board` (a `CArmBoard`): UART, peripheral base, mailbox, interrupt controller and RTC
addresses, and how to reboot and power off.

| Board | RAM found by | Interrupts | Display |
| --- | --- | --- | --- |
| QEMU `virt` | device tree | GICv2 | ramfb |
| Raspberry Pi 3 | VideoCore mailbox | BCM2836 local controller | mailbox framebuffer |
| Raspberry Pi 4 | VideoCore mailbox | GIC-400 (GICv2) | mailbox framebuffer |
| Raspberry Pi 5 | (layout only, untested) | GIC-400 | mailbox framebuffer |

## The x86-specific parts and their ARM64 versions

- **Tasks** (`Arm64/Sched.TC`): the context switch saves X0-X15, X29, SP and the resume PC in
  the same `CTask` slots x86 uses for RAX-R15, RBP, RSP and RIP. New tasks start at
  `ARM64_TASK_START`. See [Tasks](tasks.md).
- **Interrupts** (`Arm64/KInts.TC`): all IRQs enter one vector, `ARM64_IRQ`, which saves the
  interrupted task's registers on its stack and calls `ArmIrq`. That asks the interrupt controller
  which IRQ it is and calls the handler set with `IntEntrySet`. Handlers are ordinary TerryC
  functions. The generic timer ticks at 1000 Hz (`JIFFY_FREQ`). See [Interrupts](interrupts.md).
- **Memory** (`Arm64/Mem.TC`): RAM comes from the device tree or the Pi mailbox and is put in the
  same `MEM_E820` table x86 fills from the BIOS. The allocator is the x86 algorithm rewritten in
  TerryC (`Arm64/MAllocFree.TC`). See [Memory](memory.md).
- **Strings, hashing, compression, math** (`Arm64/StrA.TC`, `KHashA.TC`, `Compress.TC`,
  `KMathA.TC`, `KMathSoft.TC`): x86 asm rewritten in TerryC or ARM64 asm, giving the same results.
- **try/catch** (`Arm64/KExcept.TC`). CPU faults print a crash report (`Arm64/Fault.TC`); they
  don't reach the debugger yet.
- **Display** (`Arm64/Video.TC`): fills the kernel's `vbe` globals from ramfb (QEMU virt, set up
  through fw_cfg) or the VideoCore mailbox (Pi), so the raw console and
  [the terminal](terminal.md) work unchanged. On a Pi the framebuffer's 2 MiB blocks are mapped
  non-cacheable so the display sees every write.
- **Keyboard** (`Arm64/Keyboard.TC`): the serial console is the keyboard. Its bytes, including
  VT100 cursor-key sequences, become PS/2 scan codes in `kbd.fifo2`, and the x86 keyboard code
  takes it from there. See [Input and output](io.md).
- **Serial** (`Arm64/Serial.TC`): a PL011 UART driver. Everything the raw console and the
  terminal print also goes there.
- **Shell**: until the compiler runs on ARM64, the terminal runs `ArmShell` (built-in commands)
  instead of the TerryC command line.

## Not done yet

- the compiler running on ARM64 (needed for a TerryC command line and JIT-compiled programs);
- storage drivers (virtio-blk, SD card) and so the file systems;
- faults into the debugger;
- more than one core;
- USB and virtio input, a UART receive interrupt.

## Trying it

```sh
python tools/anokos_build.py arm64
python tools/anokos_build.py run-arm64 --board rpi4      # QEMU window; type in:
nc -U build/arm64-rpi4-serial.sock
```

See [Building](build.md) and the Raspberry Pi SD card notes in ARM64.md.
