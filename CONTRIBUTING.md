# Contributing to Anokos

Thanks for helping build Anokos. This project accepts public contributions through GitHub pull
requests.

## Workflow

1. Fork the repository and create a branch from `main` (`feature/short-name` or `fix/short-name`).
2. Make focused changes. One logical change per pull request.
3. Describe what you changed and why in the PR, and how you tested it (e.g. booted in QEMU,
   compiled with `#include`, ran a demo).
4. A maintainer reviews and merges.

For large changes (new subsystems, filesystem or ABI changes, removing major features), open an
issue first to discuss the design.

## Ground rules

- **Never modify anything in `TempleOS/`.** It is the pristine upstream reference ISO.
- Keep `src/` files byte-exact unless you mean to change them. Many `.DD` files contain binary
  sprite data after the text; editors that normalize line endings, trim trailing whitespace or
  re-encode files can corrupt them. Use an editor setting that preserves bytes, or edit inside
  the OS itself.
- Follow the existing HolyC style in the file you're editing (tabs for indentation, the existing
  naming conventions such as `CamelCase` functions and `C`-prefixed classes).
- Only contribute code you have the right to contribute. By submitting a contribution you agree
  to release it under the project's [LICENSE](LICENSE).

## Reporting bugs

Open an issue with: what you did, what you expected, what happened, and your VM/hardware setup.
Screenshots of the screen output help a lot.
