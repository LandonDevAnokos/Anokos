#!/bin/sh
# Build QEMU (x86-64 and ARM64 system emulators only) into build/qemu/.
#
# Use this when your package manager has no QEMU for your OS, e.g. macOS 14
# on Apple Silicon, where Homebrew only builds from source and QEMU 11's
# i.MX8M board files need a newer clang than the macOS 14 Command Line Tools.
# Those two boards are left out; everything Anokos uses (pc, virt, raspi3b,
# raspi4b) is built. The Anokos build tools look in build/qemu/bin first.
#
# Needs QEMU's build dependencies, e.g. on macOS:
#   brew install --only-dependencies qemu && brew install ninja meson pkgconf
#
# Usage: tools/build_qemu.sh [path/to/qemu-X.Y.Z.tar.xz]
set -e
ROOT=$(cd "$(dirname "$0")/.." && pwd)
VER=11.1.3
TARBALL=${1:-}
mkdir -p "$ROOT/build"
cd "$ROOT/build"
if [ -z "$TARBALL" ]; then
  TARBALL="$ROOT/build/qemu-$VER.tar.xz"
  [ -f "$TARBALL" ] || curl -L -o "$TARBALL" "https://download.qemu.org/qemu-$VER.tar.xz"
fi
SRC="$ROOT/build/qemu-$VER"
[ -d "$SRC" ] || tar xf "$TARBALL"
cat > "$SRC/configs/devices/aarch64-softmmu/anokos.mak" <<'EOF'
# Anokos build of QEMU: default aarch64 boards minus the i.MX8M ones.
include default.mak
CONFIG_FSL_IMX8MP_EVK=n
CONFIG_FSL_IMX8MM_EVK=n
EOF
cd "$SRC"
EXTRA=""
if [ "$(uname)" = "Darwin" ]; then
  export PATH="/opt/homebrew/bin:$PATH"
  export PKG_CONFIG_PATH="/opt/homebrew/lib/pkgconfig:/opt/homebrew/share/pkgconfig:$PKG_CONFIG_PATH"
  # HVF for ARM needs the macOS 15 SDK; TCG is used instead.
  EXTRA="--cc=clang --host-cc=clang --enable-cocoa --disable-hvf"
fi
./configure --prefix="$ROOT/build/qemu" \
  --target-list=x86_64-softmmu,aarch64-softmmu \
  --with-devices-aarch64=anokos --disable-docs --disable-werror --enable-slirp $EXTRA
make -j"$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)"
make install
echo "QEMU installed in $ROOT/build/qemu/bin"
