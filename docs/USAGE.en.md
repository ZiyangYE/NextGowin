# NextGowin Usage & Architecture Guide (`docs/USAGE.md`)

## 1. Gowin Path & Version Configuration (`bin/gowin-path.txt`)

NextGowin reads the local Gowin EDA installation path from the single-line UTF-8 text file `bin/gowin-path.txt` (located directly beside `bin/nextgowin.exe`):

```text
C:\Gowin\Gowin_V1.9.12.04_x64
```

- You may specify either the Gowin root directory (containing `IDE\bin\`) or the `IDE` directory itself.
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
.\bin\nextgowin.exe path\to\project.gprj

# Run .gprj without modifying <project_dir>\impl\
.\bin\nextgowin.exe path\to\project.gprj --no-sync-ide --no-pause

# Custom output directory and batch mode (no interactive end-of-run pause)
.\bin\nextgowin.exe path\to\project.gprj --out .\build_out --no-pause

# Override Gowin installation directory on the command line
.\bin\nextgowin.exe path\to\project.gprj --gowin-dir "C:\Gowin\Gowin_V1.9.12.04_x64" --no-pause

# Run JSON configuration flow
.\bin\nextgowin.exe --config config.json --out .\flow_out --no-pause

# Run standalone environment, DLL import, resource SHA-256, and Gowin SDK self-test
.\bin\nextgowin.exe --self-test --no-pause

# Print detected CPU features and Tier 1 baseline status
.\bin\nextgowin.exe --cpu-info --no-pause
```

---

## 4. Output Directory Structure & Gowin IDE Integration

For `<project>.gprj`, NextGowin keeps all user source/constraint/project files (`.gprj`, `.v`/`.sv`, `.cst`, `.sdc`) completely unmodified and writes outputs to two places:

1. **Gowin IDE `impl/` Directory (`<project_dir>\impl\`, enabled by default; disable with `--no-sync-ide`)**:
   - Overwrites existing Gowin IDE build artifacts using `OUTPUT_BASE_NAME` from `impl\<project>_process_config.json` (defaulting to `<project>`), preserving any existing `impl\pnr\device.cfg` settings:
     - `impl\gwsynthesis\<base>.vg` — NextGowin synthesized & mapped structural netlist
     - `impl\pnr\<base>.fs` — CRC-16 verified GW5AST-138C bitstream ready for Gowin Programmer download
     - `impl\pnr\<base>.rpt.html` & `<base>.rpt.txt` — Place & Route utilization and routing reports viewable in Gowin IDE
     - `impl\pnr\<base>.tr.html`, `<base>_tr_cata.html`, `<base>_tr_content.html` & `<base>.tr` — Static Timing Analysis HTML/text reports viewable in Gowin IDE
     - `impl\pnr\<base>.pin.html` & `<base>.pin.txt` — Package pinout reports viewable in Gowin IDE
     - `impl\pnr\<base>.vo` — Post-route structural Verilog netlist

2. **NextGowin Detailed Run Directory (`<project_dir>\<project>_nextgowin\`)**:
   - `<project>.fs`, `<project>.tr`, `<project>.tr.html`, `<project>.rpt.html`, `<project>.rpt.txt`, `<project>.pin.html`, `<project>.vo`
   - `nextgowin.log` — Timestamped console & diagnostic log (including any Gowin version warning)
   - `manifest.json` — Machine-readable stage metrics, SHA-256 provenance, `ide_sync` paths, and verification status
   - `01_frontend/` .. `06_pnr/` — Per-stage checkpoints, equivalence proofs, and live route/exemption audits
