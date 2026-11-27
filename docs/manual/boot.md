# Booting

This page follows x86-64 Anokos from the BIOS handing control to a boot loader, through real,
protected and long mode, to `KMain`, `StartOS.TC` and the Anokos terminal. The last section
covers the ARM64 port, which boots from a Linux-style `Image` and starts the terminal straight
from the kernel.

## The big picture

The kernel is one flat binary, `Kernel.BIN.C`, made by the compiler in AOT mode from
[Kernel.PRJ](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Kernel.PRJ). There is
no ELF and no linker. A tiny boot loader copies the file to physical address `BOOT_RAM_BASE`
(`0x7C00`) and jumps to it in 16-bit real mode. From there the kernel does everything itself:

```
BIOS -> boot block (BootHD / BootDVD / BootMHD)
     -> Kernel.BIN.C at 0x7C00, real mode
KStart16.TC   VBE mode, E801/E820 memory maps, PCI BIOS, A20, GDT, CR0.PE
KStart32.TC   patch absolute addresses, build page tables, first code BlkPool
KStart64.TC   long mode, boot CPU struct, Adam's heap and task, jump to KMain
KMain.TC      KMain(): pools, globals, graphics, interrupts, timers, drives,
              other cores, keyboard/mouse, load the Compiler, ExeFile("StartOS")
StartOS.TC    JIT-compile headers and the Anokos layer, registry, ~/MakeHome,
              AnoTermStart
KMain.TC      SrvTaskCont(): Adam becomes a server task, forever
```

Everything above runs in the Adam task, the first task, which never dies. See [Tasks](tasks.md).

## The kernel file and its header

The compiler puts a `CBinFile` header in front of every BIN module (see the code after
`//Needs 16 ALIGN` in
[CMain.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Compiler/CMain.TC)):

```c
class CBinFile
{
  U16 jmp;                 //0xEB, short jump over the header
  U8  module_align_bits,reserved;
  U32 bin_signature;       //'TOSB'
  I64 org,
      patch_table_offset,  //where the import/export/abs-address table starts
      file_size;
};
```

The header is 32 bytes and starts with a short `JMP`, so jumping to the start of the file runs
straight into the kernel's code. `KStart16.TC` is the first file in `Kernel.PRJ`, so its first
label, `SYS_KERNEL`, is the first byte after the header. The class `CKernel` in
[KernelA.TH](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KernelA.TH)
describes this start of the image: the header, the boot variables (`boot_src`, `boot_blk`,
`sys_run_level`, the compile time), the BIOS memory maps and the GDT. An `#assert` in
`KStart16.TC` keeps the two in step.

The patch table at `patch_table_offset` lists, among other things, every 32-bit absolute address
in the image (`IET_ABS_ADDR`). The kernel is compiled as if it sat at address 0, so these must be
fixed up once the kernel knows where it was loaded. That happens in `KStart32.TC`.

The `.C` in the file name means "contiguous" (`IsDotC`, `RS_ATTR_CONTIGUOUS`). The file system
keeps such a file in consecutive blocks, which is what lets a 512-byte boot loader read it with
nothing more than a start block and a count.

## Boot loaders

The loaders are in
[src/Anokos/Opt/Boot/](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Anokos/Opt/Boot/).
Each first copies itself high, just below `BOOT_RAM_LIMIT` (`0x97000`), so it is not overwritten
while it loads the kernel at `0x7C00`. Each ends with a hand-coded far jump to
`BOOT_RAM_BASE:0000` and passes the boot source in `EAX` (`BOOT_SRC_HARDDRV`, `BOOT_SRC_DVD`,
`BOOT_SRC_RAM`) and a block number in `EBX`.

| File | What it is |
| --- | --- |
| `BootDVD.TC` | El Torito stage 1 for CDs/DVDs. Checks for a 64-bit CPU, prints dots while it reads `BDVD_BLK_CNT` 2 KiB blocks, then shifts the image down to align it |
| `BootDVDIns.TC` | `BootDVDIns` builds the kernel and moves it to `/0000Boot/0000Kernel.BIN.C` |
| `DskISORedSea.TC` | Writes the ISO and patches `BDVD_BLK_LO`, `BDVD_BLK_CNT`, `BDVD_SHIFT_BLKS` into stage 1 |
| `BootHD.TC` | Partition boot record. Reads `BHD_BLK_CNT` blocks from `BHD_DAP_BLK` with `INT 0x13` AH=0x42 |
| `BootHDIns.TC` | `BootHDIns` builds the kernel, moves it to `/Kernel.BIN.C` and patches `BHD_BLK_CNT` and `BHD_DAP_BLK` into the partition's boot record |
| `BootMHD.TC`, `BootMHD2.TC`, `BootMHDIns.TC` | Master boot record and its second stage, for choosing a partition |
| `BootRAM.TC` | `BootRAM` soft-reboots into a new kernel file without a hardware reset |

The project's build does not run `DskISORedSea`. `tools/build_iso.py` takes the first 88 blocks
(ISO descriptors, El Torito catalog and the stage-1 loader) from the reference TempleOS ISO,
patches the stage-1 loader for the new layout, and writes the prebuilt kernel
`src/0000Boot/0000Kernel.BIN.C` into the image. See [Building](build.md).

`BootRAM` is different: it reads the new kernel into memory, copies the boot variables from the
running kernel's `CKernel` area into it, copies the small `SYS_RAM_REBOOT` routine (end of
`KStart64.TC`) to a heap block above 1 MiB, copies the new image over the old one, and calls
the routine. It turns paging and long mode off and
jumps to `CORE0_32BIT_INIT`, so a soft reboot skips the 16-bit stage and reuses the BIOS data
gathered at the first boot.

## KStart16: real mode

[KStart16.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KStart16.TC) starts
with `JMP CORE0_16BIT_INIT` over a block of data that must be reachable in real mode:
`SYS_BOOT_SRC`, `SYS_BOOT_BLK`, `SYS_RUN_LEVEL`, `MEM_E801`, `MEM_E820`, `MEM_PHYSICAL_SPACE`,
the GDT (`SYS_GDT`) and `SYS_VBE_INFO`. These are the same bytes `CKernel` describes. Later,
TerryC code reaches them through `_extern` globals in `KernelB.TH`, such as `sys_run_level` and
`mem_boot_base`.

`CORE0_16BIT_INIT` does, in order:

1. Sets up a stack under `BOOT_RAM_LIMIT` and saves `EAX`/`EBX` from the loader.
2. Reloads `CS` so that the assembler's offsets match where the code really is. It uses the
   `CALL`/`POP` trick (`GET_RIP`) to find its own address.
3. Sets `sys_run_level` to `RLF_16BIT`.
4. Unless the kernel was configured for text mode (`CFG_TEXT_MODE`), calls `VBE_SET_MODE`.
   `VBE_EDID` reads the monitor's EDID (`INT 0x10` AX=0x4F15) to get its native size, or uses
   `VBE_DFT_MAX_WIDTH` x `VBE_DFT_MAX_HEIGHT` (1920x1080) as the limit. `VBE_CHECK_MODE` walks
   the mode list and keeps the largest 32-bit direct-colour linear framebuffer mode that fits.
   The result goes into `SYS_VBE_INFO` (a `CVbeBootInfo`). If the mode is set, `RLf_VBE` is set.
5. If VBE failed, tries the old TempleOS 640x480 16-colour mode and sets `RLf_VGA` if that works.
6. Reads the memory maps: `INT 0x15` AX=0xE801 into `MEM_E801`, and `INT 0x15` EAX=0xE820 in a
   loop into `MEM_E820` (up to `MEM_E820_ENTRIES_NUM`-1 entries; a zero `type` ends the table).
7. Computes `MEM_PHYSICAL_SPACE`: the highest end address of any E820 entry, or the E801 limit.
8. Asks the PCI BIOS (`INT 0x1A` AX=0xB101) for the number of buses.
9. Disables interrupts, enables the A20 line (port `0x92`), and stores `MEM_BOOT_BASE`, the
   linear address of the code (just after the `CBinFile` header).
10. Finds the patch table (`SYS_BOOT_PATCH_TABLE_BASE`), patches the boot GDT entries with the
    load address, loads the GDT, sets `CR0` to `SYS_START_CR0` (protection on) and far-jumps
    to `CORE0_32BIT_INIT`.

The assembler only handles 16-bit code well enough for this file. Far jumps are written as raw
bytes (`DU8 0x66,0xEA`), and conditional code is split into small routines that return early,
because 16-bit conditional jumps can only be short.

## KStart32: protected mode

[KStart32.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KStart32.TC) first
defines the rest of the asm globals that are not needed in real mode (`MEM_HEAP_BASE`,
`MEM_PML2`..`MEM_PML4`, `SYS_CODE_BP`, `SYS_DATA_BP`, the progress bars, `SYS_SEMAS` and
others). `CORE0_32BIT_INIT` is also the entry point for `BootRAM`.

1. Loads flat data segments, sets `RLf_32BIT`, and uses a temporary stack at `BOOT_RAM_LIMIT`.
2. Walks the `CPatchTableAbsAddr` record at the start of the patch table and adds
   `MEM_BOOT_BASE` to every listed 32-bit absolute address. Then it jumps to the flat 32-bit code
   segment and sets `RLf_PATCHED`. From here on the kernel's absolute addresses are correct.
3. `SYS_FIND_PCIBIOS_SERVICE_DIR` and `SYS_FIND_PCI_SERVICES` (in `PCIBIOS.TC`) locate the
   BIOS32 PCI services.
4. `SYS_INIT_PAGE_TABLES` builds the identity map, and `SYS_INIT_16MEG_SYS_CODE_BP` gives the
   first code block pool the free RAM below 16 MiB. Both are in
   [Mem/PageTables.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Mem/PageTables.TC)
   and explained in [Memory](memory.md#the-identity-map).
5. Calls `SYS_ENTER_LONG_MODE` (in `KStart64.TC`): sets PSE, PAE and PGE in `CR4`, loads `CR3`
   with `MEM_PML4`, sets `IA32F_LME` in the `IA32_EFER` MSR, turns paging on and far-jumps to
   the 64-bit code segment. It returns in 64-bit mode and falls through into `KStart64.TC`.

## KStart64: long mode

[KStart64.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KStart64.TC) is
short. It sets `RLf_64BIT`, initializes the FPU and saves a clean FPU state in the
`CSysFixedArea` at `SYS_FIXED_AREA` (`0x100000`). That fixed area holds the objects that must
exist before any heap does: the boot `CCPU`, Adam's `CTask`, the first `CBlkPool` and Adam's
`CHeapCtrl`.

1. `CPUStructInit` sets up the boot CPU's `CCPU`, and `SET_GS_BASE` makes it `Gs`.
2. Adam's heap control is filled in by hand (the same work `HeapCtrlInit` does later in TerryC),
   pointed at `sys_code_bp`, and used for both Adam's `code_heap` and `data_heap`. This sets
   `RLf_16MEG_ADAM_HEAP_CTRL`.
3. `TaskInit` builds Adam's task with a `MEM_ADAM_STK` stack, `SET_FS_BASE` makes it `Fs`, and
   the stack pointer moves to Adam's stack.
4. `JMP I32 &KMain`.

## KMain step by step

`KMain` in
[KMain.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KMain.TC#L163) is
ordinary TerryC, running as Adam on CPU 0.

| Step | What it does |
| --- | --- |
| `adam_task=Fs` | Remembers Adam |
| `BlkPoolsInit` | Gives the rest of RAM to the code and data block pools, sets `RLf_FULL_HEAPS` ([Memory](memory.md#block-pools)) |
| `SysGlblsInit` | Cache line width, bit tables, `ext` table, `KeyDevInit`, disk cache, `pow10_I64`, progress bars, `dev.mem64_ptr`, fault handler stubs |
| `Mem32DevInit`, `UncachedAliasAlloc` | Device address bookkeeping and the uncached alias of the low 4 GiB |
| `LoadKernel` | Registers the kernel's exported symbols in Adam's hash table (`LoadPass1` with `LDF_NO_ABSS`, since the addresses are already patched) |
| `SysGrInit` | Fills `vbe` from `sys_vbe_info` and clears the screen, or sets up VGA or text mode |
| Task name, `TaskDerivedValsUpdate` | Names Adam "Adam Task CPU00" |
| `SysDefinesLoad` | Loads the system define lists (`ST_MONTHS`, `ST_FILE_ATTRS` and so on) |
| `Core0Init`, `IntInit1` | Allocates `cpu_structs`, starts the local APIC, loads an IDT of `IntNop` entries |
| `LBts(..,RLf_RAW)` | From here, `RawPrint` and the raw console work; before this only `Snd` and `Busy` can help you debug |
| `TimersInit` | Programs the PIT and enables the HPET |
| Memory check | Warns if `BIOSTotalMem` is under `MEM_MIN_MEG` (512 MiB) |
| `IntsInit`, `IntInit2` | Programs the 8259 PICs, turns interrupts on, installs the fault, timer and IPI handlers, sets `RLf_INTERRUPTS` ([Interrupts](interrupts.md)) |
| `TimeCal` | Calibrates the time-stamp counter |
| `BlkDevsInitAll`, `DskChg(':')` | Finds and mounts drives, changes to the boot drive, sets the home directory, sets `RLf_BLKDEV` ([I/O](io.md)) |
| `Core0StartMP` | Unless built with `CFG_NO_MP`: wakes the other cores, sets `RLf_MP` ([Multicore](multicore.md)) |
| `KbdMsInit`, `MsInit`, `KbdInit` | PS/2 keyboard and mouse; `Spawn(&MsHardDrvrInstall)` |
| `Load("Compiler")` | Loads `/Compiler/Compiler.BIN` and links it to the kernel, sets `RLf_COMPILER` ([Compiler](compiler.md)) |
| `DbgMode(OFF)`, `TimeCal` | Leaves debug mode, recalibrates |
| `ExeFile("StartOS")` | JIT-compiles and runs `/StartOS.TC`. If it throws, the exception is shown and the debugger starts |
| `RLf_ADAM_SERVER`, `SrvTaskCont` | Adam becomes a server task and never returns |

Many lines in `KMain` are generated at compile time by `#exe` blocks that read `kernel_cfg`,
the answers given when the kernel was compiled (see `KCfg.TC`). That is how the boot drive
letter, the home directory, the disk cache size and the `NoMP` option end up baked into the
kernel.

## StartOS.TC

Once the compiler is loaded, the rest of the system is plain source compiled just-in-time.
[StartOS.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/StartOS.TC) runs in Adam:

1. Includes the kernel and compiler headers (`KernelA.TH`, `CompilerA.TH`, `KernelB.TH`,
   `KernelC.TH`, `CompilerB.TH`) so JIT code knows the kernel's types and functions, then calls
   `HashTablePurge(adam_task->hash_table)`.
2. Includes `/Anokos/MakeAnokos`, which compiles the Adam layer: math, memory reports, graphics,
   sound, menus, windows, the window manager, the debugger, DolDoc (after which `RLf_DOC` is
   set), controls, the registry, autocomplete, and finally the terminal `Term.TC`.
3. Without a VBE framebuffer only: creates a DolDoc terminal for Adam and spawns the TempleOS
   window manager, `WinMgrTask` (which sets `RLf_WINMGR`).
4. `RegInit` loads the registry and sets `RLf_REGISTRY`.
5. Makes `/Tmp` if the drive is writable and sets `RLf_HOME`.
6. Includes `~/MakeHome`: `HomeLocalize`, the boot and utility code in `/Anokos/Opt`,
   `HomeWrappers`, the kernel and compiler map files (for the debugger), `HomeKeyPlugIns` and
   `HomeSys`. Without VBE, `HomeSys` starts the two user terminals of the TempleOS desktop.
7. Calls `AnoTermStart` ([Terminal](terminal.md)). With a VBE framebuffer it takes over the
   screen, spawns the "Term Pump" and "Terminal" tasks, and returns. Without one it returns at
   once.

`StartOS.TC` then ends, `ExeFile` returns into `KMain`, and Adam enters `SrvTaskCont`. In that
loop Adam waits for jobs other tasks send it (code to compile in Adam's context, for example) and
runs them. Each task's hash table chains to its parent's, ending at Adam's, which holds the
kernel's symbols, so anything compiled in Adam is visible to every task. See [Tasks](tasks.md).

## Run levels

`sys_run_level` is a 32-bit set of flags, one per boot milestone. Code checks them to know what
is available yet. For example, `SysGrInit` and `AnoTermStart` test `RLf_VBE`, and `StartOS.TC`
takes the TempleOS desktop path when it is clear. The `RLf_` constants are bit numbers for
`Bt`/`LBts`. The `RLF_` constants are the matching masks.

| Flag | Set in | Meaning |
| --- | --- | --- |
| `RLf_16BIT` | `KStart16` | Kernel started in real mode |
| `RLf_VBE` | `KStart16` | VBE linear framebuffer is up (Anokos addition, bit 21) |
| `RLf_VGA` | `KStart16` | Fallback 640x480 16-colour mode is up |
| `RLf_32BIT` | `KStart32` | Protected mode |
| `RLf_PATCHED` | `KStart32` | Absolute addresses relocated |
| `RLf_16MEG_SYS_CODE_BP` | `PageTables.TC` | First code block pool ready |
| `RLf_64BIT` | `KStart64` | Long mode |
| `RLf_16MEG_ADAM_HEAP_CTRL` | `KStart64` | Adam can `MAlloc` |
| `RLf_FULL_HEAPS` | `BlkPoolsInit` | All RAM is in the block pools |
| `RLf_RAW` | `KMain` | Raw screen output works |
| `RLf_INTERRUPTS` | `KMain` | IDT and IRQs are on |
| `RLf_BLKDEV` | `KMain` | Drives are mounted |
| `RLf_MP` | `KMain` | Other cores are running |
| `RLf_COMPILER` | `KMain` | The compiler is loaded |
| `RLf_DOC` | `MakeAnokos.TC` | DolDoc is compiled |
| `RLf_WINMGR` | `WinMgr.TC` | Window manager running (TempleOS desktop only) |
| `RLf_REGISTRY` | `StartOS.TC` | Registry loaded |
| `RLf_HOME` | `StartOS.TC` | About to run `~/MakeHome` |
| `RLf_AUTO_COMPLETE` | `ACInit.TC` | Autocomplete word lists ready |
| `RLf_ADAM_SERVER` | `KMain` | Boot finished, Adam is serving |
| `RLf_ONCE_ADAM`, `RLf_ONCE_USER` | `ARegistry.TC` | The registry's run-once code has run |

The order on a normal VBE boot:

```
real mode    16BIT, VBE
protected    32BIT, PATCHED, 16MEG_SYS_CODE_BP
long mode    64BIT, 16MEG_ADAM_HEAP_CTRL
KMain        FULL_HEAPS, RAW, INTERRUPTS, BLKDEV, MP, COMPILER
StartOS      DOC, REGISTRY, HOME
KMain        ADAM_SERVER
```

## On ARM64

The ARM64 kernel is built from
[KernelArm64.PRJ](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KernelArm64.PRJ),
which replaces `KStart16/32/64` with
[Arm64/KStart.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Arm64/KStart.TC)
and `KMain` with
[Arm64/KMain.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Arm64/KMain.TC).
With `OPTf_ARM64` on, the compiler writes a boot image (`A64ImageWrite` in `Arm64Lib.TC`)
instead of a BIN file. See [ARM64](arm64.md) for the port as a whole.

**The image header.** The file begins with an arm64 Linux `Image` header (`ARM64_IMAGE_START`):
a branch to `ARM64_ENTRY`, `text_offset` 0x80000, `image_size`, flags 0xA (little endian, 4 KiB
pages, load anywhere) and the magic `ARM\x64`. QEMU `-kernel` and the Raspberry Pi firmware
(`kernel8.img`) both load this format. The compiler adds the file size to `ARM64_IMAGE_SIZE`, so
`image_size` covers the image plus a reserved area after it, which becomes the boot stack. It also
fills `ARM64_RELOCS` and `ARM64_MAINS` with the offsets of two tables it appends to the image.

**ARM64_ENTRY** runs with the MMU off and the device tree address in `X0`:

1. Parks every CPU whose `MPIDR_EL1` affinity is not 0 in a `WFE` loop.
2. Drops to EL1. From EL3 it sets `SCR_EL3` and `ERET`s to EL2. From EL2 it sets `HCR_EL2`
   (EL1 is AArch64), lets EL1 use the physical timer, turns off FP traps and `ERET`s to EL1. The
   entry EL is saved in `ARM64_BOOT_EL`.
3. At EL1, sets `SCTLR_EL1` (MMU and caches off, stack alignment checks off, because TerryC uses
   8-byte stack slots) and enables FP/SIMD.
4. Self-relocates. For each entry of the relocation table it adds the load address to the 64-bit
   word at that offset. This is the ARM64 version of the `KStart32` patch loop. It then stores
   `ARM64_LOAD_BASE` and `ARM64_DTB`.
5. Puts `SP` at the end of the reserved area and points `VBAR_EL1` at `ARM64_VECTORS`.
6. `ARM64_MMU_INIT` builds the early identity map and turns the MMU and caches on. This has to
   happen before any TerryC runs: TerryC classes are packed, so compiled code does unaligned
   accesses, and those are only allowed on Normal memory. The layouts are described in
   [Memory](memory.md#on-arm64).
7. Runs the module's top-level code blocks listed in the `ARM64_MAINS` table, in order. On x86
   the kernel has no top-level code to run (`LoadKernel` leaves `LoadPass2` commented out).
8. Pushes the device tree pointer and calls `KMainArm64`.

**KMainArm64** does the work of `KStart64`, on the boot stack: `ArmBoardInit` picks the board
from the layout, `ArmSerInit` starts the UART, `ArmBanner` prints the board, CPU, EL and load
address, and `ArmMemInit` reads RAM into `MEM_E820` and seeds `sys_code_bp`. Then it sets `Fs`
and `Gs` (`ArmSetFs`, `ArmSetGs`), fills in Adam's `CHeapCtrl` in `arm64_sys_fixed`, calls
`TaskInit(adam,MEM_ADAM_STK)`, and switches to Adam's stack with
`ArmSwitchStk(adam->rsp,&KMainArm64B)`.

**KMainArm64B** follows `KMain` for the parts converted so far:

- `ArmDtbSave` copies the device tree into Adam's heap first, because `BlkPoolsInit` hands out
  the RAM it sits in.
- `BlkPoolsInit` (the ARM64 version), `ArmTextInit`, `ArmVideoInit` (ramfb on QEMU, the
  VideoCore mailbox on a Pi; sets `RLf_VBE`), `ArmSysGlblsInit`, `Mem32DevInit`,
  `UncachedAliasAlloc`, `SysDefinesLoad`, `Core0Init`, `IntInit1`, `IntsInit`, `IntInit2`,
  `TimeCal`, `BlkDevsInitAll` (no ARM64 storage drivers yet), `KbdMsInit`, `MsInit`, `KbdInit`
  (the serial console).
- It skips `LoadKernel`, `Core0StartMP` (single core) and loading the compiler, which does not
  run on ARM64 yet.
- It runs boot self-tests (`ArmSelfTest`, `ArmMemTest`, `ArmExceptTest`, a timer IRQ test and a
  two-task test) and prints `@@ARM64 BOOT OK` or `@@ARM64 BOOT FAILED n` on the serial port.
- There is no `StartOS.TC`. `Term.TC` is compiled into the ARM64 kernel (it is in
  `KernelArm64.PRJ`), so the kernel calls `AnoTermStart` itself. With no framebuffer it runs
  `ArmSerialConsole`, a shell on the serial port.
- It sets `RLf_ADAM_SERVER`, then Adam sleeps in a loop. On x86 Adam runs `SrvTaskCont`, which
  needs the compiler.

```
loader (QEMU -kernel / Pi firmware), MMU off, X0=DTB
ARM64_ENTRY   park CPUs, EL3/EL2 -> EL1, relocate, stack, VBAR
ARM64_MMU_INIT  identity map, MMU+caches on
top-level code blocks (ARM64_MAINS)
KMainArm64    board, UART, banner, ArmMemInit, Adam heap+task, switch stack
KMainArm64B   pools, video, globals, IRQs, timer, keyboard, self tests,
              AnoTermStart (or ArmSerialConsole), Adam idles
```
