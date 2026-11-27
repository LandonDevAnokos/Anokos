# How Anokos is put together

Anokos is a small, single-user operating system descended from TempleOS. This page is the map:
the big design decisions, the layers the system is built from, and where each part lives in
`src/`. The other pages go into each part in detail.

## The design in one paragraph

Everything runs in one address space, at the highest privilege level, with memory identity
mapped (a virtual address is the physical address). There are no processes, no user mode and no
system calls: a "task" is a thread with its own stack, heap and symbol table, and any code can call
any kernel function directly. Almost all of the system is written in TerryC (the language TempleOS
called HolyC) and compiled by the system's own compiler. Only the kernel and the compiler are
compiled ahead of time; everything else is compiled just-in-time when the system boots.

This trades protection for simplicity. A bug can overwrite anything, but there is very little
machinery between a program and the hardware, and the whole system fits in one person's head.

## Layers

```
  +--------------------------------------------------------------+
  |  Home (~/)       start-up files: HomeSys, HomeKeyPlugIns     |  JIT at boot
  +--------------------------------------------------------------+
  |  Anokos layer    src/Anokos: DolDoc, windows, graphics,      |  JIT at boot
  |  ("Adam")        file manager, the Anokos terminal ...       |  (StartOS.TC)
  +--------------------------------------------------------------+
  |  Compiler        src/Compiler -> Compiler.BIN                |  AOT, loaded by
  |                  lexer, parser, optimizer, x86-64/ARM64 code |  the kernel
  +--------------------------------------------------------------+
  |  Kernel          src/Kernel -> Kernel.BIN.C                  |  AOT, loaded by
  |                  boot, memory, tasks, interrupts, drives ... |  the boot loader
  +--------------------------------------------------------------+
  |  Hardware        x86-64 PC (BIOS), or ARM64: QEMU virt, Pi   |
  +--------------------------------------------------------------+
```

- **Kernel** (`src/Kernel`). Boot code, memory, tasks and the scheduler, interrupts, keyboard and
  mouse, the text/framebuffer console, block devices and file systems, hash tables (symbol
  tables), the binary loader and a small debugger. It's compiled to `Kernel.BIN.C`, which the boot
  loader puts in memory. See [Booting](boot.md).
- **Compiler** (`src/Compiler`). The TerryC compiler and assembler. The kernel loads
  `Compiler.BIN` during boot, after which source code can be compiled and run. See
  [The compiler](compiler.md).
- **Anokos layer** (`src/Anokos`, called "Adam" in TempleOS). Everything else a user sees:
  DolDoc (the document format used for text, help and source files), the window manager,
  graphics, menus, the file manager, autocomplete, and the Anokos terminal. `src/StartOS.TC` runs
  at boot in the Adam task and compiles this layer from source with `#include
  "/Anokos/MakeAnokos"`.
- **Home** (`~` in paths, `/Home` on the boot drive). Start-up files included by
  `src/MakeHome.TC`: localization, key plug-ins, wrappers and `HomeSys`. A copy in `/Home`
  overrides the default in the root directory (`src/HomeSys.TC` and friends).
- **Apps and demos** (`src/Apps`, `src/Demo`). Compiled when you run them.

## Tasks, Adam and the symbol tables

The first task is **Adam** (`adam_task`). The kernel builds it during boot, and everything compiled
at boot (StartOS, the Anokos layer, Home) is compiled into Adam's symbol table. Every other task is
a descendant of Adam. When code in a task looks up a name, the search goes from the task's own
hash table to its parent's and up to Adam's, so every task sees the whole system and can add its
own definitions without affecting others. See [Symbols, hash tables and loading](symbols.md).

Each CPU core has a **Seth** task (`Gs->seth_task`; on core 0 it's Adam), which serves jobs
sent to that core and is the default parent for tasks spawned there, and an **idle task**
(`Gs->idle_task`) that runs when nothing else can. See [Tasks and scheduling](tasks.md) and [Multicore](multicore.md).

The current task and the current CPU are always at hand as `Fs` (a `CTask *`) and `Gs` (a
`CCPU *`). On x86-64 they're read through the FS and GS segment bases, hence the names.

## Run levels

`sys_run_level` is a bit set that records how far boot has got. Code checks it to know what is
safe to use; for example, output goes to the raw console until a document system exists.

| Flag | Set when |
| --- | --- |
| `RLf_16MEG_ADAM_HEAP_CTRL` | Adam's heap works (very early) |
| `RLf_VBE` | a VBE framebuffer was set up (the Anokos terminal needs it) |
| `RLf_RAW` | the raw console can print |
| `RLf_INTERRUPTS` | interrupts are on |
| `RLf_BLKDEV` | drives are mounted |
| `RLf_MP` | the other CPU cores are running |
| `RLf_COMPILER` | the compiler is loaded |
| `RLf_DOC` | DolDoc is loaded (from `MakeAnokos`) |
| `RLf_WINMGR` | the window manager runs (only without a framebuffer) |
| `RLf_REGISTRY` | the registry is read |
| `RLf_HOME` | Home files are next |
| `RLf_ADAM_SERVER` | boot is finished; Adam serves jobs |

The full list is in `src/Kernel/KernelA.TH` (search for `RLf_`).

## Where things are in src/

| Path | What |
| --- | --- |
| `src/0000Boot/0000Kernel.BIN.C` | the compiled kernel the boot loader loads |
| `src/Kernel/Kernel.PRJ` | the x86-64 kernel's file list, in compile order |
| `src/Kernel/KernelArm64.PRJ` | the ARM64 kernel's file list (same files plus `Arm64/`) |
| `src/Kernel/KernelA.TH`, `KernelB.TH`, `KernelC.TH` | kernel headers: classes, constants, declarations |
| `src/Kernel/KStart16.TC` ... `KStart64.TC`, `KMain.TC` | boot, see [Booting](boot.md) |
| `src/Kernel/Mem/` | block pools, heaps, page tables, see [Memory](memory.md) |
| `src/Kernel/Sched.TC`, `KTask.TC`, `Job.TC`, `MultiProc.TC` | tasks, scheduling, jobs, cores |
| `src/Kernel/KInts.TC` | interrupts and the timer, see [Interrupts](interrupts.md) |
| `src/Kernel/KExcept.TC`, `KDbg.TC` | try/catch and the debugger, see [Exceptions](exceptions.md) |
| `src/Kernel/KeyDev.TC`, `Display.TC`, `SerialDev/` | output and input, see [Input and output](io.md) |
| `src/Kernel/BlkDev/` | drives, RedSea and FAT32, see [File systems](filesystem.md) |
| `src/Kernel/KHashA.TC`, `KHashB.TC`, `KLoad.TC` | symbol tables and the loader |
| `src/Kernel/Arm64/` | ARM64 versions of the x86-specific parts, see [ARM64](arm64.md) |
| `src/Compiler/` | the compiler, see [The compiler](compiler.md) |
| `src/Anokos/` | the Anokos layer; `Term.TC` is [the terminal](terminal.md) |
| `src/StartOS.TC` | runs at boot after the kernel |
| `src/Doc/` | the original TempleOS documentation, in DolDoc |
| `tools/` | host-side build tools (Python), see [Building](build.md) |

## Things that surprise newcomers

- **No memory protection.** A wild pointer can corrupt the kernel. In return, calling into the
  kernel costs no more than any other function call.
- **Source is the interface.** Programs are kept as source and compiled when run; the command line
  is the compiler (on x86-64, every line you type is TerryC).
- **Files are documents.** Source files are DolDoc: a literal `$` is written `$$`, and some files
  carry binary sprite data after the text. See [The TerryC language](language.md).
- **Mostly cooperative.** Tasks give up the CPU by calling `Yield`, `Sleep` or a blocking
  function. See [Tasks and scheduling](tasks.md) for exactly when the timer switches tasks.
- **Two architectures, one tree.** The ARM64 port converts the same kernel source. x86-only parts
  are wrapped in `#ifdef ARCH_ARM64` with an ARM64 version in `src/Kernel/Arm64/`. See
  [ARM64](arm64.md).
