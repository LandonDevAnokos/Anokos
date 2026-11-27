# The Anokos terminal

Anokos boots into a full-screen, black-and-white text terminal drawn straight onto the
framebuffer at the display's native resolution. It replaces the TempleOS desktop (window
manager, wallpaper and DolDoc terminal windows). The command line is still the TerryC compiler;
only the screen and the line editor are new. The code is
[src/Anokos/Term.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Anokos/Term.TC).

## When it starts

`src/StartOS.TC` calls `AnoTermStart` as its last step. `AnoTermStart` does nothing unless the
kernel got a VBE linear framebuffer (`RLf_VBE` in `sys_run_level`, see [Booting](boot.md)).
Without one, StartOS has already started the TempleOS window manager instead, and the system
looks like TempleOS.

`AnoTermStart`:

1. sizes the text grid from the kernel's `vbe` globals: `term.cols=vbe.cols`,
   `term.rows=vbe.rows` (the kernel picks `vbe.scale` so text stays readable on large screens);
2. allocates the character grid (`term.chars`), a dirty flag per row and a one-line pixel buffer;
3. replaces the line editor: `fp_getstr2=&AnoGetStr2`, so every `GetStr` (including the command
   line) uses the terminal's editor;
4. hooks output with `KeyDevAdd(&AnoTermPutKey,&AnoTermPutS,TERM_PRIORITY,FALSE)`;
5. spawns two tasks: **Term Pump** (`AnoTermPumpTask`) and **Terminal** (`AnoTermTask`).

## Output: the KeyDev hook

Everything printed (`Print`, the `""` shortcut, `PutS`, `PutKey`) goes down the kernel's
keyboard-device chain, a list of handlers ordered by priority (see
[Input and output](io.md)). The terminal's handlers sit at `TERM_PRIORITY` (`0x50000000`): after
the input filter, before the raw console. They return `TRUE` (output consumed) while
`TermOwnsOutput` is true, that is while the terminal is active and the system isn't in debug mode.
In debug mode output falls through to the raw console, so the debugger still works, and the
terminal redraws everything afterwards.

`TermPutChar` handles one character with the terminal lock held:

- printable characters go into the grid at the cursor (`TermRawChar`), wrapping at the edge;
- `\n`, `\r`, `\t` and backspace move the cursor;
- DolDoc commands (`$...$`) are collected; `$CL$` clears the screen, the visible text of links and
  text widgets (`$TX`, `$LK`, `$BT` ...) is shown, and the rest (colors, etc.) is dropped.

The grid is a ring buffer: `term.top` is the physical row shown at the top of the screen, so
scrolling clears one row and moves `top` instead of copying the whole screen.

## Drawing

Rows are drawn only when they change. `TermDirty(y)` marks a row, and `TermRender` draws dirty
rows with `TermDrawRow`, which renders each character from the kernel font (`text.font`), scaled
by `vbe.scale`, into a one-line pixel buffer and copies that line to the framebuffer (`vbe.fb`,
`vbe.pitch`) `vbe.scale` times. The cursor is an inverted cell.

While output is streaming, `TermMaybeRender` redraws at most `TERM_FPS` (30) times a second; the
pump task flushes the rest as soon as the printing task yields. This keeps big outputs fast.

## Input: the pump task

The Term Pump task loops every 15 ms:

```
  KbdMsHndlr(FALSE,FALSE)  turn keyboard bytes into scan codes
  KbdMsgsQue               send MSG_KEY_DOWN/UP to sys_focus_task
  blink the cursor every TERM_BLINK (0.5 s), render dirty rows
  Sleep(15)
```

If the focus task has died, focus goes back to the Terminal task. Without the window manager,
this task is what feeds the keyboard to programs.

## The line editor

`AnoGetStr2` replaces the DolDoc line editor for every `GetStr`. It remembers where the line
starts (`anchor_x`, `anchor_y`), reads keys with `GetKey`, and redraws the line with
`TermEditDraw` after each change.

| Key | Action |
| --- | --- |
| Left, Right, Home, End | move the cursor |
| Backspace, Delete | delete before / at the cursor |
| Up, Down | command history (`TERM_HIST_MAX`, 64 lines) |
| Esc, Ctrl+U | clear the line |
| Ctrl+L | clear the screen, keep the line |
| Enter | return the line (a `MAlloc`ed string) and add it to the history |

Lines are at most `TERM_LINE_MAX` (1024) characters.

## The command line

The Terminal task (`AnoTermTask`) sets itself as the focus task, changes to the home directory,
prints a banner, and runs

```c
while (TRUE)
  UserTaskCont;
```

`UserTaskCont` (in `src/Kernel/KTask.TC`) starts the compiler in command-line mode
(`CCF_CMD_LINE|CCF_PMT`). Whenever the lexer needs more input, `CmdLinePmt` (in
`src/Compiler/CMisc.TC`) prints the current directory and `>` and reads a line with `GetStr`,
which is the terminal's editor. `ExeCmdLine` compiles and runs each statement as TerryC, the same
as the TempleOS command line. So `Dir;`, `Cd("/Demo");` and `"%d\n",1+2;` all
work, and any function in the system can be called by name. Ctrl+Alt+C stops a running command.

Programs that need the TempleOS window manager (the editor, graphics demos) don't work in the
terminal yet.

## On ARM64

The ARM64 kernel includes `Term.TC` itself (`src/Kernel/KernelArm64.PRJ`), because there's no
compiler on ARM64 yet to load it from StartOS. Differences, all in `#ifdef ARCH_ARM64` blocks:

- `AnoTermTask` runs `ArmShell` (in `src/Kernel/Arm64/KMain.TC`) instead of `UserTaskCont`: a
  line is matched against built-in commands (`Help`, `Cls`, `Ver`, `Mem`, `Tasks`, `Date`,
  `SelfTest`, `Reboot`, `PowerOff`), typed like TerryC calls (`Mem;`, `Mem();` or `Mem`).
- Output is mirrored to the serial console (`TermRawChar` and the newline cases call
  `ArmSerPutChar`), and `TermSerEdit` redraws the line being edited on serial with VT100
  save/restore-cursor sequences. That makes the terminal usable from a terminal on the host.
- The keyboard is the serial console: `src/Kernel/Arm64/Keyboard.TC` turns its bytes into PS/2
  scan codes, so the pump task and line editor work unchanged.
- The framebuffer comes from ramfb (QEMU virt) or the VideoCore mailbox (Raspberry Pi), see
  [ARM64](arm64.md). With no framebuffer, the same shell runs on the serial console alone.

## Changing it

- Colors: `term.fg` and `term.bg` (0x00RRGGBB) are set in `AnoTermStart`.
- Text size: `vbe.scale` is set where the framebuffer is set up (`SysGrInit` in
  `src/Kernel/KMain.TC`; `ArmVideoInit` on ARM64).
- New editor keys: add a case to the `switch (sc.u8[0])` (scan codes) or `switch (ch)` (ASCII)
  in `AnoGetStr2`.
- Mind the lock: functions named `...NoLock` and `TermPutChar` expect `TermLock` to be held.
