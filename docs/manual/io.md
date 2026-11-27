# Input and output

Text output goes through one function, `PutS`, which hands the string to a priority-ordered
chain of output hooks (the "KeyDev" chain); whichever hook claims it draws it. Keyboard input
runs the other way: an IRQ handler stores raw PS/2 bytes, a polling step turns them into
64-bit scan codes, and those are posted as `MSG_KEY_DOWN`/`MSG_KEY_UP` messages to the task
that has the focus. This page follows both paths, plus the mouse and the raw console.

## The big picture

```
 output                                   input
 ------                                   -----
 "x=%d\n",x;   (statement)                IRQ 1 -> IRQKbd -> KbdPktRead
     |                                              | raw bytes
 Print -> StrPrintJoin -> PutS             kbd.fifo -> kbd.fifo2
     |                                              |
 KeyDev chain, lowest priority first      KbdMsHndlr -> KbdHndlr -> KbdBuildSC
   0x40000000 input filter                          | 64-bit scan codes
   0x50000000 Anokos terminal             kbd.scan_code_fifo
   0x60000000 raw console                           |
   0x80000000 DolDoc documents           KbdMsgsQue -> TaskMsg(sys_focus_task,...)
                                                    |
                                          GetKey / GetChar / GetStr in that task
```

## Printing

TerryC has no `printf` call in normal code. A statement that is just a string literal is a
call to `Print`, and a statement that is just a character constant is a call to `PutChars`.
The compiler does this in `PrsFunCall` in
[src/Compiler/PrsExp.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Compiler/PrsExp.TC):
when there is no function, a `TK_CHAR_CONST` token selects `PutChars` and a string selects
`Print`. An empty string or empty character constant means "the format comes next as an
argument", which is how you print a variable format string:

```c
"Hello\n";            //Print("Hello\n");
"%d files\n",n;       //Print("%d files\n",n);
'\n';                 //PutChars('\n');
"" msg,dft;           //Print(msg,dft);  (used by GetStr)
'' ch;                //PutChars(ch);
```

`Print` itself, in
[src/Kernel/StrPrint.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/StrPrint.TC),
is three lines: format into a heap buffer with `StrPrintJoin`, call `PutS`, free the buffer.
`PutChars` sends up to eight characters packed in one `U64`, one `PutKey` call each.

The other members of the family use the same formatter:

| Function | Result |
| --- | --- |
| `StrPrint(dst,fmt,...)` | Formats into `dst`, no bounds check. |
| `CatPrint(dst,fmt,...)` | Appends to the string in `dst`. |
| `MStrPrint(fmt,...)` | Returns a new `MAlloc`ed string. Safe against overflow. |
| `PrintErr` / `PrintWarn` | Prints four caller addresses, then the error or warning text. |
| `StrPrintJoin(dst,fmt,argc,argv)` | The worker. With `dst==NULL` it grows its own buffer. |

`StrPrintJoin` throws `'StrPrint'` when the format asks for more arguments than were passed,
instead of reading garbage off the stack.

## Format strings

The syntax is `%[-][0][width][.decimals][flags][h aux]code` (see `src/Doc/Print.DD`).
`*` in place of a width, decimals or aux number takes it from the argument list.

| Code | Prints |
| --- | --- |
| `%d` `%u` | Signed / unsigned decimal. |
| `%X` `%x` | Hex, upper / lower case. No `0x` prefix. |
| `%b` `%B` | Binary. |
| `%f` `%e` `%g` `%n` | Floating point; `%n` is engineering notation (exponent a multiple of 3). |
| `%c` `%C` | Character (up to 8 packed in a `U64`); `%C` upper-cases. `%h25c` repeats 25 times. |
| `%s` | String. |
| `%Q` `%q` | String with `\` and `"` escaped / unescaped (for building source code). |
| `%D` `%T` | Date / time of a `CDate`. |
| `%p` `%P` | Pointer as a symbol+offset; `%P` makes it a DolDoc link (not in raw mode). |
| `%z` | Entry number n of a `\0`-separated list. Pass n first, then the list. |
| `%Z` `%S` | Entry of a `Define` list / a `Define` string. |
| `%F` `%$F` | Contents of a text file by name / of a `CDoc` in memory. |
| `%%` | A `%`. |

Flags: `t` truncates to the width, `,` adds thousands separators (or groups of four hex
digits), `-` left-justifies, `0` pads with zeros.

## The KeyDev chain

All output, and a few kinds of key events, go through a queue of `CKeyDevEntry` hooks in
`keydev.put_key_head`
([src/Kernel/KeyDev.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KeyDev.TC)).
Each entry has a priority and two callbacks:

```c
CKeyDevEntry *KeyDevAdd(Bool (*fp_put_key)(I64 ch,I64 sc),
	Bool (*fp_puts)(U8 *st),I64 priority,Bool key_descs=FALSE);
```

`KeyDevAdd` keeps the queue sorted by priority. Adding an entry with the same priority as an
existing one replaces it (the old one is removed with `KeyDevRem`). `PutKey(ch,sc)` and
`PutS(st)` walk the queue from the lowest priority number up and stop at the first callback
that returns `TRUE`. If an entry has no `put_s`, `PutS` feeds it the string one character at
a time through `put_key`. Two more rules apply in both functions:

- A task with `DISPLAYf_SILENT` set (see `Silent`) prints nothing at all.
- If Scroll Lock is on and the printing task has the focus, it waits (yielding) until Scroll
  Lock is off. That's the "pause output" key.

Key events carrying `SCF_KEY_DESC` (used by the key map to ask "what does this key do?") are
only passed to entries that registered with `key_descs=TRUE` (`KDF_HAS_DESCS`).

The entries that exist in Anokos:

| Priority | Hook | Added by | Claims output when |
| --- | --- | --- | --- |
| `0x20000000` | `MyPutKey` | `src/HomeKeyPlugIns.TC` | It handles the key (user key macros). |
| `0x40000000` | `KDInputFilterPutKey` | `KeyDevInit` | The task is an input filter task. |
| `0x50000000` | `AnoTermPutKey` | `AnoTermStart` | The terminal is up and the debugger isn't. |
| `0x60000000` | `KDRawPutKey` | `KeyDevInit` | The task is in raw mode (`IsRaw`). |
| `0x80000000` | `KDDocPutKey` | `src/Anokos/DolDoc/MakeDoc.TC` | Never; it puts the key or text into the task's document, if it has one. |

The name "KeyDev" (keyboard device) comes from `PutKey(ch,sc)` carrying a scan code as well
as a character. Under DolDoc, the editor reads keys with `GetKey(&sc,TRUE)` (in `DocGetKey`,
`src/Anokos/DolDoc/DocGet.TC`); the echo calls `PutKey`, and `KDDocPutKey` passes the key to
`DocPutKey`, which edits the document. So the same chain is the document's output path and its
editor, and a key macro hook like `MyPutKey` sits in front of it to catch keys first.

The input filter entry is how `In` and `InStr` (in
[src/Kernel/Job.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Job.TC))
type text into a task. They spawn an `InputFilterTask` that compiles and runs text; what that
task prints is turned by `KDInputFilterPutS` into `MSG_KEY_DOWN` messages, which `TaskMsg`
routes to the filtered task as if typed.

The Anokos terminal sits at `0x50000000`, after the input filter and before the raw console,
so it receives all normal output and the DolDoc layer at `0x80000000` never sees any. When
the debugger is active its hook returns `FALSE`, so debugger output falls through to the raw
console. The terminal also sets `fp_getstr2` to its own line editor. See
[The Anokos terminal](terminal.md#output-the-keydev-hook).

## Raw output

"Raw" means drawing characters straight onto the screen from the kernel, with no document,
window or terminal in between. It's used during boot, in the debugger and after a crash.

- `Raw(val)` and `IsRaw` in
  [src/Kernel/KMisc.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KMisc.TC)
  clear and test the task's `DISPLAYf_NOT_RAW` flag. A task is raw unless that flag is set; the
  terminal task sets it.
- `KDRawPutKey`/`KDRawPutS` call `RawPutChar` for each character when `IsRaw` is true.

`RawPutChar` in
[src/Kernel/Display.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Display.TC)
first deals with DolDoc formatting. Everything between two `$` characters is a DolDoc command
like `$FG,4$`; the raw console can't show colors or links, so it drops the command and keeps
only the surrounding text (`text.raw_flags`, `RWF_IN_DOLLAR`, `RWF_LAST_DOLLAR`). A doubled
`$$` prints a single `$`. Setting `RWF_SHOW_DOLLAR` turns this off.

Then it picks one of three screens:

1. **VBE framebuffer** (`RLf_VBE` set in `sys_run_level`): `VbeRawPutChar`, with interrupts off.
   This is the normal case on Anokos.
2. **VGA text mode** (neither `RLf_VBE` nor `RLf_VGA`): character and attribute bytes written to
   `text.vga_text_alias`, white on black, 80x25.
3. **VGA 640x480 16-color**: the 8x8 font glyph written to all four planes of
   `text.vga_alias`, with a copy kept in `text.raw_scrn_image` so scrolling can redraw.

The framebuffer console keeps its state in the `vbe` global (`CVbeGlbls`, declared in
[src/Kernel/KernelA.TH](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KernelA.TH)):
`fb` (the address used for writes), `fb_phys`, `width`, `height`, `pitch`, `bpp`, `scale`,
the text grid `cols`/`rows`, the cursor `raw_col`, and colors `fg`/`bg` as `0xRRGGBB`.
`SysGrInit` in
[src/Kernel/KMain.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KMain.TC)
fills it from `sys_vbe_info`, which the real-mode boot code got from the VBE BIOS. The font is
the 8x8 `text.font`, scaled up by `vbe.scale = MaxI64(1,height/400)` so text stays readable at
high resolutions (a 1080-line screen gets scale 2). `VbeDrawChar` draws one scaled cell,
`VbeRawScroll` moves the whole framebuffer up one text row, `VbeRawCls` clears it. The raw
cursor `raw_col` counts cells from the top left; tab goes to the next multiple of 8.

`SysGrInit` also keeps a legacy 640x480 text grid in `text.cols`/`text.rows` for code that
still sizes windows by it.

## The PS/2 keyboard

The driver is
[src/Kernel/SerialDev/Keyboard.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/SerialDev/Keyboard.TC),
with its state in the `kbd` global (`CKbdStateGlbls`).

**Setup.** `KbdMsInit` (in `Mouse.TC`) allocates the FIFOs: `kbd.fifo` (8 bytes),
`kbd.fifo2` (4 KB) and `kbd.scan_code_fifo` (4096 `I64`s). `KbdInit` talks to the 8042
controller through `KbdCmdSend`/`KbdCmdRead` (ports `KBD_CTRL` and `KBD_PORT`, with a 125 ms
timeout that throws), sends the keyboard `0xF0,0x02` (select scan code set 2), sets the
LEDs, installs `IRQKbd` at vector `0x21` and unmasks IRQ 1. The bytes the driver decodes are
PS/2 set 1 codes (in the tables below, Q is `0x10` and Enter is `0x1C`).

**In the IRQ.** `IRQKbd` acknowledges the PIC and calls `KbdPktRead`, which reads one byte
from `KBD_PORT`. A byte more than 1/8 s after the previous one flushes `kbd.fifo` (a stale
half-sequence). `0xE0` prefixes are held in `kbd.fifo` until the byte after them arrives, so
`kbd.fifo2` only ever gets whole keys. The IRQ also runs `KbdBuildSC` with `in_irq=TRUE` on its
own private state, only so Ctrl+Alt hot keys can fire inside the interrupt (see below).

**Outside the IRQ.** Someone has to call `KbdMsHndlr` regularly. In Anokos that's the
terminal's pump task `AnoTermPumpTask` (every 15 ms); under the window manager it's
`WinMgrTask`. `KbdMsHndlr` drains `kbd.fifo2` through `KbdHndlr`, which calls `KbdBuildSC` to
update `kbd.scan_code`. For each new key event it pushes the scan code into
`kbd.scan_code_fifo`, counts it in `kbd.cnt` and updates the down-key bitmaps
`kbd.down_bitmap` (merged codes) and `kbd.down_bitmap2` (unmerged). With `poll_kbd=TRUE` it
reads the port itself instead of waiting for IRQs; `GetKey` does this in the debugger, where
interrupts are off.

**Scan codes.** A scan code is an `I64`. The low byte is the set 1 key code, with bit 7 meaning
"had an `0xE0` prefix" (`SCf_E0_PREFIX`). The bits above it are flags, all defined in
`KernelA.TH`:

| Flag | Meaning |
| --- | --- |
| `SCF_KEY_UP` | Release, not press. |
| `SCF_SHIFT` `SCF_CTRL` `SCF_ALT` | Modifier held. |
| `SCF_CAPS` `SCF_NUM` `SCF_SCROLL` | Lock state (toggled on release). |
| `SCF_NEW_KEY` | Set on every event except the auto-repeat of a Shift, Ctrl or Alt already held. |
| `SCF_MS_L_DOWN` `SCF_MS_R_DOWN` | Mouse buttons, so key handlers see them too. |
| `SCF_DELETE` `SCF_INS` | Delete / Insert held. |
| `SCF_KEY_DESC` | Asking for a key description, not a real key. |

`KbdBuildSC` "merges" keys through `scan_code_map` (or `num_lock_map` when Num Lock is on):
right Shift becomes `SC_SHIFT`, right Ctrl `SC_CTRL`, keypad Enter `SC_ENTER`, the
`0xE0` cursor keys `SC_CURSOR_UP` and so on. The upper 32 bits of the scan code keep the
unmerged code with the same flags, for code that must tell left from right.

`ScanCode2Char` turns a scan code into ASCII with three 0x50-entry tables at the top of the
file, `NORMAL_KEY_SCAN_DECODE_TABLE`, `SHIFT_KEY_SCAN_DECODE_TABLE` and
`CTRL_KEY_SCAN_DECODE_TABLE` (Caps Lock swaps normal and shifted). Keys with no ASCII value
(cursor keys, F-keys) give 0, so code checks the scan code for them. `Char2ScanCode` goes
the other way by searching the tables.

## Ctrl+Alt hot keys

When Ctrl and Alt are both down, `KbdBuildSC` treats the next letter as a hot key: Ctrl+Alt+Del
calls `CtrlAltDel`, Esc counts as `t` and Tab as `n`, and a letter calls the callback in
`keydev.fp_ctrl_alt_cbs`. `CtrlAltCBSet(ch,fp,no_shift_desc,shift_desc,in_irq)` installs one.
`in_irq` decides where it runs: inside the keyboard interrupt (needed to stop a task stuck in
a loop that never yields) or later from `KbdHndlr`. The defaults from `KeyDevInit`:

| Keys | Action | In IRQ |
| --- | --- | --- |
| Ctrl+Alt+C | Break the focus task (sets `CTRL_ALT_C`, acted on by the scheduler) | yes |
| Ctrl+Alt+D | Enter the debugger | yes |
| Ctrl+Alt+F | Swap the standard and auxiliary fonts | no |
| Ctrl+Alt+M | Toggle mute | no |
| Ctrl+Alt+N | Next focus task | yes |
| Ctrl+Alt+T | New terminal window (`User`); a no-op under the Anokos terminal | no |
| Ctrl+Alt+V | Flush the VGA cache | yes |
| Ctrl+Alt+X | Kill the focus task | yes |

## Messages, focus and reading keys

`KbdMsgsQue` empties `kbd.scan_code_fifo` into the task in `sys_focus_task`: `TaskMsg(task,0,
MSG_KEY_DOWN or MSG_KEY_UP,ascii,scan_code,0)`. `TaskMsg` (in `Job.TC`) appends a `CJob` to
the task's `srv_ctrl` queue and wakes it. If the task is being fed by an input filter
(`TASKf_FILTER_INPUT`), the message goes to the filter instead.

The reading functions are in
[src/Kernel/SerialDev/Message.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/SerialDev/Message.TC).
All of them read the current task's queue, so they only see keys while the task has focus.

| Function | Behavior |
| --- | --- |
| `ScanMsg(&a1,&a2,mask)` | Takes the next message whose code is set in `mask`; discards the others. Returns `MSG_NULL` if none. |
| `GetMsg(&a1,&a2,mask)` | Same, but waits (sets `TASKf_IDLE` and yields). |
| `ScanKey(&ch,&sc)` | Non-blocking: one `MSG_KEY_DOWN`, ASCII and scan code. |
| `GetKey(&sc,echo)` | Waits for a key down; returns ASCII, which is 0 for keys like the arrows. |
| `GetChar(&sc,echo)` | Waits for a key with a non-zero ASCII value. |
| `GetStr(msg,dft,flags)` | Prints `msg`, reads a line via `fp_getstr2`, returns it `MAlloc`ed. |
| `GetS(buf,size,allow_ext)` | Reads a line into a fixed buffer. |

`GetStr` doesn't edit the line itself; it calls the function pointer `fp_getstr2`. The
kernel default is `SysGetStr2` in `KDbg.TC`, a bare loop over `GetChar` with backspace. The
DolDoc layer replaces it with `DocGetStr2`, and the Anokos terminal replaces that with
`AnoGetStr2`, which has cursor movement and history. The debugger temporarily puts back
`SysGetStr2`. See [the line editor](terminal.md#the-line-editor).

## The mouse

[src/Kernel/SerialDev/Mouse.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/SerialDev/Mouse.TC)
has two layers:

- `ms_hard` (`CMsHardStateGlbls`) is the device. `MsHardDrvrInstall`, spawned as its own task
  at boot, installs `IRQMsHard` at vector `0x2C` (IRQ 12) and calls `MsHardRst`, which resets
  the mouse through the 8042 and uses the IntelliMouse knock sequence (sample rates 200, 100,
  80) to detect a wheel. Packets are 3 bytes, or 4 with a wheel or extra buttons.
  `MsHardPktRead` collects bytes in `ms_hard.fifo` and moves only whole packets to
  `ms_hard.fifo2`; `MsHardHndlr` decodes them into `prescale`, `pos`, `bttns[5]` and `speed`, and
  sets `ms_hard.evt`.
- `ms` (`CMsStateGlbls`) is the pointer that programs use: `pos`, `pos_text`, `lb`, `rb`,
  after `MsUpdate` applies `scale`, `offset` and the snap grid `ms_grid`, clamped to the
  640x480 legacy screen. Only the window manager copies `ms_hard` into `ms` (`WinMsUpdate` in
  `src/Anokos/Win.TC`).

Under the Anokos terminal the window manager isn't running, so `ms` doesn't move; the terminal
doesn't use the mouse.

## Serial ports

The x86 kernel has no serial port driver. `src/Doc/Comm.TC` is an optional 16550 UART driver
(`CommInit8n1`, `CommPutChar`, `CommPrint`, IRQ 3 and 4 handlers) that you include yourself
from a start-up file; nothing in the kernel or the KeyDev chain uses it.

## On ARM64

- **Serial console.** The PL011 UART driver is
  [src/Kernel/Arm64/Serial.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Arm64/Serial.TC):
  `ArmSerInit` (8N1, FIFOs on, 115200 baud; if the board's UART clock is unknown it keeps
  the firmware's divisor),
  `ArmSerPutChar` (adds CR before LF), `ArmSerGetChar` (returns -1 when empty). It is polled.
  `RawPutChar` sends every character to `ArmSerPutChar` first, after stripping DolDoc
  commands, and also draws it on the framebuffer if there is one. The terminal's
  `TermRawChar` also copies its output to the UART.
- **The serial console is the keyboard.**
  [src/Kernel/Arm64/Keyboard.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Arm64/Keyboard.TC)
  replaces `KbdPktRead` and `KbdInit`. Its `KbdPktRead` drains the UART and converts each
  byte into the PS/2 set 1 bytes a real keyboard would send, modifier presses and releases
  included, straight into `kbd.fifo2`. Ctrl+letter arrives as a control character, Esc
  followed by a character is Alt+character (xterm "meta"), and VT100/xterm sequences like
  `ESC[A` or `ESC[3~` become `0xE0`-prefixed cursor and editing keys, with the xterm modifier
  parameter (`ESC[1;5C` is Ctrl+Right). A lone Esc counts once nothing follows it for 30
  jiffies (`ARM_KBD_ESC_MS`). From `kbd.fifo2` on, the shared x86 code (`KbdHndlr`,
  `KbdMsgsQue`) runs unchanged.
- There's no UART receive IRQ yet, so the ARM64 `KbdMsHndlr`
  ([src/Kernel/Arm64/Mouse.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Arm64/Mouse.TC))
  always polls. Because nothing runs `KbdBuildSC` in interrupt context, the hot keys marked
  "in IRQ" above (Ctrl+Alt+C, D, N, V, X) don't fire on ARM64.
- `Arm64/KbdCtrl.TC` stubs out the 8042 functions, and `Arm64/MsHard.TC`'s `MsHardRst`
  reports no mouse. A virtio-input or USB driver would feed `ms_hard.fifo2` later.
- **Framebuffer.**
  [src/Kernel/Arm64/Video.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Arm64/Video.TC)
  does what VBE does on x86. On QEMU `virt` it sets up a `ramfb` (needs `-device ramfb`)
  through the fw_cfg DMA interface, size from `fb=WxH` in the kernel command line or
  1920x1080. On a Raspberry Pi it asks the VideoCore firmware through the mailbox, at the
  monitor's native size, and `ArmFbUncache` makes the framebuffer non-cacheable where the
  page tables allow it. `ArmVideoInit` fills the same
  `vbe` globals and sets `RLf_VBE`, so `VbeRawPutChar` and the Anokos terminal work unchanged.
  Without a framebuffer, the shell runs on the serial console alone.
