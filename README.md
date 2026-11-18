# NextGowin

面向高云（Gowin）FPGA 的综合（Synthesis）与布局布线（Place & Route，P&R）工具项目。

**首版仅面向 138K 器件。** 先围绕这一目标打通流程、验证结果，再考虑扩展支持范围。

## 为什么做这个项目

这个项目源于我在使用高云现有工具时的实际体验：P&R 太难用了，而我需要的时序驱动综合能力也没有得到满足。

我希望时序目标能够从综合阶段就参与优化决策，并贯穿后续的布局布线过程。遇到时序问题时，工具也应该提供足够清晰的反馈，让优化有依据，而不是反复尝试参数、等待结果。

NextGowin 就是为此开始的：为高云 FPGA 开发一套更好用、以时序优化为核心目标的综合与布局布线工具。

## 项目目标

- **时序驱动综合**：让时序约束参与综合与映射过程中的优化决策。
- **时序驱动布局布线**：围绕关键路径改进布局与布线结果。
- **清晰的诊断与报告**：让资源使用、关键路径和时序瓶颈更容易理解。
- **可复现的流程**：让配置、运行与结果比较更直接，方便持续迭代。

以上是开发目标，不代表当前已经实现的功能。

## 首版范围

首版聚焦 **138K**，暂不承诺支持其他容量或系列的高云器件。

具体器件型号、封装、速度等级，以及可用的逻辑、存储和专用资源支持情况，将随实现与验证进度补充。首版的重点是把单一目标做扎实，而不是尽早覆盖整个产品线。

## Windows x64 发行版

[下载运行程序、配套源码和示例](https://github.com/ZiyangYE/NextGowin/releases/tag/win64-20261118)。以下命令在解压后的目录运行。



1. 将整个 ZIP 解压到可写目录。在本机安装 Gowin V1.9.12.04 x64，把安装目录（例如 `C:\Gowin\Gowin_V1.9.12.04_x64`）写入 `bin/gowin-path.txt`。
2. 在解压目录打开 PowerShell，运行自检，再用随包示例生成位流。

```powershell
.\bin\nextgowin.exe --self-test --no-pause
.\bin\nextgowin.exe .\src\examples\blinky_gw5ast138\blinky.gprj --no-sync-ide --no-pause
```

3. 在工程旁的 `blinky_nextgowin` 目录查看位流、时序报告和日志。运行自己的工程时，也可以把 `.gprj` 拖到 `bin/nextgowin.exe` 上。**默认运行会覆盖工程中 `impl/gwsynthesis` 和 `impl/pnr` 下的同名生成文件**。上例中的 `--no-sync-ide` 可关闭同步。

[使用指南](docs/USAGE.md) · [构建说明](docs/BUILD.md) · [支持范围与限制](docs/LIMITATIONS.md) · [第三方组件](docs/THIRD_PARTY.md) · [English](README.en.md)


## 致谢

感谢 Gemini Carbon 和 astra6.2 对 NextGowin 项目的帮助，也感谢 Yosys、UC Berkeley ABC 及其他开源组件的维护者。

本项目为独立项目。
