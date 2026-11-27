# The compiler

The compiler in [src/Compiler](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Compiler)
turns TerryC source straight into machine code. There is no assembler text, object file or
linker in between: the parser emits intermediate code, a chain of optimization passes rewrites
it, and a back end writes x86-64 (or, for ARM64 builds, AArch64) bytes either into the code
heap (JIT) or into a module file (AOT).

## Source files

[Compiler.PRJ](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Compiler/Compiler.PRJ)
includes the files in this order, which is also roughly the order of the pipeline:

| File | Role |
| --- | --- |
| `CompilerA.TH`, `CompilerB.TH` | `IC_` opcodes, precedence levels, keyword numbers, exported API |
| `CInit.TC` | `intermediate_code_table`, internal types, `cmp.binary_ops` precedence table |
| `CExcept.TC` | `LexExcept`, `LexWarn`, `ICPut` (prints an intermediate code) |
| `LexLib.TC`, `Lex.TC` | tokens, `#` directives, `#include`, `CmpCtrlNew` |
| `CHash.TC` | `MapFileWrite`, hash entry sizes |
| `PrsLib.TC` | `ICAdd`, code-control stack (`COCPush`/`COCPop`), `COCCompile` |
| `AsmInit.TC`, `AsmLib.TC`, `AsmResolve.TC`, `Asm.TC` | x86 assembler for `asm {}` |
| `Arm64Lib.TC`, `AsmArm64.TC` | AArch64 encoders, ARM64 assembler, `A64ImageWrite` |
| `PrsExp.TC`, `PrsVar.TC`, `PrsStmt.TC` | expressions, declarations, statements |
| `CMisc.TC`, `CMain.TC` | `Option`, `StreamPrint`, `Cmp`, `ExePutS`, `ExeFile` |
| `OptLib.TC`, `OptPass012.TC` ... `OptPass6.TC` | machine-independent passes |
| `BackLib.TC`, `BackA/B/C.TC`, `BackFA/FB.TC`, `Templates.TC` | x86-64 code generation |
| `BackArm64.TC` | ARM64 code generation (`A64Pass`) |
| `OptPass789A.TC` | x86-64 final passes |
| `UAsm.TC` | x86 unassembler (`Ui`, `Un`, `U`) |
| `OpCodes.DD` | keyword, register and x86 opcode tables, read at start-up |

At load time `CInit` builds the tables: `CmpLoadDefines`, `CmpFillTables`, then
`AsmHashLoad` reads `OpCodes.DD` into `cmp.asm_hash` and `UAsmHashLoad` sorts the opcodes for
the unassembler. `AsmHashLoad` also hangs `cmp.asm_hash` off the Adam task's hash table, so
every task sees keywords, registers and opcodes (see [symbols](symbols.md)).

## CCmpCtrl

One compile is one `CCmpCtrl` (KernelA.TH), made by `CmpCtrlNew` in
[Lex.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Compiler/Lex.TC). It holds the
lexer state (`token`, `cur_str`, `cur_i64`, the `lex_include_stk` of open files), the symbol
tables in use (`htc`, a `CLexHashTableContext`), the intermediate code being built (`coc`),
the AOT output state (`aotc`, `aot`), the option bits (`opts`) and the error counts. Active
compiles are queued on the task (`Fs->next_cc`/`Fs->last_cc`), which is how `Option` and
`StreamPrint` find "the current compile" through `Fs->last_cc`.

`htc` has four table pointers. `hash_table_lst` is where lookups start, `local_hash_table`
holds asm local labels, `glbl_hash_table` receives new globals and functions, and
`define_hash_table` receives `#define`s. For a JIT compile all four start as `Fs->hash_table`.

## JIT and AOT

The same parser serves two entry points in
[CMain.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Compiler/CMain.TC):

```
JIT:  ExeFile(name) -> ExePutS("#include \"name\";") -> Lex -> ExeCmdLine
        loop: LexStmt2Bin (one statement) -> Call(machine_code)
AOT:  Cmp(prj) -> CmpBuf("#include \"prj\"") -> CmpJoin
        loop: LexStmt2Bin -> code appended to the module, IET_MAIN entry
      -> patch table -> CBinFile header -> FileWrite("....BIN.Z")
```

- **JIT.** `ExePutS` makes a `CCmpCtrl`, copies the options of the enclosing compile, and runs
  `ExeCmdLine` ([KTask.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KTask.TC#L314)),
  which compiles a statement, calls it, stores the result in `Fs->answer`, and repeats. A
  function definition compiles straight into `Fs->code_heap`. The terminal's command line
  (`UserTaskCont`) is the same loop with `CCF_CMD_LINE|CCF_PMT`. `ExeFile2` and `ExePutS2` are
  variants that let exceptions through.
- **AOT.** `Cmp(filename,map_name,out_name,drv_let)` sets `CCF_AOT_COMPILE` and calls
  `CmpJoin`. `CmpJoin` gives the module its own global table, chained only to
  `cmp.asm_hash`, so an AOT module sees nothing from the running system except what it
  includes itself. That is why Kernel.PRJ and Compiler.PRJ include all the headers. Code
  goes into `CAOTBinBlk` buffers through `AOTStoreCodeU8` and friends, at offset
  `aotc->rip`.

Even an AOT compile runs JIT code. Constant expressions (`LexExpressionI64` for array sizes,
`case` labels, `#if`, default arguments) are compiled with `LexExpression2Bin` and called.
`#exe {}` blocks (`PrsStreamBlk` in PrsStmt.TC) clear `CCF_AOT_COMPILE`, point all tables at
`Fs->hash_table` and run `ExeCmdLine` on the block. Asm operands are compiled to little
functions that `AsmResolve` calls once the labels are known.

## Lexer and preprocessor

`Lex` returns one token. Identifiers are looked up as they are read: first in the current
function's locals (`MemberFind` on `htc.local_var_lst`), then with `HashFind` in
`htc.hash_table_lst` using `htc.hash_mask`, which excludes import placeholders. The hit goes
in `cc->hash_entry`, so the parser knows at once whether a name is a keyword, type, function or
variable. A `#define` name is expanded on the spot by pushing its text as a new input with
`LexIncludeStr`.

Directives are handled inside `Lex` when it sees `#`: `#include` pushes a file (as a DolDoc
document through `LexAttachDoc` once `RLf_DOC` is set, otherwise as plain text), `#define`
builds a `CHashDefineStr`, the `#if` family skips text by scanning for matching `#` keywords,
`#exe` calls `PrsStreamBlk`, and `#help_index` sets `cc->cur_help_idx`. When an input ends,
`LexGetChar` pops back to the including file. The language side is in
[the TerryC language](language.md#the-preprocessor).

## Parsing to intermediate code

There is no syntax tree. `PrsStmt` ([PrsStmt.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Compiler/PrsStmt.TC#L906))
and `PrsExpression` ([PrsExp.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Compiler/PrsExp.TC))
call `ICAdd` as they go, appending `CIntermediateCode` records to `cc->coc.coc_head`.
Expressions use an operator-precedence parser with an explicit stack (`CPrsStk`); the
operator table is `cmp.binary_ops`. Control flow becomes labels (`CCodeMisc` of type
`CMT_LABEL`) and jumps: `PrsIf` emits `IC_BR_ZERO` to the else label, `PrsWhile` a label,
the condition, `IC_BR_ZERO` and `IC_JMP` back.

`PrsFun` brackets a body with `IC_ENTER` and `IC_LEAVE` and compiles it right away with
`COCCompile`. `PrsVarLst` (PrsVar.TC) lays out arguments (from `[RBP+16]` up), locals
(below `RBP`) and class members. `COCPush`/`COCPop` save and restore the code list, which is
how a function's arguments, a `for` loop's step, or a nested expression are built separately
and spliced in.

## Intermediate code

A `CIntermediateCode` (KernelA.TH) has an opcode `ic_code` (the `IC_` constants in
[CompilerA.TH](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Compiler/CompilerA.TH)),
an operand `ic_data`, a result class `ic_class`, flags `ic_flags` (`ICF_`), the source line
`ic_line`, and three `CICArg` operands `arg1`, `arg2`, `res`. A `CICArg` has a `CICType` (a raw
type such as `RT_I64` plus a mode: `MDF_STK`, `MDF_IMM`, `MDF_REG`, `MDF_DISP`, `MDF_SIB`,
`MDF_RIP_DISP32`), a register and a displacement. `ic_body` is a union: the tree links
(`CICTreeLinks`) of the early passes, later the machine code bytes for this one instruction.

The parser emits a stack machine: an `IC_ADD` takes both operands from the stack and leaves
its result there. The passes then replace stack operands with registers, immediates and
memory operands. `intermediate_code_table` in CInit.TC gives each opcode its argument count,
result count, class (`IST_DEREF`, `IST_ASSIGN`, `IST_CMP`) and printable name.

The opcodes fall into groups: constants and addresses (`IC_IMM_I64`, `IC_STR_CONST`,
`IC_ABS_ADDR`, `IC_ADDR_IMPORT`), loads and stores (`IC_DEREF`, `IC_ASSIGN`, `IC__PP`), arithmetic
and compares (`IC_ADD` ... `IC_LESS_EQU`), branches (`IC_JMP`, `IC_BR_ZERO`, the fused
`IC_BR_LESS` family), calls (`IC_CALL`, `IC_CALL_INDIRECT2`, `IC_CALL_IMPORT`, `IC_CALL_EXTERN`),
frame (`IC_ENTER`, `IC_LEAVE`, `IC_RET`), and intrinsics that kernel functions map to with
`_intern` (`IC_BT`, `IC_QUE_INS`, `IC_IN_U8`, `IC_RDTSC`, `IC_SQRT`, ...).

## Optimization passes

`COCCompile` ([PrsLib.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Compiler/PrsLib.TC#L251))
runs them in order on one function or statement:

1. **Pass 0** runs during parsing: `PrsFunCall` calls `OptPass012` on each argument to learn
   its type, so int and F64 arguments get converted.
2. **Passes 1 and 2** (`OptPass012`, run twice): fold constant arithmetic, set NOPs on
   eliminated codes, rebuild the expression trees to find the class of every value, scale
   pointer arithmetic, and turn conditions into short-circuit branches and three-argument
   compare-and-branch codes (`IC_BR_LESS` and friends).
3. **Pass 3** (`OptPass3`): gives every operand its raw type (still as a stack operand),
   merges address-plus-deref pairs into `IC_MOV` with a displacement, and counts how each
   `[RBP]` offset is used.
   At its end it picks register variables. A variable whose address is taken stays on the
   stack. Per the comment there: RAX, RBX, RCX, RDX and R8 are scratch for each intermediate
   code, R9 holds stack-machine temporaries, and RSI, RDI, R10-R15 hold register variables,
   with R12 and R13 only for non-pointers.
4. **Pass 4** (`OptPass4`): removes dead code after unconditional jumps, rewrites accesses to
   register variables (`CmpOffset2Reg`), and folds patterns such as add-constant into `IC_LEA`.
5. **Pass 5** (`OptPass5`): where an operand comes off the stack from a plain `IC_MOV` or
   `IC_REG`, folds that operand into the consumer and applies int/F64 conversions to
   immediates at compile time.
6. **Pass 6** (`OptPass6`): decides which stack temporaries can live in a register, works out
   the registers each call clobbers, and folds add-constant plus deref into a displacement.

The x86 back end then runs `OptPass789A` as passes 7, 8 and 9 to size the code, repeats
pass 9 while it keeps shrinking (short jumps become possible), and writes the bytes in
pass 10. Growth between passes is a compiler error. `PassTrace` selects passes whose
intermediate code is printed; `Trace` unassembles the result.

## The x86-64 back end

`OptPass789A` walks the codes and calls the emitters. `BackLib.TC` has the byte, REX and ModR/M
helpers (`ICU8`, `ICModr1`, `ICMov`, `ICPush`), `BackA.TC` add, subtract, multiply, divide and
shifts, `BackB.TC` unary operators, compares and branches, `BackC.TC` assignment, bit
operations, queues and `switch` jump tables, and `BackFA.TC`/`BackFB.TC` floating point.
Floating point uses the x87 unit; [Templates.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Compiler/Templates.TC)
holds prebuilt instruction sequences (`CMP_TEMPLATE_SQRT`, `..._SIN`, `..._RDTSC`,
`..._STRLEN`, ...) that `ICCopyTemplate` copies into the output, in variants that skip the
push or pop when neighbouring float codes can share the x87 stack.

Each intermediate code's bytes are kept in its own `ic_body` until the last pass joins them,
and `OptPass789A` records the address of every source line in a `CDbgInfo` for the debugger.

## The ARM64 back end

With `Option(OPTf_ARM64,ON)`, `COCCompile` with `target=TRUE` skips `OptPass789A` and runs
`A64Pass` in [BackArm64.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Compiler/BackArm64.TC)
three times: to place labels, to size, and to write. If the size changes between the last two,
that is an error. Only AOT code for the module being built takes this path (`target` is set
by `PrsFun` and `LexStmt2Bin`); the JIT code the compiler runs on the build machine stays
x86-64. Passes 0 to 6 are shared, so the x86 register numbers they allocate are mapped onto
X0-X15, with RSP as SP and RBP as X29. The encoders are in
[Arm64Lib.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Compiler/Arm64Lib.TC).
Calls to imports go to `ArmImportTrap`, and `OPTf_GLBLS_ON_DATA_HEAP` and `interrupt`
functions are rejected. More in [ARM64](arm64.md) and ARM64.md.

## The assemblers

`PrsAsmBlk` in [Asm.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Compiler/Asm.TC)
assembles `asm {}` blocks. Opcode forms come from `OpCodes.DD`, which uses TempleOS's own
names: forms with different argument counts are separate opcodes (`IMUL`, `IMUL2`). With
`OPTf_ARM64` on, each line is offered to `PrsArm64Ins` in
[AsmArm64.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Compiler/AsmArm64.TC) first.

Labels are `CHashExport` entries. A reference to a label not yet defined is recorded with
`AsmUnresolvedAdd` together with the compiled operand expression. `AOTLocalsResolve` resolves
`@@` labels at the next `::` label, and `AOTGlblsResolve` in
[AsmResolve.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Compiler/AsmResolve.TC)
resolves the rest at the end of the module. It also patches calls to functions that were
`extern` when called (`CExternUsage` lists, with `A64PatchRel` on ARM64) and turns every
`HTF_EXPORT` symbol into an export entry. In JIT mode a top-level `asm {}` block is assembled
by a nested `CmpJoin` and fixed up in place by `CmpFixUpJITAsm`, which adds `::` labels to
`Fs->hash_table`.

## The unassembler

[UAsm.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Compiler/UAsm.TC) decodes x86
code with the same opcode tables: `Ui` does one instruction, `Un(addr,cnt)` a range, and `U`
is the user command. There is no ARM64 unassembler.

## Output: BIN files

`Cmp` writes the module code after a `CBinFile` header and follows it with a patch table:

```
CBinFile   jmp (short JMP over the header), module_align_bits, bin_signature 'TOSB',
           org, patch_table_offset, file_size
code       aot_U8s bytes
patch      entries: U8 type, U32 value, name string, type-specific data ... IET_END
```

The entries are written in this order:

1. `IET_ABS_ADDR`: a count, then the U32 offsets of 32-bit absolute addresses to relocate.
2. `IET_DATA_HEAP`: globals made with `OPTf_GLBLS_ON_DATA_HEAP`; name, size, references.
3. Exports: `IET_REL32_EXPORT` (offset in the module) or `IET_IMM32_EXPORT` (absolute value)
   with the symbol name.
4. Imports and the rest: `IET_REL_I32`, `IET_IMM_I64` and the other sizes, each the offset of
   a field to patch and the symbol name. A run of references to the same symbol gives the
   name only once. `IET_MAIN` entries point at top-level code.

The loader that reads this, `Load` in KLoad.TC, is described in
[symbols](symbols.md#the-loader). With a map name, `CmpJoin` also writes a MAP file with
`MapFileWrite`: one DolDoc link per symbol, to its source line, with the line table attached.

With `OPTf_ARM64`, `Cmp` calls `A64ImageWrite` instead and writes a flat `.IMG`: the module, a
table of 64-bit absolute addresses for the boot code to relocate, and a table of top-level
code blocks to run. Their offsets go into the exported slots `ARM64_RELOCS`, `ARM64_MAINS` and
`ARM64_IMAGE_SIZE`. Only `AAT_ADD_U64` relocations are allowed, and no imports or heap
globals.

## JIT output

A JIT function's code is allocated in `Fs->code_heap` and its address stored in
`CHashFun.exe_addr`. Calls to a function that is already compiled are direct (`IC_CALL`).
Calls to one still marked `Cf_EXTERN` (declared but not compiled, or recursive) go through
its `exe_addr` field (`IC_CALL_INDIRECT2`), so they reach the code once it exists. Until then
`exe_addr` is `&UndefinedExtern`, which throws `'UndefExt'`. `COCCompile` also sets
`Fs->last_fun`, which `LastFun` and `RunFile` call.

## Calling convention

Arguments are pushed so that the first is at `[RBP+16]`, after the saved RBP and the return
address; asm reads them as `SF_ARG1[RBP]` and up. Results come back in RAX. A function with a
fixed argument count pops its own arguments with `RET1` (`Ff_RET1`); callers of varargs
functions pop them, including the hidden `argc`. Register variables are saved by the function
that uses them (`IC_ENTER` pushes `used_reg_mask`).

## Building the kernel and StartOS

Kernel and compiler are the only AOT modules. `MakeAll` in
[BootHDIns.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Anokos/Opt/Boot/BootHDIns.TC)
runs `Cmp("/Compiler/Compiler","Compiler",,':')` and `Cmp("/Kernel/Kernel","Kernel",,':')`,
writing `Compiler.BIN.Z`, `Kernel.BIN.Z` and their MAP files; the build tool's stage 1
(`tools/stage1/AnokosBuild.HC`) does the same. [Kernel.PRJ](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Kernel.PRJ)
starts with an `#exe {}` block that asks the kernel configuration questions (`KCfgNew`), and
includes the compiler headers with `OPTf_EXTERNS_TO_IMPORTS`, so kernel calls into the
compiler become imports.

At boot, `KMain` calls `Load("Compiler",LDF_SILENT)` and then `ExeFile("StartOS")`, so
[StartOS.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/StartOS.TC) and everything it
includes (`/Anokos/MakeAnokos`, `~/MakeHome`) is JIT-compiled by the Adam task.

The ARM64 kernel is built by the x86 system:
`Cmp("/Kernel/KernelArm64","B:/KernelArm64","B:/Kernel8")` with `OPTf_ARM64` set in
[KernelArm64.PRJ](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KernelArm64.PRJ)
(`tools/stage3/Arm64Build.TC`). The compiler doesn't run on ARM64 yet. See [building](build.md).
