# Building Anokos

TerryC can only be compiled by its own compiler, which runs inside the OS. So the build runs the
OS in a virtual machine and gets the results back over the VM's serial port. This page explains
the stages, the host tools and the fast edit-build-test loop. The commands are also in
[BUILDING.md](https://github.com/LandonDevAnokos/Anokos/blob/main/BUILDING.md).

## What gets built

Only two parts of the system are compiled ahead of time:

| Output | From | Used by |
| --- | --- | --- |
| `0000Boot/0000Kernel.BIN.C` | `src/Kernel/Kernel.PRJ` | the boot loader |
| `Compiler/Compiler.BIN` | `src/Compiler/Compiler.PRJ` | the kernel, which loads it at boot |
| `build/arm64/kernel8.img` | `src/Kernel/KernelArm64.PRJ` | ARM64 boards (see [ARM64](arm64.md)) |

Everything else (`src/Anokos`, `src/StartOS.TC`, Home, apps) stays source and is compiled
just-in-time at boot or when run. Changing those files needs only a new ISO, not a compile.

The compiled binaries are checked into `src/`, so a fresh clone can build an ISO without
compiling anything.

## Stages

```
  stage 1  stock TempleOS ISO + src/Kernel, src/Compiler   --VM-->  Kernel.BIN.C,
           (as .HC/.HH)                                             Compiler.BIN (serial)
  stage 2  src/ + new binaries                    --build_iso.py-->  build/Anokos.ISO
  stage 3  Anokos.ISO + Arm64Build.TC                       --VM-->  kernel8.img (serial)
```

**Stage 1** (`python tools/anokos_build.py kernel`). The Anokos compiler doesn't exist yet when
you start from nothing, so stage 1 uses the stock TempleOS compiler from the reference ISO in
`TempleOS/`. `stage1()` in `tools/anokos_build.py`:

1. copies the stock TempleOS tree, and `src/Kernel` and `src/Compiler` under the old `.HC`/`.HH`
   names into `AnokosSrc/`;
2. adds `tools/stage1/AnokosBuild.HC` and a `HomeSys.HC` that runs it at start-up;
3. builds an ISO with `tools/build_iso.py` and boots it headless;
4. `AnokosBuild.HC` compiles the compiler and the kernel, prints `@@ERRORS n`, then sends each
   binary as hex between `@@BEGIN TAG size` and `@@END TAG` lines on COM1;
5. the host reads `build/stage1_serial.log`, checks the sizes and writes `build/kernel/`.

With `--update-src` the binaries and `.MAP` files are also copied into `src/`.

**Stage 2** (`python tools/anokos_build.py iso`). `stage2()` copies `src/`, drops in the binaries
from `build/kernel/` (or uses the ones in `src/`), and writes `build/Anokos.ISO` with
`tools/build_iso.py`. That tool writes a RedSea file system (see [File systems](filesystem.md))
and takes the boot sector and El Torito boot catalog from the reference ISO. Text files go in as
`.Z` files with a "stored" compression header, which the kernel reads like any other `.Z`.

`all` runs stage 1 then stage 2.

**Stage 3** (`python tools/anokos_build.py arm64`). The ARM64 kernel is compiled by Anokos
itself. `arm64_hook()` copies `tools/stage3/Arm64Build.TC` into the ISO and adds an `#include` of
it to `HomeSys.TC`. When Anokos boots, it runs

```c
Cmp("::/Kernel/KernelArm64","B:/KernelArm64","B:/Kernel8");
```

and sends `Kernel8.IMG` back over the serial port. `Arm64Build.TC` also adds a KeyDev hook that
copies all screen output to the serial log, so compile errors end up in
`build/arm64_serial.log`.

## Host tools

| Tool | Job |
| --- | --- |
| `tools/anokos_build.py` | the stages above, plus `run` (boot the ISO, take a screenshot) and `run-arm64` |
| `tools/qemu_util.py` | finds QEMU, starts it with a monitor socket, screenshots |
| `tools/build_iso.py` | writes a RedSea ISO from a directory tree |
| `tools/build_qemu.sh` | builds QEMU into `build/qemu/` (for macOS 14, which Homebrew no longer supports) |
| `tools/redsea_extract.py` | reads RedSea ISOs; used to extract `src/` from the official TempleOS ISO |
| `tools/codemap.py` | regenerates the website's Code City data from `src/` |

The VM is QEMU (`--vm-backend qemu`) or, on x86 hosts, VirtualBox (`--vm-backend vbox`). QEMU runs
an i440FX PC with an IDE CD drive and standard VGA. The tools wait for markers in the serial log
rather than for fixed times, so a stage ends as soon as the VM is done.

## The edit-build-test loop

For kernel or compiler work:

```sh
python tools/anokos_build.py kernel --vm-backend qemu     # about 10 s
python tools/anokos_build.py all --update-src --headless  # rebuild, update src/, make the ISO
python tools/anokos_build.py run --headless               # boot it, screenshot build/run.png
```

For ARM64 work, rebuild the compiler with `kernel` only if you changed the compiler, then:

```sh
python tools/anokos_build.py arm64                        # about 6 s
python tools/anokos_build.py run-arm64 --board virt --headless --keys 'Ver\r'
```

`run-arm64` waits for `@@ARM64 BOOT OK`, types the `--keys` on the serial console (the ARM64
keyboard), and saves `build/arm64/run_<board>.png`. Boards: `virt`, `rpi3`, `rpi4`.

## Reading errors

- Stage 1: `build/stage1_serial.log` has the full compiler output. The tool stops with an error if
  `@@ERRORS` isn't 0.
- Stage 3: `build/arm64_serial.log`. A failed compile ends in the debugger (the tool stops when it
  sees "TempleOS Debugger"); the first real error is a few lines after `@@ARM64 START`.
- Warnings such as `ParenWarning` are common in the original TempleOS sources and harmless.

## Source files are bytes, not text

`src/` files are DolDoc documents and some contain binary data (sprites) after the text. Git
stores them byte-exact (`.gitattributes`). Use an editor or script that doesn't change line endings
or re-encode them. Python with `encoding='latin-1'` round-trips them safely.
