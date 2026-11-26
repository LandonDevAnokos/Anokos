"""Anokos build driver.

TerryC can only be compiled by its own compiler, which runs inside the OS.
So the compiler and kernel are bootstrapped in a VirtualBox VM:

  stage 1  Boot stock TempleOS (from TempleOS/TempleOS.ISO) with the Anokos
           compiler and kernel sources added (under TempleOS's .HC/.HH names).
           It compiles src/Compiler and src/Kernel and sends both binaries
           back over the VM's serial port.
  stage 2  Build Anokos.ISO from src/ with the new compiler and kernel.

Usage (from the repository root):
  python tools/anokos_build.py kernel      # stage 1, needs VirtualBox
  python tools/anokos_build.py iso         # stage 2
  python tools/anokos_build.py all         # kernel + iso
  python tools/anokos_build.py run [iso]   # boot an ISO in the test VM and screenshot it

Options:
  --update-src   after `kernel`, copy the new kernel binary and map into src/
  --vm NAME      VirtualBox VM to use (created if missing, default AnokosBuild)
  --timeout SEC  stage-1 time limit (default 900)
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
BUILD = os.path.join(ROOT, "build")
REF_ISO = os.path.join(ROOT, "TempleOS", "TempleOS.ISO")
KERNEL_REL = os.path.join("0000Boot", "0000Kernel.BIN.C")

sys.path.insert(0, HERE)
import build_iso          # noqa: E402
import redsea_extract     # noqa: E402


def vboxmanage():
    for p in (shutil.which("VBoxManage"),
              r"C:\Program Files\Oracle\VirtualBox\VBoxManage.exe"):
        if p and os.path.exists(p):
            return p
    raise SystemExit("VBoxManage not found; install VirtualBox")


def vbox(*args, check=True):
    r = subprocess.run([vboxmanage(), *args], capture_output=True, text=True)
    if check and r.returncode:
        raise SystemExit(f"VBoxManage {' '.join(args)} failed:\n{r.stderr}")
    return r


def vm_running(vm):
    r = vbox("showvminfo", vm, "--machinereadable", check=False)
    return 'VMState="running"' in r.stdout


def ensure_vm(vm, serial_log):
    if vbox("showvminfo", vm, check=False).returncode:
        vbox("createvm", "--name", vm, "--ostype", "Other_64",
             "--basefolder", os.path.join(BUILD, "vm"), "--register")
        vbox("storagectl", vm, "--name", "IDE", "--add", "ide", "--controller", "PIIX4")
    if vm_running(vm):
        vbox("controlvm", vm, "poweroff", check=False)
        time.sleep(3)
    vbox("modifyvm", vm, "--memory", "2048", "--cpus", "2", "--ioapic", "on",
         "--vram", "64", "--graphicscontroller", "vboxvga",
         "--boot1", "dvd", "--boot2", "none", "--boot3", "none", "--boot4", "none",
         "--audio-enabled", "off", "--usb", "off", "--mouse", "ps2", "--keyboard", "ps2",
         "--uart1", "0x3F8", "4", "--uart-mode1", "file", serial_log)


def attach_iso(vm, iso):
    vbox("storageattach", vm, "--storagectl", "IDE", "--port", "1", "--device", "0",
         "--type", "dvddrive", "--medium", os.path.abspath(iso))


def stock_tree():
    """The original TempleOS tree, extracted (raw) from the reference ISO."""
    out = os.path.join(BUILD, "stock")
    if not os.path.isdir(out):
        img = open(REF_ISO, "rb").read()
        boot = redsea_extract.find_boot(img)
        root = boot["root_clus"]
        size = next(redsea_extract.read_dir(img, root, 64))[3]
        os.makedirs(out)
        log = []
        redsea_extract.walk(img, root, size, "", out, False, log)
    return out


def copytree(src, dst):
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".gitkeep"), dirs_exist_ok=True)


# Stock TempleOS (stage 1) only knows .HC/.HH source files.
LEGACY_EXT = {".TC": ".HC", ".TH": ".HH"}
LEGACY_INCLUDE = re.compile(rb'(#include\s+"[^"\n]*?)\.(TC|TH)"')
TC_PATH = re.compile(r"(?<=[A-Za-z0-9_*~])\.(HC|HH)(?![A-Za-z0-9_])")


def copy_as_legacy(src, dst):
    """Copy TerryC sources under TempleOS names: .TC/.TH files become .HC/.HH and
    #include lines that name a .TC/.TH file are pointed at the renamed file."""
    for dp, dn, fn in os.walk(src):
        for f in fn:
            if f == ".gitkeep":
                continue
            base, ext = os.path.splitext(f)
            rel = os.path.relpath(os.path.join(dp, base + LEGACY_EXT.get(ext, ext)), src)
            out = os.path.join(dst, rel)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            data = open(os.path.join(dp, f), "rb").read()
            if ext in LEGACY_EXT or ext == ".PRJ":
                data = LEGACY_INCLUDE.sub(lambda m: m.group(1) + (b".HC\"" if m.group(2) == b"TC" else b".HH\""), data)
            open(out, "wb").write(data)


def map_to_terryc(raw):
    """Maps built in stage 1 point at the legacy .HC/.HH copies; point them at .TC/.TH."""
    text = raw.decode("latin-1")
    return TC_PATH.sub(lambda m: ".TC" if m.group(1) == "HC" else ".TH", text).encode("latin-1")


def stage1(vm, timeout, update_src):
    tree = os.path.join(BUILD, "stage1_tree")
    shutil.rmtree(tree, ignore_errors=True)
    copytree(stock_tree(), tree)
    copy_as_legacy(os.path.join(SRC, "Kernel"), os.path.join(tree, "AnokosSrc", "Kernel"))
    copy_as_legacy(os.path.join(SRC, "Compiler"), os.path.join(tree, "AnokosSrc", "Compiler"))
    shutil.copy(os.path.join(HERE, "stage1", "AnokosBuild.HC"), tree)
    shutil.copy(os.path.join(HERE, "stage1", "HomeSys.HC"), tree)
    iso = os.path.join(BUILD, "stage1.iso")
    build_iso.build(tree, iso, REF_ISO, KERNEL_REL.replace(os.sep, "/"))

    serial_log = os.path.join(BUILD, "stage1_serial.log")
    ensure_vm(vm, serial_log)
    if os.path.exists(serial_log):
        os.remove(serial_log)
    attach_iso(vm, iso)
    print("stage 1: compiling the compiler and kernel inside TempleOS...")
    vbox("startvm", vm, "--type", "headless")
    t0 = time.time()
    data = ""
    try:
        while time.time() - t0 < timeout:
            time.sleep(3)
            if os.path.exists(serial_log):
                data = open(serial_log, "rb").read().decode("latin-1")
                if "@@STAGE1 DONE" in data:
                    break
        else:
            shot = os.path.join(BUILD, "stage1_timeout.png")
            vbox("controlvm", vm, "screenshotpng", shot, check=False)
            raise SystemExit(f"stage 1 timed out; see {serial_log} and {shot}")
    finally:
        vbox("controlvm", vm, "poweroff", check=False)

    m = re.search(r"@@ERRORS (-?\d+)", data)
    errs = int(m.group(1)) if m else -1
    if errs:
        raise SystemExit(f"kernel compile failed ({errs} errors); see {serial_log}")

    outdir = os.path.join(BUILD, "kernel")
    os.makedirs(outdir, exist_ok=True)
    files = {}
    for tag, size, body in re.findall(r"@@BEGIN (\w+) (\d+)\n(.*?)\n@@END \1", data, re.S):
        raw = bytes.fromhex(re.sub(r"\s", "", body))
        if len(raw) != int(size):
            raise SystemExit(f"{tag}: got {len(raw)} bytes, expected {size}")
        files[tag] = raw
    for tag in ("KERNEL", "COMPILER"):
        if tag not in files:
            raise SystemExit(f"{tag.lower()} binary missing from serial log")
    outputs = {  # serial tag -> (build/kernel name, path in src/)
        "KERNEL": ("Kernel.BIN.C", KERNEL_REL),
        "MAP": ("Kernel.MAP", os.path.join("Kernel", "Kernel.MAP")),
        "COMPILER": ("Compiler.BIN", os.path.join("Compiler", "Compiler.BIN")),
        "CMAP": ("Compiler.MAP", os.path.join("Compiler", "Compiler.MAP")),
    }
    for tag, (name, src_rel) in outputs.items():
        if tag not in files:
            continue
        raw = map_to_terryc(files[tag]) if name.endswith(".MAP") else files[tag]
        out = os.path.join(outdir, name)
        open(out, "wb").write(raw)
        print(f"{name}: {out} ({len(raw)} bytes)")
        if update_src:
            open(os.path.join(SRC, src_rel), "wb").write(raw)
    if update_src:
        print("updated the kernel and compiler binaries and maps in src/")


def stage2(out_iso):
    tree = os.path.join(BUILD, "stage2_tree")
    shutil.rmtree(tree, ignore_errors=True)
    copytree(SRC, tree)
    built = os.path.join(BUILD, "kernel")
    for name, rel in (("Kernel.BIN.C", KERNEL_REL), ("Kernel.MAP", os.path.join("Kernel", "Kernel.MAP")),
                      ("Compiler.BIN", os.path.join("Compiler", "Compiler.BIN")),
                      ("Compiler.MAP", os.path.join("Compiler", "Compiler.MAP"))):
        if os.path.exists(os.path.join(built, name)):
            shutil.copy(os.path.join(built, name), os.path.join(tree, rel))
        else:
            print(f"note: no build/kernel/{name}, using src/{rel}")
    build_iso.build(tree, out_iso, REF_ISO, KERNEL_REL.replace(os.sep, "/"))


def run(vm, iso, wait):
    serial_log = os.path.join(BUILD, "run_serial.log")
    ensure_vm(vm, serial_log)
    attach_iso(vm, iso)
    vbox("startvm", vm, "--type", "headless")
    time.sleep(wait)
    shot = os.path.join(BUILD, "run.png")
    vbox("controlvm", vm, "screenshotpng", shot)
    print(f"screenshot: {shot} (VM '{vm}' left running)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["kernel", "iso", "all", "run"])
    ap.add_argument("iso", nargs="?", default=os.path.join(BUILD, "Anokos.ISO"))
    ap.add_argument("--vm", default="AnokosBuild")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--wait", type=int, default=40, help="seconds before the `run` screenshot")
    ap.add_argument("--update-src", action="store_true")
    a = ap.parse_args()
    os.makedirs(BUILD, exist_ok=True)
    if a.cmd in ("kernel", "all"):
        stage1(a.vm, a.timeout, a.update_src)
    if a.cmd in ("iso", "all"):
        stage2(a.iso)
    if a.cmd == "run":
        run(a.vm, a.iso, a.wait)


if __name__ == "__main__":
    main()
