# Building Anokos

## Requirements

- Python 3
- [VirtualBox](https://www.virtualbox.org/) (used to compile the kernel and to test-boot)

## Commands

Run these from the repository root:

```sh
python tools/anokos_build.py all                # build the compiler and kernel, then build/Anokos.ISO
python tools/anokos_build.py all --update-src   # same, and copy the new binaries into src/
python tools/anokos_build.py iso                # only rebuild the ISO from src/
python tools/anokos_build.py run                # boot build/Anokos.ISO in a VM and take a screenshot
```

`build/Anokos.ISO` boots on any x86-64 PC or VM with legacy BIOS boot, an IDE/ATAPI CD drive and
at least 512 MB of memory. The tools create a VirtualBox VM called `AnokosBuild` in `build/vm/`.

## How the build works

TerryC is only compiled by its own compiler, which runs inside the OS itself, so the compiler and
kernel are bootstrapped in a VM:

1. **Stage 1** (`anokos_build.py kernel`): builds an ISO of stock TempleOS with `src/Compiler` and
   `src/Kernel` added under TempleOS's `.HC`/`.HH` file names, boots it headless in VirtualBox,
   compiles the compiler and the kernel there, and receives both binaries over the VM's serial port.
   `build/stage1_serial.log` has the full compiler output. With `--update-src` the results are
   copied into `src/0000Boot/`, `src/Compiler/` and `src/Kernel/`.
2. **Stage 2** (`anokos_build.py iso`): writes `build/Anokos.ISO` from `src/` with the new
   compiler and kernel.

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

To preview the site, run `python -m http.server --directory docs` and open `http://localhost:8000`.
