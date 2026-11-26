# Contributing to Anokos

Thanks for helping build Anokos. This project accepts public contributions through GitHub pull
requests.

## Workflow

1. Fork the repository and create a branch from `main` (`feature/short-name` or `fix/short-name`).
2. Make focused changes. One logical change per pull request.
3. Describe what you changed and why in the PR, and how you tested it (e.g. booted in QEMU,
   compiled with `#include`, ran a demo).
4. A maintainer reviews and merges.

For large changes (new subsystems, filesystem or ABI changes, removing major features), open an
issue first to discuss the design.

## Where to help

The [roadmap](ROADMAP.md) lists the planned milestones in order. Work on the current milestone
is the most useful, but bug fixes and smaller improvements are always welcome. For work on a
later milestone, open an issue first so it can be planned around earlier changes; for example,
the TerryC rename (milestone 1) will touch most files in `src/`.

## Building and testing

See [Building and running](README.md#building-and-running). In short:

- Changes outside `src/Kernel/` and `src/Compiler/` are compiled at boot, so
  `python tools/anokos_build.py iso` followed by `python tools/anokos_build.py run` is enough.
- If you change `src/Kernel/`, rebuild the kernel with
  `python tools/anokos_build.py all --update-src` and commit the regenerated
  `src/0000Boot/0000Kernel.BIN.C` and `src/Kernel/Kernel.MAP` along with your source change.
- HolyC source files are DolDoc documents: write a literal `$` as `$$`. JIT-compiled globals
  start out as garbage, not zero, so initialize them explicitly.

## Ground rules

- **Never modify anything in `TempleOS/`.** It is the pristine upstream reference ISO.
- Keep `src/` files byte-exact unless you mean to change them. Many `.DD` files contain binary
  sprite data after the text; editors that normalize line endings, trim trailing whitespace or
  re-encode files can corrupt them. Use an editor setting that preserves bytes, or edit inside
  the OS itself.
- Follow the existing HolyC style in the file you're editing (tabs for indentation, the existing
  naming conventions such as `CamelCase` functions and `C`-prefixed classes).
- Only contribute code you have the right to contribute. By submitting a contribution you agree
  to release it under the project's [LICENSE](LICENSE).

## Reporting bugs

Open an issue with: what you did, what you expected, what happened, and your VM/hardware setup.
Screenshots of the screen output help a lot.
