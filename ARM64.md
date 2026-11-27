# Anokos on ARM64

This is the plan and status for converting the Anokos kernel to ARM64 (AArch64). It's part of
[milestone 5](ROADMAP.md#milestone-5-multi-architecture-support). The x86-64 kernel isn't
rewritten. It is converted: the same TerryC sources get built for ARM64 by the Anokos compiler,
and only the x86-specific parts get ARM64 versions.

## Status

| Part | State |
| --- | --- |
| Compiler: ARM64 code generator (`src/Compiler/BackArm64.TC`) | Working: integer, pointer, F64 (incl. `Sin`/`Cos`/`Tan`/`ATan`/`` ` ``), switch, varargs, bit operations, in-function asm |
| Compiler: ARM64 assembler for `asm {}` blocks (`src/Compiler/AsmArm64.TC`) | Working: integer, load/store, branches, system registers, barriers, exclusives, FP |
| Compiler: ARM64 boot image output (`Cmp()` with `OPTf_ARM64`) | Working |
| Boot: Image header, EL3/EL2 to EL1, relocation, MMU, vectors (`Arm64/KStart.TC`) | Working on QEMU virt, raspi3b, raspi4b |
| Memory: block pools, heaps, MAlloc/Free, RAM from the device tree or Pi mailbox | Working |
| Tasks: Adam task, Spawn, Yield/Sleep, task exit, try/catch/throw | Working (one core) |
| Kernel files in the ARM64 build | All of `Kernel.PRJ` except KInts, PCIBIOS and the PS/2 and IDE drivers (see the checklist) |
| Interrupts, timer IRQ, framebuffer, storage, input, more cores | Not started |
| Compiler running *on* ARM64 (JIT) | Not started |

The ARM64 kernel boots on QEMU's `virt` machine and on the emulated Raspberry Pi 3 and 4. It finds
RAM, sets up the heaps and the Adam task, and runs its tests:
- a code generator self test;
- MAlloc/Free;
- try/catch;
- two spawned tasks that print, sleep and exit.

Then it starts a small serial monitor (`h` help, `k` tasks, `m` memory, `t` self test, `r` reboot,
`o` power off). Kernel `Print` output (and anything else through `RawPutChar`) goes to the serial
console.

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
- **Assembler.** `asm {}` blocks use ARM64 syntax when `OPTf_ARM64` is on. TerryC functions are
  referenced as `&Name` when already defined, as in x86 asm. It differs from standard ARM64
  syntax in three ways:
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

Fs (the current `CTask`) is in TPIDR_EL1 and Gs (the current `CCPU`) in TPIDRRO_EL0. The CTask
register slots hold the ARM64 registers the x86 ones map to. `rflags` keeps the x86 format, with
bit 9 meaning interrupts are on, so shared code that saves and restores flags works unchanged:
`GetRFlags`/`SetRFlags` translate to and from DAIF, and inside functions the x86 one-liners `CLI`,
`STI`, `PUSHFD`, `POPFD`, `PAUSE` and `HLT` assemble to their ARM64 equivalents. Calls to symbols
x86 imports at load time (from the compiler, for example) go to `ArmImportTrap`, which reports the
caller.

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

`src/Kernel/KernelArm64.PRJ` has the same files in the same order as `Kernel.PRJ`.
`#define ARCH_ARM64` selects the ARM64 code. Shared files keep their TerryC. Their x86-only parts are
wrapped like this:

```
#ifdef ARCH_ARM64
#include "/Kernel/Arm64/<file>"   //or a few lines inline
#else
...x86 asm or port I/O...
#endif
```

ARM64 asm replacements keep the x86 labels, so the `_extern` declarations stay as they are.

| File | x86-specific | ARM64 |
| --- | --- | --- |
| KStart16/32/64 | real mode, VBE, GDT, paging, long mode, fixed area | **Done:** `Arm64/KStart` (boot, data labels, `CSysFixedArea` in the image) and `Arm64/KMain` |
| KUtils | asm | **Done:** `Arm64/KUtils` (port I/O traps; CPUID gives zeros) |
| KMathA | x87 | **Done:** `Arm64/KMathA` + `Arm64/KMathSoft` |
| StrA | asm | **Done:** `Arm64/StrA` (the PUT_* debug helpers are not ported) |
| Sched | context switch asm | **Done:** `Arm64/Sched` (no Ctrl+Alt keys, breakpoints or FP state yet) |
| Mem/MAllocFree | asm | **Done:** `Arm64/MAllocFree` (TerryC, same algorithm) |
| Mem/PageTables, BlkPool, MemPhysical | x86 page tables, E820, 16 MiB area | **Done:** `Arm64/Mem` fills `MEM_E820` from the device tree or mailbox; `Arm64/BlkPool`; identity-mapped device memory |
| KHashA | asm | **Done:** `Arm64/KHashA` (TerryC, same hash values) |
| Compress | asm | **Done:** `Arm64/Compress` |
| KExcept | try/catch asm | **Done:** `Arm64/KExcept` |
| KTask | CallStkGrow asm, Spawn's start frame | **Done:** `CallStkGrow` doesn't grow the stack yet; tasks start via `ARM64_TASK_START` |
| MultiProc | APIC, TSS, AP start-up | **Partly done:** single core; CPU structs, jobs and SpawnQue are shared |
| KMisc, KDate, Display, KDbg | PIT, HPET, CMOS, VGA ports, CR2, `INT` | **Done:** generic timer for HPET, PL031 RTC (virt), serial `RawPutChar`, FAR_EL1, `ArmPanic` |
| SerialDev/Keyboard, Mouse | PS/2 | to do: `Arm64/Input` has stand-ins; serial, virtio-input, USB |
| BlkDev/DskATA, DskATAId | IDE | to do: `Arm64/Ata` has stand-ins; virtio-blk (virt), SD card (Pi) |
| KInts | IDT, PIC | to do: GIC (virt, Pi 4) / BCM2836 local intc (Pi 3), generic timer IRQ |
| PCIBIOS | BIOS32 | to do: PCIe ECAM from the device tree (milestone 2) |
| KMain | VBE, PIT, devices | **Partly done:** `Arm64/KMain`; framebuffer, interrupts and drives to do |
| All other files | none | shared, in the ARM64 build |

Compiler features still missing on ARM64:

- `interrupt` functions (use asm vectors);
- `lock {}` read-modify-write operators aren't atomic yet (`LBts` and friends are);
- in-function asm that uses labels or addresses;
- running the compiler itself on ARM64 (it needs its own asm converted and a JIT that emits ARM64).

The suggested order:

1. Interrupts and the timer IRQ.
2. The framebuffer and the Anokos terminal.
3. Serial/virtio input.
4. Block devices and RedSea.
5. More cores.
6. The compiler on ARM64.

## QEMU on macOS 14

Homebrew has no QEMU package for macOS 14. QEMU 11 also doesn't build there with the stock
Command Line Tools: the i.MX8M board sources need a newer clang, and HVF on ARM needs the
macOS 15 SDK. `tools/build_qemu.sh` builds QEMU into `build/qemu/` with only the x86-64 and
ARM64 targets, without those boards, and with TCG emulation. The build tools look there first.
