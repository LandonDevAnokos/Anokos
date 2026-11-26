"""Build a bootable RedSea ISO (TempleOS/Anokos format) from a directory tree.

Usage:
  python tools/build_iso.py <tree_dir> <out.iso> [--base TempleOS/TempleOS.ISO]
                            [--kernel 0000Boot/0000Kernel.BIN.C]

The first 88 blocks (ISO9660 descriptors, El Torito catalog and the DVD stage-1
boot loader) are taken from the reference ISO and patched for the new layout.
The rest of the image is a RedSea file system written from <tree_dir>.

Files whose type TempleOS stores compressed (HC, DD, ...) are written as
"<name>.Z" with a CT_NONE (stored, uncompressed) archive header, which the
kernel's ExpandBuf() reads like any other .Z file.
"""
import argparse
import os
import struct
import sys

BLK = 512
DVD_BLK = 2048
DRV_OFFSET = 88            # 19<<2 + (2*2048 + 2048)/512, same as RedSeaISO()
STAGE1_BLK = 84            # 20<<2 + 1<<2: DVD stage-1 boot loader
STAGE1_PATCH = 136         # BDVD_BLK_LO offset inside the stage-1 loader
DIRENT = 64
NAME_MAX = 37              # CDIR_FILENAME_LEN-1

ATTR_DIR = 0x10
ATTR_COMPRESSED = 0x400
ATTR_CONTIGUOUS = 0x800
CT_NONE = 1
DATETIME = 0x000B3F0FF1362161  # CDate copied from the reference ISO

COMPRESS_EXTS = {"HC", "HH", "DD", "IN", "PRJ", "MAP", "BIN", "DATA", "TXT", "GR"}


def wants_z(rel_path):
    parts = rel_path.replace("\\", "/").split("/")
    if parts[0] == "Downloads":
        return False
    name = parts[-1]
    ext = name.rsplit(".", 1)[-1].upper() if "." in name else ""
    return ext in COMPRESS_EXTS


def z_wrap(data):
    return struct.pack("<qqB", 17 + len(data), len(data), CT_NONE) + data


class Node:
    def __init__(self, name, path, is_dir):
        self.name, self.path, self.is_dir = name, path, is_dir
        self.children = []
        self.data = b""
        self.clus = 0
        self.blks = 0


def scan(root, rel=""):
    node = Node(os.path.basename(rel) if rel else ".", rel, True)
    for name in sorted(os.listdir(os.path.join(root, rel) if rel else root)):
        if name in (".gitkeep",):
            continue
        child_rel = f"{rel}/{name}" if rel else name
        full = os.path.join(root, child_rel)
        if os.path.isdir(full):
            node.children.append(scan(root, child_rel))
        else:
            with open(full, "rb") as f:
                data = f.read()
            disk_name = name
            if wants_z(child_rel):
                data = z_wrap(data)
                disk_name += ".Z"
            if len(disk_name) > NAME_MAX:
                raise SystemExit(f"name too long for RedSea: {child_rel}")
            n = Node(disk_name, child_rel, False)
            n.data = data
            node.children.append(n)
    return node


def layout(node, next_clus):
    """Assign contiguous clusters depth-first; directories before their contents."""
    if node.is_dir:
        node.blks = max(1, -(-(len(node.children) + 3) * DIRENT // BLK))
    else:
        node.blks = -(-len(node.data) // BLK)
    node.clus = next_clus
    next_clus += node.blks
    if node.is_dir:
        for c in node.children:
            next_clus = layout(c, next_clus)
    return next_clus


def dirent(attr, name, clus, size):
    nb = name.encode("latin-1")
    return struct.pack("<H38sqqQ", attr, nb, clus, size, DATETIME)


def write_tree(img, node, parent):
    if node.is_dir:
        buf = bytearray(node.blks * BLK)
        self_name = "." if parent is None else node.name
        par = parent if parent is not None else node
        ents = [dirent(ATTR_DIR | ATTR_CONTIGUOUS, self_name, node.clus, node.blks * BLK),
                dirent(ATTR_DIR | ATTR_CONTIGUOUS, "..", par.clus, 0)]
        for c in node.children:
            if c.is_dir:
                ents.append(dirent(ATTR_DIR | ATTR_CONTIGUOUS, c.name, c.clus, c.blks * BLK))
            else:
                attr = ATTR_CONTIGUOUS | (ATTR_COMPRESSED if c.name.endswith(".Z") else 0)
                ents.append(dirent(attr, c.name, c.clus, len(c.data)))
        for i, e in enumerate(ents):
            buf[i * DIRENT:(i + 1) * DIRENT] = e
        img[node.clus * BLK:node.clus * BLK + len(buf)] = buf
        for c in node.children:
            write_tree(img, c, node)
    else:
        img[node.clus * BLK:node.clus * BLK + len(node.data)] = node.data


def find(node, rel):
    for part in rel.split("/"):
        names = {c.name: c for c in node.children}
        node = names.get(part) or names.get(part + ".Z")
        if node is None:
            return None
    return node


def build(tree, out, base, kernel_rel):
    with open(base, "rb") as f:
        ref = f.read()
    root = scan(tree)

    # Size the bitmap: data_area = DRV_OFFSET + bitmap_blks, root at data_area+1.
    bitmap_blks = 1
    while True:
        data_area = DRV_OFFSET + bitmap_blks
        end = layout(root, data_area + 1)
        total = -(-end // 4) * 4                     # whole 2048-byte DVD blocks
        sects = total - DRV_OFFSET
        need = -(-sects // (BLK * 8))
        if need <= bitmap_blks:
            break
        bitmap_blks = need

    img = bytearray(total * BLK)
    img[:DRV_OFFSET * BLK] = ref[:DRV_OFFSET * BLK]

    # RedSea boot record
    br = bytearray(ref[DRV_OFFSET * BLK:(DRV_OFFSET + 1) * BLK])
    struct.pack_into("<qqqqq", br, 8, DRV_OFFSET, sects, root.clus, bitmap_blks,
                     int.from_bytes(os.urandom(8), "little") >> 1)
    img[DRV_OFFSET * BLK:(DRV_OFFSET + 1) * BLK] = br

    # Allocation bitmap: bit i <-> cluster data_area+i
    bm = bytearray(bitmap_blks * BLK)
    for i in range(end - data_area):
        bm[i >> 3] |= 1 << (i & 7)
    img[(DRV_OFFSET + 1) * BLK:(DRV_OFFSET + 1 + bitmap_blks) * BLK] = bm

    write_tree(img, root, None)

    # ISO9660 primary + supplementary descriptors: volume size and root pointer
    for d in (16, 18):
        o = d * DVD_BLK
        struct.pack_into("<I", img, o + 80, total // 4)
        struct.pack_into(">I", img, o + 84, total // 4)
        struct.pack_into("<I", img, o + 152, root.clus)
        struct.pack_into(">I", img, o + 156, root.clus)

    # Stage-1 DVD loader: where the kernel lives
    k = find(root, kernel_rel)
    if k is None or k.is_dir:
        raise SystemExit(f"kernel not found in tree: {kernel_rel}")
    cnt = (len(k.data) + DVD_BLK - 1) // DVD_BLK + (1 if k.clus & 3 else 0)
    struct.pack_into("<IHH", img, STAGE1_BLK * BLK + STAGE1_PATCH, k.clus >> 2, cnt, k.clus & 3)

    with open(out, "wb") as f:
        f.write(img)
    print(f"{out}: {len(img)} bytes, root_clus={root.clus}, bitmap={bitmap_blks}, "
          f"kernel clus={k.clus} size={len(k.data)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tree")
    ap.add_argument("out")
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument("--base", default=os.path.join(here, "..", "TempleOS", "TempleOS.ISO"))
    ap.add_argument("--kernel", default="0000Boot/0000Kernel.BIN.C")
    a = ap.parse_args()
    build(a.tree, a.out, a.base, a.kernel)


if __name__ == "__main__":
    sys.exit(main())
