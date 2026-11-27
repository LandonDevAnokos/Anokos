# Anokos manual

How the Anokos kernel and operating system work, for programmers who want to read or change the
code. Every page links to the real source files on GitHub. The pages are plain Markdown: read
them here, on GitHub, or on the website's documentation page.

If you're new, read [How Anokos is put together](overview.md) first, then
[The TerryC language](language.md). After that the pages can be read in any order.

## Start here

- [How Anokos is put together](overview.md): the design, the layers and where everything is.
- [The TerryC language](language.md): what's different from C, including the traps.
- [Building Anokos](build.md): how the OS compiles itself in a VM, and the fast test loop.

## The kernel

- [Booting](boot.md): from the boot loader to the terminal.
- [Memory](memory.md): the identity map, block pools, heaps and `MAlloc`.
- [Tasks and scheduling](tasks.md): `CTask`, `Yield`, `Spawn`, jobs and messages.
- [Interrupts and timers](interrupts.md): the IDT, IRQs, the timer and jiffies.
- [Multicore](multicore.md): starting the other cores and sending them work.
- [Exceptions and the debugger](exceptions.md): `try`/`catch`/`throw`, faults and `Dbg`.
- [Input and output](io.md): `Print`, the KeyDev chain, the raw console, keyboard and mouse.
- [Block devices and file systems](filesystem.md): drives, RedSea, FAT32 and the file API.

## The compiler

- [The compiler](compiler.md): from source to machine code, ahead of time or just in time.
- [Symbols, hash tables and loading](symbols.md): how names are found and binaries loaded.

## The system

- [The Anokos terminal](terminal.md): the full-screen terminal and line editor.
- [The ARM64 port](arm64.md): how the same kernel runs on ARM64 and the Raspberry Pi.

## Other documents

- [README](https://github.com/LandonDevAnokos/Anokos/blob/main/README.md),
  [BUILDING](https://github.com/LandonDevAnokos/Anokos/blob/main/BUILDING.md),
  [ROADMAP](https://github.com/LandonDevAnokos/Anokos/blob/main/ROADMAP.md) and
  [ARM64 status](https://github.com/LandonDevAnokos/Anokos/blob/main/ARM64.md) in the repository.
- `src/Doc/`: the original TempleOS documentation, in DolDoc. Much of it still applies; where it
  and the code disagree, the code is right.

## Writing these pages

Each page explains one part of the system, names real functions and files, and links them on
GitHub. Where ARM64 differs, a page says so in an "On ARM64" note. The website renders a subset
of Markdown: headings, paragraphs, lists (one nesting level), fenced code, inline code, bold,
italic, links, tables, block quotes and rules. No HTML or images. To add a page, write the
Markdown file here and add it to the `PAGES` list in `docs/manual/index.html`.
