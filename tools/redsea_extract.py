"""Extract a TempleOS RedSea ISO image and expand its LZW-compressed (.Z) files.

Usage: python redsea_extract.py <image.iso> <out_dir> [--raw]

Clean-room implementation written from the on-disk format; uses no TempleOS code.
"""
import os
import struct
import sys

BLK = 512
ATTR_DIR = 0x10
ATTR_DELETED = 0x100
ATTR_COMPRESSED = 0x400

ARC_BITS_MAX = 12
CT_NONE, CT_7_BIT, CT_8_BIT = 1, 2, 3


def find_boot(img):
    # RedSea boot record: signature byte 0x88 at offset 3, 0xAA55 at 510.
    for blk in range(0, min(len(img) // BLK, 4096)):
        o = blk * BLK
        if img[o + 3] == 0x88 and img[o + 510:o + 512] == b"\x55\xaa":
            drv_offset, sects, root_clus, bitmap_sects = struct.unpack_from("<qqqq", img, o + 8)
            if drv_offset == blk:
                return dict(drv_offset=drv_offset, sects=sects, root_clus=root_clus,
                            bitmap_sects=bitmap_sects)
    raise SystemExit("RedSea boot record not found")


def read_dir(img, clus, size):
    o = clus * BLK
    for i in range(size // 64):
        e = o + i * 64
        attr = struct.unpack_from("<H", img, e)[0]
        name = img[e + 2:e + 40].split(b"\0")[0]
        if not name:
            break
        f_clus, f_size, dt = struct.unpack_from("<qqQ", img, e + 40)
        yield attr, name.decode("latin-1"), f_clus, f_size, dt


def expand(buf):
    """Expand a CArcCompress block: I64 compressed_size, I64 expanded_size, U8 type, body."""
    comp_size, exp_size, ctype = struct.unpack_from("<qqB", buf, 0)
    body_off = 17
    if ctype == CT_NONE:
        return bytes(buf[body_off:body_off + exp_size])
    if ctype not in (CT_7_BIT, CT_8_BIT):
        raise ValueError("bad compression type %d" % ctype)

    min_bits = 7 if ctype == CT_7_BIT else 8
    min_entry = 1 << min_bits
    n = 1 << ARC_BITS_MAX
    ent_base = [0] * n
    ent_ch = [0] * n
    ent_next = [-1] * n          # sibling chain under same basecode
    hash_head = [-1] * n         # first entry whose basecode == index

    st = dict(free_idx=min_entry, next_bits=min_bits + 1, free_limit=1 << (min_bits + 1),
              cur_entry=-1, next_entry=-1, entry_used=True)

    def get_table_entry():
        if not st["entry_used"]:
            return
        i = st["free_idx"]
        st["entry_used"] = False
        st["cur_entry"] = st["next_entry"]
        if st["next_bits"] < ARC_BITS_MAX:
            st["next_entry"] = i
            i += 1
            if i == st["free_limit"]:
                st["next_bits"] += 1
                st["free_limit"] = 1 << st["next_bits"]
        else:
            while True:
                i += 1
                if i == st["free_limit"]:
                    i = min_entry
                if hash_head[i] == -1:
                    break
            st["next_entry"] = i
            # unlink i from its parent's chain
            b = ent_base[i]
            prev = -1
            cur = hash_head[b]
            while cur != -1:
                if cur == i:
                    if prev == -1:
                        hash_head[b] = ent_next[i]
                    else:
                        ent_next[prev] = ent_next[i]
                    break
                prev, cur = cur, ent_next[cur]
        st["free_idx"] = i

    get_table_entry()
    st["entry_used"] = True

    src = bytes(buf) + b"\0\0\0\0"
    src_bits = comp_size * 8
    pos = body_off * 8

    def read(bits):
        nonlocal pos
        v = (struct.unpack_from("<I", src, pos >> 3)[0] >> (pos & 7)) & ((1 << bits) - 1)
        pos += bits
        return v

    out = bytearray()
    stack = []
    lastcode = read(st["next_bits"])
    out.append(lastcode)
    get_table_entry()
    last_ch = lastcode
    while len(out) < exp_size and pos + st["next_bits"] <= src_bits:
        basecode = read(st["next_bits"])
        if st["cur_entry"] == basecode:
            stack.append(last_ch)
            code = lastcode
        else:
            code = basecode
        while code >= min_entry:
            stack.append(ent_ch[code])
            code = ent_base[code]
        stack.append(code)
        last_ch = code

        st["entry_used"] = True
        t = st["cur_entry"]
        ent_base[t] = lastcode
        ent_ch[t] = last_ch
        ent_next[t] = hash_head[lastcode]
        hash_head[lastcode] = t

        get_table_entry()
        while stack and len(out) < exp_size:
            out.append(stack.pop())
        lastcode = basecode
    if len(out) != exp_size:
        raise ValueError("expanded %d of %d bytes" % (len(out), exp_size))
    return bytes(out)


def walk(img, clus, size, rel, out_dir, raw, log):
    # The first two entries of every RedSea directory are self and parent.
    for idx, (attr, name, f_clus, f_size, dt) in enumerate(read_dir(img, clus, size)):
        if idx < 2 or attr & ATTR_DELETED:
            continue
        path = os.path.join(rel, name)
        if attr & ATTR_DIR:
            os.makedirs(os.path.join(out_dir, path), exist_ok=True)
            walk(img, f_clus, f_size, path, out_dir, raw, log)
            continue
        data = img[f_clus * BLK:f_clus * BLK + f_size]
        dest = path
        if not raw and name.endswith(".Z"):
            try:
                data = expand(data)
                dest = path[:-2]
            except Exception as ex:  # keep the original bytes if expansion fails
                log.append("FAILED %s: %s" % (path, ex))
        with open(os.path.join(out_dir, dest), "wb") as f:
            f.write(data)
        log.append("%s -> %s (%d bytes)" % (path, dest, len(data)))


def main():
    iso, out_dir = sys.argv[1], sys.argv[2]
    raw = "--raw" in sys.argv[3:]
    with open(iso, "rb") as f:
        img = f.read()
    boot = find_boot(img)
    root = boot["root_clus"]
    root_size = next(read_dir(img, root, 64))[3]
    os.makedirs(out_dir, exist_ok=True)
    log = []
    walk(img, root, root_size, "", out_dir, raw, log)
    print("\n".join(log))
    print("boot:", boot, file=sys.stderr)


if __name__ == "__main__":
    main()
