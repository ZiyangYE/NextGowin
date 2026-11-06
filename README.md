# NextGowin

> **WIP — 2026-11-06。** 优化工作已暂停。本分支提供算法源码和一个小设计的完整复现入口；运行依赖从 Release 分块下载，每块不超过 8 MB。见 [小设计复现步骤](repro/README.md) 和 [WIP 说明](docs/WIP.md)。

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

## 当前状态

已提交综合映射、自研打包/布局/布线、时序反馈、等价检查和原厂最终 STA/编码适配的实验源码。自研 P&R 入口为 `scripts/run_ownpr_region_coopt.py`；历史 `scripts/nextgowin.py` 完整命令会调用原厂 P&R，不能作为自研 P&R 的结果。

本地完整数据下，VexRiscv 最近达到 **74.0 MHz、完整 I/O 时序零违例**；这是已有 mapped checkpoint 的增量结果，尚未实现 90/150/200 MHz、通用优于原厂或冷 RTL 一键闭环。本包未携带该实验的大型证据和器件校准数据；摘要及证据哈希见 [本地结果摘要](docs/local_result_summary.json)。

只验证不依赖厂商工具/器件数据的轻量算法测试（Python 3.11）：

```sh
python -m pip install -r requirements-dev.txt
python tools/verify_source_package.py
python -m pytest -q
```

小设计完整复现（Windows x64，无需预装 Python）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\repro\Get-Dependencies.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\repro\Run-Small.ps1
```

Git 中只保存源码、小例子及校验清单；对应 WIP Release 提供经过裁剪的 Gowin/ABC/Yosys/Python 运行时和本地提取的校准数据。没有使用开源芯片数据库，不附带旧交接 ZIP。小例子约束为 20 MHz，不能用它替代 VexRiscv 或其他设计的性能评测。

## 开发方向

- 明确 138K 目标器件的资源与约束模型。
- 建立综合、技术映射与布局布线的基础流程。
- 引入时序分析与时序驱动优化。
- 使用测试设计验证正确性，并评估资源占用、运行时间和时序表现。

---

本项目为独立项目，并非高云官方产品，也不代表高云官方立场。
