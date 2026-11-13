# 多接收区域状态复制：87.3 MHz 验收与联合优化边界

VexRiscv 已独立通过 **87.3 MHz 完整 I/O STA**：setup +0.007 ns、hold +0.133 ns，零违例。110 MHz 尚未达到，没有硬件验证。主检查点为 `results/vexriscv_frontier_partition_ablation_001/qualified_replay_checkpoint.json`，编码输出及哈希为同目录 `bitstream_interface.json`。

## 同一起点、同一截止时间的结果

| 方案 | 原单元位置 | 新增 LUT / FF | 110 MHz WNS ns | TNS ns | 报告 Fmax MHz | 独立验收 MHz |
|---|---|---:|---:|---:|---:|---:|
| 上轮内部状态缓存基线 | 固定 | — | −2.425 | −373.395 | 86.833 | 86.7 |
| 6 个分区副本与几何布局联合调整 | 29 次位置调整 | 6 / 6 | −6.733 | −743.226 | 63.196 | 未采用 |
| 仅 6 个分区副本 | 全部原单元固定 | 6 / 6 | −2.357 | −361.258 | 87.356 | **87.3** |

两种实际 P&R 候选的失败与成功均保留，没有只统计成功项。它们及证明、导出、资格复核、后端回放共用 `results/vexriscv_frontier_partition_trial_001_budget.json` 的 3,500 秒截止时间，闭环完成时累计 **2,955.97 秒**，包含 300 秒准备计入量；不是 RTL 冷启动综合时间。中间 `_place_002` 只生成五个副本，未执行 P&R，不计为第三个物理结果。

所有封装引脚、电气约束与接口周期保持不变。全程自研布局/布线，原厂仅执行最终 STA 与编码，未使用开源器件数据库或编程硬件。

## 实现与等价性

`scripts/ownpr_frontier_partition.py` 按实际关键路径选择四个发射根，每根最多选择两个接收区域，复制寄存器与其配对 D-LUT。默认使用空闲位置并固定全部原单元；不受实测时序约束的几何移动保留为显式实验参数，不是合格默认策略。

`prove_replicas` 检查已知同初态、同原语参数、同源时钟与状态输入的归纳等价，以及折回后的外部输出、所有原状态/硬核输入和保留网观察等价。`functional_revalidation.json` 将当前证明器及实际网表哈希绑定。新增集中保护 `ownpr_state_safety.py` 同时处理单元和线网的 ASYNC_REG、禁止修改和位置属性。普通 keep/syn_keep 线网须保留原名称、位标识与属性，并继续参与 CEC 观察；不把保留可见网误判为禁止一切状态复制。

最终相关测试 **13 项通过**，涵盖错误使能、线网同步器标记、未建模参数和删除保留网等负例。另有较早的 8 项缓存/别名回归通过。最新默认命令重新生成 `_place_004`，其 packed.json 与 placement.json SHA 均与已验收 `_place_003` 完全相同；见 `reproducibility.json`。

```powershell
runtime_bundle/python/python.exe scripts/ownpr_frontier_partition.py --base results/vexriscv_internal_state_place_002 --sta results/vexriscv_internal_state_trial_001/export/native --routes results/vexriscv_internal_state_trial_001/route --out results/<新候选目录>
runtime_bundle/python/python.exe scripts/replay_coopt_checkpoint.py --case vexriscv --report results/vexriscv_frontier_partition_ablation_001/qualified_replay_checkpoint.json --target-mhz 87.3 --seconds 600 --out results/<新复核目录>
```

## 为什么没有大幅提升

组合方案的几何目标下降不等于真实布线变快。较大范围的原 FF/LUT 移动暴露了未在旧终点最差路径中出现的分支，新增 **178** 个违例终点。最差路径 wire 增至 11.043 ns。该方案被完整 STA 拒绝，没有进入合格检查点。

固定原单元的对照则没有给原有 2,791 个终点新增违例，平均 setup 裕量提高约 45 ps，最大退化 4 ps；383 个原违例终点平均改善约 40 ps。不同源/路径的终点比较是最差路径包络比较，不能当作同一路径因果分解。新增副本端点另在完整 STA 中验证。

但原 103 扇出的源只被剥离四个已报告接收脚，仍有 **99 个接收端**。最差路径改从宽宏的另一个叶输入进入，抵消了局部改善。新的全局最差仍有 wire 7.474 ns、cell 3.945 ns。另一个副本的同步 RESET 线延为 **13.383 ns**，在 110 MHz 下 slack −1.769 ns；没有对它加 false path 或借用额外周期。证据在 `remaining_bottlenecks.json`。

因此后续需要完整扇出、竞争路径与状态输入的联合约束，而不是只复制旧报告里的几个引脚，或继续扩大几何移动窗口。

## 后端回归范围

`results/backend_regression_frontier_001/manifest.json` 的新鲜完整 STA 回放全部通过：UART 197 MHz（setup +0.001、hold +0.037 ns），int2float 167.5 MHz（+0.004、+0.002 ns），TMDS 112.3 MHz（+0.005、+0.048 ns）。不需要降至 95% 守卫。

这些是**冻结实现的后端兼容性检查**，没有向三个工程应用新结构优化，不构成新算子的跨设计性能证明。原始结构优化结果与旧实现回退不能混为一谈。回放时还修复了联合布线审计的输出位置：审计写到新导出目录，三项原路由审计文件哈希均保持不变。

## 状态预计算的下一阶段证据

静态扫描了 64 个内部关键逻辑根，其中 8 个满足当前不超过六个 DFFRE 状态、十八个抽象变量及 INIT=0 的边界，另有 7 个仅被 INIT=1 限制排除。`ng_coopt_1445/1455` 各六状态、十三变量，使用 6/7 个 LUT 构建次态项，均通过 8,192 组向量与初态检查。证据在 `results/vexriscv_internal_next_terms_proof_001.json`。

这仅是隔离合成观察端口的次态项证明，尚非内部反馈变换或物理实现证书。`1455` 的源含原最差捕获 FF（slack −2.425 ns），`1445` 的一个源 D 已为 −1.193 ns，且位于同一算术通路；盲目前推可能把违例搬到新 D 端。优先以实际到达时间、合成次态逻辑和全负载树判断，不把 SCC 成员关系当作“状态增强一定不可能改善”的定理。

Gemini 审查报告为根目录 `carbon-advice-20261111-frontier-review-04.md` 和 `carbon-advice-20261111-precompute-admission-05.md`。报告中的建议均按实际模型范围核对，没有把数学等价或预计局部收益当作 STA 验收。

## 研究依据及适用边界

[TD-Placer 预印本](https://arxiv.org/html/2512.00038v1) 强调细粒度时序弧权重与多负载网络环境，支持将候选目标从几何提升为时序与全网负载感知；其最终实验使用 Vivado 路由，不能视为本项目整套自研 PR 的复现。[EqMap，ICCAD 2025](https://www.csl.cornell.edu/~zhiruz/pdfs/eqmap-iccad2025.pdf) 提供 e-graph 等价映射候选的思路，其 LUT/逻辑深度结果不保证高云 routed Fmax。[2026 双输出 LUT 映射预印本](https://arxiv.org/abs/2607.22756) 的架构依赖必须与高云已标定的 LUT4、专用 MUX、FF 及 carry 连接逐项匹配，不能直接套用其他器件的可裂分 LUT 模型。

## 用户提出的统一综合与 PR 方向

将它作为架构假设评估，尚未据此决定全面重写。87.3 到 110 MHz 需要周期约 11.455 ns 缩短至 9.091 ns，约 **20.6%**，而且须同时覆盖几类竞争瓶颈。真正应验证的是：同一状态中保留等价映射候选、资源打包、位置与完整路由树，联合改变并按全局实际时序接受或回滚；不能只把现有流水线包成一个命令便称为“一步完成”。全芯片精确单体求解是否值得，与分层统一优化是否有价值，是两个问题。详细可行性判断另行记录。
