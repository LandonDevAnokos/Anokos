# Building Anokos

## Requirements

- Python 3
- [QEMU](https://www.qemu.org/) (`qemu-system-x86_64`, and `qemu-system-aarch64` for ARM64) or,
  on x86 hosts, [VirtualBox](https://www.virtualbox.org/). These compile the kernel and test-boot it.

The build tools use VirtualBox on x86-64 hosts that have it, and QEMU everywhere else, including
Apple Silicon Macs. Pick one with `--vm-backend qemu` or `--vm-backend vbox`. If your package
manager has no QEMU for your system (Homebrew on macOS 14, for example), `sh tools/build_qemu.sh`
builds one into `build/qemu/`, which the tools look in first.

## Commands

Run these from the repository root:

```sh
python tools/anokos_build.py all                # build the compiler and kernel, then build/Anokos.ISO
python tools/anokos_build.py all --update-src   # same, and copy the new binaries into src/
python tools/anokos_build.py iso                # only rebuild the ISO from src/
python tools/anokos_build.py run                # boot build/Anokos.ISO in a VM and take a screenshot
python tools/anokos_build.py arm64              # compile the ARM64 kernel inside Anokos
python tools/anokos_build.py run-arm64          # boot it in qemu-system-aarch64 (--board virt/rpi3/rpi4)
```

Add `--headless` to `run` or `run-arm64` to run without a QEMU window. The ARM64 port is described
in [ARM64.md](ARM64.md).

`build/Anokos.ISO` boots on any x86-64 PC or VM with legacy BIOS boot, an IDE/ATAPI CD drive and
at least 512 MB of memory. With VirtualBox the tools create a VM called `AnokosBuild` in
`build/vm/`. With QEMU they run a PC (i440FX, IDE CD, standard VGA) and talk to it through a monitor
socket in `build/`.

## How the build works

TerryC is only compiled by its own compiler, which runs inside the OS itself, so the compiler and
kernel are bootstrapped in an x86-64 VM (QEMU or VirtualBox):

1. **Stage 1** (`anokos_build.py kernel`): builds an ISO of stock TempleOS with `src/Compiler` and
   `src/Kernel` added under TempleOS's `.HC`/`.HH` file names, boots it headless in VirtualBox,
   compiles the compiler and the kernel there, and receives both binaries over the VM's serial port.
   `build/stage1_serial.log` has the full compiler output. With `--update-src` the results are
   copied into `src/0000Boot/`, `src/Compiler/` and `src/Kernel/`.
2. **Stage 2** (`anokos_build.py iso`): writes `build/Anokos.ISO` from `src/` with the new
   compiler and kernel.

3. **Stage 3** (`anokos_build.py arm64`, optional): boots x86-64 Anokos with
   `tools/stage3/Arm64Build.TC` added to its start-up. The Anokos compiler builds
   `src/Kernel/KernelArm64.PRJ` with its ARM64 back end, and the image comes back over the serial
   port as `build/arm64/kernel8.img`. See [ARM64.md](ARM64.md).

Everything outside `Kernel/` and `Compiler/` (the Anokos system layer and apps) is compiled
just-in-time at boot, so changes there only need stage 2.

## The Anokos terminal

At boot the kernel asks the video BIOS (VBE) for the monitor's native resolution (from its EDID)
and picks the largest 32-bit linear-framebuffer mode that fits. Without EDID it uses the largest
mode up to 1920x1080, and if there is no such mode it falls back to the old 640x480 TempleOS
desktop.

The terminal runs the TerryC command line: type statements such as `Dir;` or `Cd("/Demo");`.
It supports line editing (arrows, Home/End, Delete), command history (Up/Down), `Cls;` or Ctrl+L
to clear, and Ctrl+Alt+C to stop a running command. Programs that need the TempleOS window manager
(the editor, graphics demos) don't work in the terminal yet.

## TerryC

TerryC, named in honor of Terry A. Davis, is the language TempleOS called HolyC. Anokos uses it
for everything: the kernel, the compiler, applications and the command line.

- Source files are `.TC`, headers are `.TH` (stored compressed as `.TC.Z` and `.TH.Z`).
- `#include "Name"` and `ExeFile("Name")` look for `Name.TC` first.
- TempleOS programs still work: `.HC`/`.HH` files are recognized as source, and an extensionless
  `#include` falls back to `Name.HC` when there is no `Name.TC`.
- Source files are DolDoc documents: a literal `$` is written `$$`, and some files carry binary
  sprite data after the text. Git stores `src/` byte-exact (see `.gitattributes`), so don't let an
  editor change line endings or strip that data.

## How `src/` was produced

`src/` was extracted **only** from the official `TempleOS.ISO` in `TempleOS/`; no code was taken
from GitHub mirrors or websites. The ISO uses RedSea, TempleOS's own file system, so it was read
with [`tools/redsea_extract.py`](tools/redsea_extract.py), a clean-room implementation of RedSea
and TempleOS's LZW compression. Each expanded file was checked against the size in its compression
header. The files were later renamed from `.HC`/`.HH` to `.TC`/`.TH`.

```sh
python tools/redsea_extract.py TempleOS/TempleOS.ISO extracted/   # --raw keeps .Z files compressed
```

Reference ISO SHA-256: `5d0fc944e5d89c155c0fc17c148646715bc1db6fa5750c0b913772cfec19ba26`

## Website and Code City

The website in [`docs/`](docs/) is served by GitHub Pages from the `main` branch. The Code City
(`docs/code-map/index.html`) and graph view (`docs/code-map/graph.html`) read data generated from
`src/`. Refresh it after changing the source:

```sh
python tools/codemap.py
```

The manual (`docs/manual/`) is a set of Markdown pages about how the kernel and OS work.
`docs/manual/index.html` renders them on the website with a sidebar and search; add new pages to
its `PAGES` list.

To preview the site, run `python -m http.server --directory docs` and open `http://localhost:8000`.
