# Build v0.1.0 from the Git checkout

Install Visual Studio 2022 (MSVC v143 x64), the Windows SDK and Python 3.10+. At the repository root, run:

```powershell
python scripts\build_native_binaries.py --clean --out-dir build\tier1_bins
python scripts\build_combined_release.py --bins-dir build\tier1_bins --oss-cad-suite C:\tools\oss-cad-suite --out-zip dist\combined.zip
python scripts\package_runtime.py dist\combined.zip dist\nextgowin-win64-release.zip
```

The packaging step requires the Windows x64 OSS CAD Suite. Extract the runtime, configure your Gowin installation in `bin/gowin-path.txt` and run `bin/nextgowin.exe --self-test --no-pause`.
