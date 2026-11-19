"""Build NextGowin's single combined GitHub release ZIP (source + ready-to-run Tier 1 Windows x64 distribution).

Archive layout inside `nextgowin-win64-release.zip`:
  - `README.md`        — Concise quick-start overview and archive map
  - `SHA256SUMS.txt`   — SHA-256 checksums of `README.md` and all files in `bin/`, `docs/`, and `src/`
  - `bin/`             — Ready-to-run Tier 1 (`x86-64 + SSE2/SSE3`) executable (`nextgowin.exe`), redistributable
                         dependencies, synthesis cell libraries (`share/`), calibrated resources
                         (`nextgowin-resources/`), and `gowin-path.txt` template
  - `src/`             — Complete reproducible C/C++20 source tree, ABC sources, calibration inputs, example
                         project, and build scripts
  - `docs/`            — `BUILD.md`, `USAGE.md`, `LIMITATIONS.md`, `THIRD_PARTY_NOTICES.txt`, and `licenses/`

Python is used strictly as a build-time packaging tool; `bin/` contains zero Python runtime components.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]

if (ROOT / "scripts/workspace_budget.py").is_file():
    sys.path.insert(0, str(ROOT / "scripts"))
    from workspace_budget import reserve as _ws_reserve
else:
    def _ws_reserve(_root: Path, _bytes: int) -> None:
        pass

INBOX_WINDOWS_OS_DLLS = {
    "KERNEL32.DLL",
    "USER32.DLL",
    "GDI32.DLL",
    "ADVAPI32.DLL",
    "SHELL32.DLL",
    "SHLWAPI.DLL",
    "OLE32.DLL",
    "OLEAUT32.DLL",
    "WS2_32.DLL",
    "IPHLPAPI.DLL",
    "NETAPI32.DLL",
    "USERENV.DLL",
    "CRYPT32.DLL",
    "BCRYPT.DLL",
    "RPCRT4.DLL",
    "VERSION.DLL",
    "WINMM.DLL",
    "MSVCRT.DLL",
    "IMM32.DLL",
}

FORBIDDEN_GOWIN_FILENAMES = {
    "gowin.dll",
    "gwsyn.dll",
    "gw_sh.exe",
    "gw_ide.exe",
    "prim_sim.v",
    "gw5ast-138b.dat",
    "gw5ast-138b.fse",
    "gw5ast-138b.tm",
    "gw5ast-138c.dat",
    "gw5ast-138c.fse",
    "gw5ast-138c.tm",
}

RESOURCE_REL_FILES = [
    "results/timing/gowin138_model.json",
    "results/timing/gowin138_model_ff_checked.json",
    "results/native_route_nodes_003/manifest.json",
    "results/native_route_runtime_019/manifest.json",
    "results/native_route_runtime_019/native_register_ports.json",
    "results/native_ff_ports_001/manifest.json",
    "results/native_ff_ports_001/ff_attributes.json",
    "configs/gw5a_arch_cost_v2.json",
    "configs/fpgafly_general_coopt.json",
    "configs/fpgafly_native.json",
    "configs/cpu_gao_native.json",
]

MINGW_RUNTIME_DLLS = [
    "libgcc_s_seh-1.dll",
    "libwinpthread-1.dll",
    "libstdc++-6.dll",
    "libffi-8.dll",
    "libreadline8.dll",
    "tcl86.dll",
    "zlib1.dll",
    "libtermcap-0.dll",
]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_forbidden_private_substrings() -> list[bytes]:
    items: list[bytes] = []
    cur_user = os.environ.get("USERNAME", "").strip().lower()
    if len(cur_user) >= 3:
        items.append(cur_user.encode("utf-8"))
    for part in ROOT.parts:
        pl = part.lower()
        if pl not in (
            "c:\\", "d:\\", "users", "documents", "nextgowin",
            "repos", "src", "github", "work", "temp", "appdata", "local"
        ) and len(pl) >= 5 and not pl.startswith("ng_"):
            items.append(pl.encode("utf-8"))
    return items


def pe_imports(path: Path) -> list[str]:
    data = path.read_bytes()
    pe_off = struct.unpack_from("<I", data, 0x3C)[0]
    magic = struct.unpack_from("<H", data, pe_off + 24)[0]
    num_sec = struct.unpack_from("<H", data, pe_off + 6)[0]
    opt_sz = struct.unpack_from("<H", data, pe_off + 20)[0]
    imp_rva_off = pe_off + 24 + (120 if magic == 0x20B else 104)
    imp_rva, imp_sz = struct.unpack_from("<II", data, imp_rva_off)
    if imp_rva == 0 or imp_sz == 0:
        return []
    sec_off = pe_off + 24 + opt_sz
    secs = []
    for i in range(num_sec):
        s = sec_off + i * 40
        vsz, va, rsz, ptr = struct.unpack_from("<IIII", data, s + 8)
        secs.append((va, max(vsz, rsz), ptr))

    def rva2off(rva: int) -> int:
        for va, sz, ptr in secs:
            if va <= rva < va + sz:
                return ptr + (rva - va)
        raise RuntimeError(f"Unmapped RVA {rva:#x} in {path}")

    pos = rva2off(imp_rva)
    dlls = []
    while True:
        ilt, _, _, name_rva, iat = struct.unpack_from("<IIIII", data, pos)
        if ilt == 0 and name_rva == 0 and iat == 0:
            break
        noff = rva2off(name_rva)
        end = data.index(b"\x00", noff)
        dlls.append(data[noff:end].decode("ascii", errors="replace"))
        pos += 20
    return dlls


def strip_pe_dwarf_debug(src: Path, dst: Path) -> tuple[int, int]:
    raw = bytearray(src.read_bytes())
    orig_len = len(raw)
    pe_off = struct.unpack_from("<I", raw, 0x3C)[0]
    num_sec = struct.unpack_from("<H", raw, pe_off + 6)[0]
    opt_sz = struct.unpack_from("<H", raw, pe_off + 20)[0]
    sec_align = struct.unpack_from("<I", raw, pe_off + 24 + 32)[0]
    sec_off = pe_off + 24 + opt_sz

    keep_secs = 0
    max_file_end = 0
    max_virt_end = 0
    for i in range(num_sec):
        s = sec_off + i * 40
        name = bytes(raw[s : s + 8]).rstrip(b"\x00")
        vsz, va, rsz, ptr, _, _, _, _, ch = struct.unpack_from("<IIIIIIHHI", raw, s + 8)
        is_dwarf = name.startswith(b"/") and ((ch & 0x02000000) != 0)
        if is_dwarf:
            break
        keep_secs += 1
        if ptr > 0 and rsz > 0:
            max_file_end = max(max_file_end, ptr + rsz)
        end_va = va + max(vsz, rsz)
        aligned_va = ((end_va + sec_align - 1) // sec_align) * sec_align
        max_virt_end = max(max_virt_end, aligned_va)

    if keep_secs < num_sec and max_file_end > 0:
        for i in range(keep_secs, num_sec):
            s = sec_off + i * 40
            raw[s : s + 40] = b"\x00" * 40
        struct.pack_into("<H", raw, pe_off + 6, keep_secs)
        struct.pack_into("<II", raw, pe_off + 12, 0, 0)
        struct.pack_into("<I", raw, pe_off + 24 + 56, max_virt_end)
        struct.pack_into("<I", raw, pe_off + 24 + 64, 0)
        raw = raw[:max_file_end]

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(bytes(raw))
    return orig_len, len(raw)


def copy_yosys_share_minimal(suite_root: Path, dst_share_yosys: Path) -> list[Path]:
    src_y = suite_root / "share/yosys"
    copied = []
    for p in sorted(src_y.iterdir()):
        if p.is_file() and p.suffix == ".v":
            tgt = dst_share_yosys / p.name
            tgt.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, tgt)
            copied.append(tgt)
        elif p.is_dir() and p.name in ("gowin", "choices", "sdc"):
            for f in sorted(p.rglob("*")):
                if f.is_file() and f.suffix.lower() != ".py":
                    rel = f.relative_to(src_y)
                    tgt = dst_share_yosys / rel
                    tgt.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, tgt)
                    copied.append(tgt)
    return copied


def verify_zero_python_artifacts(pkg_dir: Path) -> None:
    forbidden = []
    for f in sorted(pkg_dir.rglob("*")):
        if not f.is_file():
            continue
        nl = f.name.lower()
        ext = f.suffix.lower()
        if ext in (".py", ".pyc", ".pyo", ".pyd") or nl.startswith("python"):
            forbidden.append(f.relative_to(pkg_dir).as_posix())
    if forbidden:
        raise RuntimeError(f"Python artifacts found inside {pkg_dir.name}: {forbidden}")


ROOT_README_MD = """\
# NextGowin v0.1.1 — Standalone Synthesis & Custom Place-and-Route (Windows x64)

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
   C:\\Gowin\\Gowin_V1.9.12.04_x64
   ```
2. **Drag-and-Drop a `.gprj` Project**: Drag any `GW5AST-138` (`138C` or `138B`) `.gprj` file (for example `src/examples/blinky_gw5ast138/blinky.gprj`) onto **`bin/nextgowin.exe`** in Windows Explorer.

NextGowin automatically executes all 6 stages through `.fs` bitstream generation, writes full checkpoints, reports, and the CRC-verified bitstream (`.fs`) into `<project_dir>\\<project>_nextgowin\\`, and **updates `<project_dir>\\impl\\gwsynthesis\\` and `<project_dir>\\impl\\pnr\\`** (overwriting prior Gowin IDE outputs so you can immediately download/debug `.fs` with Gowin Programmer and open Place & Route, Timing Analysis, and Pinout HTML reports directly inside Gowin IDE) without modifying any source or constraint files.

### Command-Line Verification

```powershell
# Run standalone CPU, DLL, resource SHA-256, and Gowin SDK self-test
.\\bin\\nextgowin.exe --self-test

# Run the complete 6-stage flow on the included example project
.\\bin\\nextgowin.exe .\\src\\examples\\blinky_gw5ast138\\blinky.gprj
```

---

## Documentation (`docs/`)

- `docs/USAGE.md` — Configuration (`bin/gowin-path.txt`), Gowin version warning dialog, stage boundaries, CLI options, and outputs.
- `docs/BUILD.md` — Reproducible native binary & archive build instructions from `src/`.
- `docs/LIMITATIONS.md` — Supported devices, HDL scope, Gowin version policy, and known limitations.
- `docs/THIRD_PARTY_NOTICES.txt` & `docs/licenses/` — Open-source attributions and full license texts.
"""


DOCS_USAGE_MD = """\
# NextGowin Usage & Architecture Guide (`docs/USAGE.md`)

## 1. Gowin Path & Version Configuration (`bin/gowin-path.txt`)

NextGowin reads the local Gowin EDA installation path from the single-line UTF-8 text file `bin/gowin-path.txt` (located directly beside `bin/nextgowin.exe`):

```text
C:\\Gowin\\Gowin_V1.9.12.04_x64
```

- You may specify either the Gowin root directory (containing `IDE\\bin\\`) or the `IDE` directory itself.
- Relative paths in `bin/gowin-path.txt` are resolved relative to `bin/` (beside `nextgowin.exe`).
- Quoted paths, spaces, and Unicode characters are fully supported.
- Lines starting with `#` and blank lines are ignored; exactly one active path line must be present.

### Supported & Validated Gowin Version
- **Officially supported and validated version**: **Gowin V1.9.12.04 x64** (`Gowin_V1.9.12.04_x64`).
- **Other versions**: If another Gowin version (or modified SDK binary/database) is detected, NextGowin displays a modal warning dialog identifying both the **expected** (`Gowin V1.9.12.04 x64`) and **detected** versions with **Continue** and **Cancel** buttons:
  - **Cancel**: Aborts cleanly (exit code `8`) before running any synthesis or P&R stage.
  - **Continue**: Records the version-mismatch warning in console output, `nextgowin.log`, and `manifest.json`, and continues execution while keeping all equivalence, placement/routing legality, timing, and bitstream frame-CRC checks strictly enforced.

---

## 2. Stage Responsibilities & Tool Boundaries

**Logic synthesis and custom place-and-route are performed by NextGowin, never by Gowin vendor synthesis or vendor P&R.**

| Stage | Subsystem | Responsibility & Tool Boundary |
| :--- | :--- | :--- |
| **1. `01_frontend`** | Local Gowin Frontend (`gwsyn.dll` + `tcl86t.dll`) | **Elaboration only**: Runs `dev` + `extract` to elaborate Verilog, SystemVerilog, VHDL, `.rao` (GAO), and `.csr` (SerDes) sources, resolve parameters/`generate` blocks, and decrypt/elaborate protected Gowin IP wrappers (e.g. DDR3 PHY, USB 2.0 SoftPHY, SerDes PHY). Stops **strictly before** Gowin `cvt`, resource inference, or technology mapping. |
| **2. `02_bridge`** | **NextGowin** (`bin/nextgowin_core.dll`) | Converts elaborated netlists, restores behavioral memory arrays, preserves `.sdc`/`.cst` constraint hierarchy paths, and reads primitive parameter declarations from `IDE/simlib/gw5a/prim_sim.v`. |
| **3. `03_resources`** | **NextGowin** + `bin/yosys.exe` | Performs NextGowin coarse synthesis, DSP inference (`MULTALU27X18`, `MULT12X12`, `MULT27X36`), BSRAM inference (`SDPX9B`, `SDPB`, `DPX9B`, `DPB`, `SP`, `SPX9`, `pROM`), default distributed SSRAM inference (`RAM16S1/2/4`, `RAM16SDP1/2/4`, `ROM16` on `GW5AST-138C`; disabled automatically on `0Kb`-SSRAM `GW5AST-138B`), and `ALU` carry-chain lowering. |
| **4. `04_mapping`** | **NextGowin** + `bin/yosys.exe` & `bin/abc.exe` | Performs AIG cut rewriting, timing-driven wide LUT/MUX2 ABC9 mapping (`LUT1`..`LUT4`, `MUX2_LUT5`, `MUX2_LUT6`), calibrated LUT input permutation, and parallel output-sliced `abc.exe cec` equivalence proofs. |
| **5. `05_netlist`** | **NextGowin** + `bin/yosys.exe` | Inserts top-level I/O pads, preserves `.sdc` `[get_nets ...]` and `.cst` `CLOCK_LOC` net aliases, runs timing-driven LUT and flip-flop fanout replication (with formal FF transition verification), and emits structural `mapped.vg`. |
| **6. `06_pnr`** | **NextGowin Custom P&R** (`bin/ownpr_bridge.dll`) + Local Gowin STA/Fusemap (`gowin.dll`) | **NextGowin** performs 100% of placement, minimax slack relocation, PathFinder negotiated-congestion timing-driven routing, and multi-pass post-STA critical-path optimization (`vendor_placement_executed = false`, `vendor_routing_executed = false`).<br>**Local Gowin (`gowin.dll`)** is invoked **only** to load device/constraint tables, run post-route Static Timing Analysis (`mapped.tr`, `mapped.tr.html`), emit post-route reports (`mapped.vo`, `mapped.rpt.txt`, `mapped.rpt.html`, `mapped.pin.html`), and encode the bitstream (`generateFusemap` -> `mapped.fs`).<br>**NextGowin** then audits live `NETLIST::node` sink-to-source routing trees, dedicated co-location, pin exemptions, and all 21,872 bitstream frame CRC-16 checksums. |

---

## 3. Drag-and-Drop & Command-Line Usage

### A. Drag-and-Drop (`.gprj`)
1. Configure `bin/gowin-path.txt`.
2. Drag a `.gprj` project file onto `bin/nextgowin.exe` in Windows Explorer.
3. NextGowin parses enabled Verilog/SystemVerilog files (`file.verilog`), physical constraints (`file.cst`), timing constraints (`file.sdc`), top module, Verilog standard, and include paths from `<project>.gprj` (and optional `impl/<project>_process_config.json`), resolving all project-relative paths against the `.gprj` directory.
4. Progress (`[1/6]` through `[6/6]`) and the final timing/bitstream summary (or diagnostic error) remain visible in the console window until Enter is pressed.

### B. Command-Line Options

```powershell
# Run full 6-stage flow from a .gprj file (writes <project>_nextgowin AND updates impl\gwsynthesis & impl\pnr)
.\\bin\\nextgowin.exe path\\to\\project.gprj

# Run .gprj without modifying <project_dir>\\impl\\
.\\bin\\nextgowin.exe path\\to\\project.gprj --no-sync-ide --no-pause

# Custom output directory and batch mode (no interactive end-of-run pause)
.\\bin\\nextgowin.exe path\\to\\project.gprj --out .\\build_out --no-pause

# Override Gowin installation directory on the command line
.\\bin\\nextgowin.exe path\\to\\project.gprj --gowin-dir "C:\\Gowin\\Gowin_V1.9.12.04_x64" --no-pause

# Run JSON configuration flow
.\\bin\\nextgowin.exe --config config.json --out .\\flow_out --no-pause

# Run standalone environment, DLL import, resource SHA-256, and Gowin SDK self-test
.\\bin\\nextgowin.exe --self-test --no-pause

# Verify a GW5AST-138 .fs bitstream (compressed/uncompressed, CRC/non-CRC)
.\\bin\\nextgowin.exe --verify-fs path\\to\\bitstream.fs --no-pause

# Print version or detected CPU features and Tier 1 baseline status
.\\bin\\nextgowin.exe --version --no-pause
.\\bin\\nextgowin.exe --cpu-info --no-pause
```

---

## 4. Output Directory Structure & Gowin IDE Integration

For `<project>.gprj`, NextGowin keeps all user source/constraint/project files (`.gprj`, `.v`/`.sv`, `.cst`, `.sdc`) completely unmodified and writes outputs to two places:

1. **Gowin IDE `impl/` Directory (`<project_dir>\\impl\\`, enabled by default; disable with `--no-sync-ide`)**:
   - Overwrites existing Gowin IDE build artifacts using `OUTPUT_BASE_NAME` from `impl\\<project>_process_config.json` (defaulting to `<project>`), preserving any existing `impl\\pnr\\device.cfg` settings:
     - `impl\\gwsynthesis\\<base>.vg` — NextGowin synthesized & mapped structural netlist
     - `impl\\pnr\\<base>.fs` — CRC-16 verified GW5AST-138C bitstream ready for Gowin Programmer download
     - `impl\\pnr\\<base>.rpt.html` & `<base>.rpt.txt` — Place & Route utilization and routing reports viewable in Gowin IDE
     - `impl\\pnr\\<base>.tr.html`, `<base>_tr_cata.html`, `<base>_tr_content.html` & `<base>.tr` — Static Timing Analysis HTML/text reports viewable in Gowin IDE
     - `impl\\pnr\\<base>.pin.html` & `<base>.pin.txt` — Package pinout reports viewable in Gowin IDE
     - `impl\\pnr\\<base>.vo` — Post-route structural Verilog netlist

2. **NextGowin Detailed Run Directory (`<project_dir>\\<project>_nextgowin\\`)**:
   - `<project>.fs`, `<project>.tr`, `<project>.tr.html`, `<project>.rpt.html`, `<project>.rpt.txt`, `<project>.pin.html`, `<project>.vo`
   - `nextgowin.log` — Timestamped console & diagnostic log (including any Gowin version warning)
   - `manifest.json` — Machine-readable stage metrics, SHA-256 provenance, `ide_sync` paths, and verification status
   - `01_frontend/` .. `06_pnr/` — Per-stage checkpoints, equivalence proofs, and live route/exemption audits
"""


DOCS_BUILD_MD = """\
# Reproducible Build Instructions (`docs/BUILD.md`)

All NextGowin runtime binaries in `bin/` are Python-free native Windows x64 binaries targeting **Tier 1 (`x86-64 + SSE2/SSE3`)**. The complete source code and build scripts required to reproduce them are located in `src/`. Python is used strictly as a build-time compilation and packaging driver.

---

## 1. Build Prerequisites (Windows 10/11 x64)

1. **Microsoft Visual Studio 2022** (Build Tools, Community, Professional, or Enterprise) with:
   - *Desktop development with C++* (`MSVC v143` x64 `cl.exe` / `link.exe`)
   - *Windows 10/11 SDK* (`10.0.x`)
2. **Python 3.10+** (build-time script runner only; never bundled or required at runtime).
3. **Yosys x64 Windows Toolchain (OSS CAD Suite)** *(needed only when re-assembling the full release archive)*:
   - Extract OSS CAD Suite x64 (`bin/yosys.exe`, `bin/yosys-abc.exe`, `lib/*.dll`, `share/yosys/`, `license/`) and pass `--oss-cad-suite <dir>` to `src/scripts/build_combined_release.py` (or place it at `src/build/dependencies/oss-cad-suite`).

---

## 2. Reproducing All Native Binaries from `src/`

Run from a PowerShell prompt at the extracted archive root:

```powershell
python src\\scripts\\build_native_binaries.py --clean --out-dir build\\tier1_bins
```

This compiles all five native PE64 binaries from `src/src/` and `src/third_party/abc/` with `/O2 /MT /fp:precise /d2Qvec-sse2only /d1trimfile` and links them with `/DEBUG:NONE /BREPRO` (deterministic headers, statically linked CRT, zero embedded local paths, and zero instructions above SSE3):
- `build\\tier1_bins\\nextgowin.exe` — Native launcher, `gowin-path.txt` reader, Win32 version-warning dialog & `.gprj` drag-and-drop entry point (imports `KERNEL32.DLL` and `USER32.DLL` only).
- `build\\tier1_bins\\bin\\nextgowin_core.dll` — 6-stage C++20 synthesis, ABC9 wide mapping, CEC, custom P&R driver, STA & frame-CRC verification engine (imports `KERNEL32.DLL` only).
- `build\\tier1_bins\\bin\\syn_accel.dll` — SSE2/SSE3 LUT4 permutation, GF(2) bit-matrix, comment stripper, BLIF emitter & 21,872-frame CRC-16 kernel (imports `KERNEL32.DLL` only).
- `build\\tier1_bins\\bin\\ownpr_bridge.dll` — SSE2/SSE3 `SimdFlatMap64`, `HotNode` analytical/minimax placement & PathFinder negotiated-congestion timing-driven router (imports `KERNEL32.DLL` only).
- `build\\tier1_bins\\bin\\abc.exe` — 64-bit Tier 1 UC Berkeley ABC with SWAR `popcnt` & Win32 `SRWLOCK` shim (imports inbox Windows OS DLLs only).

Because `/BREPRO` and `/d1trimfile` are enabled, rebuilding with the same MSVC toolset produces bit-for-bit identical PE64 binaries matching `bin/`.

---

## 3. Rebuilding the Combined Release ZIP

```powershell
python src\\scripts\\build_combined_release.py `
  --bins-dir build\\tier1_bins `
  --oss-cad-suite C:\\path\\to\\oss-cad-suite `
  --out-zip dist\\nextgowin-win64-release.zip
```

The packager automatically verifies:
1. **Zero Python runtime artifacts** (`.py`, `.pyc`, `.pyd`, `python*.dll`, `python*.exe`) inside `bin/` or `docs/`.
2. **Zero proprietary Gowin files** anywhere in the archive.
3. **100% PE64 DLL import closure** across all 15 binaries in `bin/` (inbox Windows OS DLLs only).
4. **Zero private build-machine paths** or temporary build artifacts anywhere in the archive.

---

## 4. Verifying an Extracted Combined Release

```powershell
# 1. Extract the combined release archive
Expand-Archive dist\\nextgowin-win64-release.zip -DestinationPath C:\\Temp\\ng_combined

# 2. Configure local Gowin V1.9.12.04 x64 installation path
Set-Content -Path C:\\Temp\\ng_combined\\bin\\gowin-path.txt -Value "C:\\Gowin\\Gowin_V1.9.12.04_x64" -Encoding UTF8

# 3. Run standalone self-test with scrubbed PATH
$env:PATH = "C:\\Windows\\System32"
C:\\Temp\\ng_combined\\bin\\nextgowin.exe --self-test --no-pause

# 4. Run full 6-stage synthesis + custom P&R + STA + bitstream on the example .gprj
C:\\Temp\\ng_combined\\bin\\nextgowin.exe C:\\Temp\\ng_combined\\src\\examples\\blinky_gw5ast138\\blinky.gprj --no-pause
```
"""


DOCS_LIMITATIONS_MD = """\
# Supported Environment & Known Limitations (`docs/LIMITATIONS.md`)

1. **Operating System & CPU Baseline**:
   - Requires **64-bit Windows 10 or Windows 11** on any x86-64 processor supporting **SSE2 and SSE3** (all mainstream x86 CPUs released after 2007).
   - Higher SIMD tiers (`AVX2`, `GFNI`, `AVX-512`) and runtime tier dispatch are intentionally omitted; only the deterministic Tier 1 baseline is shipped.

2. **Local Gowin Installation Dependency**:
   - NextGowin does **not** redistribute proprietary Gowin DLLs, executables, or device databases.
   - A local Windows installation of **Gowin V1.9.12.04 x64** (`Gowin_V1.9.12.04_x64`) configured via `bin/gowin-path.txt` is officially supported and validated.
   - Frontend RTL/IP elaboration (`01_frontend`) uses Gowin's `gwsyn.dll` (`dev` + `extract` only, stopping strictly before vendor synthesis/mapping), and post-route STA / fusemap encoding (`06_pnr`) uses Gowin's `gowin.dll` (with `vendor_placement_executed = false` and `vendor_routing_executed = false`).
   - Running against a non-`V1.9.12.04` Gowin installation triggers an interactive **Continue / Cancel** warning dialog; device-database or ABI changes in untested Gowin releases may cause verification checks to reject the run.

3. **Target FPGA Device Scope**:
   - Supports **Gowin GW5AST-138C** and **Gowin GW5AST-138B** devices across commercial, industrial, and engineering-sample speed grades (`C1/I0`, `C2/I1`, and `ES` on `GW5AST-138B`) and all official GW5AST-138 packages (`PBGA484A`, `PBGA676A`, and `FCPBGA676A`, corresponding to `PG484A`, `PG676A`, and `FPG676A` in part numbers; `FCPBGA484A` does not exist), including distributed SSRAM (`RAM16*` / `ROM16` on `GW5AST-138C`, enabled by default), BSRAM, DSP, ALU, CFU latches (`DL*`), `PLL`, `DDRDLL`, `DQS`, `IODELAY`/`IDES*`/`OSER*`/`IDDR*`/`ODDR*`, `CLKDIV`, `DHCE`, `GW_JTAG`, `GTR12_QUAD` SerDes, and hard `AE350_SOC`.
   - Projects targeting other Gowin families or densities fail fast with an explicit unsupported-device error, and `RAM16*`/`ROM16` primitives on `GW5AST-138B` (`0Kb` SSRAM) are rejected cleanly matching Gowin DRC.

4. **HDL & Project Feature Scope**:
   - **Supported project inputs**: Verilog-2001 and SystemVerilog-2017 sources (`file.verilog`: `.v`, `.sv`, `.vh`, `.svh`), VHDL sources (`file.vhdl`: `.vhd`, `.vhdl`), physical constraints (`file.cst`), timing constraints (`file.sdc`), Gowin Analyzer Oscilloscope configs (`file.gao`: `.rao`), and SerDes configs (`file.csr`: `.csr`).
   - **Unsupported project inputs**:
     - Synplify Pro synthesis projects
     - `Disable_Insert_Pad=true` (padless sub-module mode) in `.gprj` drag-and-drop flows

5. **Bitstream Options & Integrity Verification Scope**:
   - Explicit `.gprj` `<Option>` entries and `impl/*_process_config.json` settings (`COMPRESS`, `CRC_CHECK`, `SECURITY_BIT`, `PRINT_BSRAM_VALUE`, `POWER_ON_RESET_MONITOR`, `VCCX`, `Unused_Pin`, dual-purpose pins) take precedence over matching keys in any existing `impl/pnr/device.cfg` while preserving extra vendor `device.cfg` directives (`DOWNLOAD_MODE`, `user_code`, `spi_flash_address`).
   - Post-route `.fs` verification (`--verify-fs`) validates the GW5AST-138 sync/header packet stream, exact 21,872-frame sequence, 160-bit end-of-frames marker, 6-byte `0xFF` frame tails, and per-frame CRC-16 words (when `CRC_check=true`) across compressed and uncompressed streams; compressed payload RLE semantics, post-frame footer commands, and physical board behavior remain outside offline frame-CRC verification.
   - Gowin's report/bitstream emitters stamp the wall-clock generation time into comment headers of `.vo`, `.tr`, `.rpt.txt`, and `.fs`. Functional netlists, placement, routing, timing slacks, and all 21,872 binary bitstream configuration frames are deterministic.

6. **Offline Bitstream Generation Only**:
   - NextGowin writes verified `.fs` bitstreams to disk and never programs FPGA hardware automatically (`board_programmed = false`). Use Gowin Programmer separately if hardware programming is desired.
"""


DOCS_NOTICES_TXT = """\
================================================================================
NEXTGOWIN THIRD-PARTY SOFTWARE NOTICES AND LICENSES
================================================================================

NextGowin's combined Windows x64 release archive includes the following
open-source and redistributable components under `bin/`, `src/`, and `docs/`.
No proprietary Gowin EDA binaries, DLLs, or device databases are distributed
in this archive.

--------------------------------------------------------------------------------
1. Yosys Open SYnthesis Suite (`bin/yosys.exe`, `bin/yosys-abc.exe`, `bin/share/`)
--------------------------------------------------------------------------------
Copyright (C) 2012 - 2026  Claire Xenia Wolf <claire@yosyshq.com>
License: ISC License (https://github.com/YosysHQ/yosys)

Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

(Full composite notices for libraries statically linked into OSS CAD Suite's
`yosys.exe` — including Boost Software License 1.0, MIT, BSD, and Public Domain
notices — are provided in `docs/licenses/LICENSE.yosys`.)

--------------------------------------------------------------------------------
2. UC Berkeley ABC (`bin/abc.exe`, `src/third_party/abc/`)
--------------------------------------------------------------------------------
ABC: System for Sequential Synthesis and Verification
http://www.eecs.berkeley.edu/~alanmi/abc/

Copyright (c) The Regents of the University of California. All rights reserved.

Permission is hereby granted, without written agreement and without license or
royalty fees, to use, copy, modify, and distribute this software and its
documentation for any purpose, provided that the above copyright notice and
the following two paragraphs appear in all copies of this software.

IN NO EVENT SHALL THE UNIVERSITY OF CALIFORNIA BE LIABLE TO ANY PARTY FOR
DIRECT, INDIRECT, SPECIAL, INCIDENTAL, OR CONSEQUENTIAL DAMAGES ARISING OUT OF
THE USE OF THIS SOFTWARE AND ITS DOCUMENTATION, EVEN IF THE UNIVERSITY OF
CALIFORNIA HAS BEEN ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

THE UNIVERSITY OF CALIFORNIA SPECIFICALLY DISCLAIMS ANY WARRANTIES, INCLUDING,
BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
A PARTICULAR PURPOSE. THE SOFTWARE PROVIDED HEREUNDER IS ON AN "AS IS" BASIS,
AND THE UNIVERSITY OF CALIFORNIA HAS NO OBLIGATION TO PROVIDE MAINTENANCE,
SUPPORT, UPDATES, ENHANCEMENTS, OR MODIFICATIONS.

Bundled SAT/BDD solver subpackages inside `src/third_party/abc/src/` (CUDD,
MiniSat, Glucose, CaDiCaL, Kissat, Satoko, Boolector, bzlib, zlib) are
distributed under their respective MIT / BSD / zlib permissive licenses; see
`docs/licenses/LICENSE.abc` and `src/third_party/abc/` for full texts.

--------------------------------------------------------------------------------
3. Runtime Shared Libraries Required by Standalone `yosys.exe` Subprocess
   (`bin/tcl86.dll`, `bin/zlib1.dll`, `bin/libffi-8.dll`, `bin/libwinpthread-1.dll`,
    `bin/libgcc_s_seh-1.dll`, `bin/libstdc++-6.dll`, `bin/libreadline8.dll`,
    `bin/libtermcap-0.dll`)
--------------------------------------------------------------------------------
NextGowin invokes `bin/yosys.exe` strictly as a separate command-line process
via standard file I/O (`CreateProcessW`). NextGowin's own executables and DLLs
(`bin/nextgowin.exe`, `bin/nextgowin_core.dll`, `bin/ownpr_bridge.dll`,
`bin/syn_accel.dll`) do not link against any MinGW, GNU Readline, or GPL/LGPL
libraries.

- **Tcl 8.6 (`bin/tcl86.dll`)**:
  Copyright (c) Regents of the University of California, Sun Microsystems, Inc.,
  Scriptics Corporation, and other parties. Distributed under the Tcl/Tk License
  (BSD-style permissive license).

- **zlib (`bin/zlib1.dll`)**:
  Copyright (C) 1995-2024 Jean-loup Gailly and Mark Adler.
  Distributed under the zlib License.

- **libffi (`bin/libffi-8.dll`)**:
  Copyright (c) 1996-2024 Anthony Green, Red Hat, Inc. and contributors.
  Distributed under the MIT License.

- **MinGW-w64 Winpthreads (`bin/libwinpthread-1.dll`)**:
  Copyright (c) 2011-2024 mingw-w64 project.
  Distributed under the MIT / 3-Clause BSD License.

- **GCC Runtime Libraries (`bin/libgcc_s_seh-1.dll`, `bin/libstdc++-6.dll`)**:
  Copyright (C) Free Software Foundation, Inc.
  Distributed under the GNU General Public License v3.0 with the GCC Runtime
  Library Exception v3.1 (https://www.gnu.org/licenses/gcc-exception-3.1.html).

- **GNU Readline & Termcap (`bin/libreadline8.dll`, `bin/libtermcap-0.dll`)**:
  Copyright (C) Free Software Foundation, Inc.
  Used solely by the standalone `yosys.exe` interactive/CLI binary from
  YosysHQ OSS CAD Suite (https://github.com/YosysHQ/oss-cad-suite-build), where
  corresponding upstream build recipes and source tarballs are published under
  the GNU General Public License v3.0 / LGPL.
"""


def stage_bin_tree(stage_root: Path, bins_dir: Path, suite_root: Path) -> dict:
    bin_dir = stage_root / "bin"
    bin_dir.mkdir(parents=True)

    # 1. Verified Tier 1 native launcher + gowin-path.txt template
    cli_src = bins_dir / "nextgowin.exe"
    if not cli_src.is_file():
        raise RuntimeError(f"Missing compiled Tier 1 nextgowin.exe at {cli_src}")
    shutil.copy2(cli_src, bin_dir / "nextgowin.exe")
    (bin_dir / "gowin-path.txt").write_text("C:\\Gowin\\Gowin_V1.9.12.04_x64\n", encoding="utf-8")

    # 2. Verified Tier 1 native DLLs + 64-bit abc.exe
    for fn in ("nextgowin_core.dll", "ownpr_bridge.dll", "syn_accel.dll", "abc.exe"):
        src = bins_dir / "bin" / fn
        if not src.is_file():
            src = bins_dir / fn
        if not src.is_file():
            raise RuntimeError(f"Missing compiled Tier 1 binary {fn} in {bins_dir}")
        shutil.copy2(src, bin_dir / fn)

    # 3. Bundled Yosys + yosys-abc + 8 DWARF-stripped MinGW runtime DLLs
    shutil.copy2(suite_root / "bin/yosys.exe", bin_dir / "yosys.exe")
    shutil.copy2(suite_root / "bin/yosys-abc.exe", bin_dir / "yosys-abc.exe")
    strip_stats = {}
    for dn in MINGW_RUNTIME_DLLS:
        orig_sz, new_sz = strip_pe_dwarf_debug(suite_root / "lib" / dn, bin_dir / dn)
        if new_sz < orig_sz:
            strip_stats[dn] = {"before_bytes": orig_sz, "after_bytes": new_sz}

    # 4. Yosys synthesis cell libraries at `bin/share/` (resolved automatically by `bin/yosys.exe` `+/...`)
    copy_yosys_share_minimal(suite_root, bin_dir / "share")

    # 5. Calibrated GW5AST-138C resource bundle at `bin/nextgowin-resources/` (auto-discovered beside `nextgowin.exe`)
    res_dir = bin_dir / "nextgowin-resources"
    res_dir.mkdir(parents=True)
    for rel in RESOURCE_REL_FILES:
        src = ROOT / rel
        dst = res_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    (res_dir / "THIRD_PARTY_NOTICES.txt").write_text(DOCS_NOTICES_TXT, encoding="utf-8")
    copy_yosys_share_minimal(suite_root, res_dir / "share/yosys")

    res_inv = {}
    for f in sorted(res_dir.rglob("*")):
        if f.is_file():
            rel_posix = f.relative_to(res_dir).as_posix()
            res_inv[rel_posix] = {
                "bytes": f.stat().st_size,
                "sha256": sha256_file(f),
            }
    res_manifest = {
        "schema": "nextgowin-resource-package-v4",
        "target_device": "GW5AST-138C / GW5AST-138B",
        "supported_target_devices": [
            "GW5AST-138C",
            "GW5AST-138B",
        ],
        "supported_gowin_version": "Gowin V1.9.12.04 x64",
        "python_files_present": False,
        "validated_gowin_sdk_sha256": {
            "bin/gwsyn.dll": "254eac1ed05c7d1168889473dcc85203032fa4cd641c83bd5bbe4ea61a55b6b0",
            "bin/gowin.dll": "bc9472a5a22cf4a94e39e07439f261f3354c668f92ef9abc735e6678bea53b7b",
            "simlib/gw5a/prim_sim.v": "7109c2568188627465d218bc099cf72f12e90807c21d4d862d2335b499043544",
            "share/device/GW5AST-138C/GW5AST-138C.dat": "19e3ec885f359ec571c09ed5d3face1b0c10971b9e0f569fdf703911993c9d33",
            "share/device/GW5AST-138C/GW5AST-138C.fse": "19f859e61df5b8da361330d42d1250bd31d03bcfa2912c3ccfbf983cc4329030",
            "share/device/GW5AST-138C/GW5AST-138C.tm": "a3558f35685ebc58eefce834e82059ce651b9e2a6cbca2d6668094153ca0707f",
            "share/device/GW5AST-138B/GW5AST-138B.dat": "481f2d3a5e373bac0779948b6b2f8eec43a0a2dd62b058a2cce4d7cb4e275586",
            "share/device/GW5AST-138B/GW5AST-138B.fse": "495da9737f17f5545978cc4b68899826ff0806073b1e26251502614ce21a74ae",
            "share/device/GW5AST-138B/GW5AST-138B.tm": "fffb1704a2d262cf4d7d1db21bb7be0419d573242a0bd606132858db5a18a44e",
        },
        "file_count": len(res_inv),
        "total_uncompressed_bytes": sum(m["bytes"] for m in res_inv.values()),
        "files": res_inv,
    }
    (res_dir / "resource_manifest.json").write_text(json.dumps(res_manifest, indent=2) + "\n", encoding="utf-8")

    # 6. Verify zero Python files inside `bin/` and 100% PE64 DLL import closure
    verify_zero_python_artifacts(bin_dir)

    pe_files = sorted(
        f for f in bin_dir.rglob("*") if f.is_file() and f.suffix.lower() in (".exe", ".dll", ".pyd")
    )
    bundled_dll_names = {f.name.upper() for f in pe_files if f.suffix.lower() == ".dll"}
    external_os_imports: set[str] = set()
    unaccounted_imports: dict[str, list[str]] = {}
    binary_inventory = {}

    for pf in pe_files:
        imps = pe_imports(pf)
        bad = []
        for imp in imps:
            iu = imp.upper()
            if iu in bundled_dll_names:
                continue
            if iu in INBOX_WINDOWS_OS_DLLS:
                external_os_imports.add(iu)
            else:
                bad.append(imp)
        if bad:
            unaccounted_imports[pf.relative_to(bin_dir).as_posix()] = bad
        binary_inventory[pf.relative_to(bin_dir).as_posix()] = {
            "bytes": pf.stat().st_size,
            "sha256": sha256_file(pf),
            "imports": imps,
        }

    if unaccounted_imports:
        raise RuntimeError(f"Standalone DLL closure FAILED in bin/: {unaccounted_imports}")

    # 7. Measured disassembly ISA check on bundled external Yosys/MinGW binaries (zero AVX/AVX2/AVX-512/GFNI/POPCNT/BMI2)
    sys.path.insert(0, str(ROOT / "scripts"))
    from build_native_binaries import find_msvc_and_sdk, make_msvc_env
    msvc, kits, sdk_ver = find_msvc_and_sdk()
    msvc_env = make_msvc_env(msvc, kits, sdk_ver)
    dumpbin_exe = str(msvc / "bin/Hostx64/x64/dumpbin.exe")
    bad_ext_isa_re = re.compile(
        r"\b(ymm\d+|zmm\d+|vmov[a-z0-9]+|vpadd[a-z0-9]+|vpxor[a-z0-9]*|vpand[a-z0-9]*|vgf2p8[a-z0-9]+|popcnt|lzcnt|bzhi|pdep|pext)\b"
    )
    external_bundled_pes = ["yosys.exe", "yosys-abc.exe", *MINGW_RUNTIME_DLLS]
    for ext_fn in external_bundled_pes:
        dis = subprocess.run([dumpbin_exe, "/disasm:nobytes", str(bin_dir / ext_fn)], env=msvc_env, capture_output=True, text=True)
        if dis.returncode != 0:
            raise RuntimeError(f"dumpbin ISA audit failed on {ext_fn}")
        hits = sorted(set(m.group(1) for m in bad_ext_isa_re.finditer(dis.stdout)))
        if hits:
            raise RuntimeError(f"Bundled binary {ext_fn} contains non-Tier-1 instructions: {hits}")

    env_manifest = {
        "schema": "nextgowin-combined-bin-v1-tier1-sse3",
        "launcher": "nextgowin.exe",
        "gowin_config_file": "gowin-path.txt",
        "resources_dir": "nextgowin-resources",
        "yosys_share_dir": "share",
        "supported_gowin_version": "Gowin V1.9.12.04 x64",
        "execution_engine": "native_cpp20_tier1_sse3",
        "target_isa": "x86-64 + SSE2 + SSE3 (All x86 CPUs released after 2007)",
        "higher_tier_binaries_present": False,
        "runtime_tier_selection_present": False,
        "isa_audit_verified": True,
        "isa_audit_scope": "dumpbin disassembly verified zero AVX/AVX2/AVX-512/GFNI/POPCNT/BMI2 across all NextGowin .obj units and all 10 bundled Yosys/MinGW binaries (MSVC static CRT routines remain __isa_available-guarded).",
        "python_runtime_present": False,
        "python_files_count": 0,
        "pyd_modules_count": 0,
        "stripped_debug_sections": strip_stats,
        "pe_binary_count": len(pe_files),
        "external_inbox_os_dlls_only": sorted(external_os_imports),
        "non_os_external_dll_imports": 0,
        "binaries": binary_inventory,
    }
    (bin_dir / "env_manifest.json").write_text(json.dumps(env_manifest, indent=2) + "\n", encoding="utf-8")

    return {
        "pe_binary_count": len(pe_files),
        "external_os_dlls": sorted(external_os_imports),
        "resource_file_count": len(res_inv) + 1,
        "uncompressed_bytes": sum(f.stat().st_size for f in bin_dir.rglob("*") if f.is_file()),
    }


def stage_docs_tree(stage_root: Path, suite_root: Path) -> dict:
    docs_dir = stage_root / "docs"
    lic_dir = docs_dir / "licenses"
    lic_dir.mkdir(parents=True)

    (docs_dir / "BUILD.md").write_text(DOCS_BUILD_MD, encoding="utf-8")
    (docs_dir / "USAGE.md").write_text(DOCS_USAGE_MD, encoding="utf-8")
    (docs_dir / "LIMITATIONS.md").write_text(DOCS_LIMITATIONS_MD, encoding="utf-8")
    (docs_dir / "THIRD_PARTY_NOTICES.txt").write_text(DOCS_NOTICES_TXT, encoding="utf-8")

    yosys_lic = suite_root / "license/LICENSE.yosys"
    if not yosys_lic.is_file():
        yosys_lic = ROOT / "licenses/LICENSE.yosys"
    if not yosys_lic.is_file():
        raise RuntimeError("Missing LICENSE.yosys")
    shutil.copy2(yosys_lic, lic_dir / "LICENSE.yosys")

    abc_lic = ROOT / "third_party/abc/copyright.txt"
    if not abc_lic.is_file():
        raise RuntimeError("Missing third_party/abc/copyright.txt")
    shutil.copy2(abc_lic, lic_dir / "LICENSE.abc")

    verify_zero_python_artifacts(docs_dir)
    return {
        "file_count": sum(1 for f in docs_dir.rglob("*") if f.is_file()),
        "uncompressed_bytes": sum(f.stat().st_size for f in docs_dir.rglob("*") if f.is_file()),
    }


def stage_src_tree(stage_root: Path, suite_root: Path) -> dict:
    src_root = stage_root / "src"
    src_root.mkdir(parents=True)

    for fn in ("README.md", "BUILD.md", "THIRD_PARTY_NOTICES.txt", ".gitignore"):
        if (ROOT / fn).is_file():
            shutil.copy2(ROOT / fn, src_root / fn)

    lic_dir = src_root / "licenses"
    lic_dir.mkdir(parents=True)
    yosys_lic = suite_root / "license/LICENSE.yosys"
    if not yosys_lic.is_file():
        yosys_lic = ROOT / "licenses/LICENSE.yosys"
    if yosys_lic.is_file():
        shutil.copy2(yosys_lic, lic_dir / "LICENSE.yosys")
    abc_lic = ROOT / "third_party/abc/copyright.txt"
    if abc_lic.is_file():
        shutil.copy2(abc_lic, lic_dir / "LICENSE.abc")

    src_rels = [
        "src/cpu_dispatch.hpp",
        "src/nextgowin_cli.cpp",
        "src/ownpr_bridge_tiered.cpp",
        "src/syn_accel_tiered.cpp",
        "src/win32_pthread_shim.c",
        "src/native/ng_common.hpp",
        "src/native/ng_common.cpp",
        "src/native/ng_json.hpp",
        "src/native/ng_json.cpp",
        "src/native/ng_pipeline.hpp",
        "src/native/ng_frontend_bridge.cpp",
        "src/native/ng_mapper_cec.cpp",
        "src/native/ng_emitter_opt.cpp",
        "src/native/ng_ownpr_driver.cpp",
        "src/native/ng_flow_main.cpp",
        "scripts/build_native_binaries.py",
        "scripts/build_standalone_packages.py",
        "scripts/build_combined_release.py",
        "third_party/abc_sources.txt",
        "third_party/abc/copyright.txt",
        "third_party/abc/lib/pthread.h",
        "third_party/abc/lib/sched.h",
        "third_party/abc/lib/semaphore.h",
        *RESOURCE_REL_FILES,
    ]
    for rel in src_rels:
        s = ROOT / rel
        if not s.is_file():
            raise RuntimeError(f"Missing required source file: {s}")
        d = src_root / rel
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s, d)

    ex_root = ROOT / "examples/blinky_gw5ast138"
    if ex_root.is_dir():
        for f in sorted(ex_root.rglob("*")):
            if f.is_file():
                rel = f.relative_to(ROOT)
                d = src_root / rel
                d.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, d)

    abc_src_root = ROOT / "third_party/abc/src"
    allowed_abc_exts = {".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".tpp", ".inc", ".txt", ""}
    for f in sorted(abc_src_root.rglob("*")):
        if not f.is_file():
            continue
        ext = f.suffix.lower()
        nl = f.name.lower()
        if ext in allowed_abc_exts or "license" in nl or "copyright" in nl:
            if ext in (".obj", ".o", ".lib", ".a", ".pdb", ".exe", ".dll", ".pyc", ".pyd"):
                continue
            rel = f.relative_to(ROOT)
            d = src_root / rel
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, d)

    return {
        "file_count": sum(1 for f in src_root.rglob("*") if f.is_file()),
        "uncompressed_bytes": sum(f.stat().st_size for f in src_root.rglob("*") if f.is_file()),
    }


def audit_combined_zip(zip_path: Path) -> None:
    forbidden_subs = build_forbidden_private_substrings()
    allowed_top = {"README.md", "SHA256SUMS.txt", "bin", "docs", "src"}
    win_users_bs = b"c:" + b"\\users\\"
    win_users_fs = b"c:" + b"/users/"

    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            parts = Path(info.filename).parts
            if not parts or parts[0] not in allowed_top:
                raise RuntimeError(f"Unexpected root entry in {zip_path.name}: {info.filename}")

            top = parts[0]
            bn = Path(info.filename).name.lower()
            ext = Path(info.filename).suffix.lower()

            if bn in FORBIDDEN_GOWIN_FILENAMES:
                raise RuntimeError(f"Proprietary Gowin file detected inside {zip_path.name}: {info.filename}")

            if ext in (".obj", ".lib", ".pdb", ".pyc", ".pyo", ".pyd"):
                raise RuntimeError(f"Forbidden build/Python binary artifact inside {zip_path.name}: {info.filename}")

            if top in ("bin", "docs") and (ext == ".py" or bn.startswith("python")):
                raise RuntimeError(f"Python file detected inside {top}/ of {zip_path.name}: {info.filename}")

            if top == "src" and bn.startswith("python") and ext in (".dll", ".exe"):
                raise RuntimeError(f"Python runtime binary inside src/ of {zip_path.name}: {info.filename}")

            is_self_packager = info.filename.endswith(
                ("scripts/build_standalone_packages.py", "scripts/build_combined_release.py")
            )
            low_data = zf.read(info.filename).lower()
            for needle in forbidden_subs:
                if needle in low_data:
                    raise RuntimeError(
                        f"Private path/identifier {needle!r} detected inside {zip_path.name} -> {info.filename}"
                    )
            if not is_self_packager:
                for pfx in (win_users_bs, win_users_fs):
                    idx = 0
                    while True:
                        idx = low_data.find(pfx, idx)
                        if idx < 0:
                            break
                        tail = low_data[idx + len(pfx) : idx + len(pfx) + 8]
                        if not tail.startswith(b"public"):
                            raise RuntimeError(
                                f"Private user path {low_data[idx:idx+32]!r} detected inside {zip_path.name} -> {info.filename}"
                            )
                        idx += len(pfx)


def main() -> None:
    ap = argparse.ArgumentParser(description="Build NextGowin single combined release ZIP (bin + src + docs).")
    ap.add_argument(
        "--bins-dir",
        type=Path,
        default=Path(os.environ.get("NEXTGOWIN_BINS_DIR", str(ROOT / "build/tier1_bins"))),
    )
    ap.add_argument(
        "--oss-cad-suite",
        type=Path,
        default=ROOT / "build/dependencies/oss-cad-suite",
    )
    ap.add_argument(
        "--out-zip",
        type=Path,
        default=ROOT / "dist/nextgowin-release/nextgowin-win64-release.zip",
    )
    args = ap.parse_args()

    _ws_reserve(ROOT, 150_000_000)

    bins_dir = args.bins_dir.resolve()
    suite_root = args.oss_cad_suite.resolve()
    out_zip = args.out_zip.resolve()
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    if out_zip.exists():
        out_zip.unlink()

    with tempfile.TemporaryDirectory(prefix="ng_combined_stage_") as tmp_s:
        stage_root = Path(tmp_s)

        (stage_root / "README.md").write_text(ROOT_README_MD, encoding="utf-8")
        bin_info = stage_bin_tree(stage_root, bins_dir, suite_root)
        docs_info = stage_docs_tree(stage_root, suite_root)
        src_info = stage_src_tree(stage_root, suite_root)

        # Generate root SHA256SUMS.txt covering README.md + all files in bin/, docs/, and src/
        sum_lines = []
        for f in sorted(stage_root.rglob("*")):
            if f.is_file() and f.name != "SHA256SUMS.txt":
                rel_posix = f.relative_to(stage_root).as_posix()
                sum_lines.append(f"{sha256_file(f)}  {rel_posix}")
        (stage_root / "SHA256SUMS.txt").write_text("\n".join(sum_lines) + "\n", encoding="utf-8")

        with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for f in sorted(stage_root.rglob("*")):
                if f.is_file():
                    arcname = f.relative_to(stage_root).as_posix()
                    zf.write(f, arcname)

    audit_combined_zip(out_zip)

    zip_sha = sha256_file(out_zip)
    zip_bytes = out_zip.stat().st_size
    sha_sidecar = out_zip.with_name(out_zip.name + ".sha256")
    sha_sidecar.write_text(f"{zip_sha}  {out_zip.name}\n", encoding="utf-8")

    summary = {
        "zip_path": str(out_zip),
        "zip_bytes": zip_bytes,
        "zip_mib": round(zip_bytes / (1024.0 * 1024.0), 3),
        "zip_sha256": zip_sha,
        "sha256_sidecar": str(sha_sidecar),
        "checksummed_entries": len(sum_lines),
        "bin": bin_info,
        "docs": docs_info,
        "src": src_info,
    }
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
