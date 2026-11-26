"""Build the data for the Code City and graph view (docs/code-map/) from the TerryC sources.

Usage (from the repository root):
  python tools/codemap.py              # writes docs/code-map/codemap.json

It reads every .TC/.TH (and legacy .HC/.HH) file under src/ and records:
  nodes   functions, global variables, classes, #defines and asm labels,
          plus one node per source file
  edges   calls/uses between them, class types, inheritance and #includes
  details parameters and local variables of each function, members of
          each class, and the declaration text

This is a fast approximate parser, not the real compiler: it recognizes
declarations by their shape. Names that are defined in several places
(for example two demos that both define Main) are resolved by preferring
the same file, then the same top-level folder, then the core system.
"""
import json
import math
import os
import re
import subprocess
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
OUT = os.path.join(ROOT, "docs", "code-map", "codemap.json")
OUT_SRC = os.path.join(ROOT, "docs", "code-map", "source.json")
DOLDOC_TEXT = {"TX", "LK", "MA", "MU", "BT", "CB", "LS", "DA", "TR", "CK"}
EXTS = (".TC", ".TH", ".HC", ".HH")

BUILTIN_TYPES = {
    "U0", "I0", "U8", "I8", "U16", "I16", "U32", "I32", "U64", "I64", "F64", "Bool",
    "U8i", "I8i", "U16i", "I16i", "U32i", "I32i", "U64i", "I64i", "F64i", "U0i", "I0i",
}
MODIFIERS = {
    "public", "static", "extern", "_extern", "import", "_import", "export", "_export",
    "intern", "_intern", "interrupt", "haserrcode", "argpop", "noargpop", "lastclass",
    "reg", "noreg", "no_warn", "lock", "const", "unsigned", "signed",
}
KEYWORDS = {
    "if", "else", "while", "for", "do", "switch", "case", "default", "break", "return",
    "goto", "try", "catch", "throw", "sizeof", "offset", "class", "union", "asm",
    "start", "end", "defined", "lastclass", "TRUE", "FALSE", "NULL", "ON", "OFF",
} | MODIFIERS
CORE_DIRS = ("Kernel", "Compiler", "Adam", "Anokos", "")

KIND_FILE, KIND_FUNC, KIND_GLOBAL, KIND_CLASS, KIND_DEFINE, KIND_ASM = range(6)
KIND_NAMES = ["file", "function", "global", "class", "define", "asm"]
EDGE_CALL, EDGE_USE, EDGE_TYPE, EDGE_INHERIT, EDGE_INCLUDE, EDGE_RUN = range(6)
EDGE_NAMES = ["calls", "uses", "type", "inherits", "includes", "runs"]

TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|0[xX][0-9A-Fa-f]+|\d+(?:\.\d+)?|::|->|\+\+|--|<<|>>|&&|\|\||[^\sA-Za-z0-9_]")


# ---------------------------------------------------------------- lexing

def strip_text(text):
    """Blank out comments, strings, chars and DolDoc commands, keep line breaks.
    Returns (code, preproc) where preproc is a list of (line, directive_text)."""
    out = []
    pre = []
    i, n, line = 0, len(text), 1
    at_line_start = True
    while i < n:
        c = text[i]
        if c == "\n":
            out.append(c)
            line += 1
            i += 1
            at_line_start = True
            continue
        if at_line_start and c in " \t":
            out.append(c)
            i += 1
            continue
        if at_line_start and text.startswith("#exe", i):
            # #exe { code } runs at compile time: lex its body as normal code
            out.append("    ")
            i += 4
            at_line_start = False
            continue
        if at_line_start and c == "#":
            j = i
            buf = []
            while j < n and text[j] != "\n":
                if text[j] == "\\" and j + 1 < n and text[j + 1] == "\n":
                    buf.append(" ")
                    j += 2
                    out.append("\n")
                    continue
                buf.append(text[j])
                j += 1
            directive = "".join(buf)
            pre.append((line, directive))
            out.append(" " * len(directive))
            line += directive.count("\n")
            i = j
            at_line_start = False
            continue
        at_line_start = False
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                out.append(" ")
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            out.append("  ")
            while i < n and not (text[i] == "*" and i + 1 < n and text[i + 1] == "/"):
                out.append("\n" if text[i] == "\n" else " ")
                if text[i] == "\n":
                    line += 1
                i += 1
            i += 2
            out.append("  ")
            continue
        if c in "\"'":
            q = c
            out.append(" ")
            i += 1
            while i < n and text[i] != q:
                if text[i] == "\\":
                    i += 1
                if i < n:
                    out.append("\n" if text[i] == "\n" else " ")
                    if text[i] == "\n":
                        line += 1
                i += 1
            i += 1
            out.append(" ")
            continue
        if c == "$":
            # DolDoc command in code, e.g. $IB,"<1>",BI=1$; "$$" is a literal $
            j = text.find("$", i + 1)
            if j == -1:
                j = n - 1
            seg = text[i:j + 1]
            out.append(" " * len(seg.replace("\n", "")) + "\n" * seg.count("\n"))
            line += seg.count("\n")
            i = j + 1
            continue
        out.append(c)
        i += 1
    return "".join(out), pre


def tokenize(code):
    toks = []
    for ln, text in enumerate(code.split("\n"), 1):
        for m in TOKEN_RE.finditer(text):
            toks.append((m.group(0), ln))
    return toks


def is_ident(t):
    return t[0].isalpha() or t[0] == "_"


# ---------------------------------------------------------------- parsing

class FileInfo:
    def __init__(self, path):
        self.path = path
        self.defs = []        # (kind, name, line, extra dict)
        self.refs = []        # (owner_index_in_defs or -1 for file level, name, line, is_type)
        self.includes = []    # (line, target)
        self.inherit = []     # (class_def_index, base_name)


def split_top(tokens, sep=","):
    parts, cur, depth = [], [], 0
    for t in tokens:
        if t[0] in "([{":
            depth += 1
        elif t[0] in ")]}":
            depth -= 1
        if t[0] == sep and depth == 0:
            parts.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        parts.append(cur)
    return parts


def strip_modifiers(stmt):
    k = 0
    while k < len(stmt) and stmt[k][0] in MODIFIERS:
        k += 1
        if stmt[k - 1][0] in ("_extern", "_intern", "_import", "_export") and k < len(stmt):
            k += 1  # skip the asm label / intrinsic code after it
    return stmt[k:]


def starts_with_type(stmt, types):
    s = strip_modifiers(stmt)
    return bool(s) and s[0][0] in types and len(s) > 1 and (is_ident(s[1][0]) or s[1][0] in "*(")


def decl_names(stmt, types):
    """Names declared by `Type a,*b=1,c[3];` or `U0 (*fp)(...)`."""
    s = strip_modifiers(stmt)
    if not s or s[0][0] not in types:
        return []
    names = []
    for part in split_top(s[1:]):
        k = 0
        while k < len(part) and part[k][0] in ("*", "&"):
            k += 1
        if k < len(part) and part[k][0] == "(":
            k += 1
            while k < len(part) and part[k][0] == "*":
                k += 1
        if k < len(part) and is_ident(part[k][0]) and part[k][0] not in KEYWORDS:
            names.append(part[k])
    return names


def func_signature(stmt):
    """If stmt looks like `[mods] Type [*] Name(params)`, return (name_tok, params, ret_type)."""
    s = strip_modifiers(stmt)
    if len(s) < 3 or s[0][0] in KEYWORDS:
        return None
    for k in range(1, len(s) - 1):
        if s[k + 1][0] == "(" and is_ident(s[k][0]) and s[k][0] not in KEYWORDS:
            if any(t[0] not in ("*",) and not is_ident(t[0]) for t in s[1:k]):
                return None
            if s[k - 1][0] == "(":
                return None
            depth, j = 0, k + 1
            while j < len(s):
                if s[j][0] == "(":
                    depth += 1
                elif s[j][0] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            params = s[k + 2:j]
            return s[k], params, s[0][0]
        if s[k][0] == "(":
            return None
    return None


def param_names(params):
    names = []
    for part in split_top(params):
        if not part or part[0][0] == ".":
            continue
        cut = [t for t in part]
        for idx, t in enumerate(part):
            if t[0] == "=":
                cut = part[:idx]
                break
        fp = [cut[k + 2] for k in range(len(cut) - 2)
              if cut[k][0] == "(" and cut[k + 1][0] == "*" and is_ident(cut[k + 2][0])]
        ids = [t for t in cut if is_ident(t[0]) and t[0] not in KEYWORDS]
        if fp:
            names.append(fp[0])        # function pointer: Bool (*name)(...)
        elif len(ids) >= 2:
            names.append(ids[-1])
    return names


def parse_file(path, rel, types):
    raw = open(path, "rb").read()
    nul = raw.find(b"\0")
    if nul != -1:
        raw = raw[:nul]              # DolDoc binary trailer (sprites)
    text = raw.decode("latin-1").replace("\r\n", "\n")
    code, pre = strip_text(text)
    fi = FileInfo(rel)

    for line, d in pre:
        m = re.match(r"#define\s+([A-Za-z_]\w*)", d)
        if m:
            fi.defs.append((KIND_DEFINE, m.group(1), line, {"sig": d.strip()[:160]}))
        m = re.match(r'#include\s+"([^"]+)"', d)
        if m:
            fi.includes.append((line, m.group(1)))

    toks = tokenize(code)
    i, n = 0, len(toks)
    stmt = []

    def add_refs(owner, tokens, local):
        for k, (t, ln) in enumerate(tokens):
            if not is_ident(t) or t in KEYWORDS or t in local:
                continue
            if k and tokens[k - 1][0] in (".", "->"):
                continue
            if k + 1 < len(tokens) and tokens[k + 1][0] == ":" and owner >= 0 and \
                    fi.defs[owner][0] == KIND_FUNC and (k == 0 or tokens[k - 1][0] in (";", "{", "}")):
                continue  # goto label
            fi.refs.append((owner, t, ln, t in types))

    def block_end(start):
        depth, j = 0, start
        while j < n:
            if toks[j][0] == "{":
                depth += 1
            elif toks[j][0] == "}":
                depth -= 1
                if depth == 0:
                    return j
            j += 1
        return n - 1

    while i < n:
        t, ln = toks[i]
        if t == "{":
            body_end = block_end(i)
            body = toks[i + 1:body_end]
            s = strip_modifiers(stmt)
            if stmt and stmt[-1][0] == "asm":
                parse_asm(fi, body)
            elif s and s[0][0] in ("class", "union") and len(s) > 1 and is_ident(s[1][0]):
                parse_class(fi, s, body, types)
            elif stmt and "=" in [x[0] for x in stmt] and not func_signature(stmt):
                # initializer: continue the statement until ';'
                stmt.extend(toks[i:body_end + 1])
                i = body_end + 1
                continue
            else:
                sig = func_signature(stmt)
                if sig:
                    name, params, ret = sig
                    pnames = param_names(params)
                    sig_txt = " ".join(x[0] for x in strip_modifiers(stmt))
                    locals_ = parse_locals(body, types)
                    fi.defs.append((KIND_FUNC, name[0], name[1], {
                        "sig": tidy(sig_txt)[:200],
                        "params": [p[0] for p in pnames],
                        "locals": [[v[0], v[1]] for v in locals_],
                        "end": body[-1][1] if body else name[1],
                    }))
                    owner = len(fi.defs) - 1
                    local = {p[0] for p in pnames} | {v[0] for v in locals_}
                    add_refs(owner, [p for p in params], local)
                    add_refs(owner, [x for x in stmt if x is not name], local)
                    add_refs(owner, body, local)
                else:
                    add_refs(-1, stmt + body, set())   # top-level code block
            stmt = []
            i = body_end + 1
            continue
        if t == ";":
            handle_stmt(fi, stmt, types, add_refs)
            stmt = []
            i += 1
            continue
        if t == "}":
            i += 1
            continue
        stmt.append(toks[i])
        i += 1
    if stmt:
        handle_stmt(fi, stmt, types, add_refs)
    return fi


def tidy(s):
    s = re.sub(r"\s+([,)\]])", r"\1", s)
    s = re.sub(r"([(\[])\s+", r"\1", s)
    s = re.sub(r"\s*\*\s*", " *", s)
    s = re.sub(r"([A-Za-z0-9_])\s+\(", r"\1(", s)
    s = re.sub(r"\)\s+\(", ")(", s)
    return s.replace("( ", "(").replace(" = ", "=")


def handle_stmt(fi, stmt, types, add_refs):
    if not stmt:
        return
    s = strip_modifiers(stmt)
    sig = func_signature(stmt)
    kinds = {x[0] for x in stmt[:3]}
    if sig and ("_extern" in kinds or "_intern" in kinds):
        name = sig[0]
        label = None
        for k, x in enumerate(stmt):
            if x[0] == "_extern" and k + 1 < len(stmt):
                label = stmt[k + 1][0]
        fi.defs.append((KIND_FUNC, name[0], name[1], {
            "sig": tidy(" ".join(x[0] for x in s))[:200],
            "params": [p[0] for p in param_names(sig[1])],
            "locals": [], "decl": "intrinsic" if "_intern" in kinds else "asm",
            "label": label}))
        return
    if sig:
        return  # prototype / extern declaration; the definition is elsewhere
    if starts_with_type(stmt, types):
        names = decl_names(stmt, types)
        label = None
        if stmt[0][0] == "_extern" or (len(stmt) > 1 and stmt[1][0] == "_extern"):
            for k, x in enumerate(stmt):
                if x[0] == "_extern" and k + 1 < len(stmt):
                    label = stmt[k + 1][0]
        is_extern = any(x[0] in ("extern", "import", "_import") for x in stmt[:2])
        if not is_extern:
            for nm in names:
                fi.defs.append((KIND_GLOBAL, nm[0], nm[1], {
                    "sig": tidy(" ".join(x[0] for x in s))[:200], "type": s[0][0],
                    "label": label}))
        if s[0][0] in types and s[0][0] not in BUILTIN_TYPES:
            fi.refs.append((len(fi.defs) - 1 if names and not is_extern else -1, s[0][0], s[0][1], True))
        # initializer references belong to the file
        add_refs(-1, [x for x in s[1:] if x not in names], {n[0] for n in names})
        return
    add_refs(-1, stmt, set())


def parse_locals(body, types):
    locals_ = []
    stmt = []
    for t in body:
        if t[0] in (";", "{", "}"):
            if stmt and starts_with_type(stmt, types) and not func_signature(stmt):
                locals_.extend(decl_names(stmt, types))
            stmt = []
        else:
            stmt.append(t)
    return locals_


def parse_class(fi, s, body, types):
    name = s[1]
    base = None
    if len(s) > 3 and s[2][0] == ":" and is_ident(s[3][0]):
        base = s[3][0]
    members = []
    stmt = []
    for t in body:
        if t[0] in (";", "{", "}"):
            if stmt:
                members.extend(decl_names(stmt, types))
                ms = strip_modifiers(stmt)
                if ms and ms[0][0] in types and ms[0][0] not in BUILTIN_TYPES:
                    fi.refs.append((len(fi.defs), ms[0][0], ms[0][1], True))
            stmt = []
        else:
            stmt.append(t)
    fi.defs.append((KIND_CLASS, name[0], name[1], {
        "sig": " ".join(x[0] for x in s[:4] if x[0] != "{"),
        "members": [[m[0], m[1]] for m in members], "base": base}))
    if base:
        fi.inherit.append((len(fi.defs) - 1, base))


def parse_asm(fi, body):
    owner = -1
    for k, (t, ln) in enumerate(body):
        if is_ident(t) and k + 1 < len(body) and body[k + 1][0] == "::":
            fi.defs.append((KIND_ASM, t, ln, {"sig": t + "::"}))
            owner = len(fi.defs) - 1
        elif is_ident(t) and t.isupper() is False and k and body[k - 1][0] in ("&",):
            fi.refs.append((owner, t, ln, False))
        elif is_ident(t) and (k == 0 or body[k - 1][0] not in (".",)):
            fi.refs.append((owner, t, ln, False))


def readable_source(path):
    """Source text as TempleOS shows it: DolDoc commands replaced by their
    visible text, "$$" as "$", tabs expanded. Line count is preserved."""
    raw = open(path, "rb").read().split(b"\0")[0].decode("latin-1").replace("\r\n", "\n")
    out, i, n = [], 0, len(raw)
    while i < n:
        c = raw[i]
        if c != "$":
            out.append(c)
            i += 1
            continue
        j = raw.find("$", i + 1)
        if j == -1:
            out.append(raw[i:])
            break
        cmd = raw[i + 1:j]
        if not cmd:
            out.append("$")
        else:
            m = re.search(r'"((?:[^"\\]|\\.)*)"', cmd)
            if cmd[:2].upper() in DOLDOC_TEXT and m:
                out.append(m.group(1).replace('\\"', '"'))
            out.append("\n" * cmd.count("\n"))
        i = j + 1
    return "".join(out).expandtabs(8)


# ---------------------------------------------------------------- graph

def collect_types(paths):
    types = set(BUILTIN_TYPES)
    for p in paths:
        raw = open(p, "rb").read().split(b"\0")[0].decode("latin-1")
        for m in re.finditer(r"\b(?:class|union)\s+([A-Za-z_]\w*)", raw):
            types.add(m.group(1))
    return types


def fib_sphere(count, radius, center, seed=0.0):
    pts = []
    if count == 1:
        return [center]
    golden = math.pi * (3 - math.sqrt(5))
    for k in range(count):
        y = 1 - (k / (count - 1)) * 2
        r = math.sqrt(max(0.0, 1 - y * y))
        th = golden * k + seed
        pts.append((center[0] + radius * math.cos(th) * r,
                    center[1] + radius * y,
                    center[2] + radius * math.sin(th) * r))
    return pts


def main():
    paths = []
    for dp, dn, fn in os.walk(SRC):
        dn.sort()
        for f in sorted(fn):
            if f.upper().endswith(EXTS):
                paths.append(os.path.join(dp, f))
    types = collect_types(paths)
    files = []
    for p in paths:
        rel = os.path.relpath(p, SRC).replace("\\", "/")
        files.append(parse_file(p, rel, types))

    # ---- nodes
    nodes = []          # [kind, name, file_idx, line]
    details = []
    by_name = defaultdict(list)
    def_node = []       # per file: list mapping def index -> node index
    for fidx, fi in enumerate(files):
        nodes.append([KIND_FILE, fi.path.split("/")[-1], fidx, 1])
        details.append({"sig": fi.path})
        mapping = []
        for kind, name, line, extra in fi.defs:
            mapping.append(len(nodes))
            nodes.append([kind, name, fidx, line])
            details.append(extra)
            by_name[name].append(len(nodes) - 1)
        def_node.append(mapping)

    # prefer real definitions over _extern/_intern declarations
    def rank(ni, ref_file):
        kind, name, fidx, line = nodes[ni]
        path = files[fidx].path
        top = path.split("/")[0] if "/" in path else ""
        ref_top = files[ref_file].path.split("/")[0] if "/" in files[ref_file].path else ""
        score = 0
        if fidx == ref_file:
            score += 100
        elif top == ref_top:
            score += 50
        elif top in CORE_DIRS:
            score += 25
        if details[ni].get("decl"):
            score -= 5
        if kind == KIND_DEFINE:
            score -= 1
        return score

    def resolve(name, ref_file):
        cands = by_name.get(name)
        if not cands:
            return None
        if len(cands) == 1:
            return cands[0]
        best = max(cands, key=lambda c: rank(c, ref_file))
        top_score = rank(best, ref_file)
        if top_score < 25 and len({nodes[c][2] for c in cands}) > 1:
            return None  # ambiguous: e.g. several demos each define Main
        return best

    file_node = {fi.path: k for k, fi in enumerate(files)}
    file_node_idx = {}
    for ni, nd in enumerate(nodes):
        if nd[0] == KIND_FILE:
            file_node_idx[nd[2]] = ni

    edges = set()
    for fidx, fi in enumerate(files):
        for owner, name, line, is_type in fi.refs:
            src = def_node[fidx][owner] if owner >= 0 else file_node_idx[fidx]
            dst = resolve(name, fidx)
            if dst is None or dst == src:
                continue
            dk = nodes[dst][0]
            sk = nodes[src][0]
            if dk == KIND_FILE:
                continue
            if dk == KIND_CLASS:
                et = EDGE_TYPE
            elif sk == KIND_FILE:
                et = EDGE_RUN
            elif dk == KIND_FUNC or (dk == KIND_ASM and sk == KIND_FUNC):
                et = EDGE_CALL
            else:
                et = EDGE_USE
            edges.add((src, dst, et))
        for cidx, base in fi.inherit:
            dst = resolve(base, fidx)
            if dst is not None:
                edges.add((def_node[fidx][cidx], dst, EDGE_INHERIT))
        # includes, resolved relative to the including file's folder or src root
        here = os.path.dirname(fi.path)
        for line, target in fi.includes:
            tgt = target.replace("::/", "/").replace("~/", "")
            base = tgt.lstrip("/") if tgt.startswith("/") else os.path.normpath(os.path.join(here, tgt)).replace("\\", "/")
            for ext in ("", ".TC", ".TH", ".HC", ".HH"):
                if base + ext in file_node:
                    edges.add((file_node_idx[fidx], file_node_idx[file_node[base + ext]], EDGE_INCLUDE))
                    break

    # ---- layout: folders on a big sphere, files around their folder, symbols around their file
    dirs = sorted({fi.path.rsplit("/", 1)[0] if "/" in fi.path else "(root)" for fi in files})
    tops = sorted({d.split("/")[0] for d in dirs}, key=lambda d: (d != "Kernel", d != "Compiler", d))
    top_pos = dict(zip(tops, fib_sphere(len(tops), 2400, (0, 0, 0))))
    dir_pos = {}
    for top in tops:
        subs = [d for d in dirs if d.split("/")[0] == top]
        for d, p in zip(subs, fib_sphere(len(subs), 260 + 160 * math.sqrt(len(subs)), top_pos[top], 1.3)):
            dir_pos[d] = p
    pos = [None] * len(nodes)
    for d in dirs:
        fids = [k for k, fi in enumerate(files)
                if (fi.path.rsplit("/", 1)[0] if "/" in fi.path else "(root)") == d]
        fr = 70 + 55 * math.sqrt(len(fids))
        for fidx, fp in zip(fids, fib_sphere(len(fids), fr, dir_pos[d], 0.7)):
            syms = [m for m in def_node[fidx]]
            pos[file_node_idx[fidx]] = fp
            sr = 8 + 4.5 * math.sqrt(len(syms))
            for ni, sp in zip(syms, fib_sphere(len(syms), sr, fp, fidx * 0.37)):
                pos[ni] = sp

    for ni, nd in enumerate(nodes):
        x, y, z = pos[ni]
        nd.extend([round(x, 1), round(y, 1), round(z, 1)])

    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                                capture_output=True, text=True).stdout.strip()
    except OSError:
        commit = ""
    counts = defaultdict(int)
    for nd in nodes:
        counts[KIND_NAMES[nd[0]]] += 1
    n_locals = sum(len(d.get("locals", [])) for d in details)
    n_members = sum(len(d.get("members", [])) for d in details)
    data = {
        "meta": {"commit": commit, "files": len(files), "counts": counts,
                 "lines": sum(readable_source(os.path.join(SRC, fi.path)).count("\n") + 1 for fi in files),
                 "locals": n_locals, "members": n_members, "edges": len(edges),
                 "repo": "https://github.com/LandonDevAnokos/Anokos/blob/main/src/"},
        "kinds": KIND_NAMES,
        "edgeKinds": EDGE_NAMES,
        "files": [fi.path for fi in files],
        "dirs": [[d] + [round(v, 1) for v in dir_pos[d]] for d in dirs],
        "tops": [[t] + [round(v, 1) for v in top_pos[t]] for t in tops],
        "nodes": nodes,
        "edges": sorted(edges),
        "details": [{k: v for k, v in d.items() if v not in (None, [], "")} for d in details],
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))
    with open(OUT_SRC, "w", encoding="utf-8") as f:
        json.dump({"files": [fi.path for fi in files],
                   "text": [readable_source(os.path.join(SRC, fi.path)) for fi in files]},
                  f, separators=(",", ":"), ensure_ascii=False)
    print(f"{OUT_SRC}: {os.path.getsize(OUT_SRC)//1024} KB")
    print(f"{OUT}: {os.path.getsize(OUT)//1024} KB, {len(nodes)} nodes, {len(edges)} edges, "
          f"{n_locals} locals, {n_members} members; {dict(counts)}")


if __name__ == "__main__":
    sys.exit(main())
