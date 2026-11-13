# NextGowin — WIP checkpoint, 2026-11-13

面向 Gowin GW5AST-138 的通用综合与自研布局布线协同优化。此次发布是按用户要求整理的中间点，优化暂停，尚未完成目标。

目标是让 UART、int2float、TMDS、VexRiscv **分别超过公平原厂综合 + P&R 基线20%**。保留固定引脚、接口周期、初始化语义及完整 I/O 时序；允许使用高云最终 STA 和位流编码，不使用开源器件数据库，不自动烧录硬件。

当前保留的自研实现原生报告如下。公平基线仍未冻结，完整源级/配置证明尚未完成，因此这些数字不能解读为已实现20%优势或对应目标约束已闭合。

| 工程 | 原生报告 Fmax（MHz） |
| --- | ---: |
| UART | 197.028 |
| int2float | 167.613 |
| TMDS | 137.743 |
| VexRiscv | 90.380 |

## 中间点内容

- 当前综合、打包、自研布局布线、原生导出及验证源码，以及历史探索文档和测试。
- 4个当前实现的76项哈希绑定证据，包括时序报告。
- 最新存储器证明输入、模型、命令、日志、反例和67项检查点回归。
- 标签常量位压缩的逐位映射、经原始模型验证的补齐位投影、33组隐藏流水状态候选、183个已证明状态不变量。

**完整等价性仍未证明。** 最新实际四阶归纳反例剩下4组存储器 `write_high` 条件；它不是从原始初值出发的可达硬件反例。复现这个失败不会被计为功能验证成功。[当前进展及下一步](docs/PADDING_PROJECTION_CHECKPOINT_20261113.md)，[位映射诊断](docs/MEMORY_LANE_AND_CLOCK_PROOF_20261113.md)。

## 独立复现

Windows x64、PowerShell，仓库路径尽量短。先安装已发布的固定运行依赖，再安装本次数据：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\repro\Get-Dependencies.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\checkpoint\Get-Checkpoint.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\checkpoint\Run-Checkpoint.ps1 -Mode audit
powershell -NoProfile -ExecutionPolicy Bypass -File .\checkpoint\Run-Checkpoint.ps1 -Mode tests
powershell -NoProfile -ExecutionPolicy Bypass -File .\checkpoint\Run-Checkpoint.ps1 -Mode projection
powershell -NoProfile -ExecutionPolicy Bypass -File .\checkpoint\Run-Checkpoint.ps1 -Mode reproduce
```

固定运行依赖约185 MB，沿用既有发布；新增数据约31 MB，分成4块，每块不超过8,000,000字节，逐块及整体核验 SHA-256。无需预装 Python、Yosys 或 ABC。[模式和证据边界](checkpoint/README.md)。

`audit` 核对保留的证据和报告，不重新执行 P&R。`projection` 重建最新投影控制器并比较字节哈希。`reproduce` 预期重现尚未解决的归纳反例；超时、不同反例或意外“通过”都会要求复查。

本次源码也重新通过了六行 RTL 的完整综合、自研 P&R、hold 修复和 STA/位流流程：20 MHz 约束下 setup +44.407 ns、hold +0.022 ns，约340秒。安装运行依赖后执行 `repro/Run-Small.ps1` 即可；这不是大型 CPU 的性能证明。早期固定标签 [wip-small-repro-20261106](https://github.com/ZiyangYE/NextGowin/tree/wip-small-repro-20261106) 及其原始验证记录保持不变。本次新增数据不是整个工作目录，也不是所有历史大工程的完整运行缓存。

## 继续开发

先解决使能与无效分支地址之间的关联，以实际阵列转移语义证明规范化；随后完成源级、SDK原语、存储器及配置证明，冻结公平基线，再继续多工程联合优化。不得删除真实 I/O 或必要存储器条件、假定复位序列、补造初值，或把原厂 P&R 当作自研结果。

每个完整优化尝试不超过1小时，工作目录不超过40,000,000,000字节。历史大 JSON 的无损压缩清单包含在检查点中；原始内容与哈希保留，直接读取旧文件的工具应先恢复原始字节。

项目许可见 [LICENSE](LICENSE)。第三方运行时和高云组件保留原有许可，发布不含许可证密钥。历史 README 已保存于 [docs/history](docs/history/WORKSPACE_README_BEFORE_CHECKPOINT_20261113.md)。
