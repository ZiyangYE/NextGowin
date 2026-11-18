# NextGowin — Standalone Synthesis & Custom Place-and-Route (Windows x64)

**NextGowin** is a self-contained, Python-free native C++20 logic synthesis and custom place-and-route (`ownpr`) toolchain for **Gowin GW5AST-138 (`GW5AST-138C` and `GW5AST-138B`)** FPGAs on Windows x64.

All prebuilt binaries in `bin/` target the **Tier 1 baseline (`x86-64 + SSE2/SSE3`)**, compatible with all 64-bit x86 CPUs released after 2007 without requiring Python or Visual C++ Redistributables.

---

## Archive Layout

```text
.
├── README.md          # Quick-start guide and directory map
├── SHA256SUMS.txt     # SHA-256 checksums for all distributed files
├── bin/               # Ready-to-run Tier 1 executable, DLLs, resources & gowin-path.txt
├── docs/              # Usage guide, reproducible build instructions, limitations & licenses
└── src/               # Complete C/C++20 source tree, ABC sources, configs & build scripts
```

---

## Quick Start (Drag-and-Drop)

NextGowin does **not** bundle proprietary Gowin binaries or device databases; a local Windows installation of **Gowin V1.9.12.04 x64** is required at runtime for frontend elaboration, post-route STA, and bitstream generation.

1. **Configure Gowin Path**: Open `bin/gowin-path.txt` and set the single-line path to your local Gowin installation directory, for example:
   ```text
   C:\Gowin\Gowin_V1.9.12.04_x64
   ```
2. **Drag-and-Drop a `.gprj` Project**: Drag any `GW5AST-138` (`138C` or `138B`) `.gprj` file (for example `src/examples/blinky_gw5ast138/blinky.gprj`) onto **`bin/nextgowin.exe`** in Windows Explorer.

NextGowin automatically executes all 6 stages through `.fs` bitstream generation, writes full checkpoints, reports, and the CRC-verified bitstream (`.fs`) into `<project_dir>\<project>_nextgowin\`, and **updates `<project_dir>\impl\gwsynthesis\` and `<project_dir>\impl\pnr\`** (overwriting prior Gowin IDE outputs so you can immediately download/debug `.fs` with Gowin Programmer and open Place & Route, Timing Analysis, and Pinout HTML reports directly inside Gowin IDE) without modifying any source or constraint files.

### Command-Line Verification

```powershell
# Run standalone CPU, DLL, resource SHA-256, and Gowin SDK self-test
.\bin\nextgowin.exe --self-test

# Run the complete 6-stage flow on the included example project
.\bin\nextgowin.exe .\src\examples\blinky_gw5ast138\blinky.gprj
```

---

## Documentation (`docs/`)

- `docs/USAGE.md` — Configuration (`bin/gowin-path.txt`), Gowin version warning dialog, stage boundaries, CLI options, and outputs.
- `docs/BUILD.md` — Reproducible native binary & archive build instructions from `src/`.
- `docs/LIMITATIONS.md` — Supported devices, HDL scope, Gowin version policy, and known limitations.
- `docs/THIRD_PARTY_NOTICES.txt` & `docs/licenses/` — Open-source attributions and full license texts.
