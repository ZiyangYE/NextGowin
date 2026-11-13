# 宽逻辑、状态前沿和保留布线的协同优化

当前主结果提高到 **74.0 MHz 完整 I/O 合格**，setup **+0.028 ns**、hold **+0.133 ns**，零违例，其他最终检查与编码导出通过。主检查点为 `results/vexriscv_shared_global_001/reconciled_001/qualified_replay_checkpoint.json`。相对上一项 71.4 MHz 提高 **3.641%**，仍不是用户要求的大幅优势；90/150/200 MHz 与通用优于原厂均未完成。最新共享寄存器、全局布局、实测树取舍及后续任务见本文末尾；此前章节保留历史尝试。

固定封装引脚、电气约束和接口周期不变，未调用原厂 P&R、开源器件数据库或自动编程硬件。原厂只提供器件/时序查询、最终 STA 与编码。旧主检查点和冻结交付包保持不变。

## 一次候选内结合逻辑重构和状态复制

`ownpr_cut_state_frontier.py` 检查新宽宏的直接寄存器输入。对已知初值、相同正沿时钟的 DFFRE/DFFSE，读取经过核验的历史 D/CE/复位 setup slack，按目标周期换算；筛掉输入已过紧的候选，再按距离选择最多 8 组副本。每组复制原 FF 及配对 D 逻辑，原寄存器继续保留。新副本使用相同 CLK/CE/RESET/SET，接口不增加周期。

这只是历史余量与几何筛选，**不保证新增负载后的时序**。最终对“原网表 → 宽宏重构＋新增状态”做一次整体初态/逐拍归纳证明，并重新处理时钟、数据路由、全部 I/O STA 与导出。

本轮选择 8 组。源至宏的跨度从 61、41、21、20、15、15、14、13 个网格缩短到 1–2 个。状态复制阶段仅额外挪动两个未配对普通 LUT，没有搬动该阶段已有的 FF；此前宽宏合法化的移动仍计入整体结果。

以下为实测整棵负载树接收端的 setup 最大分量，不能当作单一 RR 弧或整路径收益。数值来自第一次联合候选与未复制的共享宏实现：

| 原寄存器后缀 | 原 Q 连接 ns | 新 Q 连接 ns | 新副本 D 在 90 MHz 下 slack ns |
|---|---:|---:|---:|
| 53406 | 4.926 | 0.157 | +5.083 |
| 53388 | 4.674 | 0.324 | +5.707 |
| 47395 | 1.218 | 0.558 | +4.866 |
| 53397 | 2.163 | 0.477 | +7.401 |
| 48216 | 1.527 | 0.164 | +1.731 |
| 46842 | 2.050 | 0.329 | +1.665 |
| 47720 | 0.875 | 0.350 | +2.479 |
| 47713 | 1.221–1.253 | 0.350 | +4.444 |

动态 CE 的两个新副本也有正 setup 余量，整体 hold 仍为 +0.134 ns。`summarize_cut_state_frontier.py` 根据绑定的逻辑接收端、实际网/节点、测量文件来源哈希和状态复制证书逐项匹配；完整数据在 `input_wire_diagnosis*.json`。

## 为什么 Q 连接显著改善，整机反而先退化

| 独立完整尝试 | 数据布线起点 | 报告 Fmax MHz | 90 MHz WNS ns | TNS ns | 时间 s |
|---|---|---:|---:|---:|---:|
| 共享宽宏对照 | 原 67.9 MHz 状态 | 67.997 | −3.595 | −317.653 | 517.422 |
| `joint_cut_frontier_001`，增加 8 组副本 | 原 67.9 MHz 状态 | 65.398 | −4.180 | −319.293 | 538.031 |
| `joint_cut_frontier_002`，相同网表和布局 | 已验证的共享宽宏数据树 | 67.997 | −3.595 | −313.789 | 534.031 |

第一联合候选的新最坏路径经过 `ng_coopt_1612/F → ng_coopt_2065/I0`。该网在共享对照和联合候选中具有相同的物理源/接收端，但整树重新连接产生了不同分支，接收端延迟从 **0.345 ns 增至 4.114 ns**。这并非新副本 D/CE 已经违例。

新增 `--route-seed-report` 将逻辑基线与数据路线起点分开：核验种子来源哈希、原厂未做 P&R、固定 I/O/硬资源站点及除主周期外相同的约束，只提供数据线树。原逻辑选择、原时钟锚点和所有最终检查保持；增量路由仍逐网检查当前物理签名、图合法性和保留资源，不继承旧时序值。

第二联合候选的网表、布局、时钟树和物理网络均与第一项联合候选逐字节相同。复用 6,515 网、只重接 47 网，异常网的 PIP 集合与共享对照完全相同，其接收端实测恢复到 **0.345 ns**。这消除了路线起点造成的退化，但没有提高已验收主频，所以两个联合候选没有直接替换主结果。

## 刷新 STA 后继续选择关键逻辑

旧选择器使用每个终点的一条最坏路径估计收益。原计划中的 `ng_coopt_1986` 候选与 `ng_coopt_1348` 同时涉及报告路径 0、3、97；选择后者后，这三个编号的估计剩余需求都变为零。但优化后，启动寄存器和路径会切换，不能据此认为这些终点已闭合。

`vexriscv_joint_cut_refresh_001` 从第二联合候选重新捕获实际 STA，不再沿用旧报告。新选择为 `ng_coopt_965`、740、1138、1552、1395、1541、1564、2517；当前最坏路径中的 `ng_coopt_965` 因而进入重构。该轮不增加更多状态副本，继续保留此前已证明的 8 个。

最初数据路由失败，日志与失败状态保留：`net_7455` 反向搜索反复触及 100,000 节点上限。源点有限搜索没有证明它困在封闭区域，其他 6,560 棵树可验证且无冲突。

`ownpr_router.connection_search` 增加有界的源端搜索回退。反向搜索耗尽预算后，正向搜索仍使用同一有向图、拥塞/历史代价、保留节点和已接树的到达代价；不是忽略拥塞，也不直接提交几何引导路径。实际日志记录：反向 **100,001** 节点未完成，正向 **1,295** 节点找到连接；随后三轮协商处理两个相邻冲突，6,561 网、51,045 PIP 完整连通且无冲突。

恢复检查点时还修正了图上下文：`ownpr_route_checkpoint.py --placement` 校验源网表/布局哈希并配置经过校准的宽宏 FF 终端边。旧的无布局检查把合法的 `OF1 → DI1` 当作不存在的边，初次派生失败记录未删除。

`repair_ownpr_attempt.py` 从已检查的树恢复，沿用原尝试的 UTC 开始时间和 2,400 秒绝对截止时间，没有重新开始计时。最终连同诊断、恢复、导出和频率资格重放，共 **2,006.375 秒（约 33.44 分钟）**，在原限额内完成。最初失败的 `manifest.json` 保留；最终接受状态在 `repair_001/manifest.json`。

## 最终效果与代价

- **69.6 MHz** 完整 I/O：setup +0.015 ns、hold +0.134 ns，零违例；报告 Fmax 69.673 MHz。
- 同一 90 MHz 目标下，setup 违例从原 67.9 MHz 实现的 276 降到 236，TNS 从 −280.459 改善到 −194.919 ns；90 MHz 仍失败。
- 相对原主结果净增 **55 个 LUT1–4、47 个宽 MUX、8 个 FF**；12 个原 FF 位置发生变化，I/O 和硬资源站点不变。不能把收益描述为零面积代价。
- 等价证据链直接核对：原 67.9 MHz 网表 → 联合状态复制的归纳证明 → 刷新后的组合 CEC → 最终资格文件，哈希一致。不是仅凭测试通过推断功能正确。
- 本轮相关检查共 **64 项通过**。源端搜索测试初次有一个测试调用参数错误，修正后 23 项通过，失败日志保留。

仍需处理替代关键路径、跨迭代复用已证明的等价状态副本、更多共享/硬资源/时钟模式和动态整树代价选择；当前不是完整全局最优映射器。未证明冷 RTL 总耗时、任意设计通用优势、150/200 MHz 或硬件运行。

新最坏路径已变为 `slice$48209 → slice$47388`，布线 7.800 ns、单元含 C2Q 6.479 ns。第二条竞争路径从 `slice$53398` 发射，布线 10.582 ns、单元含 C2Q 3.645 ns。后续仍需同时处理较深的逻辑/算术路径与新暴露的长输入，不能继续假设上一轮的启动路径不变。

## 复现入口

```powershell
runtime_bundle/python/python.exe scripts/run_ownpr_region_coopt.py --report results/vexriscv_state_frontier_002/qualified_replay_checkpoint.json --route-seed-report results/vexriscv_shared_wide_001/backend_qualified_checkpoint.json --transform multilevel --timing-feedback --protect-slack 0.5 --target-aware-protection --share-wide-luts --input-register-copies 8 --input-copy-min-span 8 --target-mhz 90 --cut-count 8 --cut-width 7 --min-cut-cells 2 --cut-window 4 --cut-growth 48 --gain-target-ns 4 --legalize-macros --seconds 2400 --out results/<新联合目录>

runtime_bundle/python/python.exe scripts/run_ownpr_region_coopt.py --report results/vexriscv_joint_cut_frontier_002/replay_checkpoint.json --transform multilevel --timing-feedback --protect-slack 0.5 --target-aware-protection --share-wide-luts --target-mhz 90 --cut-count 8 --cut-width 7 --min-cut-cells 2 --cut-window 4 --cut-growth 48 --gain-target-ns 4 --legalize-macros --seconds 2400 --out results/<新刷新目录>
```

第二条使用当前路由器的新回退逻辑；不保证逐字节重现旧失败路径。复放已验收状态可直接使用 `replay_coopt_checkpoint.py` 和本文件顶部的主检查点。所有运行必须使用新输出目录。

最终审计：`results/vexriscv_joint_cut_frontier_summary_001/final_audit.json`；对照：`summary.json`、`restored_tree.json`、`input_wire_diagnosis_002.json`；搜索证据：`results/vexriscv_joint_cut_refresh_001/repair_001/route/progress.jsonl`。

本轮采用带哈希核验的无损压缩和重复输入引用，未改原始主结果或冻结 ZIP。相关清单位于 `cleanup/shared_wide_terminal_exports_20261106_001.json`、`native_capture_input_dedup_20261106_003.json`、`joint_frontier_terminal_exports_20261106_001.json`、`joint_frontier_refresh_reserve_20261106_001.json`。最终审计时工作区约 **38.47 GB**，唯一上限为 40,000,000,000 字节。

## 最新：全部引脚反馈发现候选，71.4 MHz 验收

`ownpr_pin_cut_selection.py` 从已核验的输出引脚 slack 发现逻辑根，不要求根先出现在详细最坏路径清单。当前有 1,020 个普通 LUT 根处于选择窗口；实际有界检查其中 256 个，其余 764 个未探索。256 个中有 102 个没有出现在此前路径列表。本轮选择 12 个重构，4 个来自这部分：`ng_coopt_1463`、1561、1550、2031。

旧内锥的排名代价使用经过哈希核验的整树接收端延迟及原厂来源单元弧，扣除不能靠消除内部节点而消失的第一段输入布线。它仍只估计旧内部延迟减去新末级单元延迟，**没有预测所有新输入的到达时刻、新负载与新布线**。聚合引脚 slack 不是逐转换 arrival/required，不能将这个排名分数解释为新路径时序预算。

候选允许共享原样保留的前缀，例如两个不同根都使用 `ng_coopt_1058`；仍拒绝“一个被改写根属于另一个 cut 内部”的依赖冲突。只把实际重写的根计为已处理，不再将端点或某条旧报告路径标为闭合。时钟逻辑被排除；部分 CFU 合法化保护其他待重写根，避免提前把它们困进新宽宏。

实际结果：

| 指标 | 69.6 MHz 基线 | 新实现 |
|---|---:|---:|
| 独立完整 I/O 合格频率 | 69.6 MHz | **71.4 MHz** |
| 报告 Fmax | 69.673 MHz | 71.466 MHz |
| 90 MHz setup WNS | −3.242 ns | −2.882 ns |
| 90 MHz setup TNS | −194.919 ns | −176.925 ns |
| 90 MHz setup 违例数 | 236 | 228 |
| 合格频率下 setup / hold WNS | +0.015 / +0.134 ns | +0.013 / +0.134 ns |

完整捕获、重构、CEC、自研时钟/数据 P&R、检查、导出和 71.4 MHz 资格重放耗时 **855.125 秒**，在同一个 2,400 秒期限内结束。14 项相关检查通过，日志 `results/pin_cut_selection_tests_001.log`。净增 **44 LUT、44 宽 MUX**，没有新增状态，15 个已有 FF 的位置改变；所有固定 I/O、硬资源站点和接口周期保持。CEC 直接连接前后网表哈希并验证 8,676 个观察点。新旧检查点的 20 项证据哈希通过最终核验。

对相同 2,785 个 setup 端点按周期归一化比较，130 个改善超过 10 ps，99 个退化超过 10 ps；最坏发起点可能改变，这不是逐条完整路径相减。新最坏路径从 `slice$46577` 至 `slice$47388`，单元含 C2Q **4.000 ns**、布线 **9.892 ns**；第二条的单元为 3.484 ns、布线 10.406 ns。最终仍由连接延迟主导。

还发现明确的控制侧代价：`slice$47927` 随宽 LUT6 根从 `[70,100,0]` 移到 `[79,99,1]`，D 端 setup 改善约 **2.529 ns**，CE 端却退化约 **4.207 ns**。其实际整树 CE 线延迟从 **0.352125 ns 增至 4.691250 ns**；在 90 MHz 下 CE slack 为 −0.697 ns。当前候选的几何位置代价没有完整覆盖随动寄存器的控制输入，后续必须同时优化 D/Q、CE/SR 和时钟。不能把本轮整体合格等同于每条连接都更好。

最终审计与详细路径：`results/vexriscv_pin_frontier_summary_001/final_audit.json`。复现命令使用新目录：

```powershell
runtime_bundle/python/python.exe scripts/run_ownpr_region_coopt.py --report results/vexriscv_joint_cut_refresh_001/repair_001/qualified_replay_checkpoint.json --transform multilevel --timing-feedback --pin-frontier --pin-root-limit 256 --protect-slack 0.5 --target-aware-protection --share-wide-luts --target-mhz 90 --cut-count 12 --cut-width 7 --min-cut-cells 2 --cut-window 4 --cut-growth 48 --gain-target-ns 4 --legalize-macros --seconds 2400 --out results/<新引脚前沿目录>
```

## 结构性差距：寄存器打包容量尚未利用

`audit_packing_gap.py` 按物理 CFU 统计以下状态。两份原厂结果是历史参考，网表不同；此表用于确认实际采用过的打包组合，**不能解释为同网表性能倍率或面积算法贡献率**。

| 物理实现 | 占用 CFU | 含 FF 的 CFU | FF 总数 | 同 CFU FF 最大数 | 宽 MUX 与 FF 共址 CFU |
|---|---:|---:|---:|---:|---:|
| 此轮起点，自研 69.6 MHz | 1,400 | 743 | 1,549 | 3 | 2 |
| 较早自研网表交原厂 P&R | 850 | 612 | 1,526 | 8 | 8 |
| 原厂综合＋P&R 历史参考 | 669 | 514 | 1,319 | 8 | 23 |

原厂参考还分别有 51 / 43 个 ALU 与 FF 共址 CFU，自研当前为零。新范围之外的组合需要逐项校准，不能仅凭原厂出现过就开放任意混合。容量占用也受网表大小、hold 缓冲及复制影响，不能全部归因于寄存器打包。

当前普通 FF 有 49 类完整控制/参数签名，其中 853 个共享同一组时钟、CE 和复位连接。按签名最多可组成 757 对，这只是忽略位置、可用槽位与时序的数量上界，不是已得到的合法方案。生产后端仍按独立控制组最多放 3 FF/CFU，且限制宽宏/算术宏与其他 FF 混用；这是值得优先补齐的结构能力。

`native_shared_ff_encoding.py` 现在支持指定 FF 类型、CE/复位条件和成对槽位。新增三个实际校准：`native_shared_ff_re_one_zero_001`、`native_shared_ff_re_one_zero_slot2_001`、`native_shared_ff_re_one_zero_slot4_001`，均为 type-17、DFFRE、CE=1、RESET=0、默认初值、槽位 0/1、2/3、4/5。每项核对独立 LUT→D 直连、103 个预测配置位和 8 个实际变化位，零缺失、零多余、零数值差异，并恢复原位流载荷。每项耗时约 34–35 秒，不执行原厂 P&R。

这三项是**隔离配置校准**，尚未证明共享控制/时钟接收点的真实自研布线、完整 CPU STA 或硬件运行，`production_packer_enabled=false`。下一步先将严格相同控制网络身份的普通 FF 成对打包接入后端和全局布局，再做完整对照；随后扩展宽宏/算术共址，同时补足移动 FF 的控制路径预算。不能只增加候选数量而忽视这些物理限制。

本阶段最终工作区约 **36.42 GB**。为预留后续实验，已对终止的历史算术打包导出做哈希核验的无损压缩/引用，记录为 `cleanup/measured_carry_terminal_exports_20261106_001.json`、`cleanup/pin_frontier_next_reserve_20261106_001.json`；此前本阶段存储处理见 `cleanup/historical_state_exports_20261106_001.json`、`cleanup/native_capture_input_dedup_20261106_004.json`。原主结果逻辑内容、冻结交付 ZIP 不变，40 GB 是唯一上限。

## 最新：共享 FF、全局布局和实测布线联合达到 74.0 MHz

`ownpr_shared_ff_packing.py` 已接通显式准入的共享控制组。当前支持 type-17、正沿 DFFRE、默认初值、RESET=0、CE=1 或动态 CE，槽位为 0/1、2/3、4/5。两个 FF 的实际时钟/使能/复位网络身份必须相同；两个不同的动态网络不能因为都标为 `signal` 就合并。动态 CE 的三个槽位组合新增了独立编码校准，与此前常量 CE 校准共同核对。

`ownpr_pack_shared_ff.py` 将配对 LUT 和 FF 同时移动，代价包含全部已绑定 D/Q/CE/SR 连接，时钟另行重接和验收。`ownpr_global_relayout.py` 把共享对作为不可拆分单元进行密度优化和合法化。当前只是时序加权的几何目标，仍不预测实际加载后的所有线延迟。

最终原厂回读逐对核验 **256 对、512 个寄存器**的实际位置、共享 CLK/CE/RESET 节点、独立 Q 节点和 LUT→D 直连。全部 FF 数量保持 1,549；整个实现占用 CFU 从 1,405 降至 **1,319**，含 FF 的 CFU 从 741 降至 **636**，其中 12 个 CFU 实际使用 6 个 FF。宽宏和进位链仍有其他共享限制，不能将此泛化成任意器件/控制组合都已支持。

### 保留失败对照，而不是把线长变短当作成功

| 阶段 | 报告 Fmax MHz | 90 MHz setup WNS ns | 90 MHz TNS ns | setup / hold 违例 |
|---|---:|---:|---:|---:|
| 原 71.4 MHz 合格实现 | 71.466 | −2.882 | −176.925 | 228 / 0 |
| 64 对局部共享配对 | 63.635 | −4.603 | −350.707 | 274 / 0 |
| 256 对＋全局重布局 | 68.737 | −3.437 | −82.362 | 135 / 1 |
| 全局布局＋三 LUT hold 修复 | 68.737 | −3.437 | −82.362 | 135 / 0 |
| 再合入实测 BRAM 地址树 | **74.156** | **−2.374** | **−78.709** | **135 / 0** |

64 对局部候选在 60,266 个候选配对中选择，几何加权距离下降约 8%，却退化。原因之一是配对时将 `ng_coopt_387` 从 `[69,99,1]` 交换到 `[71,101,0]`，其输入支路从约 0.1575 ns 增至 3.726 ns。它在旧周期下有约 2.035 ns setup 余量，换算到 90 MHz 后已不足以承担这种移动。后续配对阶段加入目标周期下的旁路保护；随后的全局布局仍显式重排全部可移动单元，不能把该保护说成全局位置冻结。

256 对全局候选移动 3,729 个布局单元；按实际单元计，1,524 个原 FF 和 4,219 个原 LUT 位置改变。数据从头重布，9 轮协商后全部连通、无资源冲突，路由阶段约 850 秒。最差路径转向 BRAM 地址，另有 `externalInterruptArray[20]`（固定 V18）的一个 −0.129 ns hold 违例。为该分支加入三个组合 LUT 缓冲，CEC 验证 8,676 个原观察点，hold 违例清零。没有新增流水级，也没有调整输入延迟或引脚。

### 允许改善最坏时序的有限取舍，再用完整 STA 决定

`net_4203` 从 `ng_coopt_3138` 驱动三个 BRAM 地址接收端。重新接入历史合法路线的下游片段，整树测量找到了以下变化：

| 接收端 | 原最大 setup 线延迟 ns | 新最大 setup 线延迟 ns |
|---|---:|---:|
| cache way 0、bank 0，ADB[11] | 3.427875 | 0.906750 |
| cache way 0、bank 1，ADB[11] | 3.106125 | 1.404000 |
| cache way 1、bank 0，ADB[9] | 1.353375 | 1.923750 |

第一支路在两个 setup 转换方向中的最小改善为 **2.418750 ns**；表内最大分量差为 2.521125 ns，二者不是同一统计量。第三支路慢了 **0.570375 ns**，其预测 slack 从 −1.371 变到约 −1.941 ns，仍优于原全局最差 −3.437 ns。

旧筛选器把所有负 setup 余量的可用预算截为零，因此一律拒绝该取舍。新增显式 `--explore-current-wns` 仅产生不可直接验收的候选：要求有实际关键支路改善、每个支路 setup 损失不超过 0.75 ns、预测最差接收端仍优于原 WNS，且 **hold 预算不放宽**。随后完整 STA 和独立频率验收决定是否接受；并未放松 90 MHz SDC 或把预测当成闭合证明。

该树与已完成的 hold 修复按严格相同的物理源/接收端签名合并。完整当前资源图重新验证连通性与冲突；审计确认除 `net_4203` 外，其他 hold 修复后的数据树逐项不变。最终原厂核对自研配置、全部 I/O 时序与编码结果。

结果为 **74.0 MHz 完整 I/O 合格**，setup **+0.028 ns**、hold **+0.133 ns**，零违例。比 71.4 MHz 提高 **3.641%**；90 MHz 的 TNS 改善约 **55.513%**，但仍有 135 个 setup 违例。相对原主结果只净增 **3 LUT4**，FF、宽 MUX、BRAM、DSP 数量不变。

完整全局尝试、失败筛选、hold 修复、68.6 MHz 中间资格检查、树取舍合并和最终资格检查，沿用同一个 UTC 开始时间与 **3,500 秒**截止时间；最后控制器记录 **3,218.064 秒（53.634 分钟）**。不是重新启动计时，也不是从原始 RTL 冷启动的耗时。原始被拒绝 manifest 保留；最终接受在 `reconciled_001/manifest.json`，之后的只读跨文件审计另有 UTC 时间。

最终入口与审计：

- `results/vexriscv_shared_global_001/reconciled_001/qualified_replay_checkpoint.json`
- `results/vexriscv_shared_ff_summary_001/final_audit.json`
- 失败/初始全局对照：同目录的 `local_audit.json`、`global_initial_audit.json`
- `audit_shared_ff_route.py` 已纳入正式最终出口核验，覆盖每一对实际 FF。

相关共享模式、全局合法化、出口、负 WNS 取舍、路线合并及接收端重建检查均通过；日志依次为 `shared_ff_packing_tests_001.log`、`shared_ff_global_tests_001.log`、`shared_ff_handoff_tests_001.log`、`shared_global_wns_trade_tests_001.log`、`shared_route_reconciliation_tests_001.log`、`shared_receiver_reconstruction_tests_001.log`。这些测试组有交集，不将组内数量直接相加。

后验分析还发现：未启用历史权重的 hold 路线没有 `search_weights.json`。`measured_inputs` 现可使用明确提供的、哈希一致的网表/布局及硬端点重建逻辑接收端，并逐网比较全部物理签名。真实旧捕获的接收端集合完全匹配，最终状态重建了 15,495 个接收端；不修改已验收路线或补造旧时序值。

### 剩余瓶颈和研究边界

目前最坏路径是 **reset（V14 / IOR11B）→ ng_coopt_975 → slice$46833/RESET**。IBUF 有 121 个负载，第一段普通布线 **14.997 ns**，第二段 0.851 ns；单元含 IBUF 共 1.125 ns。下一优先项是保持固定引脚、电气条件和接口周期的通用高扇出网络分区、组合缓冲和整树实测选择；不能将 reset 从 I/O 评测中排除。硬资源站点本轮仍固定，BRAM 地址布局/树选择及其他竞争路径也未解决。

进一步核查了 [TD-Placer 作者论文](https://arxiv.org/html/2512.00038v1) 与 [作者实现说明](https://github.com/yyiloe/TD-Placer/blob/main/README.md)：关键在于随位置更新的延迟模型和细粒度路径权重，而不只是减小几何线长；作者说明其完整全局布局器并未随预测代码公开。本项目没有复现其完整模型，未采用其器件数据库或预训练器件数据。[ICCAD 2025 官方程序](https://confcats-event-sessions.s3.us-east-1.amazonaws.com/iccad25/uploads/ICCAD_2025_Program_v8.pdf)核实了可微 FPGA 布局与 ML 延迟校准论文，但未取得全文，不宣称复现。

另对“提前计算状态函数、保持外部周期”的方向做了有界筛查：前 128 个逻辑根中发现 14 个同钟同步状态 cut，均未同时满足当前筛选的 2 ns 下一状态输入余量。`ng_coopt_1135` 的 D 输入较宽裕，但控制输入紧张；这不是不可行证明，也没有实现或验证派生状态寄存器。结果仅在 `derived_state_screen.json`，未用于选择主实现。

当前工作区约 **37.91 GB**。本阶段无损存储清单为 `cleanup/shared_ff_rejected_export_20261106_001.json`、`cleanup/shared_global_refinement_reserve_20261106_001.json`、`cleanup/shared_global_trade_reserve_20261106_001.json`。冻结交付 ZIP 和旧结果逻辑哈希保持不变；唯一容量上限仍为 40,000,000,000 字节。

从 71.4 MHz 检查点运行当前联合入口（使用新目录）：

```powershell
runtime_bundle/python/python.exe scripts/run_ownpr_region_coopt.py --report results/vexriscv_pin_frontier_001/qualified_replay_checkpoint.json --transform shared_ff --timing-feedback --shared-ff-count 256 --shared-ff-radius 24 --shared-ff-global --target-mhz 90 --seconds 3500 --out results/<新共享全局目录>
```

这条命令执行首次全局候选，不包含上述人工诊断后的全部恢复决策。重放最终已验收状态请使用本文顶部主检查点与 `replay_coopt_checkpoint.py`；恢复命令及各阶段源码快照保存在原尝试/恢复目录。完整冷 RTL 自动闭环、90/150/200 MHz 和多设计通用优于原厂均仍未完成。
