# VexRiscv：当前综合与 P&R 为什么落后

日期：2026-11-05。结论：当前差距同时来自物理实现与映射/打包；已测得的直接证据首先指向关键连接位置、局部直连利用和高扇出控制网。不能把差距归因于 GW5AST-138 器件上限，也不能把本次参考结果称为官方最高性能。

## 对照条件与结果

相同 GW5AST-LV138FPG676AC1/I0、工具 DLL、固定封装引脚、I/O 电气约束及 54 MHz SDC；setup slow 0.873 V/0 C，hold fast 0.927 V/85 C。输入、输出 min/max delay 都为 0 ns，hold uncertainty 为 0.050 ns；这是当前评测合同，不是已验证的板级接口预算。没有重新分配引脚。

| 输入与实现 | 报告 Fmax MHz | 54 MHz setup WNS ns | hold WNS ns | setup/hold 违例 |
|---|---:|---:|---:|---:|
| 当前自研综合/物理优化 + 自研 P&R | 54.089 | +0.031 | +0.132 | 0 / 0 |
| 当前功能网表 + 官方 P&R，比较专用时钟归一化 | 66.109 | +3.392 | +0.175 | 0 / 0 |
| 同源 RTL 官方综合 + 官方 P&R | 73.209 | +4.859 | -0.754 | 0 / 81 |

第一行已独立按 **54.0 MHz** 完整 I/O 验收。后两行的 66.109/73.209 是当前布局的报告 Fmax，未按这两个频率重新验收。第三行存在 hold 失败，尤其不能称为“73 MHz 全 I/O 合格”。第二行相对第一行报告 Fmax 高 **22.22%**；第三行相对第二行高 10.74%，但存在后述综合条件差异，不是纯粹的综合算法贡献率。

官方综合使用同一个已适配 RTL `VexRiscv_defined_outputs.v`，仅沿用既有全 X 指令总线写数据输出置零处理。该 RTL SHA256 为 `6e812e5bb8eb45bda0abfc826ede6f142b7db741b7576943de0bfc98a714884f`。官方综合以 200 MHz 为映射目标；我们当前网表经历了低频约束下的物理优化。两者最终 P&R/STA 使用同一 54 MHz SDC，不能声称综合搜索目标及历史完全一致。本次未完成两套综合结果间完整时序等价证明。

## 已定位的物理差距

我们的最坏路径包含 4.268 ns 单元延迟、13.884 ns 布线和 0.344 ns clock-to-Q。布线占数据延迟约 **75.1%**。以下是相同源/目的引脚、相同 RR 转换方向在两套实现报告中实际出现的连接：

| 连接 | 自研 ns | 官方 P&R ns | 位置证据 |
|---|---:|---:|---|
| `ng_coopt_1000/F → ng_coopt_2015/I1` | 3.072 | 0.273 | 自研 R69C87→R74C98；官方 R54C96→R56C96 |
| `ng_coopt_1588/F → ALU slice 0 I0` | 0.763 | 0.324 | 官方把 LUT 放在算术链起点附近 |

这些连接已匹配转换方向，但不能将差值简单相加当作完整路径净收益。另观测到官方把 ALU SUM→LUT 和一条 LUT→LUT 连接放在同 tile，报告延迟为 0.005 ns；这两条在现有报告里没有匹配到相同转换方向，故不纳入上表的定量对照。位置差异仍给出明确的物理优化方向。同一个终点 `own_i_1182/D` 的最坏路径布线总延迟从 13.884 降至 9.861 ns，但两次 STA 选择的起点不同，不能冒充同一完整路径比较。

当前基础路由器仍有明显结构短板：`ownpr_route_design.bind` 给普通信号统一 `criticality=0.5`；`run_ownpr_timing_route.py` 使用原厂来源的空载 PIP 延迟估计，而非每次全局迭代更新的接收端 arrival/required/slack 和整棵树负载。已有局部 STA 反馈、共享树修复和 cut 重映射有效，但没有替代全局时序驱动目标。这解释了为什么持续微调只能带来小幅增益。

官方 P&R 跑我们网表时，最坏路径仍存在扇出 **244** 的控制节点，某支路 **6.660 ns**。官方全流程的前 20 条 setup 路径全部从 `execute_to_memory_IS_DIV_s0` 出发，经过扇出 298/172 的控制网络。说明流水线停顿/控制分发是两套流程共有的瓶颈，单补 DSP 后端不会自动消除它。

## 综合、打包与 hold 修复的差距

| 资源 | 当前自研实现网表 | 官方综合网表 |
|---|---:|---:|
| LUT1–4 合计 | 4,179 | 2,464 |
| fabric FF | 1,526 | 1,319 |
| 宽 MUX | 22 | 36 |
| ALU | 382 | 375 |
| BRAM | 10 | 10 |
| DSP | 4 | 4 |
| I/O buffer | 284 | 282 |

自研多出 1,715 LUT，其中有 **427 个 `ng_ownpr_pass_*` LUT1** 和 **467 个 `ng_hold_buffer_*` LUT4**。扣除这两类，仍有 3,285 LUT，比官方多 821 个；它们包含映射差异、物理重构和复制，不能全归因于某一个映射算法。这里比较的是最终自研实现网表与官方综合输出，并非两套前端裸综合面积。

官方四个 MULTALU27X18 的 `OREG_CLK="CLK0"`，自研四个 DSP 所有寄存器时钟模式均为 `BYPASS`。自研未利用这些 DSP 输出寄存器，可能增加 fabric FF 与 DSP 边界布线；尚未逐位证明多出的 207 个 FF 都来自这里。官方 BRAM 为 SDPB，自研为 6 DPX9B + 4 SDPX9B；模式和初始化等价性须单独核查。

官方保留顶层两个 Wishbone ERR 端口声明，但删掉了对应 IBUF；自研保留两个 IBUF。这两个未使用端口使物理 I/O 数有差异，本次官方全流程不能替代严格 284 I/O 的完整参考。官方最坏 hold 是 `dBusWishbone_DAT_MISO[21]` 输入到其寄存器，-0.754 ns。467 个 hold 修复 LUT 的代价必须在满足相同输入 hold 条件后比较，不能直接删除。

## 修正旧参考的含义

旧 79.8 MHz 参考使用旧网表、200 MHz 目标，且存在 setup/hold 违例。原先导出把完全 BYPASS DSP 的无功能时钟脚接 GND，官方自动时钟识别把 GND 网识别成未约束时钟。当前原样网表交给官方 P&R 也会触发这个现象，产生 994 setup/2,541 hold 违例。

本次第二行仅在比较副本中把四个 DSP 的八个已验证 BYPASS 时钟脚接到现有缓冲时钟，消除伪时钟；未改数据/状态、有效控制、CST、SDC，未增加 false path。原始自研合格文件不变。副本是功能相同的比较网表，**不是字节相同的网表**；也增加了无功能时钟脚的物理负载。归一化后只有 clk，setup/hold 均零违例。记录在 `inactive_clock_normalization.json`。旧 79.8 MHz 不能继续用作合格频率或公平速度比例。

## 后续任务顺序与验收

1. **全局时序反馈**：校验内部 arrival/slack 查询，区分 rise/fall 与 min/max；将每个接收端预算用于布局和整树路由。先对照最终 STA，不能把尚未动态验证的 DLL 函数签名当作已实现能力。
2. **算术链边界与 LUT 链成组优化**：把上表的 ALU 输入、输出 LUT 和相邻 LUT 链纳入联合打包/合法化；释放必要 FF、时钟端点，避免固定历史布局限制搜索。每次同时检查竞争路径与 hold。
3. **控制网的物理分区与逻辑重构**：围绕 DIV/stall/CE 的高扇出网络，比较局部逻辑复制、接收端分区、共享树重构，目标是实际负载延迟。已有复制失败案例，不按扇出阈值盲目复制。
4. **减少后端额外资源**：校准寄存器直接输入及 DSP OREG 模式，吸收等价现有寄存器，保留接口周期；hold 用最小延迟预算、路由和时钟协同修复，保留必要缓冲。
5. **完成严格官方参考**：保持 284 物理 I/O 与相同 min/max 约束，修复官方参考 hold；随后分别在 150/200 MHz 目标下比较两套流程，记录多次结果及超时。当前结果不是已达到这两个目标。

## 复现与证据

- `results/vexriscv_comparison_001/analyze.py`：从三份 STA 自动生成 `analysis.json`，含逐行关键路径、同终点对照和相同连接位置/延迟。
- `results/vexriscv_comparison_001/normalize_inactive_clocks.py`：比较专用、严格检查 BYPASS 条件的归一化。
- 官方综合：`scripts/native_project_synthesis.py --frontend results/coopt_vexriscv_rebuild_001/synthesis/01_frontend --sdc results/coopt_vexriscv_rebuild_001/clock200.sdc --out <新目录>`。当前精简 runtime 缺 Tcl 初始化文件，运行时设置 `TCL_LIBRARY=C:/Gowin/Gowin_V1.9.12.04_x64/IDE/lib/tcl8.6`；失败尝试已保留。
- 官方 P&R：`experiments/native_primitive_binding_oracle.py --netlist <归一化副本或官方 mapped.vg> --cst results/vexriscv_54_0mhz_001/export/native/project.cst --sdc results/vexriscv_54_0mhz_001/export/native/probe.sdc --out <新目录>`。明确标记为原厂参考。
- 官方综合耗时 9.97 s；两项成功官方 P&R 约 50.16/44.36 s，含本次捕获开销；不代表与已有自研增量流程相同的冷启动总耗时。
- 自研 54.0 MHz 的 `results/vexriscv_control_coopt_summary_001/replay_checkpoint.json`、`summary.json`；最终直接 CEC 和归档后完整出口审计通过，425 核心回归及最终存储兼容性相关 14 项检查通过。
- 比较报告已无损压缩，Python 分析器通过 `ownpr_image_storage` 自动读取 `.tr.gz`；其余 `.pr.gz`/HTML gzip 可解压还原。未运行硬件，尚未证明通用优势。

最终归档后针对性回归：12 项通过（`results/vex_comparison_archive_tests_001.log`），比较解析器从压缩报告重跑成功。历史 CEC 输入和日志的无损归档位于 `cleanup/historical_cec_logs_20261105.zip`；原路径及 SHA256 见 `cleanup/phase_112_historical_cec_logs.json`，需直接重跑这些历史 ABC 命令时先还原相应成员。当前合格结果及直接测试依赖保留。
