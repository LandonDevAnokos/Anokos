"""Anokos build driver.

TerryC can only be compiled by its own compiler, which runs inside the OS.
So the compiler and kernel are bootstrapped in an x86-64 virtual machine,
either QEMU (any host, including Apple Silicon Macs) or VirtualBox (x86 hosts):

  stage 1  Boot stock TempleOS (from TempleOS/TempleOS.ISO) with the Anokos
           compiler and kernel sources added (under TempleOS's .HC/.HH names).
           It compiles src/Compiler and src/Kernel and sends both binaries
           back over the VM's serial port.
  stage 2  Build Anokos.ISO from src/ with the new compiler and kernel.

Usage (from the repository root):
  python tools/anokos_build.py kernel      # stage 1, needs QEMU or VirtualBox
  python tools/anokos_build.py iso         # stage 2
  python tools/anokos_build.py all         # kernel + iso
  python tools/anokos_build.py run [iso]   # boot an ISO in the test VM and screenshot it
  python tools/anokos_build.py arm64       # cross-compile the ARM64 kernel inside Anokos
  python tools/anokos_build.py run-arm64   # boot build/arm64/kernel8.img in qemu-system-aarch64

ARM64 (stage 3): boots x86-64 Anokos (with the compiler and kernel from
stage 1) in QEMU, runs tools/stage3/Arm64Build.TC there, which compiles
src/Kernel/KernelArm64.PRJ with the Anokos compiler's ARM64 back end, and
receives the boot image over the serial port as build/arm64/kernel8.img.

Options:
  --update-src   after `kernel`, copy the new kernel binary and map into src/
  --vm-backend   qemu or vbox (default: VirtualBox on x86 hosts that have it,
                 otherwise QEMU)
  --vm NAME      VirtualBox VM to use (created if missing, default AnokosBuild)
  --headless     `run`/`run-arm64` without a display window (QEMU)
  --board NAME   `run-arm64` machine: virt (default), rpi3 or rpi4
  --timeout SEC  stage-1 time limit (default 900)
"""
import argparse
import os
import platform
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
import qemu_util          # noqa: E402

QEMU_SOCK = os.path.join(BUILD, "qemu-x86_64.sock")


def pick_backend(choice):
    if choice != "auto":
        return choice
    has_vbox = shutil.which("VBoxManage") or os.path.exists(
        r"C:\Program Files\Oracle\VirtualBox\VBoxManage.exe")
    if has_vbox and platform.machine().lower() in ("x86_64", "amd64"):
        return "vbox"
    return "qemu"


def qemu_x86_args(iso, serial_log, mem=2048):
    """An x86-64 PC that TempleOS/Anokos is happy with: i440FX + PIIX IDE
    (the CD on the secondary master, like the VirtualBox VM), PS/2 input and
    a standard VGA with VBE modes up to 1920x1080."""
    args = ["-machine", "pc", "-cpu", "qemu64", "-m", str(mem), "-smp", "2",
            "-cdrom", os.path.abspath(iso), "-boot", "d", "-no-reboot",
            "-device", "VGA,vgamem_mb=32,xres=1920,yres=1080",
            "-serial", f"file:{serial_log}"]
    if platform.system() == "Linux" and os.access("/dev/kvm", os.R_OK | os.W_OK) and \
            platform.machine().lower() in ("x86_64", "amd64"):
        args += ["-accel", "kvm"]
    return args


def wait_serial(serial_log, marker, timeout, alive=lambda: True, fail_marker=None):
    """Poll a serial log until `marker` (or `fail_marker`) shows up.
    Returns the log text, or None on timeout."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(3)
        if os.path.exists(serial_log):
            data = open(serial_log, "rb").read().decode("latin-1")
            if marker in data or (fail_marker and fail_marker in data):
                return data
        if not alive():
            break
    return None


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


def stage1(vm, timeout, update_src, backend):
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
    if os.path.exists(serial_log):
        os.remove(serial_log)
    print(f"stage 1: compiling the compiler and kernel inside TempleOS ({backend})...")
    t0 = time.time()
    if backend == "qemu":
        vm_ = qemu_util.QemuVM(qemu_util.require_qemu("x86_64"),
                               qemu_x86_args(iso, serial_log), QEMU_SOCK,
                               log=os.path.join(BUILD, "stage1_qemu.log"))
        try:
            data = wait_serial(serial_log, "@@STAGE1 DONE", timeout, vm_.running)
            if data is None:
                shot = os.path.join(BUILD, "stage1_timeout.png")
                if vm_.running():
                    vm_.screenshot_png(shot)
                raise SystemExit(f"stage 1 timed out; see {serial_log} and {shot}")
        finally:
            vm_.quit()
    else:
        ensure_vm(vm, serial_log)
        attach_iso(vm, iso)
        vbox("startvm", vm, "--type", "headless")
        try:
            data = wait_serial(serial_log, "@@STAGE1 DONE", timeout)
            if data is None:
                shot = os.path.join(BUILD, "stage1_timeout.png")
                vbox("controlvm", vm, "screenshotpng", shot, check=False)
                raise SystemExit(f"stage 1 timed out; see {serial_log} and {shot}")
        finally:
            vbox("controlvm", vm, "poweroff", check=False)
    print(f"stage 1 finished in {time.time() - t0:.0f}s")

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


def stage2(out_iso, tree=None, extra=None):
    tree = tree or os.path.join(BUILD, "stage2_tree")
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
    if extra:
        extra(tree)
    build_iso.build(tree, out_iso, REF_ISO, KERNEL_REL.replace(os.sep, "/"))


ARM64_DIR = os.path.join(BUILD, "arm64")


def arm64_hook(tree):
    """Make the ISO run the ARM64 cross build at the end of start-up."""
    shutil.copy(os.path.join(HERE, "stage3", "Arm64Build.TC"), tree)
    home_sys = os.path.join(tree, "HomeSys.TC")
    data = open(home_sys, "rb").read()
    open(home_sys, "wb").write(data + b"\n#include \"::/Arm64Build\"\n")


def serial_files(data):
    files = {}
    for tag, size, body in re.findall(r"@@BEGIN (\w+) (\d+)\n(.*?)\n@@END \1", data, re.S):
        raw = bytes.fromhex(re.sub(r"\s", "", body))
        if len(raw) != int(size):
            raise SystemExit(f"{tag}: got {len(raw)} bytes, expected {size}")
        files[tag] = raw
    return files


def arm64(timeout):
    """Stage 3: compile the ARM64 kernel inside x86-64 Anokos."""
    os.makedirs(ARM64_DIR, exist_ok=True)
    iso = os.path.join(BUILD, "arm64build.ISO")
    stage2(iso, os.path.join(BUILD, "stage3_tree"), arm64_hook)
    serial_log = os.path.join(BUILD, "arm64_serial.log")
    if os.path.exists(serial_log):
        os.remove(serial_log)
    print("stage 3: compiling the ARM64 kernel inside Anokos (qemu)...")
    t0 = time.time()
    vm_ = qemu_util.QemuVM(qemu_util.require_qemu("x86_64"),
                           qemu_x86_args(iso, serial_log), QEMU_SOCK,
                           log=os.path.join(BUILD, "arm64build_qemu.log"))
    try:
        data = wait_serial(serial_log, "@@ARM64 DONE", timeout, vm_.running,
                           fail_marker="TempleOS Debugger")
        if data is not None and "@@ARM64 DONE" not in data:
            time.sleep(2)
            vm_.quit()
            raise SystemExit(f"ARM64 build stopped in the debugger; see {serial_log}")
        if data is None:
            shot = os.path.join(BUILD, "arm64build_timeout.png")
            if vm_.running():
                vm_.screenshot_png(shot)
            raise SystemExit(f"ARM64 build timed out; see {serial_log} and {shot}")
    finally:
        vm_.quit()
    print(f"stage 3 finished in {time.time() - t0:.0f}s")
    m = re.search(r"@@ERRORS (-?\d+)", data)
    errs = int(m.group(1)) if m else -1
    if errs:
        raise SystemExit(f"ARM64 kernel compile failed ({errs} errors); see {serial_log}")
    files = serial_files(data)
    if "IMAGE" not in files:
        raise SystemExit(f"ARM64 image missing from {serial_log}")
    img = os.path.join(ARM64_DIR, "kernel8.img")
    open(img, "wb").write(files["IMAGE"])
    print(f"ARM64 image: {img} ({len(files['IMAGE'])} bytes)")
    if "MAP" in files:
        open(os.path.join(ARM64_DIR, "KernelArm64.MAP.Z"), "wb").write(files["MAP"])


ARM64_MACHINES = {
    # name: (QEMU machine args, RAM)
    "virt": (["-machine", "virt", "-cpu", "cortex-a72"], "1G"),
    "rpi3": (["-machine", "raspi3b"], "1G"),
    "rpi4": (["-machine", "raspi4b"], "2G"),
}


def send_serial_keys(ser_sock, keys):
    """Type `keys` on the ARM64 serial console (the kernel's keyboard)."""
    import socket
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(ser_sock)
    s.setblocking(False)
    for ch in keys.encode("latin-1"):
        s.send(bytes([ch]))
        time.sleep(0.12 if ch == 13 else 0.03)
        try:
            while s.recv(4096):
                pass
        except BlockingIOError:
            pass
    time.sleep(1.5)
    s.close()


def run_arm64(board, headless, wait, img=None, keys=None):
    img = img or os.path.join(ARM64_DIR, "kernel8.img")
    if not os.path.exists(img):
        raise SystemExit(f"{img} not found; run `python tools/anokos_build.py arm64` first")
    mach, mem = ARM64_MACHINES[board]
    serial_log = os.path.join(ARM64_DIR, f"run_{board}_serial.log")
    shot = os.path.join(ARM64_DIR, f"run_{board}.png")
    # The serial port is a socket (type into it: it's the keyboard) that
    # also logs everything to serial_log.
    ser_sock = os.path.join(BUILD, f"arm64-{board}-serial.sock")
    for f in (serial_log, ser_sock):
        if os.path.exists(f):
            os.remove(f)
    args = [*mach, "-m", mem, "-kernel", os.path.abspath(img),
            "-chardev", f"socket,id=ser0,path={ser_sock},server=on,wait=off,logfile={serial_log}",
            "-serial", "chardev:ser0"]
    if board == "virt":
        args += ["-device", "ramfb"]
    sock = os.path.join(BUILD, "qemu-aarch64.sock")
    vm_ = qemu_util.QemuVM(qemu_util.require_qemu("aarch64"), args, sock,
                           log=os.path.join(ARM64_DIR, "run_qemu.log"), display=not headless)
    data = wait_serial(serial_log, "@@ARM64 BOOT OK", wait, vm_.running) or ""
    if data and vm_.running():
        time.sleep(1.5)  # terminal start
        if keys:
            send_serial_keys(ser_sock, keys.encode().decode("unicode_escape"))
        vm_.screenshot_png(shot)
        print(f"screenshot: {shot}")
    if os.path.exists(serial_log):
        data = open(serial_log, "rb").read().decode("latin-1")
    print(data.replace("\r", ""))
    if headless:
        vm_.quit()
    else:
        print(f"QEMU left running; serial console: {ser_sock} (e.g. `nc -U {ser_sock}`), "
              f"log {serial_log}")
    if "@@ARM64 BOOT OK" not in data:
        raise SystemExit("ARM64 kernel did not report a successful boot")


def run(vm, iso, wait, backend, headless):
    serial_log = os.path.join(BUILD, "run_serial.log")
    shot = os.path.join(BUILD, "run.png")
    if backend == "qemu":
        vm_ = qemu_util.QemuVM(qemu_util.require_qemu("x86_64"),
                               qemu_x86_args(iso, serial_log), QEMU_SOCK,
                               log=os.path.join(BUILD, "run_qemu.log"), display=not headless)
        time.sleep(wait)
        if vm_.running():
            vm_.screenshot_png(shot)
            print(f"screenshot: {shot} (QEMU left running; close its window or "
                  f"`echo quit | nc -U {QEMU_SOCK}` to stop it)")
        else:
            print("QEMU exited; see build/run_qemu.log")
        return
    ensure_vm(vm, serial_log)
    attach_iso(vm, iso)
    vbox("startvm", vm, "--type", "headless")
    time.sleep(wait)
    vbox("controlvm", vm, "screenshotpng", shot)
    print(f"screenshot: {shot} (VM '{vm}' left running)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["kernel", "iso", "all", "run", "arm64", "run-arm64"])
    ap.add_argument("iso", nargs="?", default=os.path.join(BUILD, "Anokos.ISO"))
    ap.add_argument("--vm", default="AnokosBuild")
    ap.add_argument("--timeout", type=int, default=2400)
    ap.add_argument("--wait", type=int, default=40, help="seconds before the `run` screenshot")
    ap.add_argument("--update-src", action="store_true")
    ap.add_argument("--vm-backend", choices=["auto", "qemu", "vbox"], default="auto")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--board", choices=sorted(ARM64_MACHINES), default="virt")
    ap.add_argument("--keys", help="run-arm64: type this on the serial console after boot "
                    "(Python escapes, e.g. 'Mem;\\r'), then screenshot")
    a = ap.parse_args()
    os.makedirs(BUILD, exist_ok=True)
    backend = pick_backend(a.vm_backend)
    if a.cmd in ("kernel", "all"):
        stage1(a.vm, a.timeout, a.update_src, backend)
    if a.cmd in ("iso", "all"):
        stage2(a.iso)
    if a.cmd == "run":
        run(a.vm, a.iso, a.wait, backend, a.headless)
    if a.cmd == "arm64":
        arm64(a.timeout)
    if a.cmd == "run-arm64":
        run_arm64(a.board, a.headless, a.wait, keys=a.keys)


if __name__ == "__main__":
    main()
