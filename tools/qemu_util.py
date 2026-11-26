"""QEMU helpers shared by the Anokos build tools.

Finds the QEMU binaries, starts VMs with a monitor socket, sends monitor
commands (screendump, quit) and converts QEMU's PPM screenshots to PNG
without any third-party Python packages.
"""
import os
import shutil
import socket
import struct
import subprocess
import time
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# build/qemu is where tools/build_qemu.sh installs a QEMU built from source
# (needed on macOS versions Homebrew no longer has QEMU packages for).
SEARCH_DIRS = (os.path.join(ROOT, "build", "qemu", "bin"), "/opt/homebrew/bin", "/usr/local/bin")


def find_qemu(arch):
    """Path of qemu-system-<arch>, or None."""
    name = f"qemu-system-{arch}"
    local = os.path.join(SEARCH_DIRS[0], name)
    if os.path.exists(local):
        return local
    p = shutil.which(name)
    if p:
        return p
    for d in SEARCH_DIRS + (r"C:\Program Files\qemu",):
        for cand in (os.path.join(d, name), os.path.join(d, name + ".exe")):
            if os.path.exists(cand):
                return cand
    return None


def require_qemu(arch):
    p = find_qemu(arch)
    if not p:
        raise SystemExit(f"qemu-system-{arch} not found; install QEMU "
                         "(macOS: `brew install qemu`, Debian/Ubuntu: `apt install qemu-system`)")
    return p


def firmware_path(name):
    """Locate a firmware file shipped with QEMU (e.g. edk2-aarch64-code.fd)."""
    for d in (os.path.join(ROOT, "build", "qemu", "share", "qemu"), "/opt/homebrew/share/qemu", "/usr/local/share/qemu", "/usr/share/qemu",
              "/usr/share/qemu-efi-aarch64"):
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p
    return None


class QemuVM:
    """A QEMU process with a human monitor on a unix socket."""

    def __init__(self, binary, args, monitor_sock, log=None, display=False):
        self.monitor_sock = monitor_sock
        if os.path.exists(monitor_sock):
            os.remove(monitor_sock)
        cmd = [binary, *args, "-monitor", f"unix:{monitor_sock},server,nowait"]
        if not display:
            cmd += ["-display", "none"]
        self.cmd = cmd
        out = open(log, "wb") if log else subprocess.DEVNULL
        self.proc = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL)
        for _ in range(100):
            if os.path.exists(monitor_sock) or self.proc.poll() is not None:
                break
            time.sleep(0.1)
        if self.proc.poll() is not None:
            raise SystemExit(f"QEMU exited immediately:\n  {' '.join(cmd)}"
                             + (f"\n  see {log}" if log else ""))

    def running(self):
        return self.proc.poll() is None

    def monitor(self, command, wait=0.5):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(self.monitor_sock)
        s.settimeout(5)
        try:
            s.recv(4096)  # banner + prompt
        except socket.timeout:
            pass
        s.sendall(command.encode() + b"\n")
        time.sleep(wait)
        data = b""
        try:
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    break
                data += chunk
                if data.endswith(b"(qemu) "):
                    break
        except socket.timeout:
            pass
        s.close()
        return data.decode("latin-1")

    def screenshot_png(self, png_path):
        ppm = png_path + ".ppm"
        if os.path.exists(ppm):
            os.remove(ppm)
        self.monitor(f"screendump {ppm}", wait=1.0)
        for _ in range(50):
            if os.path.exists(ppm) and os.path.getsize(ppm) > 0:
                break
            time.sleep(0.1)
        else:
            raise SystemExit("QEMU screendump produced no file")
        time.sleep(0.2)
        ppm_to_png(ppm, png_path)
        os.remove(ppm)
        return png_path

    def quit(self):
        if self.running():
            try:
                self.monitor("quit", wait=0.2)
            except OSError:
                pass
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if os.path.exists(self.monitor_sock):
            os.remove(self.monitor_sock)


def ppm_to_png(ppm_path, png_path):
    data = open(ppm_path, "rb").read()
    # P6 header: magic, width, height, maxval, each separated by whitespace.
    fields, pos = [], 0
    while len(fields) < 4:
        while data[pos:pos + 1].isspace():
            pos += 1
        if data[pos:pos + 1] == b"#":
            pos = data.index(b"\n", pos) + 1
            continue
        start = pos
        while not data[pos:pos + 1].isspace():
            pos += 1
        fields.append(data[start:pos])
    pos += 1
    if fields[0] != b"P6" or int(fields[3]) != 255:
        raise ValueError(f"unsupported PPM {fields}")
    w, h = int(fields[1]), int(fields[2])
    pixels = data[pos:pos + w * h * 3]
    raw = b"".join(b"\x00" + pixels[y * w * 3:(y + 1) * w * 3] for y in range(h))

    def chunk(tag, body):
        return (struct.pack(">I", len(body)) + tag + body +
                struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF))
    png = (b"\x89PNG\r\n\x1a\n" +
           chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) +
           chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))
    open(png_path, "wb").write(png)
