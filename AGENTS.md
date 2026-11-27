# AGENTS.md

Guidance for AI coding agents working on Anokos. Humans: see [CONTRIBUTING.md](CONTRIBUTING.md).

Anokos is an operating system descended from TempleOS. The kernel, compiler, system layer and
apps are all written in **TerryC** (TempleOS's HolyC, renamed) and compiled by Anokos's own
compiler, inside the OS. Targets: x86-64 (main) and ARM64 (QEMU `virt`, Raspberry Pi 3/4; in
progress).

## Read first

- [docs/manual/](docs/manual/README.md): how the kernel and OS work, page by page, with links
  to the source. Start with `overview.md` and `language.md`.
- [BUILDING.md](BUILDING.md): build stages and commands.
- [ARM64.md](ARM64.md): ARM64 port status, design and the per-file conversion checklist.
- [ROADMAP.md](ROADMAP.md): milestones and what's planned next.

## Repository layout

| Path | What |
| --- | --- |
| `src/Kernel/` | kernel (AOT-compiled). `Kernel.PRJ` = x86-64 file list, `KernelArm64.PRJ` = ARM64 |
| `src/Kernel/Arm64/` | ARM64 versions of the x86-specific parts |
| `src/Compiler/` | TerryC compiler and assemblers (x86-64 and ARM64 back ends) |
| `src/Anokos/` | system layer, JIT-compiled at boot (DolDoc, windows, `Term.TC` terminal) |
| `src/StartOS.TC` | runs at boot after the kernel |
| `src/0000Boot/0000Kernel.BIN.C`, `src/Compiler/Compiler.BIN`, `*.MAP` | generated binaries, checked in |
| `tools/` | host build tools (Python 3) |
| `docs/` | website (GitHub Pages from `main`): `manual/`, `code-map/` |
| `TempleOS/` | the pristine upstream ISO. **Never modify.** |

## Hard rules

- **Kernel and compiler code is TerryC and ARM64/x86 assembly only**, compiled by the Anokos
  compiler inside Anokos. Don't add C, a host compiler, or a GNU toolchain to build OS code.
  Host-side Python tooling in `tools/` is fine.
- **The ARM64 port converts the existing kernel; it isn't a rewrite.** In shared files, wrap
  x86-only parts as below and put the ARM64 version in `src/Kernel/Arm64/` with the same
  labels/function names, so callers don't change:

  ```
  #ifdef ARCH_ARM64
  #include "/Kernel/Arm64/<File>"
  #else
  ...x86 code...
  #endif
  ```

  Every change to a shared file must keep x86-64 building and booting.
- **`src/` files are bytes, not UTF-8 text.** Many contain non-UTF-8 bytes and binary sprite
  data after the text. Edit existing files with a byte-preserving method (for example Python
  with `encoding='latin-1'` for read and write). Editors or tools that re-encode, normalize line
  endings or strip trailing whitespace will corrupt them. New files you create can be plain ASCII.
- Don't hand-edit generated files (`*.BIN*`, `*.MAP`, `docs/code-map/*.json`). Regenerate them.
- Commit or push only when the maintainer asks. Work on a branch, not `main`.

## Build and test

Run from the repo root. Python 3 and QEMU are required (`sh tools/build_qemu.sh` builds QEMU
into `build/qemu/` where packages aren't available, such as macOS 14).

```sh
python3 tools/anokos_build.py kernel --vm-backend qemu       # stage 1: compiler + kernel (~10 s)
python3 tools/anokos_build.py all --update-src --headless    # stage 1+2, copy binaries into src/
python3 tools/anokos_build.py run --headless                 # boot the ISO, screenshot build/run.png
python3 tools/anokos_build.py arm64                          # stage 3: ARM64 kernel (~6 s)
python3 tools/anokos_build.py run-arm64 --board virt --headless --keys 'Ver\r'   # virt|rpi3|rpi4
python3 tools/codemap.py                                     # refresh the website's Code City data
```

- Stage 1 log: `build/stage1_serial.log`; success is `@@ERRORS 0`.
- Stage 3 log: `build/arm64_serial.log`; on failure the first real error is a few lines after
  `@@ARM64 START` (filter out hex dump lines and `ParenWarning`s, which are normal).
- `run-arm64` waits for `@@ARM64 BOOT OK`, types `--keys` on the serial console (the ARM64
  keyboard), and saves `build/arm64/run_<board>.png`. Look at the screenshot.
- Changes outside `src/Kernel` and `src/Compiler` are JIT-compiled at boot: `iso` + `run` is
  enough to test them.

Before you call a change done:

- kernel or compiler change: stage 1 has 0 errors, and `run` boots to the terminal;
- shared kernel file or ARM64 change: also `arm64` builds and `run-arm64` reports
  `@@ARM64 BOOT OK` on `virt`, `rpi3` and `rpi4`;
- after `--update-src`, commit the regenerated binaries and MAP files with the source;
- after source changes, run `tools/codemap.py` and commit `docs/code-map/*.json`.

## TerryC traps

These cause most bugs. Details are in `docs/manual/language.md`.

- **Operator precedence is not C's.** Tightest to loosest: `` ` `` `<<` `>>`, then `*` `/` `%`,
  then `&`, then `^`, then `|`, then `+` `-`, then comparisons. So `a+b<<2` is `a+(b<<2)` and
  `x&0xFF+1` is `(x&0xFF)+1`. Parenthesize.
- No `?:` operator. Chained comparisons work: `0<=x<10`.
- No unsized global arrays (`U8 a[]={...}` at global scope); give the size.
- No arrays of function pointers; use `U8 *tbl[N]` and `Call(tbl[i])`.
- `#define` has no parameters. `#ifdef`, `#ifndef`, `#else`, `#endif` work.
- Casts are postfix: `ptr(U8 *)`, `x(I64)`.
- In strings, a literal `$` is `$$` (files are DolDoc documents).
- JIT-compiled globals aren't zeroed; initialize them.
- Classes are packed (no padding).
- `MemCpy` copies forward only; overlapping copies with `dst>src` need a backward loop.
- Fixed-argument functions pop their own arguments. Asm routines callable from TerryC must
  remove their stack arguments before returning (`RET1 n` on x86, `ADD SP,SP,n` then `RET` on
  ARM64).
- ARM64 inline asm: refer to an already-defined TerryC function as `&Name`, to a later one as a
  bare label. `ADR` can't reach TerryC globals. A statement can't start with an ARM64-only
  mnemonic such as `WFI`; use the pseudo-op (`HLT`).
- Boot starts in debug mode. The terminal hides output until `DbgMode(OFF)`.

## Docs

- When behavior changes, update the matching `docs/manual/*.md` page. New pages must be added to
  the `PAGES` list in `docs/manual/index.html`. The renderer supports headings, paragraphs,
  lists (one nesting level), fenced code, inline code, bold, italic, links, tables, block quotes
  and rules. No HTML or images.
- Keep `ARM64.md` (status table and checklist) current for ARM64 work.
- Link source files with full GitHub URLs (`https://github.com/LandonDevAnokos/Anokos/blob/main/src/...`).
- Write plainly: short sentences, no marketing tone.

## Pull requests

Follow CONTRIBUTING.md: one logical change per PR, explain what and why, and say exactly how you
tested it (which build stages, which boards, screenshots). Say that the work was done with AI.
