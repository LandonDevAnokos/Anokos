# Memory

Anokos has one address space, identity-mapped, shared by every task on every core, all in ring 0.
Physical RAM is split into two block pools (code and data). Each task gets heaps carved from
those pools, and `MAlloc` hands out pieces of a heap. When a task dies, its heaps go back to the
pools in one sweep.

## One address space

There are no per-process page tables, no user/kernel split and no swapping. Virtual address X is
physical address X. Long mode cannot run with paging off, so the kernel builds page tables once at
boot that map everything to itself, and never changes them except for device mappings. All
tasks run in ring 0 (the GDT in `KStart16.TC` has ring-3 segments only so the demos can try
them). A pointer is valid in every task, so tasks can share data just by passing pointers.

Code lives low. `src/Doc/MemOverview.DD` explains that the compiler uses 32-bit relative `CALL`
and `JMP`, so all code must sit within 2 GiB. So the code heap is the bottom of RAM, at most
2 GiB, and some structures used through 32-bit addresses come from it: `Spawn` allocates each
`CTask` there "so addr will be short", and `Core0Init` puts `cpu_structs` there for the other
cores' start-up code.

## Finding the RAM

In real mode `KStart16.TC` asks the BIOS for two memory maps (see [Booting](boot.md#kstart16-real-mode)):

- `MEM_E801`: two `U16`s, KiB between 1 MiB and 16 MiB, and 64 KiB blocks above 16 MiB.
- `MEM_E820`: up to `MEM_E820_ENTRIES_NUM` (48) `CMemE820` entries `{base,len,type}`. Type 1 is
  usable RAM. An entry with type 0 ends the table.

`BIOSTotalMem` in
[Mem/MemPhysical.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Mem/MemPhysical.TC)
returns the larger of the E801 total and the sum of all type 1 lengths. `MEM_PHYSICAL_SPACE`
(`mem_physical_space`) is the highest end address of any E820 entry, of any type.

## The identity map

`SYS_INIT_PAGE_TABLES` in
[Mem/PageTables.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Mem/PageTables.TC)
runs in 32-bit mode from `KStart32.TC`, before long mode is switched on:

1. Sets `MEM_PAGE_SIZE` to 2 MiB. The code to use 1 GiB pages when the CPU has them is there but
   commented out, so x86 always uses 2 MiB pages.
2. Sets `MEM_MAPPED_SPACE` to `MEM_PHYSICAL_SPACE` plus 4 GiB. The extra 4 GiB at the top is
   for the uncached alias described below.
3. Counts the 2 MiB, 1 GiB and 512 GiB entries needed (`MEM_2MEG_NUM`, `MEM_1GIG_NUM`,
   `MEM_512GIG_NUM`).
4. Zeroes the `CSysFixedArea` at `SYS_FIXED_AREA` (`0x100000`) and places the tables right after
   it: `MEM_PML2`, then `MEM_PML3`, then `MEM_PML4`. `MEM_HEAP_BASE` is the first byte after the
   tables.
5. Fills the PML2 with 2 MiB entries `0x87` (present, writable, user, page size), each mapping
   its own address. The PML3 and PML4 point to the next level.

`KStart64.TC` then loads `MEM_PML4` into `CR3`. `MemPageTable(a)` returns a pointer to the entry
that maps address `a`, which is how the kernel edits the map later.

The low part of the resulting map, on x86:

```
0x00007C00   Kernel.BIN.C (BOOT_RAM_BASE), mem_boot_base just after its header
0x00097000   MP_VECT_ADDR: start-up code for the other cores
0x000A0000   VGA memory (reach it through text.vga_alias)
0x00100000   CSysFixedArea: boot CPU, Adam's CTask and CHeapCtrl, sys_code_bp
             then PML2, PML3, PML4
MEM_HEAP_BASE  code heap ... up to mem_heap_limit (at most 2 GiB)
               data heap: the rest of RAM (E820 type 1 ranges)
mem_physical_space  uncached alias of the low 4 GiB (dev.uncached_alias)
mem_mapped_space    end of the map
```

### The uncached alias

All normal entries are cacheable. Hardware registers must not be cached, so `UncachedAliasAlloc`
maps a second copy of the lowest 4 GiB at the top of the mapped space. It calls `Mem64DevAlloc`
for four 1 GiB pieces, then writes each 2 MiB entry as `0x197` plus the offset (present,
writable, user, page size, global, and cache-disable). The result is `dev.uncached_alias`. Code reaches
devices at `dev.uncached_alias+address`: the local APIC (`LAPIC_EOI`, `LAPIC_ICR_LOW`), the HPET
(`HPET_GCAP_ID`), and VGA memory (`text.vga_alias`).

`SysGrInit` makes one exception for the VBE framebuffer. If the framebuffer lies inside the
normal identity map, it uses that cacheable address, because the firmware's MTRRs already make
video memory uncached or write-combining. Only a framebuffer above the identity map goes through
the alias.

## Block pools

A block pool, `CBlkPool`, is a source of raw memory. It counts in units of `MEM_PAG_SIZE`,
which is **512 bytes** (`MEM_PAG_BITS` is 9). These "pags" are allocation units, not hardware
pages. Every run of pags starts with a `CMemBlk` header:

```c
class CMemBlk
{
  CMemBlk *next,*last;
  U32 mb_signature,  //MBS_USED_SIGNATURE_VAL or MBS_UNUSED_SIGNATURE_VAL
      pags;
};
```

There are two pools, both reached through globals bound to asm labels in `KStart32.TC`:

- `sys_code_bp`: the low RAM. Its `CBlkPool` lives in the fixed area.
- `sys_data_bp`: the rest. `BlkPoolInit` puts its `CBlkPool` at the start of the first data
  range. On machines with 2 GiB of RAM or less it is `NULL`, and the code pool is used for both.

### Filling the pools

`SYS_INIT_16MEG_SYS_CODE_BP` (in `PageTables.TC`) gives `sys_code_bp` the memory from
`MEM_HEAP_BASE` to the E801 limit below 16 MiB, and sets `RLf_16MEG_SYS_CODE_BP`. That is enough
for Adam's heap and the early boot.

`BlkPoolsInit` in
[Mem/BlkPool.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Mem/BlkPool.TC)
runs first thing in `KMain`. It picks a code heap limit from `BIOSTotalMem`:

| Total RAM | Code heap limit |
| --- | --- |
| up to 2 GiB | all of it |
| 2 to 4 GiB | a quarter of it |
| over 4 GiB | 2 GiB |

It adds 16 MiB up to that limit to `sys_code_bp` and sets `mem_heap_limit`. Then each E820 type 1
range above the limit goes to the data pool. The first range becomes the pool itself
(`BlkPoolInit`), and Adam's `data_heap` is switched to a new `CHeapCtrl` on it. Later ranges are
added with `BlkPoolAdd`. Finally it sets `RLf_FULL_HEAPS`. The code pool part assumes RAM is
contiguous from 16 MiB to the limit.

### Allocating pags

`MemPagAlloc(pags,bp)` in
[Mem/MemPag.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Mem/MemPag.TC)
looks in three places:

1. `free_pag_hash[pags]`: freed runs of exactly that size, for requests under
   `MEM_FREE_PAG_HASH_SIZE` (256 pags, 128 KiB).
2. For bigger requests, the size is rounded up to a power of two plus `MEM_EXTRA_HASH2_PAGS` (2),
   and `free_pag_hash2[i]` holds freed runs of that size. The extra two pags mean that a request
   for an exact power of two, plus a header, does not jump to the next power of two.
3. `mem_free_lst`: memory that was never handed out. It takes the first block big enough and cuts
   the run from its top end.

If all three fail it takes any bigger run from `free_pag_hash2` and returns the whole thing. If
there is none, it returns `NULL`.

`MemPagFree` pushes the run onto the matching hash list. Freed runs are never merged with their
neighbours or put back on `mem_free_lst`, so the pools can fragment over a long session. Both
functions take the pool's lock with interrupts off (`PUSHFD`, `CLI`, spin on
`LBts(&bp->locked_flags,BPlf_LOCKED)`), which protects against both interrupts on this core and
the other cores.

## Heaps

A heap, `CHeapCtrl`, sits on top of a block pool:

```c
public class CHeapCtrl
{
  CBlkPool *bp;
  U32 hc_signature,pad;   //HEAP_CTRL_SIGNATURE_VAL
  I64 locked_flags,alloced_u8s,used_u8s;
  CTask *mem_task;
  CMemBlk *next_mem_blk,*last_mem_blk; //every run this heap got from bp
  CMemBlk *last_mergable;
  CMemUnused *malloc_free_lst;         //big free pieces
  CMemUsed *next_um,*last_um;          //used chunks, heap debug builds only
  CMemUnused *heap_hash[MEM_HEAP_HASH_SIZE/sizeof(U8 *)]; //small free chunks by size
};
```

Each task has two heaps, `code_heap` and `data_heap`. `Spawn` in `KTask.TC` creates them with
`HeapCtrlInit(,task,sys_code_bp)` and `HeapCtrlInit(,task,sys_data_bp)`. Without a data pool,
`data_heap` is the same as `code_heap`. `MAlloc` uses the data heap unless you pass the code heap
yourself. The loader does that for compiled code (`KLoad.TC`), and the JIT compiler and
`IntFaultHndlrsNew` do it for generated machine code.

Adam's heap is special because it exists before any TerryC code runs. `KStart64.TC` fills in the
`adam_hc` in `CSysFixedArea` by hand, the same way `HeapCtrlInit` would, and points both of
Adam's heaps at it. `BlkPoolsInit` later gives Adam a separate data heap. Adam never dies, so
Adam's heap is the closest thing Anokos has to kernel memory. Use `AMAlloc`, `ACAlloc`,
`AStrNew` and `AMAllocIdent` for anything that must outlive the current task.

You can make your own heap with `HeapCtrlInit(NULL,task,bp)` and pass the `CHeapCtrl` to
`MAlloc` in place of a task. `HeapLog` does this so its records don't disturb the heap it
watches. `HeapCtrlDel` frees a heap and all its memory.

## MAlloc and Free

| Function | What it does |
| --- | --- |
| `MAlloc(size,task_or_hc=NULL)` | Allocate. `NULL` means the current task. Throws `'OutMem'` |
| `CAlloc(size,task_or_hc=NULL)` | `MAlloc` and zero |
| `MAllocIdent(src,..)`, `StrNew(str,..)` | Copy a heap object or a string |
| `MAllocAligned(size,align,..,misalign)`, `CAllocAligned` | Power-of-two alignment |
| `AMAlloc`, `ACAlloc`, `AStrNew`, `AMAllocIdent` | Same, in Adam's heap |
| `Free(ptr)` | Free. `NULL` is allowed |
| `MSize(ptr)` | Usable size of the object (can be more than you asked for) |
| `MSize2(ptr)` | Internal size, header included |
| `MHeapCtrl(ptr)` | The `CHeapCtrl` the object came from |

`MAlloc`, `Free`, `MSize`, `MSize2` and `MHeapCtrl` are written in assembly in
[Mem/MAllocFree.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Mem/MAllocFree.TC)
(`_MALLOC`, `_FREE` and so on) and declared with `_extern` at the bottom of the file. The rest
are TerryC wrappers. `MAlloc` tells a `CTask` from a `CHeapCtrl` by the signature at the same
offset (`#assert CTask.task_signature==CHeapCtrl.hc_signature`).

### Chunk layout

Every chunk starts with a `CMemUsed` header, 16 bytes in normal builds (the heap-debug fields
are `U0`, size zero):

```
            +-----------+-----------+------------------------
            | hc        | size      | your data ...
            +-----------+-----------+------------------------
            ^ CMemUsed              ^ CMemUsed.start = pointer returned
```

`size` includes the header and is a multiple of 8. A free chunk reuses the same bytes as a
`CMemUnused` `{next,size}`, so freeing needs no extra memory.

### The algorithm

`_MALLOC` adds the header, rounds up to 8 bytes, locks the heap (interrupts off, then spin on
`HClf_LOCKED`) and then:

1. **Small, with a free chunk of that size.** If the size is under `MEM_HEAP_HASH_SIZE`
   (1024 bytes), it pops the list `heap_hash[size/8]`. There are 128 lists, one per 8-byte size.
2. **Big.** If the size is 1024 bytes or more, it gets a whole run of pags with
   `MemPagTaskAlloc` and returns the memory after the `CMemBlk` header. Requests of about
   1 KiB and up always cost whole 512-byte pags.
3. **Small, nothing free of that size.** It walks `malloc_free_lst`, a list of larger free
   pieces, and takes the first piece that is big enough. It cuts the chunk from the top of the
   piece and leaves the rest on the list, unless the rest would be too small to hold a
   `CMemUnused`. If no piece fits, it asks `MemPagTaskAlloc` for at least 16 pags (8 KiB) and
   adds that as a new piece. If the new run sits right below the previous one
   (`last_mergable`), the two runs are merged.

`_FREE` reads the header, checks the heap's signature, and either pushes the chunk onto
`heap_hash[size/8]` (small) or returns its pags with `MemPagTaskFree` (big). Small chunks are
never merged again. To keep `malloc_free_lst` short (its comment says a long list once caused
crashes), `MemPagTaskAlloc` moves small leftovers from it into `heap_hash` as the heap grows.

### Aligned allocations

`MAllocAligned` over-allocates and returns an aligned address inside the block. In the 8 bytes
before it, where `CMemUsed.size` would be, it stores the negative distance back to the real
start. `Free`, `MSize`, `MSize2` and `MHeapCtrl` step back when they see a negative size, so
plain `Free` works.

## Memory owned by tasks

`MemPagTaskAlloc` links every run a heap gets from its pool onto that heap's
`next_mem_blk` list. When a task exits, `TaskEnd` puts it on its CPU's dying queue.
`TaskKillDying` calls `TaskDel` once `DYING_JIFFIES` (a fifth of a second) has passed, and
`TaskDel` runs `HeapCtrlDel` on both heaps. `HeapCtrlDel` walks the run list and gives every
run back to the pool. Everything the task allocated is gone at once, its stack included
(`TaskStkNew` allocates it from the task's own heap).

This has one big consequence: **memory you allocate belongs to your task, wherever the pointer
goes.** If you build something for another task, or for the system, allocate it in that task's
heap (`MAlloc(size,other_task)`) or in Adam's (`AMAlloc`). `TaskMemAlloced` and `TaskMemUsed` in
`Anokos/AMem.TC` report a task's totals. See [Tasks](tasks.md).

## Device memory

`dev.mem64_ptr` starts at `mem_mapped_space` (set in `SysGlblsInit`). `Mem64DevAlloc(&n)` moves
it down by `n` GiB, marks those entries uncached and present, and returns it. `Mem64DevFree` only
gives back the most recent allocation. The uncached alias is the main user.

`Mem32DevInit` builds `dev.mem32_head`, a list of `CMemRange`s for the 32-bit range
`0xE0000000` to `0xEFFFFFFF` with the E820 entries that fall in it. `Mem32DevAlloc` would hand out
pieces of the free parts, but its own comment says it doesn't work and is not used.

## Debugging the heap

- **Signatures.** `MAlloc` checks `hc_signature` and calls `SysBadMAlloc` on a bad heap. `Free`
  checks it too and calls `SysBadFree`. `MemPagTaskFree` checks `MBS_USED_SIGNATURE_VAL`. All of
  these `Panic`. A double free is not reliably caught: freeing a small chunk overwrites its `hc`
  field with the free-list link, so a second `Free` checks whatever that link points at.
- **Fill values.** The kernel options `MemInit` and `HeapInit` (`KCfg.TC`) fill new pool memory
  or every `MAlloc` chunk with a chosen byte, to catch code that expects zeroed memory.
- **Integrity checks.** `HeapWalk(task)` and `HeapCtrlWalk(hc)` in
  [Anokos/AMem.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Anokos/AMem.TC) check
  that every free chunk lies inside one of the heap's runs. `IsInHeap` and `IsInHeapCtrl` test one
  address. `HeapRep(task)` prints the free lists of another task.
- **Logging.** `HeapLog(ON,task)` in `Anokos/Opt/Utils/HeapLog.TC` installs hooks in
  `ext[EXT_HEAPLOG_MALLOC]` and `ext[EXT_HEAPLOG_FREE]` that `MAlloc` and `Free` call while
  `SEMA_HEAPLOG_ACTIVE` is set. `HeapLogAddrRep` and `HeapLogSizeRep` report what is still
  allocated. This is the tool for finding leaks.
- **Callers.** `_CFG_HEAP_DBG` `TRUE` in `KernelA.TH` adds `caller1`, `caller2` and list links
  to `CMemUsed` and keeps a list of used chunks (`next_um`). Rebuild everything after changing it.
- `MemRep` (`Anokos/Opt/Utils/MemRep.TC`) prints memory use for the whole system.

## On ARM64

The memory code is shared. The ARM64 versions of the x86-only parts are in `src/Kernel/Arm64`.

**Finding RAM.** `ArmMemInit` in
[Arm64/Mem.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Arm64/Mem.TC)
fills the same `MEM_E820` table. It takes RAM from the device tree's `/memory` nodes
(`ArmFdtMem`), or on a Raspberry Pi without them from the VideoCore mailbox (`ArmMboxMem`, tag
`0x00010005`), or failing both from `ArmMemDefault` (512 MiB at 1 GiB on QEMU virt, 960 MiB at 0
on a Pi). Since the format is the same, `BIOSTotalMem` and `Mem32DevInit` work unchanged.

**Pools.** The heaps start after the image and its reserved area (`arm_heap_start`).
`ArmMemInit` gives `sys_code_bp` the first `ARM_BOOT_HEAP_SIZE` (16 MiB), like x86's low 16 MiB,
and sets `RLf_16MEG_SYS_CODE_BP`. The ARM64 `BlkPoolsInit` in
[Arm64/BlkPool.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Arm64/BlkPool.TC)
adds the rest of the RAM range holding the image, up to 2 GiB past the heap start, to the code
pool. Every other RAM range, and the part of the image's range above the code pool, goes to the
data pool if it is at least 64 pags (32 KiB). x86 assumes RAM starts at
0; on QEMU virt it starts at 1 GiB, which is why this was rewritten. `CSysFixedArea` cannot sit
at a fixed address when the image loads anywhere, so `SYS_FIXED_AREA` points at
`arm64_sys_fixed`, a page-aligned block inside the image.

**MAlloc.** [Arm64/MAllocFree.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Arm64/MAllocFree.TC)
is the x86 asm rewritten in TerryC (`ArmMAlloc`, `ArmFree`, `ArmMSize`, `ArmMSize2`,
`ArmMHeapCtrl`), with the same data structures, size classes and locking. The labels `_MALLOC`,
`_FREE` and so on are one-instruction branches to these functions, so the `_extern` lines in the
shared `MAllocFree.TC` stay as they are. The heap-debug caller tracking of `_CFG_HEAP_DBG` is not
ported.

**The identity map.** There is no `SYS_INIT_PAGE_TABLES`. `ARM64_MMU_INIT` in
[Arm64/KStart.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Arm64/KStart.TC)
builds the map in asm before any TerryC runs: 4 KiB granule, 39-bit addresses (`T0SZ` 25), one
L1 table of 512 entries of 1 GiB (`ARM64_PT_L1`). Some GiBs are split into 2 MiB blocks with the
L2 tables `ARM64_PT_L2A` and `ARM64_PT_L2B`. `MAIR_EL1` index 0 is Device-nGnRnE, 1 is Normal
write-back (`ARM64_PTE_NORMAL`), 2 is Normal non-cacheable. The layout is picked from the load
address and the CPU (`ARM64_LAYOUT`), using the table `ARM64_LAYOUT_KINDS` for the first 16 GiB:

| Layout | When | Map |
| --- | --- | --- |
| `ARM64_LAYOUT_VIRT` | loaded at 1 GiB or above | GiB 0 devices, RAM from 1 GiB up to 256 GiB |
| `ARM64_LAYOUT_RPI3` | below 1 GiB, other CPUs (Cortex-A53) | GiB 0 in 2 MiB blocks, devices from `0x3F000000`; everything above devices |
| `ARM64_LAYOUT_RPI4` | below 1 GiB, Cortex-A72 | GiB 0 in 2 MiB blocks, all RAM; GiBs 1-7 RAM, but GiB 3 in 2 MiB blocks with devices from `0xFC000000`; devices from 8 GiB |
| `ARM64_LAYOUT_RPI5` | below 1 GiB, Cortex-A76 | GiB 0 in 2 MiB blocks, all RAM; GiBs 1-15 RAM; devices from 16 GiB |

Everything not marked RAM is mapped as Device memory, and everything above 256 GiB is Device.
That is why `UncachedAliasAlloc` on ARM64 just sets `dev.uncached_alias` to `NULL` (device
addresses are already uncached as they are), and why `Mem64DevAlloc` only hands out address
space without touching page tables. `ArmMemInit` sets `mem_page_size` to 1 GiB and
`mem_mapped_space` to `1<<39` to describe this map.

**Framebuffer.** On a Pi, the firmware puts the framebuffer in GiB 0, which is mapped as cacheable
RAM. `ArmFbUncache` in
[Arm64/Video.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Arm64/Video.TC)
cleans the cache over the framebuffer, then rewrites each of its 2 MiB L2 entries with MAIR
index 2 (Normal non-cacheable). It clears each entry and flushes the TLB before writing the new
one, the "break before make" order ARM requires. It only works in a GiB that is split into 2 MiB
blocks and returns `FALSE` otherwise. `ArmMboxFbInit` calls it. On QEMU virt the ramfb
framebuffer is an ordinary `MAllocAligned` block in Adam's heap and is left cacheable.
