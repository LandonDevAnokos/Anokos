<p align="center">
  <img src="docs/assets/logo.png" alt="Anokos logo: a steaming bowl of chili" width="220">
</p>

<h1 align="center">Anokos</h1>

<p align="center">
  An open-source operating system descended from TempleOS.<br>
  <a href="https://landondevanokos.github.io/Anokos/">Website</a> ·
  <a href="https://landondevanokos.github.io/Anokos/code-map/">Code City</a> ·
  <a href="ROADMAP.md">Roadmap</a> ·
  <a href="CONTRIBUTING.md">Contributing</a>
</p>

Anokos boots straight into a full-screen terminal at your display's native resolution. Everything,
from the kernel and compiler to the apps and the command line, is written in **TerryC** (formerly
HolyC) and compiled by the system itself. It runs on x86-64 today, with ARM64 and PowerPC planned.

> [!NOTE]
> **AI notice:** All work on Anokos so far, including the code, build tools, documentation and
> website, has been done by an AI (a large language model) under the direction of the project's
> maintainer. Human contributions are wanted and appreciated far more. If you can review, test,
> fix or build anything, please [get involved](CONTRIBUTING.md).

## Quick start

You need Python 3 and [VirtualBox](https://www.virtualbox.org/).

```sh
python tools/anokos_build.py all    # build the compiler, kernel and build/Anokos.ISO
python tools/anokos_build.py run    # boot it in a virtual machine
```

See [BUILDING.md](BUILDING.md) for how the build works and how to boot on real hardware.

## What's in the repository

- [`src/`](src/): the operating system: `Kernel/`, `Compiler/`, the `Anokos/` system layer
  (including the terminal), `Apps/`, `Demo/` and `Doc/`
- [`tools/`](tools/): build tools
- [`docs/`](docs/): the website and Code City
- [`TempleOS/`](TempleOS/): the original TempleOS ISO, kept unmodified

## Roadmap

1. ~~TerryC language rename~~ (done)
2. PCI and PCI Express support
3. Ring 3 (user mode)
4. GPU and graphics support
5. Multi-architecture support: ARM64 (Raspberry Pi) and ppc64 (Power Mac G5)

Details are in [ROADMAP.md](ROADMAP.md).

## License

Anokos is licensed under the [GNU General Public License v2.0 only](LICENSE), like the Linux
kernel. It is derived from TempleOS by Terry A. Davis, which he released into the public domain.
