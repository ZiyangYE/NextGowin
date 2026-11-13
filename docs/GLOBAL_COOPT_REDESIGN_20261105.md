# 从局部修补转向全局协同优化

2026-11-05 用户更新：取消 10 GB 清理目标，**40 GB 是唯一容量上限**；优先争取根本性的大幅收益。固定封装引脚、接口周期、完整 I/O 时序覆盖及每次完整尝试不超过一小时保持不变。

## 已查明的结构问题

旧 `ownpr_place.place` 将可移动单元从 I/O 中心附近初始化，反复做加权邻居平均，随后贪心放到最近合法站点。连续优化没有显式密度项，容易先聚集，再由合法化被动拉开。单独修补最坏连接会不断遇到其他竞争路径。

当前已优化 CPU 也不能直接重新进入旧 packer：对既有 LUT6/FF 共址结构会尝试再添加直通 LUT，并报 `Generated pass-through name collision`。只改生成名称并不能解决结构丢失。因此本次采用新的全局重布局入口，完整保留已经证明的逻辑打包关系，而不是悄悄拆掉宽逻辑/FF 共址。

## 已实现并验收的反馈基础

`ownpr_router` 支持逐接收端关键度、优先连接关键接收端，以及对已完成路由进行可变的全局拥塞协商。`run_ownpr_pin_coopt.py` 每次接受候选后，重新获取该实现的内部 STA；只有固定合同下完整 STA 通过且 setup 改善，才更新保留状态。两个成功阶段：

| 阶段 | 在 54.8 MHz 下 setup WNS | Hold WNS | 报告 Fmax |
|---|---:|---:|---:|
| 起始实现 | +0.021 ns | +0.132 ns | 54.865 MHz |
| 第一轮全局协商 | +0.335 ns | +0.132 ns | 55.825 MHz |
| 刷新 STA 后第二轮 | +0.406 ns | +0.132 ns | 56.046 MHz |

两轮分别实际改变 37、27 个网络的 PIP/节点集合；第一轮早期日志按列表/记录差异统计为 38 或更多，包含顺序或检查点记账差异，实际物理变化以集合比较为准。随后 **56.0 MHz 独立完整 I/O 验收通过**：setup +0.015 ns、hold +0.132 ns。它是全局架构试验的保留基线，不能作为已经实现巨大提升的结论。

保留状态：`results/vexriscv_pin_global_summary_001/replay_checkpoint.json`。439 项核心回归通过，包含逐接收端路由、预算拒绝/接受条件和带 SHA 的透明重复数据引用。用户取消 10 GB 目标后，停止继续为此归档；已完成的无损引用保持可读，冻结 ZIP 不变。

## 新的全局重布局试验

`ownpr_global_relayout.py` 将普通 LUT/FF、完整宽逻辑宏及其关联 FF、水平进位链建模为可移动单元。采用实际引脚关键度加权的连接吸引力、软密度惩罚和独立的离散资源合法化。当前阶段固定 BRAM/DSP 站点及封装 I/O；尚未实现硬资源站点、逻辑重映射和新打包结构的共同搜索。

候选 `results/vexriscv_global_relayout_001`：

- 网表字节完全不变；所有 I/O、硬资源配置及接口周期不变。
- 移动 4,108 个单元，整体单元数 4,474；保留 LUT6/FF 共址及进位链结构。
- 合法化后的时序加权曼哈顿线长从 101,248.81 降至 68,410.98，减少约 32.4%。这是几何代理指标，**不是频率改善**。
- 独立宽宏、进位链、FF 控制/初始化和硬资源容量检查通过，新增两项真实 CPU 结构检查通过。
- 连续优化仍存在软密度溢出，最终离散容量由合法化检查保证；不能称为精确密度最优解或完整顶会实现。

新时钟树已重接并经编码检查。第一次数据布线在增量复用检查中失败：旧位置的专用 `OF1→DI1` 边被放到新物理模式视图中检查，尚未进行搜索。失败记录为 `results/vexriscv_global_relayout_trial_001`。第二次 `results/vexriscv_global_relayout_trial_002` 保留已验证的新时钟树，从头布置数据路线，给数据布线 1,800 秒、完整流程 2,400 秒。最终状态须读该目录的 manifest 和 STA，不以线长预测代替验收。

## 研究依据与尚未实现的部分

- AMF-Placer 的混合尺寸宏、扩散、打包和合法化方法说明了 FPGA 不能只按独立点单元处理。当前借鉴宏单元建模思想，未复制其完整优化器。[ICCAD 2021 作者论文](https://chengengjie.github.io/papers/C15-ICCAD21-AMF-Placer.pdf)
- TD-Placer 将细粒度关键路径权重、二次布局目标和资源供需调整结合。其非线性延迟预测部分尚未在本项目实现；当前使用原厂来源 STA 反馈及几何代理。[作者预印本](https://arxiv.org/html/2512.00038v1)
- 2025 年的关键路径提取研究明确区分 pin 权重与路径级优化；只改 net 权重仍可能错过共享路径效应。当前用每轮重新 STA 避免重复消费旧预算，尚不是完整路径级优化。[作者预印本](https://arxiv.org/html/2503.11674v1)
- DATE 2026 HPPlacer 提出路径级目标、pin-pair 动态权重及局部时钟到寄存器连接优化；这是 VLSI 标准单元研究，迁移到 Gowin 需受其离散布线和时钟资源约束。[作者论文](https://www.cse.cuhk.edu.hk/~byu/papers/C317-DATE2026-HPPlacer.pdf)
- ICCAD 2025 的可微 FPGA 布局与 ML 延迟校准工作已在作者实验室发表目录核实，但本次未获得其完整正文，不宣称已复现。[作者发表目录](https://www.cerc.utexas.edu/utda/publications/publications.html)

后续判断依据是完整 I/O STA 的明显提升，最终仍须面对 150/200 MHz 和通用优于原厂的目标。若全局重布局不足，优先补充负载/路径延迟目标、BRAM/DSP 联合布局、区域控制逻辑重构和注册硬核后端，不再把单条局部改善当作任务完成。


## 最终实测：62.2 MHz，仍未达到巨大提升目标

最终保留 `results/vexriscv_global_joint_summary_001/replay_checkpoint.json`：**62.2 MHz 完整 I/O STA 合格**，setup +0.009 ns、hold +0.134 ns，零违例；报告 Fmax 62.234 MHz。比本次全局改造前的 56.0 MHz 高 11.1%，不能把这个结果称作已实现巨大提升、150/200 MHz 或通用优于原厂。

| 阶段 | 报告 Fmax MHz | Setup / Hold 违例 | 处置 |
|---|---:|---:|---|
| 全局重布局后直接布线 | 46.648 | 165 / 3 | 拒绝 |
| 保留新布局，重建关键负载树 | 61.664 | 0 / 3 | 仅作待修复候选 |
| 添加 12 LUT 修复三个输入 hold 分支 | 61.664 | 0 / 0 | 在 56 MHz 合同下通过 |
| 两轮重新 STA 驱动全局路由 | 62.234 | 0 / 0 | 在 56 MHz 合同下通过 |
| 单独调整时钟周期验收 | 62.234 | 0 / 0 | **62.2 MHz 合格** |

关键失败不是总布线资源增加：重布局后数据 PIP 从 52,818 降到 49,463，但 `net_2354` 的关键支路从 28 个节点变为 90 个节点，实际慢角延迟从 3.509 增至 9.466 ns。该连接几何距离反而从 59 降到 51。总线长/总 PIP 都不能代替关键负载树延迟。

重建候选能将关键支路改善约 6.6 ns，但保守的独立引脚预算预测七个内部接收端会有最多约 0.50 ns 的 hold 风险，原筛选器因此拒绝。显式标记为不可验收的探索候选并执行完整 STA 后，这七个内部 hold 风险未实际出现；只剩原有三个 IBUF 输入违例。每个分支增加四个 LUT 后全部通过。没有放松 SDC、删除 hold 检查、改变引脚或增加接口周期。内部 FF-Q hold 缓冲支持已做单元检查，但本例最终缓冲的是三个外部输入分支，不能声称它已在完整 CPU 上被实际需要并验证。

hold 修复中另外修复了一个流程问题：关键树生成器漏记搜索配置，后续阶段退回了无引导搜索，导致两个长输入分支触发搜索上限。保留该失败记录，复用已通过 CEC 的缓冲网表，以明确的引导搜索参数重跑路由，38 秒完成。最终控制器最初的失败记录没有改写成成功，恢复与最终验收另有证据。

最终直接 CEC 检查 8,584 个硬输入/端口观测点，完整出口审计及 444 项核心回归通过。LUT 增加 12，FF、BRAM、DSP 和 ALU 逻辑不变；全局位置和时钟端点已改变。最终 bitstream 载荷与相同物理实现的 56 MHz 导出一致，未自动上板。

时间记录明确区分试验：第一项被拒绝的全局试验约 2,995 秒；后续联合修复试验（含参数失败恢复、回归与频率验收）约 3,052 秒。它们是两个完成并记录结果的试验，**没有测得一次从原始 RTL 冷启动到最终位流的连续端到端耗时**，因此不能据此宣称完整冷启动流程已满足一小时。

下一阶段优先验证更根本的能力：区域化寄存器/控制逻辑复制（须建立初始化与时序状态的归纳等价证明）、进位链与邻近 LUT 的混合打包、注册 DSP 后端，以及真正基于路径/负载和最小延迟的全局目标。这些都尚未完成，不能用当前 11.1% 改善替代原目标。

## 区域化寄存器复制已验证，但单锥不改变全局瓶颈

后续真实实验 `results/vexriscv_regional_replication_001` 已完成。`ownpr_regional_replication.py` 沿普通 LUT 控制锥追溯同步寄存器边界，复制寄存器、其配对 D 输入 LUT 和控制锥；将新簇放到指定接收区域附近，重新接入自研时钟树并完成数据布线。它不增加流水级，不移动既有单元或封装引脚。当前范围限于普通 LUT 与可配对 DFFRE/DFFSE，尚不支持任意宽宏、异步 FF 和硬核内部状态复制。

原有直接边界 CEC 仍拒绝新增状态。新增 `ownpr_register_equivalence.py` 单独建立归纳证明：副本初值必须已知且相同，时钟完全一致；在副本 Q 等于原 Q 的不变量下，证明 D/CE/同步 RESET 或 SET 相等；折叠 Q 后再证明所有原有状态/硬输入及外部观察点等价。黑盒原语定义必须完全不变。8 项针对性与原边界检查测试通过，包含错误初值、时钟、D/CE/RESET、多驱动、额外状态与输出破坏的拒绝检查。实际 CPU 的证明文件带输入网表与源文件哈希，位于 `verified_proof/manifest.json`。

本例复制 `ng_coopt_1086` 的三 LUT 控制锥及六个寄存器，共增加 **9 LUT、6 FF**，只把 `ng_coopt_1592/I0` 切换到区域副本。全流程的固定引脚、电气约束和 I/O SDC 未改变。

| 指标 | 原基线 | 区域复制 |
|---|---:|---:|
| 实际验收频率 | 62.2 MHz | 62.2 MHz |
| setup WNS | +0.009 ns | +0.088 ns |
| hold WNS | +0.134 ns | +0.134 ns |
| setup / hold 违例 | 0 / 0 | 0 / 0 |
| 报告 Fmax（不等于单独频率验收） | 62.234 MHz | 62.542 MHz |
| 同一物理接收端的目标分支慢角布线 | 3.268125 ns | 1.011375 ns |

目标分支改善 **69.05%**，但报告频率仅提高 **0.495%**。125 个既有 setup 端点改善，12 个退化，最大退化 0.017 ns；既有 hold 端点无退化。新增状态产生的实际受检查端点也纳入完整 STA。原来最差路径不再控制结果，新的最差路径经过 `ng_coopt_389 → LUT5 宏 → 算术链 → ng_coopt_1079/2535/2899`；其上仍有 2.346、2.292、1.885 ns 等连接。具体端点与新旧最坏发起点见 `endpoint_comparison.json`，不是把不同路径的局部延迟相减当成全局增益。

原最差数据路径的单元延迟 6.558 ns、布线 9.113 ns、C2Q 0.344 ns；即使大幅缩短布线，维持这些逻辑弧也不足以实现 150/200 MHz。因此下一步必须同时覆盖竞争路径并压缩逻辑层级，联合考虑新增副本输入路径、进位链边界和宽 LUT cut 的物理成本。单锥局部改善不能作为整体优化完成的依据。

完整自研时钟/数据路由、校验与导出阶段 **350 秒**；从优化算子源文件首次创建到最终实现 manifest 的保守尝试时间窗口约 **575.66 秒**，包含准备时的两次格式错误修复。它是已有 mapped checkpoint 的增量尝试，不是原始 RTL 冷启动时间。原厂仅用于器件绑定/编码校准、最终 STA 与编码，没有调用原厂布局或布线。完整 bitstream 仍未上板验证。

复现实验需要先用新的输出目录运行：

```powershell
runtime_bundle/python/python.exe scripts/ownpr_regional_replication.py --base results/vexriscv_global_joint_001/hold/round_000/placement --out results/region_repro/candidate --source ng_coopt_1086 --sink ng_coopt_1592/I0
runtime_bundle/python/python.exe scripts/run_ownpr_checkpoint.py --placement results/region_repro/candidate --cst results/vexriscv_evaluation_pins_001/fixed.cst --sdc results/vexriscv_62_2mhz_001/clock.sdc --io-reference results/native_vexriscv_packed_io_reference_001 --anchor-clock-tree results/vexriscv_global_postroute_loop_001/round_001/route/clock_tree.json --reuse-routes results/vexriscv_global_postroute_loop_001/round_001/route --search-bias 30 --root-arrival-weight 1 --guide-edge-delay 0.05 --guided-corridor --seconds 2400 --route-seconds 1800 --out results/region_repro/implementation
```

已完成实现的重放入口是 `results/vexriscv_regional_replication_001/replay_checkpoint.json`。原保留基线与冻结交付包不变。本轮没有为了微小增益再扫描频率。`native_pin_feedback_006` 未形成完整 capture，执行句柄已缺失且无 Python 进程；该采集未用于决策，排除记录在其 `observation_state.json`。40 GB 仍为唯一空间上限。
