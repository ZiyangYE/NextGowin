# 寄存器前沿、七输入逻辑与增量路由回退的联合对照

本轮按 40,000,000,000 字节唯一容量上限执行；固定封装引脚、电气约束、接口周期和完整 I/O STA 不变。目标仍是大幅提高 VexRiscv 性能，不能把单处路径改善或理论层级减少当作整机收益。原厂仅提供器件查询、最终 STA 与编码，不运行原厂 P&R。

当前保留结果为 **67.9 MHz 全 I/O 合格**，setup +0.016 ns、hold +0.134 ns、零违例，配置/导出检查通过。相对 67.1 MHz 提高 **1.192%**，尚未实现用户要求的大幅优势。主检查点为 `results/vexriscv_state_frontier_002/qualified_replay_checkpoint.json`；旧 67.1 MHz 基线保持不变。

## 完整尝试结果

| 尝试 | 变化 | 报告 Fmax MHz | 90 MHz setup WNS ns | 完整时间 s | 决策 |
|---|---|---:|---:|---:|---|
| state_frontier_001 | 5 个状态根复制，清空整个 CFU | 65.929 | −4.057 | 596.219 | 拒绝 |
| state_frontier_002 | 相同逻辑，改为单槽合法化 | 67.972 | −3.601 | 815.312 | 67.9 MHz 独立验收通过 |
| seven_cut_001 | 8 个多层 cut，含 4 个七输入 cut | 62.367 | −4.923 | 615.343 | 拒绝 |
| seven_cut_002 | 相同网表/布局，保留树协商回退 | 64.989 | −4.276 | 534.360 | 拒绝 |

表中每个时间是一次独立完整尝试，不能把整个研发过程称为只花这些时间中的一个。报告 Fmax 不等于已重放验收的频率。

## 已实现的结构能力

1. `ownpr_regional_replication.py` 支持直接复制受支持的 DFFRE/DFFSE 发射寄存器及其配对 D 逻辑，使原 Q 的部分负载转移到靠近使用位置的副本。保留相同 CLK/CE/RESET/SET，检查已知初值，并执行初态、下一状态与可观察输出的归纳等价证明。不改变接口周期，也不宣称支持任意状态重定时。
2. `ownpr_macro_legalize.clear_register_slot` 只为一个 LUT/FF 对寻找可用控制组，至多移动一个未配对普通 LUT。禁止移动原 FF、宽宏、硬资源与受保护单元。新副本仍须重接时钟和完整 STA。它替代了复制一个 FF 却清空整个 CFU 的过大扰动。
3. 七输入 cut 枚举显式考虑完整 LUT5/6 宏与下游普通 LUT 的合并，避免被有界枚举中的小 cut 挤掉。通过 Shannon 分解映射为已支持的 LUT4/MUX 宏，晚到输入可接最终选择端。没有宣称存在原生独立 LUT7；未校准的 LUT7/FF 共址被排除。
4. `run_ownpr_timing_route.py` 的分支复用失败回退改成保留已完成树的可协商重布。缺失接收端与冲突网络先重布，其他树保持，必要时再释放受影响树。原来冻结的完全不变网络继续受保护；无法修复才进入既有全局协商。所有结果仍进行连通性、冲突、配置、完整 STA 与导出检查。

## 已定位的失败机制

第一轮寄存器复制增加 5 FF / 5 LUT，目标覆盖 201 条报告路径，却为腾位移动了 30 个原单元（含 6 个 FF）。报告 Fmax 降至 65.929 MHz。原关键路径之外的 `ng_coopt_1580` 和 `ng_coopt_2579` 被挤走，形成新的长路径。第二轮使用完全相同的逻辑网表，只改变合法化方式：移动 5 个未配对 LUT，所有原 FF 位置不变。

第一轮宽逻辑批次选择 8 个 cut，其中 4 个七输入 cut；净增 33 LUT / 33 宽 MUX，移动 39 个原普通 cluster（含 9 FF），报告 Fmax 降至 62.367 MHz。最坏路径来自 `memory_arbitration_isValid`，布线 13.689 ns、单元含 C2Q 2.300 ns。相同源/目的引脚和 RR 转换方向的 `ng_coopt_2140/F → ng_coopt_2741/I2` 从 0.642 ns 增至 6.914 ns。

路由日志给出了直接原因链：188 个变化网络中，本来为 107 个网络保留了 2,279 个公共接收端、6,016 个 PIP；仅 `net_1765`、`net_3427` 的新接收端被保留分支挡住，整个批次就退回从头重布。上述退化的 `net_1459` 本可保留 174 个接收端和 358 个 PIP，却一并失去。这里需要修复的是回退策略；不能把这次退化解释成七输入映射或器件的固有限制。保留树也可能因新增分支负载改变时序，因此仍以实测 STA 验收。

新回退的 `seven_cut_002` 完整对照已完成：网表、布局、物理网络与时钟树均逐字节相同，仅路由实现不同，153 棵网络树改变。含包装开销的路由时间从 145.234 降至 54.000 秒，`net_1459` 的原树公共 PIP 从 66 增至 358，新树总 PIP 从 456 降至 371。相同 RR 弧 `ng_coopt_2140/F → ng_coopt_2741/I2` 实测恢复到 **0.642 ns**，与基线相同。报告 Fmax 从 62.367 提高到 64.989 MHz，90 MHz setup 违例从 630 降到 274、TNS 从 −874.103 改善到 −279.128 ns。但仍低于原基线，所以宽逻辑批次未采用。

这一次最坏路径经过 `ng_coopt_240`：它为第一处七输入宏腾位而被移动，输出到 `ng_coopt_1053/I0` 的线为 3.887 ns。最坏路径的布线为 10.930 ns、单元含 C2Q 为 4.362 ns。保留树回退修复了上一轮控制网退化，却不能自动修复宏合法化产生的位置代价。需要对被挤占单元也执行面向目标周期的预算，而不是仅根据旧频率的 slack 阈值和几何代价筛选。

## 为什么仍不是大幅提升

状态复制改善之后，最坏路径换成 `$auto$ff.cc:337:slice$47248 → $auto$ff.cc:337:slice$47388`，布线 8.548 ns、单元含 C2Q 6.116 ns。第二、第三条竞争路径的布线仍为 10.822 / 10.633 ns。在 90 MHz 下，276 个 setup 终点违例分布在 21 个发射组；前五组只覆盖其中 190 个。仅改善某一个选择输入不能令其他竞争路径消失。

新的完整报告还确认：保留单元/时钟/I/O 贡献，数学上把所有报告路径的数据布线降到零，仍被一个固定输出路径限制在约 123.914 MHz。这个反事实计算不代表可实现频率，也不是器件极限；它说明 150/200 MHz 需要同时改变逻辑/寄存器打包与合法时钟实现。`frequency_diagnosis.json` 保存全部报告终点覆盖检查和计算来源。

下一阶段应让区域变换在实际负载树上比较新增输入、全部输出、受挤占单元与竞争路径的联合 setup/hold 预算，并联合考虑固定输出的单元/时钟路径。当前历史 pin 权重和几何代理只能决定尝试顺序，尚不具备这种完整的动态候选评价能力；不能把有限搜索的停滞解释成高云器件无法高频运行。

## 复现与证据

从 `results/vexriscv_measured_carry_005/qualified_replay_checkpoint.json` 出发，每次新建输出目录。统一使用 2,400 秒总预算，包含重新捕获、候选/证明、自研 P&R、导出与必要的频率资格重放。

```powershell
runtime_bundle/python/python.exe scripts/run_ownpr_region_coopt.py --report results/vexriscv_measured_carry_005/qualified_replay_checkpoint.json --transform regions --timing-feedback --protect-slack 0.5 --allow-state-roots --coverage --launch-frontier --cluster-regions --legalize-macros --target-mhz 90 --groups 5 --depth 4 --seconds 2400 --out results/<新的状态复制目录>

runtime_bundle/python/python.exe scripts/run_ownpr_region_coopt.py --report results/vexriscv_measured_carry_005/qualified_replay_checkpoint.json --transform multilevel --timing-feedback --protect-slack 0.5 --target-mhz 90 --cut-count 8 --cut-width 7 --min-cut-cells 2 --cut-window 4 --cut-growth 48 --gain-target-ns 4 --legalize-macros --seconds 2400 --out results/<新的宽逻辑目录>
```

`scripts/summarize_state_wide_coopt.py` 检查基线哈希、等价证明、固定边界、导出状态与时限，并按周期归一化比较原终点余量。还匹配相同源/目的 pin 与转换方向的布线弧；端点最差路径的起点可能改变，不冒充完整同路径对照。结果在 `results/vexriscv_state_wide_summary_001/summary.json`。

新增结构能力及归纳证明相关 16 项检查通过；分支回退/检查点相关 17 项、路由器相关 21 项、宏复制/历史反馈兼容性相关 10 项检查通过，共 64 项。日志分别是 `results/state_wide_regression_001.log`、`results/branch_fallback_regression_001.log`、`results/branch_fallback_router_regression_001.log`、`results/state_wide_compatibility_001.log`。

为预留同时进行的有界尝试，核验并替换已完成 capture 的重复 JSON 输入为带 SHA256 的透明引用，节省 924,560,777 字节；七份 capture 均重新检查 2,765 个 setup/hold 终点匹配。原始来源、冻结 ZIP 与既有基线未修改。清单见 `cleanup/native_capture_input_dedup_20261106_001.json`。

最终审计见 `results/vexriscv_state_wide_summary_001/final_audit.json`：主检查点来源哈希复核通过，64 项针对性检查通过，四项完整尝试均在各自 2,400 秒限额内完成。审计时工作区 37,591,580,098 字节，另预留 1,000,000 字节记录空间，低于 40 GB。90/150/200 MHz、通用原厂优势和更完整硬资源/多时钟支持仍未完成。
