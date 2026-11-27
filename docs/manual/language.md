# The TerryC language

TerryC is the renamed HolyC of TempleOS: a C-like language that the Anokos compiler turns
straight into machine code, either ahead of time into a module or just in time at the
command line. This page lists what a C programmer has to unlearn, with each point checked
against the compiler in [src/Compiler](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Compiler).

## Types

The built-in types are registered by `AsmHashLoad` from the `internal_types_table` in
[CInit.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Compiler/CInit.TC).

| TerryC | C equivalent | Size |
| --- | --- | --- |
| `U0` | `void`, but with size zero | 0 |
| `I8`, `U8` | `signed char`, `unsigned char` | 1 |
| `Bool` | a signed byte (`RT_I8`) | 1 |
| `I16`, `U16` | `short`, `unsigned short` | 2 |
| `I32`, `U32` | `int`, `unsigned int` | 4 |
| `I64`, `U64` | 64-bit `long` | 8 |
| `F64` | `double` | 8 |

There is no 32-bit float (`RT_F32` is marked "Not implemented" in
[KernelA.TH](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KernelA.TH)).
Use `U8 *` for byte pointers; the parser warns `use "U8 *" instead of "U0 *"`.

All values are widened to 64 bits when they are loaded, and intermediate results are
64-bit. Pointers are `RT_PTR`, which is the same raw type as `I64`: signed, so a function can
return a negative error code where a pointer is expected.

There is no `typedef`, `struct`, `enum` or `continue`. The keyword list is in
[OpCodes.DD](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Compiler/OpCodes.DD)
(the `KEYWORD` lines). Use `class` for types and `goto` instead of `continue`.

## Programs without main

There is no `main()`. Code outside functions runs as soon as it is compiled, in order. In
JIT mode, `ExeCmdLine` ([KTask.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KTask.TC#L314))
compiles one statement with `LexStmt2Bin` and calls it before reading the next one. A file
is a script that defines functions and then calls them.

In an AOT module each top-level statement becomes an `IET_MAIN` entry, and the loader runs
them in order when the module is loaded (see [the compiler](compiler.md#output-bin-files)).

## Print and PutChars shortcuts

A statement that starts with a string literal is a call to `Print`. One that starts with a
character constant is a call to `PutChars`. `PrsFunCall` in
[PrsExp.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Compiler/PrsExp.TC)
looks those two functions up by name, so they must already be declared.

```c
U0 Hello(U8 *fmt,U8 *name,I64 age)
{
  "Hello World!\n";         //Print("Hello World!\n");
  "%s is %d\n",name,age;    //Print with args, no parentheses
  "" fmt,name,age;          //empty string: the format is a variable
  '*';                      //PutChars('*');
}
```

A character constant can hold up to eight characters (`'ABC'` is `0x434241`), and
`PutChars` prints all of them.

## Calling functions

A function whose arguments all have defaults can be called without parentheses: `Dir;`
is the same as `Dir();`. Default arguments don't have to be at the end, and you skip one
by leaving its slot empty:

```c
U0 Test(I64 i=4,I64 j,I64 k=5) { Print("%d %d %d\n",i,j,k); }
Test(,3);   //prints 4 3 5
```

Default values are evaluated once, at compile time (`PrsVarLst` compiles the expression and
`Call`s it), so they must be constants or something the compiler can run then. String
defaults work. The keyword `lastclass` as a default passes the class name of the previous
argument as a string.

A bare function name is a call. To get the function's address, for a callback, write
`&Fun`.

Varargs functions (`...`) get two hidden locals, `I64 argc` and `I64 argv[]`, made by
`PrsDotDotDot` in [PrsVar.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Compiler/PrsVar.TC):

```c
I64 AddNums(...)
{
  I64 i,res=0;
  for (i=0;i<argc;i++)
    res+=argv[i];
  return res;
}
```

`StrPrintJoin(NULL,fmt,argc,argv)` is how a varargs function forwards its arguments to the
`Print` formatter.

## Casts are postfix

A cast comes after the value: `ptr(U8 *)`, `x(I64)`. Writing a C-style prefix cast gives
the error `Use TempleOS postfix typecasting`. A cast only changes the type the compiler
assumes for the bits; it doesn't convert. That is how KernelA.TH defines `pi` as
`0x400921FB54442D18(F64)`. To convert between integers and floats use `ToI64`, `ToF64`
and `ToBool`, or assign to a variable of the other type, which converts as in C.

## Operator precedence

TerryC's precedence is not C's. The table is `cmp.binary_ops`, filled in by `CmpFillTables`
in [CInit.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Compiler/CInit.TC#L287),
with levels from `PREC_EXP` to `PREC_ASSIGN` in
[CompilerA.TH](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Compiler/CompilerA.TH).
From tightest to loosest (unary operators bind tighter than all of these):

1. `PREC_EXP`: the power operator (a backtick, right to left), `<<`, `>>`
2. `PREC_MUL`: `*`, `/`, `%`
3. `PREC_AND`: `&`
4. `PREC_XOR`: `^`
5. `PREC_OR`: `|`
6. `PREC_ADD`: `+`, `-`
7. `PREC_CMP`: `<`, `>`, `<=`, `>=`
8. `PREC_CMP2`: `==`, `!=`
9. `PREC_AND_AND`: `&&`
10. `PREC_XOR_XOR`: `^^` (logical xor)
11. `PREC_OR_OR`: `||`
12. `PREC_ASSIGN`: `=` and the compound assignments, right to left

So shifts bind tighter than `*`, and the bit operators bind tighter than `+` and `-`:

```c
a+b<<2      //a+(b<<2)
x&0xFF+1    //(x&0xFF)+1
a*b<<1      //a*(b<<1)
```

This is the most common source of bugs when porting C code. The ARM64 encoders in
[Arm64Lib.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Compiler/Arm64Lib.TC)
carry a note about it and parenthesize every mixed expression. Since many build files turn
on `OPTf_WARN_PAREN`, which warns about parentheses that aren't needed, that file switches the
warning off around itself.

There is no `?:` operator. Use `if`/`else`.

## Chained comparisons

`0<=x<10` means `0<=x && x<10`. When `PrsExpression2` reduces a comparison while the
next operator is also a comparison, it marks the first one `ICF_PUSH_CMP` and adds an
`IC_PUSH_CMP`, and the pair is joined with `&&`.

```c
if (13<=age<20)
  "Teen-ager\n";
```

The check is on the intermediate-code class `IST_CMP`, which also covers `==` and `!=`,
so `a<b==c` is chained too, not compared against a Bool as in C.

## Classes and unions

`class` replaces both `struct` and `typedef`. Members are packed: `PrsVarLst` gives each
member the class's current size as its offset, with no alignment padding. Kernel classes
add explicit `pad` members and check layouts with `#assert`, for example
`#assert !($$&7)` at the end of `CCmpCtrl`. Inside a class definition `$$` is the current
offset. Several members may be named `pad` or `reserved`.

Only one base class is allowed (`class CHashFun:CHashClass`). A forward declaration is
`extern class CTask;`, after which you can use pointers to it.

A union is used like a class, without the `union` keyword at the use site. A type written
in front of a union declaration is the type of the union when used by itself. That is how
the integer types get sub-integer access:

```c
I64i union I64     //I64i is the internal type
{
  I8i i8[8];  U8i u8[8];  I16 i16[4];  U16 u16[4];  I32 i32[2];  U32 u32[2];
};
I64 i=0x123456780000DEF0;
i.u16[1]=0x9ABC;   //write bytes 2-3 of i
```

So `x.u8[0]` is the low byte of any `I64` and `x.i32[1]` its signed upper half. There are no
bit fields; use this, or `Bt`/`Bts`/`Btr`. `ScopingLinkage.DD` notes that taking `&i` or
using `i.u8[2]` on a local forces it out of a register.

`sizeof(Class)` and `offset(Class.member)` take one level of member only. Class members can
carry meta data after their declaration (`PrsVarLst` reads identifier/value pairs), used by
`DocForm`.

## Linkage: public, extern, import

- `public` sets `HTF_PUBLIC` on the symbol. It doesn't change linkage. It decides what the
  help system and `Who("+p")` show (see [symbols](symbols.md#help-index-and-doldoc-links)).
- `extern` declares a function, global or class that is defined later, or, in JIT code,
  binds to an existing symbol of the same name. When the name is an exported kernel symbol
  (`HTT_EXPORT_SYS_SYM`), `PrsGlblVarLst` turns the `extern` into a binding to that address.
  You can only `extern` a name once.
- `_extern LABEL type name` binds a TerryC name to an asm label of a different name, for
  example `_extern _HASH_FIND CHash *HashFind(...)` in
  [KHashA.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KHashA.TC).
  For a function the label must start with `_` (by convention those labels follow the
  TerryC stack calling convention).
- `import` and `_import` are for AOT modules: the symbol is resolved by the loader
  ([symbols](symbols.md#the-loader)). In JIT code they are an error, `import not needed`.
- `_intern` binds a function name to an intermediate code, so the call is inlined: KernelB.TH
  has `_intern IC_TOUPPER I64 ToUpper(U8 ch);`.

`Option(OPTf_EXTERNS_TO_IMPORTS,ON)` makes `extern` act as `import` and `_extern` as
`_import`, so one header serves both a module being built and the modules that use it.
[Compiler.PRJ](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Compiler/Compiler.PRJ)
uses it around the kernel headers.

## Statements

- `switch` always builds one jump table (`PrsSwitch` in
  [PrsStmt.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Compiler/PrsStmt.TC#L580)),
  so the case range is limited to 0xFFFF values; don't use wide, sparse cases. `switch [i]`
  skips the range check. `case 4...7:` is a range. A bare `case:` is the previous case plus
  one, starting from 0. Duplicate cases are an error.
- `start:` and `end:` group cases into a sub-switch. The code after `start:` runs before
  any case of the group, and the code after `end:` runs when a case of the group breaks:

```c
switch (i) {
  case 0: "Zero ";  break;
  start:
    "[";
    case 1: "One";  break;
    case 3: "Three"; break;
  end:
    "] ";
    break;
}
```

- `try {} catch {}` with `throw(ch)`. The thrown value is an 8-byte character constant such
  as `'Compiler'`, read in the handler as `Fs->except_ch`. A handler sets
  `Fs->catch_except=TRUE` to stop the search, and `PutExcept` prints the exception. A function
  with `try` gets no register variables. Don't `goto` out of a `try`; `return` is handled. See
  [exceptions](exceptions.md).
- `lock {}` sets `ICF_LOCK` on the intermediate codes inside (`cc->lock_cnt` in `ICAdd`), and
  the x86 back end adds a `LOCK` prefix to their read-modify-write instructions.
- `no_warn a,b;` suppresses the unused-variable warning for those locals.
- `reg` or `noreg` before a local's name forces it into or out of a register; `reg R15 i`
  names the register.
- Labels for `goto` exist only inside functions, and must not have the same name as a global.

## Function flags

`interrupt`, `haserrcode`, `argpop`, `noargpop` and `public` go before a function.
`interrupt` makes `IC_LEAVE` in
[OptPass789A.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Compiler/OptPass789A.TC)
restore the clobbered registers and return with `IRETQ`; `haserrcode` also drops the CPU's
error code first. Normally a function with a fixed argument count pops its own arguments
(`Ff_RET1`, a `RET imm16`); `noargpop` turns that off and `argpop` asks for it. `interrupt`
implies `noargpop`.

## Inline assembly

`asm {}` blocks hold assembler code. At file level they define labels: `LABEL::` is
exported, `LABEL:` is a global label of the module, and `@@10:` is a local label. Each `::`
label ends the scope of the `@@` labels before it (`AOTLocalsResolve` resolves them and starts
a new local table), so `@@` names can be reused between exported labels. Inside a function an `asm {}` block becomes an `IC_ASM` intermediate code, and
single instructions such as `CLI` or `PUSHFD` can be written directly as statements. Asm code
reaches TerryC globals and functions with `&`: `CALL &Fun`, `MOV RAX,I64 [&glbl_var]`. Local
stack variables are `&i[RBP]`. The opcodes are TempleOS's own forms (`IMUL2`, `RET1`, `SHL1`),
listed in OpCodes.DD.

On ARM64, with `OPTf_ARM64` on, the same block takes ARM64 syntax; see
[the compiler](compiler.md#the-assemblers) and [ARM64](arm64.md).

## The preprocessor

There is no separate preprocessor pass. `Lex` in
[Lex.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Compiler/Lex.TC#L441)
handles `#` directives as it reads tokens. Because the parser reads one token ahead, a
directive can take effect a token later than you expect; an extra `;` after it helps.

- `#include "file"` reads a file. There is no `<>` form. A name without an extension gets
  `.TC.Z`, or `.HC.Z` when only a TempleOS file exists (`SrcFileDft`).
- `#define NAME text` stores a string (`CHashDefineStr`). Each use of the name is replaced
  by the text. There are no function-like macros.
- `#if expr`, `#ifdef NAME`, `#ifndef NAME`, `#else`, `#endif`. `#ifdef` is true if the
  name is any symbol in scope, not only a `#define`. `defined(NAME)` works in expressions.
- `#ifaot` and `#ifjit` test whether the code is being compiled ahead of time.
- `#assert expr` prints a warning at compile time when the expression is false.
- `#exe {}` compiles and runs the code inside at compile time, in the compiling task.
  `StreamPrint` inside it inserts text into the source after the block. KMain.TC uses this to
  put the boot drive settings from the kernel configuration into the kernel.
- `#help_index "Topic"` and `#help_file "file"` feed the help system.

`__FILE__`, `__LINE__`, `__DATE__`, `__TIME__`, `__DIR__` and `__CMD_LINE__` are ordinary
`#define`s in KernelA.TH whose text is an `#exe {StreamPrint(...);}`.

## DolDoc in source files

Source files are DolDoc documents, the format of the editor and help system. `$` starts a
DolDoc command, so in source text `$$` stands for one dollar sign, and in a string literal
`"$$"` gives one `$` (`LexInStr`). The escape `\d` also gives `$`. In code, a `$$` token is the
current address in asm, the current offset in a class definition, and otherwise the
address of the current instruction (`IC_RIP`).

## Options

`Option(OPTf_x,ON)` changes a flag of the current compile (`Fs->last_cc->opts`, in
[CMisc.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Compiler/CMisc.TC)). In JIT
code a plain statement works, because it runs as soon as it is compiled (StartOS.TC does
this). In an AOT build, put it in `#exe {Option(...);}`. The flags are in KernelA.TH:

| Flag | Effect |
| --- | --- |
| `OPTf_ECHO` | print source as it is compiled |
| `OPTf_TRACE` | unassemble the generated code |
| `OPTf_WARN_UNUSED_VAR` | warn about unused locals (on by default) |
| `OPTf_WARN_PAREN` | warn about parentheses that aren't needed |
| `OPTf_WARN_DUP_TYPES` | warn about repeated local type statements |
| `OPTf_WARN_HEADER_MISMATCH` | warn when a definition differs from its header (on by default) |
| `OPTf_EXTERNS_TO_IMPORTS` | treat `extern` as `import` |
| `OPTf_KEEP_PRIVATE` | mark new symbols `HTF_PRIVATE`, kept out of MAP files and `Who` |
| `OPTf_NO_REG_VAR` | no register variables |
| `OPTf_GLBLS_ON_DATA_HEAP` | put globals on the data heap; AOT ones then can't be initialized |
| `OPTf_NO_BUILTIN_CONST` | disable the 80-bit float constants |
| `OPTf_ARM64` | AOT compile function bodies to ARM64 |

The two defaults are set by `CmpCtrlNew` in Lex.TC.

## JIT and AOT

Almost everything is compiled just in time: the kernel boots, loads the compiler, and the
compiler then compiles `StartOS.TC` and everything it includes into memory. Only the kernel
and the compiler itself are compiled ahead of time into `.BIN` files with `Cmp`. Both use
the same front end; the differences are where code and symbols go. The details are in
[the compiler](compiler.md) and [symbols](symbols.md).

## Pitfalls

- Global arrays need a size. `PrsArrayDims` gives `[]` a count of 0, so the code paths in
  PrsVar.TC that size an array from its initializer (they test for a count below 0) are
  never reached.
- C's array-of-function-pointers declarator `U0 (*fp[4])()` doesn't parse: `PrsType` wants
  `)` right after the name. Use a pointer to function pointers, as KernelB.TH does with
  `I64 (**ext)(...)`.
- The stack doesn't grow. Allocate large local data on the heap.
- `MemCpy` only copies forward.
- Since `Print` and `PutChars` are found by name, a file compiled without the kernel headers
  gets `Missing header for Print() and PutChars()` on its first string statement.
