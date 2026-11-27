# Block devices and file systems

Storage has three layers: a block device (`CBlkDev`, one disk or RAM disk or image file),
drives on it (`CDrv`, one partition each, named by a letter `A`-`Z`), and a file system on
each drive (RedSea, Anokos's own, or FAT32). Everything is in 512-byte blocks, read with
polled ATA PIO, through one shared write-through block cache. The code is in
[src/Kernel/BlkDev/](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/BlkDev/),
included in order by `MakeBlkDev.TC`.

## The layers

```
FileRead / FileWrite / FOpen / Dir / Cd ...     DskFile, DskCFile, DskDirB, DskFind
        |  path -> drive + directory + name      DskStrA, DskDirContext
RedSeaFileRead ...      FAT32FileRead ...       FileSysRedSea, FileSysFAT
        |  clusters                              DskClus  (ClusRead, ClusAlloc)
BlkRead(dv,buf,blk,cnt) / BlkWrite               DskBlk
        |  disk cache (DskCache), drive locks (DskDrv, DskBlkDev)
RAM_dsk memcpy | ISO file (FBlkRead) | ATARBlks / ATAWBlks (DskATA)
```

Block numbers at the `BlkRead` level are absolute on the device. A drive covers
`[drv_offset, drv_offset+size)`, and `BlkRead`/`BlkWrite` throw `'Drv'` outside it.
`BLK_SIZE` is 512 (`BLK_SIZE_BITS` 9) everywhere, even on CD/DVD, which has 2048-byte
sectors (`DVD_BLK_SIZE`); the ATAPI code reads whole sectors and copies out the 512-byte
blocks asked for.

## Block devices and drives

Both kinds of object live in fixed arrays in the `blkdev` global (`CBlkDevGlbls`, in
[src/Kernel/KernelA.TH](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/KernelA.TH)):
`blkdev.blkdevs[BLKDEVS_NUM]` and `blkdev.drvs[DRVS_NUM]`, 26 each. A slot is in use when its
signature is set (`BD_SIGNATURE_VAL` `'BDSV'`, `DRV_SIGNATURE_VAL` `'DVSV'`); `BlkDevChk` and
`DrvChk` throw if it isn't.

`CBlkDev` holds the device: `type`, `flags`, the IDE port bases `base0`/`base1` and `unit`
(master 0, slave 1), `blk_size`, `max_blk`, `max_reads`/`max_writes` per command, the RAM
buffer `RAM_dsk` or the image file `file_dsk`, and `dev_id_record` (the ATA IDENTIFY data,
used by `DrvModelNum` and `DrvSerialNum`).

`CDrv` holds one partition: `fs_type`, `drv_offset`, `size`, the pointer `bd` to its device,
and file-system fields (`root_clus`, `data_area`, `spc` sectors per cluster, `fat1`/`fat2`,
the cached FAT or bitmap block `cur_fat_blk`, and RedSea's free list).

The device type is fixed by the drive letter, in `Let2BlkDevType`
([DskDrv.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/BlkDev/DskDrv.TC)):

| Letters | Type | What it is |
| --- | --- | --- |
| `A`-`B` | `BDT_RAM` | RAM disk, always RedSea. |
| `C`-`L` | `BDT_ATA` | IDE/ATA hard disk partitions. |
| `M`-`P` | `BDT_ISO_FILE_READ` | A RedSea image file mounted read-only. |
| `Q`-`S` | `BDT_ISO_FILE_WRITE` | A new image file, formatted RedSea, for building a CD. |
| `T`-`Z` | `BDT_ATAPI` | IDE CD/DVD drives. |

`blkdev.let_to_drv[]` maps a letter to its `CDrv`. It starts as the identity map; `DrvMap`
can point a letter at another drive.

**Mounting.** `BlkDevNextFreeSlot(let,type)` takes a free `CBlkDev` with defaults for the
type (ATAPI gets `BDF_REMOVABLE|BDF_READ_ONLY` and 2048-byte blocks; RAM disks skip the cache).
`BlkDevAdd` in
[DskAddDev.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/BlkDev/DskAddDev.TC)
then creates the drives. For an ATA disk it reads the MBR and follows extended partitions
(types 5 and 15), one drive letter per partition. Partition type `0x88` (`MBR_PT_REDSEA`) is
RedSea; the FAT32 types are checked further, because RedSea formats its partition as FAT32 in
the table: if the boot record's signature byte is `0x88` it's RedSea, else FAT32. Type 7 is
recorded as NTFS, which isn't supported.

The drives that exist at boot are compiled into the kernel. When you build a kernel,
`KCfgNew` asks for them through `Mount2`
([src/Anokos/ABlkDev/Mount.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Anokos/ABlkDev/Mount.TC)),
which writes TerryC calls like `bd=BlkDevNextFreeSlot('C',2);` into a document.
`BlkDevsInitAll` pastes that document into its own body at compile time with `StreamDoc`, so
the mount code runs at boot without questions. The `MountIDEAuto` option probes instead.

**Locking.** `BlkDevLock`/`DrvLock` give one task exclusive use of a device or drive. They
spin with `Yield` and are re-entrant: if the task already holds the lock they return `FALSE`,
meaning "don't unlock", which is why the code is full of `unlock=DrvLock(dv); ...
if (unlock) DrvUnlock(dv);`. Two devices on the same IDE channel share one lock through
`lock_fwding`. When a task dies, `DrvsRelease` and `BlkDevsRelease` free what it held.
Directory and allocation updates also take `BreakLock`, so Ctrl+Alt+C can't stop them halfway.

## Drive letters and paths

Each task has its own current drive and directory, `Fs->cur_dv` and `Fs->cur_dir` (always
starting with `/`). Paths use `/` and an optional drive prefix:

| Path | Means |
| --- | --- |
| `C:/Home/Foo.TC` | Absolute, on drive `C`. |
| `/Kernel/KMain.TC` | Absolute, on the current drive. |
| `Foo.TC`, `../x` | Relative to `Fs->cur_dir`. `.` and `..` work as usual. |
| `::/Doc/Help.DD` | `:` as the drive letter is the boot drive, `blkdev.boot_drv_let`. |
| `~/Notes.DD` | `~` as a path component is the home directory, `blkdev.home_dir`. |
| `~:/` | `~` as the drive letter is the home directory's drive. |

`Let2Let` resolves `:` and `~` (and 0, the current drive) to a real letter; `Let2Drv` and
`Let2BlkDev` go on to the objects. `DirNameAbs` and `FileNameAbs` in
[DskStrA.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/BlkDev/DskStrA.TC)
turn any path into an absolute one with a letter, like `C:/Home/Foo.TC`.

The boot drive letter is chosen when the kernel is configured and compiled in
(`kernel_cfg->boot_drv_let`). `HomeSet` sets the home directory; the default is `::/Home`,
stored as an absolute path such as `C:/Home`. `Cd` with no argument goes home.

`Drv(let)` changes the task's drive and resets its directory to `/`. `DskChg(let)` is for
removable media: it re-initializes the device and drops its cached blocks.

Names are at most 37 characters (`CDIR_FILENAME_LEN` 38 with the terminator) from the set in
`char_bmp_filename`; `FileNameChk` checks them. Two endings are special: `.Z` means compressed
and `.C` means contiguous. Both need a second dot (`Foo.DD.Z`, `Img.ISO.C`); see `IsDotZ`,
`IsDotC` and `FileAttr`.

## The ATA/ATAPI driver

[DskATA.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/BlkDev/DskATA.TC)
is a polled PIO driver for IDE disks and CD/DVD drives, using port I/O on `base0` (the
command block, `ATAR0_*` registers) and `base1` (control). There's no DMA and no interrupt;
the wait loops (`ATAWaitNotBUSY`, `ATAWaitDRQ`) call `Yield` so other tasks run meanwhile.

- `ATABlkSel` loads the LBA and count, 48-bit (`BDF_EXT_SIZE`, used for all ATA disks) or 28-bit.
- `ATAReadBlks` issues `ATA_READ_MULTI(_EXT)` and reads the data with `RepInU32`/`RepInU16`
  in `ATAGetRes`, retrying three times before throwing `'BlkDev'`. `BlkDevInit` limits a
  command to 128 blocks (`max_reads`).
- `ATAWriteBlks` issues `ATA_WRITE_MULTI(_EXT)` and writes one sector at a time; ATA disks get
  `max_writes=1`.
- `ATAInit` reads the native size (`ATAReadNativeMax`, which also stores the IDENTIFY record)
  and, for ATAPI, spins the disc up and sets the maximum speed.
- ATAPI reads go through SCSI packets (`ATAPIReadBlks2`, command `0xA8`). `ATAPIReadBlks`
  reads extra sectors around the request and puts all of them in the cache, because seeking
  on a CD is slow.
- `ATARBlks`/`ATAWBlks` are the entry points `BlkRead`/`BlkWrite` use. They split requests
  into `max_reads`/`max_writes` pieces.

[DskATAId.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/BlkDev/DskATAId.TC)
finds devices. `ATARep` looks for PCI IDE controllers (class `0x0101xx`), takes their port
bases from BARs 0-3, falls back to the legacy ports `0x1F0`/`0x3F6` and `0x170`/`0x376`, and
probes both units on each channel. `MountIDEAuto` mounts the first hard disk at
`blkdev.first_hd_drv_let` and the first CD/DVD at `blkdev.first_dvd_drv_let` (`C` and `T`).
`BootDVDProbe` tells which CD/DVD the system booted from: it reads the boot image from the disc
and compares the kernel's `compile_time` with the running kernel's.

## The disk cache

[DskCache.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/BlkDev/DskCache.TC)
keeps `CCacheBlk` entries (one 512-byte block each, keyed by drive and block number) on an
LRU queue and in a hash table of `DSK_CACHE_HASH_SIZE` buckets. Its size comes from the
kernel config expression passed to `DskCacheInit` at boot. It's protected by the
`SEMA_DSK_CACHE` semaphore.

- `BlkRead` first calls `RCache`, which serves leading and trailing blocks of the request from
  the cache, then reads only the middle from the device and adds it with `DskCacheAdd`.
- `BlkWrite` writes to the device and then updates the cache, so the cache is write-through
  and never holds data that isn't on the device yet.
- Devices without `BDF_READ_CACHE` (RAM disks) bypass it. `DskCacheInvalidate` drops a
  drive's blocks after a media change.

## RedSea

RedSea
([FileSysRedSea.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/BlkDev/FileSysRedSea.TC),
background in `src/Doc/RedSea.DD`) is TempleOS's file system. It trades flexibility for
simplicity: a cluster is one block, cluster numbers are absolute block numbers
(`Clus2Blk` returns its argument), every file is one contiguous run of blocks, and the only
allocation structure is a bitmap. There is no FAT chain to follow, so reading a file is a
single `BlkRead`.

```
block:  drv_offset     drv_offset+1 ... drv_offset+n     after that
        CRedSeaBoot    allocation bitmap, n blocks       root directory, files ...
                                      data_area = drv_offset+n = bit 0 of the bitmap
```

The boot record `CRedSeaBoot` has the signature byte `MBR_PT_REDSEA` (`0x88`) at offset 3,
`0xAA55` at the end, and the fields `drv_offset`, `sects`, `root_clus`, `bitmap_sects` and
`unique_id`. `RedSeaInit` loads them into the `CDrv`. The bitmap has one bit per block
counted from `data_area`, so a 512-byte bitmap block covers 4096 blocks (2 MB); `RedSeaFmt`
makes `n = ceil(sects/4096)` bitmap blocks. Bit 0 lands on the last bitmap block itself, so
formatting allocates one block first ("Alloc #1") to mark it used.

**Allocation.** The first allocation or free on a drive builds an in-memory list of free runs
(`CFreeLst`, `RedSeaFreeLstBuild`) by scanning the bitmap. `RedSeaAllocClus` takes the
smallest run that fits (best fit) and throws `'Drv'` if none does, even if there's enough free
space in pieces. `RedSeaFreeClus` merges freed blocks back into a neighboring run. Bitmap
changes go through `DrvFATBlkSet`, which keeps one bitmap block in `dv->cur_fat_blk`, and
`DrvFATBlkClean`, which writes it back.

**Directories.** A directory is a contiguous file of 64-byte entries (`CDIR_SIZE`). The
on-disk part of `CDirEntry` starts at its `start` member:

```c
U16   attr;                      //RS_ATTR_* flags
U8    name[CDIR_FILENAME_LEN];   //38 bytes, zero-terminated
I64   clus,size;                 //first block, size in bytes
CDate datetime;
```

The fields before `start` (`next`, `parent`, `sub`, `full_name`, `user_data`) are only used in
memory, for the trees `FilesFind` returns. Entry 0 describes the directory itself: its
`clus` and, more importantly, its `size`, which is how the code knows how many blocks to read.
Entry 1 is `..` with the parent's block. The list ends at the first entry with an empty name.
Deleting sets `RS_ATTR_DELETED`, and `RedSeaDirNew` reuses such slots. When a directory is
full, it's reallocated one block bigger somewhere else and its entry in the parent (or
`root_clus` in the boot record) is rewritten.

| Attribute | Meaning |
| --- | --- |
| `RS_ATTR_DIR` | Directory. |
| `RS_ATTR_READ_ONLY` `RS_ATTR_HIDDEN` `RS_ATTR_SYSTEM` `RS_ATTR_ARCHIVE` | As in FAT. |
| `RS_ATTR_DELETED` | Free directory slot. |
| `RS_ATTR_COMPRESSED` | The data is a `CArcCompress` (name ends in `.Z`). |
| `RS_ATTR_CONTIGUOUS` | One block run. Always set on RedSea. |
| `RS_ATTR_RESIDENT` | `FileRead` keeps a copy in memory (see below). |

**Writing.** Files can't grow in place. `RedSeaFileWrite` deletes the old file (freeing its
blocks), allocates a new run of `ceil(size/512)` blocks, writes the data and adds the directory
entry. That's why the whole-file API is the normal way to write on RedSea.

## FAT32

[FileSysFAT.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/BlkDev/FileSysFAT.TC)
reads and writes FAT32 partitions, mainly to exchange files with other systems. `FAT32Init`
reads the `CFAT32Boot` sector: `fat1` follows the reserved sectors, `fat2` follows `fat1`,
and `data_area` is set so that cluster 2 is the first data cluster. Cluster chains are
followed through the FAT one block (128 entries) at a time with `DrvFATBlkSet` and
`ClusNumNext`; values from `0x0FFFFFF8` up end a chain. Changes are written to both FAT copies.

Directory entries are converted between FAT's 32-byte format and `CDirEntry`. Names that
aren't valid 8.3 get long-name entries; the writer (`FAT32DirFill`) creates at most two of
them, 13 characters each, plus the short entry. Dates are DOS dates (`CDate2Dos`,
`Dos2CDate`). Files are allocated as cluster chains, or contiguously when the name ends in `.C`
(`FAT32AllocContiguousClus`). `Fmt` (in `DskFmt.TC`) formats an ATA drive as FAT32 by
default and any other drive as RedSea. Formatting also sets the partition's type in the MBR
(`DrvTypeSet`); RedSea partitions get a FAT32 type there too.

## The file API

The functions most code uses, in `DskFile.TC`, `DskCFile.TC`, `DskDirB.TC`, `DskFind.TC`
and `DskCopy.TC`:

| Function | Does |
| --- | --- |
| `FileRead(name,&size,&attr)` | Reads a whole file into a new buffer (with a terminating 0). |
| `FileWrite(name,buf,size,cdt,attr)` | Writes a whole file, replacing any old one. |
| `FileFind(name,&de,fuf_flags)` | Finds one file or directory; fills a `CDirEntry`. |
| `FilesFind(mask,fuf_flags)` | Returns a tree of `CDirEntry`; free it with `DirTreeDel`. |
| `Dir(mask)`, `Cd(dir)`, `DirMk(dir)`, `Del(mask)` | The shell commands. |
| `FOpen(name,flags,cnt)` | Opens a `CFile` for block-level access. |

`FileRead` in
[DskFile.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/BlkDev/DskFile.TC)
does more than its name suggests. It first looks for a resident copy (`HTT_FILE` in the Adam
task's hash table). Then it tries the name and the name with `.Z` toggled (`ToggleZorNotZ`), so
`Foo.DD` finds `Foo.DD.Z`. If that fails it searches the parent directories, up to the root.
Last, it expands compressed data. `FileWrite` compresses when the name ends in `.Z`.

Masks for `FilesFind`, `Dir` and `Del` are wildcard patterns separated by `;`, and a pattern
starting with `!` excludes (`"*.TC;!*Tmp*"`). The `FUF_*` flags select recursion
(`FUF_RECURSE`), files or directories only, file kinds such as `FUF_JUST_SRC`, and flattening
the tree.

Most of these functions start with `DirContextNew(path)`
([DskDirContext.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/BlkDev/DskDirContext.TC)).
It saves the task's drive and directory in a `CDirContext`, changes to the drive and directory
part of the path, and leaves the last part in `dirc->mask`. The file system code then works on
`Fs->cur_dir`, and `DirContextDel` changes back. So file operations depend on the calling
task's current directory, and each task can be in a different one.

**CFile.** `FOpen` flags are `"r"`, `"w"` (replace) or `"w+"` (update an existing file), plus
`c` for contiguous; `cnt` is the size in blocks to preallocate. Access is in whole blocks:
`FBlkRead(f,buf,blk,cnt)` and `FBlkWrite(f,buf,blk,cnt)`, where `blk` defaults to
`FFB_NEXT_BLK` (continue from the last position). On FAT32 a non-contiguous `CFile` buffers one
cluster in `f->clus_buf` and extends the chain as you write. On RedSea every `CFile` is
contiguous, so pass `cnt` when creating one. `FClose` writes the buffer and updates the
directory entry. `src/Demo/Dsk/DataBase.TC` is an example.

## Compressed .Z files

[src/Kernel/Compress.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Compress.TC)
is an LZW coder. `CompressBuf(src,size)` returns a `CArcCompress`: a header with
`compressed_size`, `expanded_size` and `compression_type`, then the bit stream. Codes start at
8 bits for 7-bit data (`CT_7_BIT`, chosen when no byte has bit 7 set) or 9 bits otherwise
(`CT_8_BIT`), and grow up to `ARC_BITS_MAX` (12). If the output wouldn't be smaller, the data
is stored as is (`CT_NONE`). `ExpandBuf(arc)` reverses it and checks the type and size first.

The file system doesn't know about compression; only the whole-file layer does.
`FileAttr` sets `RS_ATTR_COMPRESSED` for `.Z` names, `FileWrite` compresses before writing
and `FileRead` expands after reading, so callers always see plain data. Copying a file to a
name with or without `.Z` converts it (`CopySingleZ` in `DskCopy.TC`). `FBlkRead` on a `.Z`
file returns the compressed bytes.

## CD/DVD and image files

ISO 9660 itself isn't supported (`FSt_ISO9660` is listed as "Not Supported"). Anokos CDs are
RedSea inside an ISO wrapper. `ISOInit` in
[DskCDDVD.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/BlkDev/DskCDDVD.TC)
reads the volume descriptors; if the supplementary descriptor's publisher is
`"TempleOS RedSea"`, it mounts the RedSea image that starts after the descriptors (block 76,
or 12 blocks later when there's an El Torito boot record descriptor). The writer side is
`src/Anokos/Opt/Boot/DskISORedSea.TC`, called through `BDT_ISO_FILE_WRITE` drives.
`DVDImageRead` copies a disc to an `.ISO` file and `DVDImageWrite` burns one.

A `BDT_ISO_FILE_READ` drive mounts a RedSea image file. It's opened as a contiguous `CFile`,
so on FAT32 the file must be named `*.ISO.C`; `BlkDevInit` says so if it isn't.

## On ARM64

- There are no storage drivers yet. `MakeBlkDev.TC` includes
  [src/Kernel/Arm64/Ata.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Arm64/Ata.TC)
  instead of `DskATA.TC` and `DskATAId.TC`: stand-ins with the same names that find nothing,
  return `FALSE` and throw `'BlkDev'` if asked to transfer data, so the rest of the layer
  compiles unchanged.
- `KernelArm64.PRJ` builds the kernel config without questions and with an empty `add_dev`
  document, so `BlkDevsInitAll` mounts no drives. `blkdev.boot_drv_let` is `B`.
  `DskCacheInit` isn't called, so there's no disk cache (`RCache` and `DskCacheAdd` check
  for that).
- The file system, RAM disk and compression code is shared. `ArcEntryGet`, which is asm on
  x86, is `ArmArcEntryGet` in TerryC
  ([src/Kernel/Arm64/Compress.TC](https://github.com/LandonDevAnokos/Anokos/blob/main/src/Kernel/Arm64/Compress.TC)).
- Planned: virtio-blk on QEMU `virt` and the SD card on the Raspberry Pi, after which RedSea
  drives work as on x86. See [The ARM64 port](arm64.md).
