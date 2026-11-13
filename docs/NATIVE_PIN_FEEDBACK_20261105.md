# 内部引脚 STA 反馈及 VexRiscv 实测

本轮针对原厂对照暴露的预算缺口，完成原厂来源内部引脚查询的隔离校验，并接入自研整树布线优化。尚未完成全局布局/路由每轮 STA 更新，也未验证任意时钟、转换方向或器件版本。

## 查询语义与证据

在当前自研 54.0 MHz VexRiscv 布局、时钟和数据布线上，只执行原厂最终 STA；没有执行原厂 P&R。探针使用已校验 DLL 的导出接口：

- `getSinkSlack(pin, true)` 对应本例 aggregate setup slack；`false` 对应 hold slack。两者分别与全部 **2,750 个**报告终点匹配，误差小于 1 ps。
- 查询 **30,029 个**逻辑引脚，其中 **23,831 个**有有效 setup/hold 值；**6,198 个**返回 `1e10` 哨兵，明确排除，不作为无限余量使用。
- 同一 LUT 的不同输入得到不同预算。例如 `ng_coopt_2015/I0` setup slack 约 7.202 ns，`I1` 仅约 0.031 ns；旧的结构锥/全局最小余量无法表达这个差异。
- `getArrivalTimeForSyn` 在显式选择对应 slack 模式后，本例 `(false,true)` / `(true,true)` 得到 aggregate hold/setup 最坏经过引脚的到达时间。8,595 条 setup 与 10,005 条 hold 的报告记录在 1 ps 内匹配，覆盖 8,503 / 9,938 个不同引脚。**没有证明第二个布尔参数是 rise/fall，不能把四种调用组合称为四种转换时间。**
- 带布尔参数的 slack 查询会改写缓存。无参数 getter 在生成报告后，内部引脚和终点可能保留不同分析类型，不能直接作为 setup 查询。
- 查询前后 setup/hold 表相同。重复生成报告会追加脉宽报告；独立“不调用查询、只重复报告”的进程复现了完全相同内容，去除文件路径/时间元数据后前后报告分别一致。尚未把该检查扩大为所有原厂对象无副作用的证明。

原始捕获、完整终点对照、内部路径对照分别在 `results/native_pin_feedback_001/{pin_feedback,analysis}.json`，无查询对照在 `results/native_pin_feedback_control_001`。大文件无损 gzip，读工具透明解压。

## 实际接入与受控对照

`scripts/ownpr_pin_feedback.py` 校验捕获 SHA、原网表/位置/路由相关输入、CST/SDC 和完整终点表。它将逻辑引脚预算映射到 **14,779 个物理接收端**；多个逻辑引脚共用物理节点时取最小预算，任一缺失即回退。setup 留 1 ps、hold 留 10 ps 余量。候选按完整负载树的两种慢角/快角延迟变化筛选，最后仍强制完整 STA。

`scripts/ownpr_critical_tree_rebuild.py --pin-feedback <捕获目录>` 已接入该预算。对照保持网表、布局、时钟树、搜索参数、目标网及第一批八个候选的实际测量值完全相同：

| 条件 | 局部候选选择 |
|---|---|
| 旧全局余量：内部接收端统一 setup 0.030 / hold 0.122 ns | 八个原始候选及额外修复候选均拒绝 |
| 新内部引脚预算 | 选出一条完整树，关键支路两种慢角转换至少改善 2.5819 ns |

改变的是 FF `$auto$ff.cc:337:slice$47551` 驱动的 `net_2785`，11 个接收端，PIP **50→58**。其他 **6,493 个数据网**、全部寄存器/硬资源、网表、布局、时钟、封装引脚和接口周期不变。候选允许部分非关键支路增加最多约 2.0554 ns 慢角延迟，并用对应真实 slack 检查；不能把局部最小延迟优化作为所有支路都变快的承诺。

54 MHz 完整导出通过：setup **+0.292 ns**、hold **+0.132 ns**，零违例；报告 Fmax **54.865 MHz**，原结果为 54.089 MHz。新的最坏起点变为 `own_i_0936/Q`，说明竞争路径限制了全局收益。随后 **54.8 MHz 独立完整 I/O 验收通过**：setup +0.021 ns、hold +0.132 ns，零违例，详见 `results/vexriscv_54_8mhz_001/manifest.json`。合格频率比 54.0 MHz 提高约 1.48%。

## 复现

```powershell
runtime_bundle/python/python.exe experiments/native_pin_feedback_probe.py --source results/vexriscv_54_0mhz_001/export/native --placement results/vexriscv_control_batch_place_002 --out <新捕获目录>
runtime_bundle/python/python.exe experiments/analyze_native_pin_feedback.py --capture <新捕获目录>
runtime_bundle/python/python.exe scripts/ownpr_critical_tree_rebuild.py --placement results/vexriscv_control_batch_place_002 --routes results/vexriscv_control_batch_trial_002/route --sta results/vexriscv_54_0mhz_001/export/native --pin-feedback <新捕获目录> --count 3 --seconds 300 --include-single-sink --search-mode both --out <新路由目录>
```

随后必须通过 `export_ownpr.py` 完整导出；不能把路由器的 `routed` 状态当作时序合格。最终选择与带 SHA 的复跑输入由 `results/vexriscv_pin_budget_summary_001/finalize.py` 生成。

## 接下来仍须完成

1. 将内部引脚预算用于全局路由的逐接收端关键度、全局布局吸引力和候选分区；当前只接入整树物理优化。
2. 每次接受新树后刷新 STA，处理多个竞争逻辑锥；本轮只改一个网，避免将单网预算重复花在多条路径上。
3. 验证负 slack、更多转换/时钟模式及硬资源内部状态，之后才扩大查询适用范围。
4. 保持 synthesis 联合优化，继续关键链打包、DSP 寄存器吸收和 hold 开销改进。54 MHz 级结果仍未实现 150/200 MHz 或通用优于原厂的目标。

最终验证：429 项核心回归通过；到达图透明归档读取修正后，14 项 pin/整树/剪枝检查及 15 项到达图/扩展时序检查通过。归档后对 54.8 MHz 结果重新执行完整出口审计，验收通过。原厂编码位流与同一物理实现的 54 MHz 导出载荷一致；尚未上板验证功能。
