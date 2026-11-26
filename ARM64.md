# Anokos on ARM64

This is the plan and status for converting the Anokos kernel to ARM64 (AArch64). It's part of
[milestone 5](ROADMAP.md#milestone-5-multi-architecture-support). The x86-64 kernel isn't
rewritten. It is converted: the same TerryC sources get built for ARM64 by the Anokos compiler,
and only the x86-specific parts get ARM64 versions.

## Status

| Part | State |
| --- | --- |
| Compiler: ARM64 code generator (`src/Compiler/BackArm64.TC`) | Working: integer, pointer, F64, switch, varargs and bit operations; self test passes |
| Compiler: ARM64 assembler for `asm {}` blocks (`src/Compiler/AsmArm64.TC`) | Working: integer, load/store, branches, system registers, barriers, FP |
| Compiler: ARM64 boot image output (`Cmp()` with `OPTf_ARM64`) | Working |
| Boot: Image header, EL3/EL2 to EL1, self-relocation, MMU, vectors (`src/Kernel/Arm64/KStart.TC`) | Working on QEMU virt, raspi3b, raspi4b |
| Kernel asm converted: KUtils, KMathA, string asm from StrA | Done (see the checklist) |
| Rest of the kernel (memory, tasks, interrupts, devices, KMain) | Not started; see the checklist |
| Compiler running *on* ARM64 (JIT) | Not started |

The ARM64 kernel boots to a serial console on QEMU's `virt` machine and on the emulated Raspberry
Pi 3 and 4. It prints the board, CPU and boot details, runs a self test of the ARM64 code
generator, and starts a small serial monitor (`h` help, `t` self test, `r` reboot, `o` power off).

## Building and running

Everything is compiled by the Anokos compiler inside Anokos, as on x86-64. There's no C
compiler or GNU assembler involved.

```sh
python tools/anokos_build.py kernel            # stage 1: build the compiler (with the ARM64 back end)
python tools/anokos_build.py arm64             # stage 3: compile the ARM64 kernel inside Anokos
python tools/anokos_build.py run-arm64         # boot it on QEMU virt (add --board rpi3 or rpi4)
```

`arm64` boots x86-64 Anokos in QEMU with `tools/stage3/Arm64Build.TC` added to its start-up.
That runs

```
Cmp("::/Kernel/KernelArm64","B:/KernelArm64","B:/Kernel8");
```

and sends `Kernel8.IMG` back over the serial port as `build/arm64/kernel8.img`. Stage 3 takes
about 6 seconds. `run-arm64` uses `qemu-system-aarch64 -kernel build/arm64/kernel8.img` and
writes the serial output to `build/arm64/run_<board>_serial.log`. Without `--headless`, QEMU
stays open with its serial console in the log file.

You can also run the compile yourself in the Anokos terminal:
`Cmp("/Kernel/KernelArm64","B:/KernelArm64","B:/Kernel8");`.

## How it works

### Compiler

`Option(OPTf_ARM64,ON)` in a PRJ file (see `src/Kernel/KernelArm64.PRJ`) switches an AOT
compile to ARM64.

- **Front end and optimizer are shared.** Parsing and optimizer passes 0-6 are unchanged. Instead
  of the x86 back end (`OptPass789A` and `BackA/B/C/FA/FB`), `A64Pass` in `BackArm64.TC` turns the
  intermediate code into ARM64 instructions.
- **Registers.** The optimizer allocates x86 register numbers. They map onto X0-X15, with RSP as
  SP and RBP as X29. X16-X17 and X19-X22 are scratch registers for the code generator.
- **Only module code is ARM64.** During an AOT compile the compiler also JIT-compiles small
  expressions and runs them on the build machine (`#exe`, constant expressions, asm operands).
  Those stay x86-64. `COCCompile(...,target=TRUE)` marks the code that goes into the module.
- **Assembler.** `asm {}` blocks use ARM64 syntax when `OPTf_ARM64` is on, with three differences
  from standard ARM64 syntax:
  - immediates have no `#`, because TerryC uses `#` for directives: `ADD SP,SP,16`, `LDR X1,[X2,8]`;
  - `B.EQ` and `BEQ` are both accepted;
  - `MOV Xd,imm64` expands to MOVZ/MOVN/MOVK as needed.

  Labels, `ALIGN`, `DU8`-`DU64` and `DUP` work as on x86. Branches, ADR/ADRP and literal loads to
  labels are PC relative and patched through the usual unresolved-symbol lists, using new
  ARM64 relocation types.
- **Output.** With `OPTf_ARM64`, `Cmp()` writes a flat boot image (`.IMG`) instead of a BIN file. It
  is the module, followed by a table of 64-bit absolute addresses for the boot code to relocate
  and a table of top-level code blocks to run. The boot code exports the slots the compiler fills
  in: `ARM64_RELOCS`, `ARM64_MAINS` and `ARM64_IMAGE_SIZE`. Imports aren't allowed, because there's
  no loader.

### Calling convention

It's the x86-64 TerryC convention, so the frame layout the optimizer computes stays valid:

- Args are pushed right to left, 8 bytes each.
- Functions start with `STP X29,X30,[SP,-16]!` and `MOV X29,SP`, so the first arg is at `[X29+16]`.
- Results come back in X0. F64 results come back as raw bits.
- Functions with a fixed arg count pop their own args, like x86 `RET1`. Varargs callers pop them.
- Callees keep X6, X7, X10-X15, X29 and SP.
- Stack slots are 8 bytes, so SP is only 8-byte aligned. The boot code turns off SP alignment
  checking.
- Compiled statement blocks (and `switch` sub-blocks) return by popping their return address,
  like x86 `RET`. `Call()` handles both kinds.

TerryC classes are packed, so TerryC code makes unaligned accesses. Those need Normal memory, so
`KStart` turns the MMU on with an identity map before any TerryC code runs.

### Boot

`src/Kernel/Arm64/KStart.TC` replaces KStart16/32/64. The image starts with an arm64 Linux
`Image` header, which both QEMU `-kernel` and the Raspberry Pi firmware (`kernel8.img`) load.
The boot code then:

1. parks secondary CPUs;
2. drops from EL3 or EL2 to EL1;
3. applies the relocation table;
4. sets up a 4 KiB-granule identity map from a per-board memory layout;
5. installs the exception vectors;
6. runs top-level code and calls `KMainArm64`.

It also defines the kernel's boot data labels (`SYS_RUN_LEVEL`, `MEM_HEAP_BASE`, the progress
bars, `SYS_SEMAS` and so on) with the same names and sizes as on x86, so `KernelB.TH` binds to
them unchanged.

## Boards and the Raspberry Pi

One image runs on every board. `KStart` picks a memory layout from where it was loaded and the
CPU part number. `src/Kernel/Arm64/Board.TC` then fills in a `CArmBoard` with the UART, the
peripheral window, the mailbox base and the reset/power-off method.

| Board | Layout | UART | Tested |
| --- | --- | --- | --- |
| QEMU `virt` | RAM from 1 GiB | PL011 at 0x09000000 | QEMU |
| Raspberry Pi 3 (BCM2837) | RAM below 0x3F000000 | PL011 at 0x3F201000 | QEMU `raspi3b` |
| Raspberry Pi 4 (BCM2711) | peripherals at 0xFC000000 | PL011 at 0xFE201000 | QEMU `raspi4b` |
| Raspberry Pi 5 (BCM2712) | peripherals from 64 GiB | PL011 at 0x107D001000 | not yet |

To try it on real Raspberry Pi hardware (untested so far), copy the firmware (`bootcode.bin` on
the Pi 3, plus `start*.elf` and `fixup*.dat`) and `build/arm64/kernel8.img` to a FAT SD card, and
add this `config.txt`:

```
arm_64bit=1
kernel=kernel8.img
enable_uart=1
dtoverlay=disable-bt
```

Use a 3.3 V serial adapter on GPIO 14/15. The Pi 5 uses its 3-pin debug UART.

To add a board:

1. add a memory layout to `ARM64_MMU_INIT` and `ARM64_LAYOUT_KINDS` in `KStart`;
2. add a `BOARD_` number in `KernelA64.TH`;
3. add a case in `ArmBoardInit`.

## Converting the kernel: checklist

`src/Kernel/KernelArm64.PRJ` follows the include order of `Kernel.PRJ`, using a file from
`src/Kernel/Arm64/` wherever the x86 file can't be used. Shared files go in unchanged once their
dependencies are in. TerryC has `#ifdef`/`#ifndef` but no `#else`, so a shared file with a little
x86 code wraps it in `#ifndef ARCH_ARM64` and puts the ARM64 version under `#ifdef ARCH_ARM64`.

| x86 file | What's x86-specific | ARM64 |
| --- | --- | --- |
| KStart16/32/64 | real mode, VBE, GDT, paging, long mode | **Done:** `Arm64/KStart` |
| KUtils | all asm | **Done:** `Arm64/KUtils` (port I/O traps; CPUID gives zeros) |
| KMathA | x87 | **Done:** `Arm64/KMathA` + `Arm64/KMathB` |
| StrA | asm block | **Partly done:** `Arm64/StrA` has the string routines; StrMatch, StrIMatch and the PUT_* helpers are still to port |
| Mem/MAllocFree, Mem/PageTables, Mem/MemPhysical | asm, x86 page tables | to do: ARM64 page tables from the device tree |
| KTask, Sched | context switch asm, FS/GS | to do: ARM64 context switch; Fs is TPIDR_EL1, Gs is TPIDRRO_EL0 |
| KInts, KExcept | IDT, PIC/APIC | to do: vectors (started in KStart) + GIC (virt, Pi 4) / BCM2836 local intc (Pi 3) |
| MultiProc | APIC IPIs | to do: PSCI CPU_ON (virt), spin tables (Pi) |
| KHashA, Compress | asm | to do |
| KDate, KMisc, Display, KMain | CMOS, PIT/HPET, VGA/VBE ports | to do: generic timer, PL031 RTC, ramfb/virtio-gpu (virt) and mailbox framebuffer (Pi) |
| PCIBIOS | BIOS32 | to do: PCIe ECAM from the device tree (milestone 2) |
| SerialDev/Keyboard, Mouse | PS/2 ports | to do: virtio-input (virt), USB later (Pi) |
| BlkDev/DskATA, DskATAId | IDE ports | to do: virtio-blk (virt), SD/EMMC (Pi) |
| KGlbls, StrB, StrPrint, StrScan, KDataTypes, KHashB, KDefine, KDbg, KeyDev, QSort, Job, EdLite, FunSeg, BlkDev (file systems), KLoad | none found | shared; add as dependencies allow |

Compiler features still missing on ARM64:

- `Sin`, `Cos`, `Tan`, `ATan` and the power operator as built-ins (`Exp`, `Ln`, `Pow` and `Arg`
  already work);
- `asm {}` blocks inside functions;
- `interrupt` functions (use asm vectors);
- `lock {}` on read-modify-write operators, which is not atomic yet. `LBts` and friends are atomic.
- running the compiler itself on ARM64 (it needs its own asm converted and a JIT that emits ARM64).

The suggested order:

1. Memory and tasks, to get `MAlloc`, `Fs`/`Gs` and `Print` over serial.
2. Interrupts and the timer.
3. The framebuffer and the Anokos terminal.
4. Block devices and RedSea.
5. The compiler on ARM64.

## QEMU on macOS 14

Homebrew has no QEMU package for macOS 14. QEMU 11 also doesn't build there with the stock
Command Line Tools: the i.MX8M board sources need a newer clang, and HVF on ARM needs the
macOS 15 SDK. `tools/build_qemu.sh` builds QEMU into `build/qemu/` with only the x86-64 and
ARM64 targets, without those boards, and with TCG emulation. The build tools look there first.
