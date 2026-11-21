# 构建 NextGowin v0.1.2

以下命令从 GitHub 源码仓库根目录执行。安装 Visual Studio 2022 的「使用 C++ 的桌面开发」工作负载、MSVC v143 x64、Windows 10/11 SDK 和 Python 3.10 或更新版本。运行发行程序仅需运行包和本机 Gowin 安装。

```powershell
python scripts\build_native_binaries.py --clean --out-dir build\tier1_bins
```

启动器位于 `build/tier1_bins/nextgowin.exe`。同目录的 `bin/` 子目录包含流程驱动、综合加速库、布局布线库和 ABC。若要组装包含 Yosys 和器件资源的完整运行环境，还需 Windows x64 版 OSS CAD Suite。传入其实际安装路径，依次生成构建中间包和最终运行 ZIP：

```powershell
python scripts\build_combined_release.py --bins-dir build\tier1_bins --oss-cad-suite C:\tools\oss-cad-suite --out-zip dist\combined.zip
python scripts\package_runtime.py dist\combined.zip dist\nextgowin-win64-release.zip
```

解压最终 ZIP，在 `bin/gowin-path.txt` 中配置本机 Gowin V1.9.12.04 x64，然后执行 `bin\nextgowin.exe --self-test --no-pause`。运行包内的 `SHA256SUMS.txt` 可逐项核验。[源码与示例](https://github.com/ZiyangYE/NextGowin/tree/v0.1.2) · [English](BUILD.en.md)
