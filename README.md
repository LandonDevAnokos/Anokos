# Anokos

**Anokos** is an open-source x86-64 operating system and kernel, built as a heavily modified
descendant of [TempleOS](https://en.wikipedia.org/wiki/TempleOS), the public-domain operating
system written by Terry A. Davis.

The goal is a community-developed OS anyone can use, study and contribute to, developed in the
open on GitHub.

> **Status:** early bootstrap. `src/` currently holds the unmodified TempleOS source as extracted
> from the original ISO. Anokos-specific changes start from here.

## Repository layout

| Path | Contents |
| --- | --- |
| [`src/`](src/) | The Anokos source tree (HolyC `.HC`/`.HH`, DolDoc `.DD`, data files). Starts as the full TempleOS tree. |
| [`TempleOS/`](TempleOS/) | The original `TempleOS.ISO`, kept **byte-for-byte unmodified** as the reference upstream. Do not edit. |
| [`tools/`](tools/) | Host-side tooling. `redsea_extract.py` extracts a RedSea-format ISO and expands its LZW `.Z` files. |

### Inside `src/`

| Directory | What it is |
| --- | --- |
| `Kernel/` | Core kernel: memory, scheduler, interrupts, block devices, RedSea/FAT32 filesystems |
| `Compiler/` | The HolyC JIT/AOT compiler and assembler |
| `Adam/` | The "Adam" system layer: window manager, DolDoc, graphics, sound, autocomplete, GUI controls |
| `Apps/` | Bundled applications |
| `Demo/` | Example and demo programs |
| `Doc/` | System documentation (DolDoc format) |
| `Misc/` | Miscellaneous data, including the OS install/build helpers |
| `0000Boot/` | The prebuilt boot kernel binary from the ISO |
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

## Running it

Anokos is not bootable on its own yet. Until the build pipeline is in place, the stock system can
be tried by booting `TempleOS/TempleOS.ISO` in a VM (QEMU, VirtualBox or VMware, x86-64, at least
512 MB RAM).

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Anokos is licensed under the **GNU General Public License v2.0 only** (GPL-2.0-only), the same
license as the Linux kernel; see [LICENSE](LICENSE). It is derived from TempleOS by Terry A. Davis,
which was released into the public domain.
