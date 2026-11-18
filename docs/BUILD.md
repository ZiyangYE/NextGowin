# 从源码构建 Windows x64 发行包

以下命令在解压目录的 PowerShell 中执行。构建需要 Visual Studio 2022 的「使用 C++ 的桌面开发」、MSVC v143 x64、Windows 10/11 SDK，以及 Python 3.10 或更高版本。重新组包还需要 Windows x64 版 OSS CAD Suite，其中应包含 Yosys、配套 DLL、共享文件和许可文本。

## 编译

```powershell
python src\scripts\build_native_binaries.py --clean --out-dir build\tier1_bins
```

生成的五个原生文件分别是启动器 `nextgowin.exe`、流程驱动 `nextgowin_core.dll`、综合加速库 `syn_accel.dll`、布局布线库 `ownpr_bridge.dll` 和逻辑综合与验证工具 `abc.exe`。构建目标是 x86-64 + SSE2/SSE3，使用静态 CRT，启用 `/BREPRO`。逐字节比较构建结果时应记录并匹配 MSVC 工具集。

## 重新组包

```powershell
python src\scripts\build_combined_release.py --bins-dir build\tier1_bins --oss-cad-suite C:\tools\oss-cad-suite --out-zip dist\nextgowin-win64-release.zip
```

打包脚本检查运行目录中的 Python 文件、Gowin 专有文件、PE64 动态库依赖及构建机器的本地路径。上游脚本生成的文档以其内置模板为准。本次 GitHub 发行包另外整理了中文指南及文件校验清单。

## 验证

解压新包，在 `bin/gowin-path.txt` 填写本机 Gowin 安装目录。从新包根目录运行：

```powershell
.\bin\nextgowin.exe --self-test --no-pause
.\bin\nextgowin.exe .\src\examples\blinky_gw5ast138\blinky.gprj --no-sync-ide --no-pause
```

[英文构建说明](BUILD.en.md)
