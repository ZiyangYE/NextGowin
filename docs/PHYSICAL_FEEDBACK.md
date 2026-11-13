# 跨层次控制网络与物理反馈实验

首组真实工程结果见 [FPGAFly 接入记录](FPGAFly_REMAP.md)。局部 ACD 候选改善了一次物理结果，但仍有 4 个 setup 端点违例；该次最差数据路径的互连延迟占比约 88%。以下工作用于探索这些跨模块负载，仍属研究原型。

## 受控结构展平

[flatten_gowin_core.py](../scripts/flatten_gowin_core.py) 只展开 `frame_engine` 的层次，按位解析严格等宽的无符号端口、拼接、重复和 assign。它拒绝宽度不匹配、未知常量、参数覆盖、递归层次和 inout。原始根端口保留；外围 DDR、PLL、JTAG 与顶层板级结构保持原来的模块体。

通过 [原语方向提取器](../scripts/extract_primitive_ports.py)，从原厂功能仿真源码建立 [136 种原语的方向表](../results/timing/primitive_ports.json)，检查单驱动、所有被使用的网线类都有驱动，以及所有叶单元的方向可识别。随后反向解析实际生成文件，核对所有叶实例的类型、参数、端口逐位连接及根输出别名。不可达的旧模块定义被删除，避免产生多个候选顶层；它们不是被删除的硬件实例。

当前 [展平清单](../results/fpgafly_flat_core_002/manifest.json) 包含 200 个层次实例、78,086 个叶单元，展平前后叶单元数相同。完整文件从 212 个模块定义变为 13 个，其他可达模块体逐字节相同。较早的 `flat_core_001` 是原生 P&R 输入；加入方向与驱动检查后重新生成的 `002` 与它完全同字节，见 [重新核验](../results/fpgafly_flat_core_001/revalidation.json)。

这些检查是受限结构化网表的图保持验证，不是 RTL elaboration 或仿真签核，也不把层次展平本身视作 Boolean 优化。已单独完成 [展平种子的 P&R](../results/fpgafly_flat_seed_pnr_001/manifest.json)：五时钟及原始约束激活，ui_clk 报告 Fmax 为 77.823 MHz，最差已报告 setup slack 为 -0.350 ns，150 个 setup 端点违例、hold 违例为 0。它仍未达到 80 MHz，且与层次原种子的物理差异说明需要保留这一对照，才能单独评估复制效果。

## 布线估计的训练与保留报告

[wire_feedback.py](../scripts/wire_feedback.py) 从主 setup 路径表提取数据网线，排除 launch clock tree。采用非负最小二乘拟合偏置、行距离、列距离和驱动扇出对数四项，并保留报告、器件和 corner 哈希。

[首次模型](../results/timing/wire_feedback_001.json) 使用原始基线的 63 条去重数据网线训练；42 模块优化报告的 61 条线作为未参与拟合的报告验证。训练 MAE 为 0.416 ns，验证 MAE 为 0.734 ns，验证最大低估为 3.766 ns。这里的保留集是同一工程的新布局报告，不是独立电路套件；样本仅来自有限条被报告路径，存在明显选择偏差。

该误差远大于当前 0.076 ns 的剩余违例量，模型只能辅助排序候选，不能作为签核边界，也不能宣布估计 slack 为正就达到时序收敛。

## 基于实测慢线的同 cut 复制

[physical_replication.py](../scripts/physical_replication.py) 以原生 setup slack 和慢线为触发条件，评估在接收端附近复制驱动 LUT 的假设；它考虑已观测关键输入的距离变化，并对未知输入增加罚项。实际不固定新 LUT 的坐标，最终位置由高云后端决定。

默认最多 8 个副本、0.1% 局部面积增长、每条输入最多增加 4 个负载；筛选需满足预先设定的线延迟、slack 和估计局部收益阈值。物理报告的输入引脚可能经过重排，因此按原始网络连接定位逻辑引脚，不直接把报告 I3 当成源网表 I3。它只重接 LUT 输入，不改寄存器或硬块端口连接；时钟和异步控制保护仍生效。

这是借鉴复制思路的独立物理反馈启发式，不是 FAPO 的完整最小割算法；被报告路径也不等于完整关键 DAG。每个副本保留原始真值表及相同 cut。把副本输出折回原驱动后，须恢复完整原 LUT 图；原非 LUT 语句须保留，再对实际生成的整个 core 切口做 CEC。

[复制候选](../results/fpgafly_physical_copies_002/manifest.json) 增加了 3 个 LUT，并通过折回图核验与完整切口 CEC。候选基于较早的层次网表物理报告生成，坐标与最终展平种子的布局可能不同；这一点属于实验假设，不是相同位置保证。[配对物理实验](../results/fpgafly_flat_physical_pair_002/manifest.json) 已完成，复用展平种子并使用相同板级约束。[独立比较](../results/fpgafly_flat_physical_pair_002/comparison.json) 显示这次复制导致退化：

| 指标 | 展平种子 | 增加 3 个 LUT 副本 |
| --- | --- | --- |
| ui_clk 报告 Fmax / MHz | 77.823 | 75.389 |
| 最差已报告 setup slack / ns | -0.350 | -0.765 |
| ui_clk setup TNS / ns | -21.829 | -137.492 |
| setup / hold 违例端点 | 150 / 0 | 363 / 0 |
| 物理 LUT | 39035 | 39038 |
| 寄存器 / BSRAM | 48093 / 270 | 48093 / 270 |

两者均未达到 80 MHz。候选最差数据路径的互连约占 79.87%，见 [路径分解](../results/fpgafly_flat_physical_pair_002/optimized/path_accounting.json)。这一结果否定了本次启发式候选的收益，不能用局部估计替代实际 P&R，也不据一次样本推断所有复制方法无效。原生运行时间为 1756 秒与 2137 秒，期间有其他主机工作，不作为受控性能对照。

早期 `physical_copies_001` 虽通过逻辑 CEC，但将新网线声明放在重接的旧 LUT 后面，不符合高云映射网表读取器的声明顺序要求；`flat_physical_pair_001` 因此在读取阶段被拒绝。`002` 将声明提前，保持相同三个复制操作，重新通过完整切口 CEC，并由后端重新读取。失败试验继续保留，不能当成布线成功结果。

## 运行入口

```powershell
python scripts/extract_primitive_ports.py --out results/timing/primitive_ports.json
python scripts/flatten_gowin_core.py results/fpgafly_source_001/source.vg --root frame_engine --top thefly_top --out results/core_flat_new
python scripts/wire_feedback.py --train results/fpgafly_native_baseline_001/mapped.tr --validate results/fpgafly_physical_pair_002/optimized/mapped.tr --out results/wire_model_new.json
python scripts/physical_replication.py results/core_flat_new/flattened.vg --root frame_engine --report results/fpgafly_native_baseline_001/mapped.tr --wire-model results/wire_model_new.json --leaf-mapping results/core_flat_new/leaf_mapping.json --instance-prefix core --out results/copies_new
```

原生 P&R 与独立时序审计的命令见接入记录。`compare_project_timing.py` 会拒绝比较器件、corner、工具、实现选项或板级约束不一致的结果。本次退化候选不接入默认映射选择，原始固件保持原样。全关键 DAG、受控位置约束、模型校准与多次物理验证仍是后续任务。
