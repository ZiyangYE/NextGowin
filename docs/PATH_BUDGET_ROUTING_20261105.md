# 按组合路径保护时序余量的路由修复

本轮解决深度重映射之后的一个具体退化：逻辑和位置未改变的 `net_576`，在拥塞协商后至 `ng_coopt_1103/I1` 的布线延迟从 2.063 ns 变为 4.365 ns。目标是保护已经得到的综合收益，同时允许邻网为恢复低延迟路线腾出资源。

## 已实现与验证

`ownpr_budgeted_restore.py` 从历史自研检查点中查找具有相同物理源和接收端集合、且实际负载延迟更好的线网树。恢复前检查当前时钟、I/O 保留资源、其他网络端点和实际 PIP；恢复目标保持固定，邻网以可协商的旧路线树开始修复。每份候选对全部修改过的网络重新测量完整负载树，并检查 setup 与 hold 预算。最终整设计 STA 决定验收。

当前实现没有改变逻辑、布局、时钟树、固定封装引脚或接口周期。历史路线来自本项目已有的自研 P&R；没有调用原厂布局或布线，也没有使用开源器件数据库。

`native_pin_feedback_007` 在当前深度候选上重新采集 30,415 个引脚，全部 2,757 个 setup 和 hold 终点与报告匹配。使用显式 setup/hold 模式，不使用混合缓存值；预算是最坏经过引脚的裕量，不是已经验证的逐转换到达时间。

## 预算的累计方式

对每个修改网络，分别计算 setup 与 hold 的最大归一化损失：

`charge(net) = max_sink(positive_wire_delay_loss / available_pin_slack)`

setup 使用慢角两个转换的最大增量；hold 使用快角两个转换的最大减少量。增益不会抵消其他路径的损失。缺失或为零的预算不能用于正损失。

第一版把所有网络的消耗全局相加，虽然保守，但把独立路径也限制在同一份余量内。它没有采用任何修改，输出路线与原检查点逐字节相同，证据保留在 `vexriscv_budgeted_restore_001`。

现在 `ownpr_wire_budget.WireDependency` 建立保守的数据依赖关系，同步寄存器作为路径边界，其他单元按全部输入到全部输出传播。未知硬核不被假设为时序边界；可能的环折叠成强连通分量并累计整个分量的损失。预算检查取依赖图上最大加权路径，要求其归一化损失不超过 1；无法映射到逻辑位的网络仍全局计费。

这防止同一路径各网络重复使用整份裕量，同时允许独立组合路径分别优化。它仍是**线网延迟模型的保守约束**，不覆盖单元延迟、slew、未验证时钟模式等变化；完整 STA、脉宽及其他检查保持强制。

## 实际 CPU 结果

| 指标 | 修复前深度候选 | 按路径预算修复后 |
|---|---:|---:|
| 62.2 MHz 下 setup WNS | +0.071 ns | +0.759 ns |
| hold WNS | +0.134 ns | +0.134 ns |
| 报告 Fmax | 62.477 MHz | 65.280 MHz |
| `net_576` 指定分支 | 4.365000 ns | 2.063250 ns |

恢复 `net_576`、`net_3212`、`net_7549` 和 `net_3531` 四棵历史树，共改变 20 网，另 6,498 网保持 PIP 不变。修复耗时 53.875 秒，独立连通/冲突检查通过。按依赖路径累计的 setup/hold 消耗分别为 0.782827 / 0.823142；若不区分路径而全局相加，则会得到 4.062036 / 3.200674 并错误地排除这一已通过最终 STA 的候选。

进一步在 **65.2 MHz** 下完成完整 I/O 资格验收：setup **+0.019 ns**、hold **+0.134 ns**，零违例，其他检查通过并生成位流。修复后的报告 Fmax 为 65.280 MHz。自当前引脚反馈采集开始，包含第一版拒绝尝试、实现、修复、检查与最终导出的保守墙钟时间为 **2,636.449 秒**，未超过一小时；这不是从 RTL 冷启动的耗时。

机器摘要：`results/vexriscv_budgeted_restore_002/summary.json`。修复后合格检查点：同目录 `qualified_replay_checkpoint.json`。22 项相关回归通过，覆盖串联预算、独立路径、重汇聚、环、未知预算和路由检查点等情况，见 `results/wire_budget_tests_001.log`。

## 保留结果与下一步

项目主结果仍是 `results/vexriscv_multilevel_summary_001/replay_checkpoint.json` 中的五锥方案：同样通过 65.2 MHz，且比本轮深度候选少 14 个 LUT 和 14 个专用 MUX。没有把约 0.018 MHz 的报告值差异当成明显优势，也没有更新主检查点。

本轮验证了“保住已有低延迟路线”的能力，尚未突破整体最高合格频率。下一步需要将这一保护机制用于更多 synthesis/packing/placement 候选，并改善本身没有更好历史路线的临界连接；单纯恢复历史树不能创造新的更优拓扑。150/200 MHz、通用优于原厂、注册 DSP 与多时钟/更广硬核模式仍未完成。

## 复现入口

`run_ownpr_pin_coopt.py` 新增 `--history-report`，调用有界修复与完整导出；接受修改后清除旧引脚反馈，下一轮重新采集。`--capture` 可省略以重新采集。没有修改时不重复导出；候选必须与当前检查点和固定 I/O/时序契约匹配才允许更新所选状态。

```powershell
runtime_bundle/python/python.exe scripts/run_ownpr_pin_coopt.py --report results/vexriscv_budgeted_restore_002/source_checkpoint.json --case vexriscv --history-report results/vexriscv_regional_replication_001/replay_checkpoint.json --capture results/native_pin_feedback_007 --rounds 1 --count 4 --seconds 1800 --out results/vexriscv_budget_repro
```

这些阶段已分别实际执行，控制器的已有候选验收分支也已运行并接受该完整导出。没有把上述新入口一次全新连续调用或冷启动 RTL 流程宣称为已测量结果。冻结交付 ZIP 未改变，未自动上板，工作区继续执行 40 GB 唯一容量上限。
