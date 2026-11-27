# Anokos Roadmap

This roadmap lists the major milestones planned for Anokos, in the order we intend to work on
them. Each milestone builds on the ones before it, so the order follows technical dependencies
rather than size or priority alone.

Plans may change as the project grows. Discussion and proposals are welcome in GitHub issues.

| # | Milestone | Status |
| --- | --- | --- |
| 0 | [Bootable terminal](#milestone-0-bootable-terminal) | Done |
| 1 | [TerryC language rename](#milestone-1-terryc-language-rename) | Done |
| 2 | [PCI and PCI Express support](#milestone-2-pci-and-pci-express-support) | Planned |
| 3 | [Ring 3 (user mode)](#milestone-3-ring-3-user-mode) | Planned |
| 4 | [GPU and graphics support](#milestone-4-gpu-and-graphics-support) | Planned |
| 5 | [Multi-architecture support](#milestone-5-multi-architecture-support) | ARM64 in progress |

### Why this order

- **TerryC comes first** because it renames files and identifiers across the whole tree. Doing it
  before other large changes avoids merge conflicts and means new code is written with the final
  names from the start.
- **PCI/PCIe is the foundation for hardware.** Graphics cards, modern storage and USB controllers
  are all found and configured through it.
- **Ring 3 comes before graphics and porting** because it changes how programs, drivers and the
  kernel talk to each other. Settling that model early keeps later drivers and ports from being
  written twice.
- **GPU support depends on PCIe** for finding and configuring graphics hardware, and on the Ring 3
  model for how applications reach it.
- **Multi-architecture support is last** because it is the largest effort. It needs a portable
  kernel, new compiler back ends and new boot paths, and benefits from every milestone before it.

---

## Milestone 0: Bootable terminal

**Status: Done**

- [x] Boot directly into a full-screen terminal with no desktop environment
- [x] Native display resolution through VBE, with a 640x480 fallback
- [x] Line editing, command history and Ctrl+Alt+C to stop a command
- [x] Host build pipeline that produces a bootable ISO

---

## Milestone 1: TerryC language rename

**Status: Done**

Rename the system programming language from HolyC to **TerryC**, in honor of Terry A. Davis, and
give its files their own extensions.

**Scope**

- [x] Rename source extensions: `.HC` to `.TC` and `.HH` to `.TH` (compressed files become
      `.TC.Z` and `.TH.Z`)
- [x] Update the compiler, `#include` lookup, file loaders and default-extension handling to use
      the new names
- [x] Rename "HolyC" in the compiler, documentation, help index and error messages
- [x] Update host tools (`build_iso.py`, `anokos_build.py`) and `.gitattributes` for the new
      extensions
- [x] Decide whether `.HC`/`.HH` files are still accepted for compatibility with existing TempleOS
      programs. **Decision:** yes. `.TC`/`.TH` are the default, `.HC`/`.HH` files are still recognized
      as source, and an extensionless `#include` or `ExeFile()` falls back to `.HC` when no `.TC`
      file exists.

**Done when** the full system builds and boots with no `.HC`/`.HH` files and no user-facing
"HolyC" references.

---

## Milestone 2: PCI and PCI Express support

Give Anokos a proper way to discover and configure hardware.

**Scope**

- [ ] Enumerate all PCI buses, devices and functions, including bridges
- [ ] PCI Express configuration access through memory-mapped configuration space (ECAM), found
      through ACPI
- [ ] Read and assign device memory and I/O ranges (BARs)
- [ ] Message-signaled interrupts (MSI and MSI-X)
- [ ] A simple driver model: drivers register for vendor/device IDs or device classes
- [ ] A command to list devices, similar to `lspci`

**Done when** Anokos lists every PCI/PCIe device in a VM and on real hardware, and a driver can
claim a device and receive its interrupts.

---

## Milestone 3: Ring 3 (user mode)

TempleOS runs everything in ring 0, the CPU's most privileged mode, so any program can crash or
overwrite the whole system. This milestone adds user mode, so applications run in ring 3 and the
kernel is protected from them.

**Scope**

- [ ] Separate kernel and user memory, with per-process address spaces
- [ ] A system call interface for programs to request kernel services
- [ ] Run the terminal and user programs in ring 3, with the kernel and drivers in ring 0
- [ ] Handle faults in user programs without bringing down the system
- [ ] Keep a clear, documented boundary between kernel and user code that can carry over to other
      CPU architectures

**Done when** a misbehaving user program can crash only itself, and the system keeps running.

**Starting point:** Terry left a working ring 3 example in
[`src/Demo/Lectures/Ring3.TC`](src/Demo/Lectures/Ring3.TC). It switches to ring 3 using the
ring-3 code and data segments that already exist in the kernel's GDT (`GDT_CS64_RING3` and
`GDT_DS_RING3` in `src/Kernel/KStart16.TC`) and each CPU's ring-3 task state segment, then
returns to ring 0 through a general protection fault handler.

---

## Milestone 4: GPU and graphics support

Move beyond the boot-time framebuffer to real graphics hardware.

**Scope**

- [ ] A graphics driver interface built on the PCIe driver model
- [ ] Drivers for common virtual GPUs first (for example VirtIO GPU and the QEMU/Bochs display),
      then selected real hardware
- [ ] Mode setting at runtime, multiple resolutions and display detection
- [ ] Hardware-accelerated drawing where available
- [ ] A modern graphics library replacing the 16-color TempleOS graphics, so graphical programs
      and demos can run again

**Done when** Anokos can change display modes on a supported GPU and run graphical programs
through the new graphics library.

---

## Milestone 5: Multi-architecture support

Run Anokos on more than x86-64.

| Architecture | Target hardware |
| --- | --- |
| x86-64 | PCs and virtual machines (current platform) |
| ARM64 (AArch64) | Raspberry Pi 3, 4 and 5 |
| PowerPC 64 (ppc64) | Power Mac G5 |

> **Note:** PowerBook G4 models use 32-bit PowerPC processors, not ppc64. Supporting them would
> need a separate 32-bit PowerPC port, to be decided once the ppc64 port is underway.

**Status:** the ARM64 port has started ahead of milestones 2-4. Progress and the per-file plan
are in [ARM64.md](ARM64.md).

**Scope**

- [ ] Split architecture-specific code (boot, interrupts, memory management, context switching)
      from portable kernel code (ARM64: boot, memory, tasks, interrupts and the asm routines done)
- [ ] TerryC compiler back ends that generate ARM64 and ppc64 machine code (ARM64 code
      generator and assembler working; ppc64 not started)
- [ ] Boot paths for each platform: Raspberry Pi firmware and device tree, and Open Firmware on
      Power Macs (ARM64 boot image runs on QEMU virt and the emulated Pi 3 and 4)
- [ ] Drivers for each platform's essentials: display, keyboard, timer, interrupt controller and
      storage
      (ARM64: framebuffer on QEMU virt and the Pi mailbox, serial console keyboard, generic timer,
      GICv2 and the BCM2836 local interrupt controller; the Anokos terminal runs on the emulated
      Pi 3 and 4)
- [x] Cross-build support in the host tools, so all architectures can be built from one machine
      (`anokos_build.py arm64`)

**Done when** Anokos boots to its terminal on a Raspberry Pi and a Power Mac G5, as well as on
x86-64.
