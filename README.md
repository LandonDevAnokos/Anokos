# Anokos

**Anokos** is an open-source operating system and kernel, built as a heavily modified
descendant of [TempleOS](https://en.wikipedia.org/wiki/TempleOS), the public-domain operating
system written by Terry A. Davis.

The goal is a community-developed OS anyone can use, study and contribute to, developed in the
open on GitHub. Anokos runs on x86-64 today, with ARM64 and PowerPC planned.

> **Status:** first bootable milestone. Anokos boots straight into its own full-screen terminal
> (white text on black) at the display's native resolution, with no desktop environment.
> See the [roadmap](ROADMAP.md) for what comes next.

**Website:** [landondevanokos.github.io/Anokos](https://landondevanokos.github.io/Anokos/), including a
[3D code map](https://landondevanokos.github.io/Anokos/code-map/) of every function, variable and class in
the source and how they connect.

## Roadmap

Planned milestones, in order (details in [ROADMAP.md](ROADMAP.md)):

1. **TerryC:** rename the HolyC language to TerryC, with `.TC`/`.TH` source files
2. **PCI and PCI Express:** full hardware discovery and a driver model
3. **Ring 3:** run applications in user mode, protected from the kernel
4. **GPU and graphics:** real graphics drivers and a modern graphics library
5. **Multi-architecture:** ARM64 (Raspberry Pi) and ppc64 (Power Mac G5) alongside x86-64

## Repository layout

| Path | Contents |
| --- | --- |
| [`src/`](src/) | The Anokos source tree (HolyC `.HC`/`.HH`, DolDoc `.DD`, data files). Starts as the full TempleOS tree. |
| [`TempleOS/`](TempleOS/) | The original `TempleOS.ISO`, kept **byte-for-byte unmodified** as the reference upstream. Do not edit. |
| [`docs/`](docs/) | The project website, published with GitHub Pages, including the 3D code map. |
| [`ROADMAP.md`](ROADMAP.md) | Planned milestones and the order they will be worked on. |
| [`tools/`](tools/) | Host-side tooling: ISO extraction (`redsea_extract.py`), ISO building (`build_iso.py`), the build driver (`anokos_build.py`) and the code map generator (`codemap.py`). |

### Inside `src/`

| Directory | What it is |
| --- | --- |
| `Kernel/` | Core kernel: boot, VBE framebuffer, memory, scheduler, interrupts, block devices, RedSea/FAT32 filesystems |
| `Anokos/` | The Anokos system layer: the full-screen terminal (`Term.HC`) |
| `Compiler/` | The HolyC JIT/AOT compiler and assembler (HolyC is being renamed TerryC) |
| `Adam/` | The "Adam" system layer: window manager, DolDoc, graphics, sound, autocomplete, GUI controls |
| `Apps/` | Bundled applications |
| `Demo/` | Example and demo programs |
| `Doc/` | System documentation (DolDoc format) |
| `Misc/` | Miscellaneous data, including the OS install/build helpers |
| `0000Boot/` | The boot kernel binary, built from `Kernel/` by `tools/anokos_build.py` |
| `Downloads/` | Helper files from the ISO (Linux-side tools, songs, etc.) |
| `Home/`, `Tmp/` | User home and temp directories (Home is empty on the stock ISO) |

## How `src/` was produced

`src/` was extracted **only** from the official `TempleOS.ISO` in `TempleOS/`; no code was taken
from GitHub mirrors or websites.

The ISO's filesystem is RedSea (TempleOS's own format), which standard tools like 7-Zip cannot
read, so extraction is done with [`tools/redsea_extract.py`](tools/redsea_extract.py), a
clean-room Python implementation of the RedSea directory format and TempleOS LZW decompression.
Files stored compressed on the ISO (`*.Z`) are expanded and saved without the `.Z` suffix
(e.g. `Kernel/KMain.HC.Z` → `src/Kernel/KMain.HC`). Each expanded file is verified against the
size recorded in its compression header.

Reproduce it with:

```sh
python tools/redsea_extract.py TempleOS/TempleOS.ISO extracted/
# add --raw to dump the files without expanding .Z compression
```

Reference ISO SHA-256: `5d0fc944e5d89c155c0fc17c148646715bc1db6fa5750c0b913772cfec19ba26`

### A note on file contents

Many `.DD` and some `.HC` files are DolDoc documents. They're mostly text, with inline `$...$`
markup commands and sometimes a binary trailer holding embedded sprites/graphics. Git is
configured (see `.gitattributes`) to store `src/` byte-exact, so don't let your editor
"fix" line endings or strip those trailers.

## Building and running

You need Python 3 and [VirtualBox](https://www.virtualbox.org/).

```sh
python tools/anokos_build.py all            # compile the kernel, then build build/Anokos.ISO
python tools/anokos_build.py run            # boot build/Anokos.ISO in a VM and take a screenshot
```

Boot `build/Anokos.ISO` in any x86-64 VM or PC (BIOS/legacy boot, IDE/ATAPI CD drive, at
least 512 MB RAM).

### How the build works

HolyC is only compiled by the HolyC compiler, which runs inside the OS itself, so the kernel is
bootstrapped in a VM:

1. **Stage 1** (`anokos_build.py kernel`): builds an ISO of stock TempleOS with `src/Kernel`
   added, boots it headless in VirtualBox, compiles the Anokos kernel there and receives the
   binary over the VM's serial port (`build/stage1_serial.log` has the full compiler output).
   Add `--update-src` to copy the result into `src/0000Boot/` and `src/Kernel/Kernel.MAP`.
2. **Stage 2** (`anokos_build.py iso`): writes `build/Anokos.ISO` from `src/` with the new kernel.

Everything outside `Kernel/` and `Compiler/` (the Anokos layer, Adam, apps) is compiled
just-in-time at boot, so changes there only need stage 2.

### The Anokos terminal

At boot the kernel asks the video BIOS (VBE) for the monitor's native resolution (from its EDID)
and picks the largest 32-bit linear-framebuffer mode that fits; without EDID it uses the largest
mode up to 1920x1080. If no such mode exists it falls back to the old 640x480 TempleOS desktop.

The terminal runs the standard HolyC command line: type statements such as `Dir;` or
`Cd("/Demo");`. It supports line editing (arrows, Home/End, Delete), command history (Up/Down),
`Cls;` / Ctrl+L to clear and Ctrl+Alt+C to stop a running command. Programs that need the
TempleOS window manager (the editor, games, graphics demos) don't work in this milestone.

## Website and code map

The website lives in [`docs/`](docs/) and is served by GitHub Pages from the `main` branch.
The 3D code map reads `docs/code-map/codemap.json`, which is generated from `src/` by a fast
approximate parser. Refresh it after changing the source:

```sh
python tools/codemap.py
```

To preview the site locally, serve the `docs/` folder, for example with
`python -m http.server --directory docs`, and open `http://localhost:8000`.

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Anokos is licensed under the **GNU General Public License v2.0 only** (GPL-2.0-only), the same
license as the Linux kernel; see [LICENSE](LICENSE). It is derived from TempleOS by Terry A. Davis,
which was released into the public domain.
