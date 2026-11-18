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
   - Supports **Gowin GW5AST-138C** and **Gowin GW5AST-138B** devices across commercial, industrial, automotive, and engineering-sample speed grades (`C1/I0`, `C2/I1`, `ES`, etc.) and supported packages (`FCPBGA676A`, `FCPBGA484A`, etc.), including distributed SSRAM (`RAM16*` / `ROM16` on `GW5AST-138C`, enabled by default), BSRAM, DSP, ALU, CFU latches (`DL*`), `PLL`, `DDRDLL`, `DQS`, `IODELAY`/`IDES*`/`OSER*`/`IDDR*`/`ODDR*`, `CLKDIV`, `DHCE`, `GW_JTAG`, `GTR12_QUAD` SerDes, and hard `AE350_SOC`.
   - Projects targeting other Gowin families or densities fail fast with an explicit unsupported-device error, and `RAM16*`/`ROM16` primitives on `GW5AST-138B` (`0Kb` SSRAM) are rejected cleanly matching Gowin DRC.

4. **HDL & Project Feature Scope**:
   - **Supported project inputs**: Verilog-2001 and SystemVerilog-2017 sources (`file.verilog`: `.v`, `.sv`, `.vh`, `.svh`), VHDL sources (`file.vhdl`: `.vhd`, `.vhdl`), physical constraints (`file.cst`), timing constraints (`file.sdc`), Gowin Analyzer Oscilloscope configs (`file.gao`: `.rao`), and SerDes configs (`file.csr`: `.csr`).
   - **Unsupported project inputs**:
     - Synplify Pro synthesis projects
     - `Disable_Insert_Pad=true` (padless sub-module mode) in `.gprj` drag-and-drop flows

5. **Post-Route Timestamp Metadata**:
   - Gowin's report/bitstream emitters stamp the wall-clock generation time into comment headers of `.vo`, `.tr`, `.rpt.txt`, and line 1 of `.fs` (`//Generate Time: ...`). Functional netlists, placement, routing, timing slacks, and all 21,872 binary bitstream configuration frames and CRC-16 words are deterministic.

6. **Offline Bitstream Generation Only**:
   - NextGowin writes verified `.fs` bitstreams to disk and never programs FPGA hardware automatically (`board_programmed = false`). Use Gowin Programmer separately if hardware programming is desired.
