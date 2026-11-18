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
python src\scripts\build_native_binaries.py --clean --out-dir build\tier1_bins
```

This compiles all five native PE64 binaries from `src/src/` and `src/third_party/abc/` with `/O2 /MT /fp:precise /d2Qvec-sse2only /d1trimfile` and links them with `/DEBUG:NONE /BREPRO` (deterministic headers, statically linked CRT, zero embedded local paths, and zero instructions above SSE3):
- `build\tier1_bins\nextgowin.exe` — Native launcher, `gowin-path.txt` reader, Win32 version-warning dialog & `.gprj` drag-and-drop entry point (imports `KERNEL32.DLL` and `USER32.DLL` only).
- `build\tier1_bins\bin\nextgowin_core.dll` — 6-stage C++20 synthesis, ABC9 wide mapping, CEC, custom P&R driver, STA & frame-CRC verification engine (imports `KERNEL32.DLL` only).
- `build\tier1_bins\bin\syn_accel.dll` — SSE2/SSE3 LUT4 permutation, GF(2) bit-matrix, comment stripper, BLIF emitter & 21,872-frame CRC-16 kernel (imports `KERNEL32.DLL` only).
- `build\tier1_bins\bin\ownpr_bridge.dll` — SSE2/SSE3 `SimdFlatMap64`, `HotNode` analytical/minimax placement & PathFinder negotiated-congestion timing-driven router (imports `KERNEL32.DLL` only).
- `build\tier1_bins\bin\abc.exe` — 64-bit Tier 1 UC Berkeley ABC with SWAR `popcnt` & Win32 `SRWLOCK` shim (imports inbox Windows OS DLLs only).

Because `/BREPRO` and `/d1trimfile` are enabled, rebuilding with the same MSVC toolset produces bit-for-bit identical PE64 binaries matching `bin/`.

---

## 3. Rebuilding the Combined Release ZIP

```powershell
python src\scripts\build_combined_release.py `
  --bins-dir build\tier1_bins `
  --oss-cad-suite C:\path\to\oss-cad-suite `
  --out-zip dist\nextgowin-win64-release.zip
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
Expand-Archive dist\nextgowin-win64-release.zip -DestinationPath C:\Temp\ng_combined

# 2. Configure local Gowin V1.9.12.04 x64 installation path
Set-Content -Path C:\Temp\ng_combined\bin\gowin-path.txt -Value "C:\Gowin\Gowin_V1.9.12.04_x64" -Encoding UTF8

# 3. Run standalone self-test with scrubbed PATH
$env:PATH = "C:\Windows\System32"
C:\Temp\ng_combined\bin\nextgowin.exe --self-test --no-pause

# 4. Run full 6-stage synthesis + custom P&R + STA + bitstream on the example .gprj
C:\Temp\ng_combined\bin\nextgowin.exe C:\Temp\ng_combined\src\examples\blinky_gw5ast138\blinky.gprj --no-pause
```
