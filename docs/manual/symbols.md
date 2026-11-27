# Symbols, hash tables and loading

Anokos has no linker and no separate symbol files at run time. Every name the system knows,
from a kernel function to a `#define` typed at the command line, is an entry in a hash table,
and every task has one, chained to its parent's. The compiler looks names up there, the loader
fills it from `.BIN` modules, and the debugger, help system and editor links read it back.

## CHash and CHashTable

The base classes are in [KernelA.TH](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KernelA.TH#L641):

```c
public class CHash
{
  CHash *next;     //next entry in the same bucket
  U8    *str;      //the name
  U32   type,      //HTT_ type bits and HTF_ flags
        use_cnt;   //incremented on every successful find
};

public class CHashTable
{
  CHashTable *next;  //table searched next (the parent task's)
  I64   mask,locked_flags;
  CHash **body;      //mask+1 buckets
};
```

`HashTableNew(size)` in [KHashB.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KHashB.TC#L50)
allocates `size` buckets, which must be a power of two, and sets `mask=size-1`. A task's table
has `TASK_HASH_TABLE_SIZE` (1024) buckets.

`CHashSrcSym` adds what a symbol from source needs: `src_link` (a DolDoc link such as
`FL:/Kernel/KTask.TC,314`), `idx` (its help index), `dbg_info` (line-to-address table),
`import_name` and `ie_lst` (pending import/export patches during AOT). Most entry classes
derive from it.

## Entry types

The low bits of `type` say what an entry is (one bit per type, so a lookup mask can ask for
several at once). The comment next to each define names the class:

| Type | Class | What it is |
| --- | --- | --- |
| `HTT_EXPORT_SYS_SYM` | `CHashExport` | a named address or value (`val`): asm labels, module exports |
| `HTT_IMPORT_SYS_SYM` | `CHashImport` | a placeholder for an unresolved module import |
| `HTT_DEFINE_STR` | `CHashDefineStr` | a `#define` (`data` is the text) or a define list |
| `HTT_GLBL_VAR` | `CHashGlblVar` | a global variable: `var_class`, `data_addr`, `dim` |
| `HTT_CLASS` | `CHashClass` | a class or union: `size`, `member_lst_and_root`, `base_class` |
| `HTT_INTERNAL_TYPE` | `CHashClass` | a built-in type such as `I64i` or `U8` |
| `HTT_FUN` | `CHashFun` | a function: `exe_addr`, `return_class`, `arg_cnt`, `flags` |
| `HTT_KEYWORD`, `HTT_ASM_KEYWORD` | `CHashGeneric` | language and assembler keywords |
| `HTT_OPCODE`, `HTT_REG` | `CHashOpcode`, `CHashReg` | x86 instructions and registers |
| `HTT_MODULE` | `CHashGeneric` | a loaded module; `user_data0` is its `CBinFile` |
| `HTT_HELP_FILE` | `CHashSrcSym` | a file named by `#help_file` |
| `HTT_FILE`, `HTT_FRAME_PTR` | `CHashGeneric` | file data, named values (`FramePtrAdd`) |
| `HTT_WORD`, `HTT_DICT_WORD` | `CHashAC`, `CHashGeneric` | autocomplete tables only |

`CHashFun` derives from `CHashClass`: a function is a class whose members are its arguments
and locals. A function pointer type is also a `CHashFun`.

The high byte holds flags. `HTF_PUBLIC` marks `public` symbols, `HTF_PRIVATE` symbols compiled
with `OPTf_KEEP_PRIVATE`, `HTF_EXPORT` and `HTF_IMPORT` symbols that an AOT module exports or
imports, `HTF_IMM` an export whose value is absolute rather than module-relative, and
`HTF_UNRESOLVED`, `HTF_RESOLVE`, `HTF_GOTO_LABEL` and `HTF_LOCAL` are used by the assembler
while it resolves labels. `HashVal` in KHashB.TC returns the "obvious" value of any entry:
`exe_addr` for a function, `data_addr` for a global, `val` for an export.

## Hashing and lookup

The functions are asm in [KHashA.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KHashA.TC)
with TerryC wrappers bound by `_extern`:

- `HashStr(st)` (`SYS_HASH_STR`) shifts the hash left by one, adds the carry and the next byte,
  for every byte, then folds the upper bits in. The result is masked to pick a bucket.
- `HashSingleTableFind(name,table,mask,instance=1)` walks one bucket and returns the first
  entry whose `type` shares a bit with `mask` and whose name matches. It increments `use_cnt`.
- `HashFind(name,table,mask,instance=1)` does the same, then follows `table->next` until it
  finds a match or runs out of tables. `instance` counts matches across the whole chain, so
  `instance=2` returns the entry the first one shadows.
- `HashAdd(entry,table)` puts the entry at the head of its bucket, with interrupts off. A newer
  entry with the same name therefore shadows the older one; nothing is replaced.
- `HashRemDel(entry,table,instance)` unlinks that exact entry and frees it with `HashDel`.
- `HashBucketFind` returns the bucket, `HashAddAfter` inserts after a given entry.

Common masks are `HTG_SRC_SYM` (symbols that come from source), `HTG_ALL`, and
`HTG_ALL-HTT_IMPORT_SYS_SYM`, which the loader uses so placeholders don't satisfy lookups.

## One table per task

`TaskInit` gives every task a table, and `Spawn` in
[KTask.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KTask.TC) chains it
to the parent's: `task->hash_table->next=parent->hash_table`. The default parent is the CPU's
Seth task; the chain always ends at the Adam task. When the compiler loads, `AsmHashLoad`
hangs its own table of keywords, registers, opcodes and internal types below Adam's:

```
user task table --> parent table --> ... --> Adam table --> cmp.asm_hash
 (its own funs,                         (kernel, compiler,     (keywords, regs,
  globals, defines)                      Anokos layer)          opcodes, I64i ...)
```

So a symbol is found in the current task first, then in each ancestor, and a task can define
its own version of any name without affecting others. Symbols work like environment variables
in other systems: children inherit them, siblings don't see each other's. To make something
visible to every task, compile it in Adam, which is what `StartOS.TC` and `MakeHome.TC` do, or
send it there with `Adam("...")` ([Job.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Job.TC)).

`Who` in [AHash.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Anokos/AHash.TC) lists the
symbols of a table and its parents (`"+p"` for public only, `"-r"` for just the local table).

## How the compiler resolves names

`Lex` looks each identifier up as it reads it: first the current function's locals, then
`HashFind` in `cc->htc.hash_table_lst` with a mask that leaves out import placeholders (see
[the compiler](compiler.md#lexer-and-preprocessor)). Where that chain starts depends on the mode:

- **JIT.** `CmpCtrlNew` points every table of the compile at `Fs->hash_table`. New functions,
  globals, classes and defines go into the compiling task's table; lookups see the whole chain.
- **AOT.** `CmpJoin` makes a fresh global table for the module and chains it straight to
  `cmp.asm_hash`. The module sees keywords and types, and nothing else that isn't in its own
  source. `#exe {}` blocks inside it still run against `Fs->hash_table`.

Duplicates are allowed for most kinds of symbol, so you can `#include` the same file again
while working on it and each new definition shadows the last. In JIT mode `PrsFunJoin` and
`PrsClass` check only the current table (`HashSingleTableFind`), and only to find an earlier
`extern` declaration to complete.

## How a JIT function becomes visible

`PrsFunJoin` in [PrsStmt.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Compiler/PrsStmt.TC)
adds the `CHashFun` to the table before the body is parsed, so the function can call itself.
While it is being compiled it has `Cf_EXTERN` set and `exe_addr=&UndefinedExtern`. `PrsFun` then
compiles the body into the code heap, stores the address in `exe_addr`, clears `Cf_EXTERN`, and
calls `SysSymImportsResolve(name)` to patch any loaded module that was waiting for that name.

Calls compiled while the target is still `Cf_EXTERN` go indirectly through `&exe_addr`, so they
work once the code exists. Calls to a finished function are direct `CALL`s to its address.
That has a consequence: if you redefine a function, the new entry shadows the old one for code
compiled afterwards, but code compiled earlier keeps calling the old version.

A JIT `extern` of a name that is an exported kernel symbol (`HTT_EXPORT_SYS_SYM`) becomes a
binding: `PrsGlblVarLst` builds a `CHashFun` or `CHashGlblVar` at the export's address.

## The kernel's symbols

The kernel is compiled AOT, and in an AOT module every function and global gets `HTF_EXPORT`
(`PrsFun`, `PrsGlblVarLst`), so its patch table carries an export for each. At boot, `KMain`
calls `LoadKernel` in [KLoad.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KLoad.TC#L240):

```c
HashGenericAdd(KERNEL_MODULE_NAME,HTT_MODULE|HTF_PUBLIC,
      mem_boot_base-sizeof(CBinFile));
LoadPass1(sys_boot_patch_table_base,mem_boot_base,LDF_NO_ABSS|LDF_SILENT);
```

The absolute addresses were already patched by KStart32 during 32-bit start-up, hence
`LDF_NO_ABSS`, and the kernel's top-level code isn't run. Every kernel symbol is now an
`HTT_EXPORT_SYS_SYM` with just a name and an address in Adam's table. The kernel's references
to later layers are imports that stay pending, quietly (`LDF_SILENT`):
[KExts.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KExts.TC) imports
`DocSave`, `Ed` and others from the Anokos layer, and the compiler headers are included with
`OPTf_EXTERNS_TO_IMPORTS` for calls such as `ExeFile`.

`Load("Compiler")` adds the compiler's exports and resolves the kernel's pending compiler
imports. Then [StartOS.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/StartOS.TC)
includes KernelA.TH, CompilerA.TH, KernelB.TH, KernelC.TH and CompilerB.TH again in JIT mode.
That gives the JIT compiler the classes and `#define`s, which the export table doesn't carry,
and turns each `extern` into a typed `CHashFun` or `CHashGlblVar` bound to the export. The
next line, `HashTablePurge(adam_task->hash_table)`, deletes the export entries that the new
entries shadow, and the import placeholders that were already resolved.

## The loader

`Load(filename,ld_flags,bfh_addr)` reads a `.BIN.Z` file written by `Cmp` (format in
[the compiler](compiler.md#output-bin-files)):

1. Check `bin_signature` against `BIN_SIGNATURE_VAL` (`'TOSB'`), or throw `'BinModul'`.
2. Choose an address: `org` if the module asked for one, otherwise an aligned block in
   `Fs->code_heap` (or the file buffer itself when that is already suitable).
3. Add an `HTT_MODULE` entry named after the file, pointing at the header.
4. `LoadPass1`: walk the patch table. Exports become `HTT_EXPORT_SYS_SYM|HTF_IMM` entries
   (module base added for `IET_REL32_EXPORT`), and each new export calls
   `SysSymImportsResolve`. `IET_ABS_ADDR` adds the module base to each listed 32-bit field.
   `IET_DATA_HEAP` allocates the global and patches its references. Imports go to
   `LoadOneImport`.
5. `LoadPass2` (skipped with `LDF_JUST_LOAD`): call every `IET_MAIN` block, which runs the
   module's top-level code.

All entries go into `Fs->hash_table`, the table of the task that calls `Load`.

`LoadOneImport` finds the name with `HashFind(name,Fs->hash_table,HTG_ALL-HTT_IMPORT_SYS_SYM)`
and writes the address (`exe_addr` of a function, `data_addr` of a global, `val` otherwise)
into each field, relative or absolute, 8 to 64 bits. If the name isn't defined yet it prints
`Unresolved Reference` (unless `LDF_SILENT`) and adds a `CHashImport` that remembers the module
base and where in the patch table the entry is. Later, `SysSymImportsResolve(name)` finds those
placeholders in the current task's table, replays the patch, and sets their type to
`HTT_INVALID`. The loader, `PrsFun`, JIT global variables, `_extern` and JIT asm exports
(`CmpFixUpJITAsm`) all call it. Because it searches a single table, a pending kernel import is
only satisfied by a definition made in Adam.

## MAP files and debug information

`Cmp` with a map name calls `MapFileWrite` ([CHash.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Compiler/CHash.TC)),
which writes a DolDoc file with one link per symbol that has a `src_link` and isn't an import or
private. A function's link also carries its `CDbgInfo` as an attached binary:

```
$LK,"MAlloc",A="FL:::/Kernel/Mem/MAllocFree.TC.Z,391"$
$LK,"KbdLEDsSet",A="FL:::/Kernel/SerialDev/Keyboard.TC.Z,55",BI=3$
```

A `CDbgInfo` holds `min_line`, `max_line` and the code address of each line in between.
`MapFileLoad` in AHash.TC reads a MAP back: for each symbol in the task's table it sets the
`src_link` and, for functions that have none, attaches the line table, adding the module's load
address from its `HTT_MODULE` entry. `MakeHome.TC` does this for the kernel and the compiler, so
their functions get source links and line numbers even though the export table carries only
addresses. JIT code gets both directly from `HashSrcFileSet` and `OptPass789A`.

Going from an address back to a name, `FunSegFind` in
[FunSeg.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/FunSeg.TC) scans the
tables of the task chain (of every task in debug mode) for the closest function or export at or
below the address, within 0xFFFF bytes. `SrcLineNum` and `SrcEdLink` use the line table on top
of that. The `%p` and `%P` format codes of `Print` use it (`StrPrintFunSeg`) to show an address as a
symbol plus offset.

## Help index and DolDoc links

`HashSrcFileSet` (KHashB.TC) runs for each new source symbol. It sets `src_link` to
`FL:file,line`, sets `HTF_PRIVATE` under `OPTf_KEEP_PRIVATE`, and copies the current
`#help_index` string into `idx`. A help index is a `;`-separated list of `/` paths such as
`"Compiler/Internal;Hash/System"`. `#help_file "name"` adds an `HTT_HELP_FILE` entry.

DolDoc links use this table. In [DocLink.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Anokos/DolDoc/DocLink.TC):

- `$LK,"text",A="MN:SymName"$` (`LK_MAN_PAGE`) finds `SymName` with
  `HashFind(...,Fs->hash_table,HTG_SRC_SYM)` and follows its `src_link`, opening the definition.
  `Man("SymName")` does the same.
- `$LK,"text",A="HI:Topic"$` (`LK_HELP_INDEX`) calls `DocHelpIdx`, which runs
  `Who("+p",,"Topic",doc)`: every public symbol whose `idx` contains the topic, plus the help
  files filed under it.

That is the main job of `public`: a symbol without it still links and runs, but doesn't appear
in the help index.

## On ARM64

- The ARM64 image keeps no symbol table yet. `A64ImageWrite` in
  [Arm64Lib.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Compiler/Arm64Lib.TC#L483)
  reads the export entries only to find the slots `ARM64_RELOCS`, `ARM64_MAINS` and
  `ARM64_IMAGE_SIZE`, and `KMainArm64B` in `Arm64/KMain.TC` doesn't call `LoadKernel`. Adam's table
  holds only what is added at run time, such as the define lists from `SysDefinesLoad`.
- There is no loader. A call to an import is compiled as a call to `ArmImportTrap`
  ([Arm64/Fault.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Arm64/Fault.TC)),
  which prints the caller's image offset on the serial port and halts. An import's address is
  compiled as 0. Any import entry left in the module makes `A64ImageWrite` fail.
- The hash functions are TerryC in
  [Arm64/KHashA.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Arm64/KHashA.TC).
  `ArmHashStr` gives the same values as `SYS_HASH_STR`, and `_HASH_FIND` and the other labels
  branch to the TerryC versions, so `HashFind` and friends behave the same.
- The compiler doesn't run on ARM64 yet, so nothing is JIT-compiled there and no symbols
  are added by compiling. See [ARM64](arm64.md).
