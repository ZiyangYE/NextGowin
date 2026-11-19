# NextGowin v0.1.1

NextGowin 是面向高云 GW5AST-138C 和 GW5AST-138B 的 Windows x64 FPGA 综合与布局布线工具。输入是 Gowin IDE 的 `.gprj` 工程，输出包括可交给 Gowin Programmer 的 `.fs` 位流、布线后网表、引脚分配和时序报告。

[下载 Windows x64 运行包](https://github.com/ZiyangYE/NextGowin/releases/download/v0.1.1/nextgowin-win64-release.zip) · [版本说明与 SHA-256](https://github.com/ZiyangYE/NextGowin/releases/tag/v0.1.1) · [English](README.en.md)

## v0.1.1 更新

本版修复了 LED 示例中进位链首级和寄存器常量输入的位流编码，改进 RAM 切片与初始化、PLL 初始化属性和器件料号识别。新增 `--verify-fs`，可独立检查压缩及 CRC 不同设置下的位流帧结构。

## 第一次运行

1. 准备 Windows 10/11 x64、支持 SSE2 和 SSE3 的处理器，以及本机安装的 **Gowin V1.9.12.04 x64**。运行包自带 Yosys、ABC 和所需运行库，无需安装 Python。
2. 解压整个运行包，用文本编辑器打开 `bin/gowin-path.txt`，填入 Gowin 安装根目录，例如 `C:\Gowin\Gowin_V1.9.12.04_x64`。
3. 从解压目录运行环境自检。它会检查原生 DLL、综合工具、资源文件和本机 Gowin SDK。

```powershell
.\bin\nextgowin.exe --self-test --no-pause
```

4. 把自己的 `.gprj` 拖到 `bin/nextgowin.exe` 上，或在 PowerShell 中指定工程路径。想先试一个小工程，可以从[对应版本的源码](https://github.com/ZiyangYE/NextGowin/tree/v0.1.1/examples/blinky_gw5ast138)取得完整的 `blinky_gw5ast138` 目录，其中包含工程、RTL、引脚约束和时序约束。

```powershell
.\bin\nextgowin.exe "D:\FPGA\blinky_gw5ast138\blinky.gprj" --no-sync-ide --no-pause
```

**默认会覆盖工程中 `impl/gwsynthesis` 和 `impl/pnr` 下的同名生成文件。** 上例使用 `--no-sync-ide`，结果单独保存在工程旁的 `blinky_nextgowin` 目录。先查看 `.tr` 的建立与保持时序、`.pin.html` 的引脚分配，再决定是否用 Gowin Programmer 下载位流。

## 从 RTL 到位流

| 阶段 | 工作内容 |
| --- | --- |
| 工程读取与展开 | 读取工程、HDL、CST 和 SDC，调用本机 Gowin 前端展开层次、参数和 IP。 |
| 综合与映射 | NextGowin 配合 Yosys 和 ABC 推断存储器、DSP 与进位链，把逻辑映射到 LUT 和多路选择器，并进行组合等价检查。 |
| 布局与布线 | NextGowin 的布局器和布线器根据时序调整单元位置与走线，检查布线树。 |
| 报告与位流 | 调用本机 Gowin 库进行布线后静态时序分析和位流编码，检查配置帧 CRC，并输出 `.fs`、`.vo`、资源及引脚报告。 |

## 工程开发与构建

[源码目录](https://github.com/ZiyangYE/NextGowin/tree/v0.1.1/src)包含 C++ 启动器和六阶段流程实现。构建脚本在 `scripts/`，映射配置在 `configs/`，时序与布线数据在 `results/`，随包构建的 ABC 源码在 `third_party/abc/`。开发者可以从发布标签检出与此版本对应的文件。

```powershell
git clone --branch v0.1.1 https://github.com/ZiyangYE/NextGowin.git
cd NextGowin
python scripts\build_native_binaries.py --clean --out-dir build\tier1_bins
```

详细说明见[使用指南](docs/USAGE.md)、[构建说明](docs/BUILD.md)、[器件与工程限制](docs/LIMITATIONS.md)和[第三方许可](docs/THIRD_PARTY.md)。

## 致谢

感谢 Google DeepMind 的 **Gemini 4 Carbon** 和 OpenAI 的 **GPT-6.2 Astra** 对项目开发的帮助。感谢 Yosys、UC Berkeley ABC 及其他开源组件的作者和维护者。

NextGowin 是独立项目。
