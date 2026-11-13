# NextGowin

面向高云（Gowin）FPGA 的综合（Synthesis）与布局布线（Place & Route，P&R）协同优化工具项目。

**首版仅面向 138K 器件。** 先围绕这一目标打通流程、验证结果，再考虑扩展支持范围。

**后续守卫已完成：** 同一结构搜索在 UART / int2float / TMDS 上实际测量了 6 / 6 / 3 个候选，原始输出保持不变，并重新通过 197 / 167.5 / 112.3 MHz 完整 I/O STA。另对三项实际改写的隔离候选完成导出、完整 STA 与逐端点模型对照；它们不作为交付，也不计为性能收益。VexRiscv 仍为 88.6 MHz，110 MHz 未完成。[本轮证据与新参考模型](docs/STRUCTURAL_GUARDS_AND_REFERENCE_MODELS_20261111.md)，[附守卫证据的检查点](results/joint_lut_structural_guards_001/qualified_replay_checkpoint.json)。后文的“待守卫”是前一阶段历史状态。

**最新 VexRiscv 局部检查点：88.6 MHz 完整 I/O STA 合格**（setup +0.001 ns、hold +0.133 ns），增加 2 LUT、无新增寄存器。已实现限定范围的 LUT 拓扑重建与多锥映射/位置/路由联合事务，并修复负数 TNS 评分方向。110 MHz、大幅架构收益及新结构算法的跨工程守卫仍未完成；尚不作通用采用声明。[本轮实现、失败修复与剩余任务](docs/JOINT_LUT_GRAPH_AND_BATCH_20261111.md)，[Vex 可复现检查点](results/vexriscv_joint_lut_graph_trial_001/qualified_replay_checkpoint.json)。后续段落保留各阶段历史结果。

**2026-11-11 最新状态：VexRiscv 88.1 MHz 完整 I/O STA 合格**，setup +0.008 ns、hold +0.133 ns，零违例。当前唯一主频优化目标为 110 MHz，尚未完成；UART/int2float/TMDS 是泛化回归守卫。新增路由子树事务将实际频率从 87.3 提升到 88.1 MHz，但乐观上界分析显示，仅重分配现有副本输出树不足以闭合 110 MHz，下一阶段转向组合映射、打包和局部布局布线共同搜索。新增算法尚未证明跨工程泛化。[本轮证据和下一阶段任务](docs/ROUTED_SUBTREE_AND_ARCHITECTURAL_BOUND_20261111.md)，[可复现检查点](results/vexriscv_routed_branch_trial_001/qualified_replay_checkpoint.json)。

下文至“为什么做这个项目”之前保留历史阶段记录，当前结果以上述检查点为准。

当前自研 VexRiscv 达到 **74.0 MHz 完整 I/O 合格**，setup +0.028 ns、hold +0.133 ns，零违例。256 对共享寄存器、全局重布局、负载树重接和三 LUT hold 修复完成联合验收；相对 71.4 MHz 提高 **3.641%**，FF/MUX 不增加。完整尝试沿用原 UTC 开始时间及 3,500 秒限额，最终验收共 **3,218.064 秒**。同一 90 MHz 目标下，setup TNS 从 −176.925 改善至 −78.709 ns，但 90/150/200 MHz 和大幅通用优势仍未完成。新的瓶颈为固定 V14 复位输入的 121 负载树，一条支路达 14.997 ns。工作区唯一上限为 **40 GB**。[联合优化、失败对照及限制](docs/JOINT_CUT_STATE_20261106.md)，[主检查点](results/vexriscv_shared_global_001/reconciled_001/qualified_replay_checkpoint.json)。

此前实测输入/单元/全部输出树后进行联合路径预算筛选，4 个普通 LUT 与算术单元共址取得 67.1 MHz 合格结果。[此前测量与预算对照](docs/MEASURED_CARRY_20261106.md)。

此前已接通校准范围内的 LUT5/6 与普通 LUT 共享、部分 CFU 合法化及已有宽宏的邻接打包。实际 LUT5 共址候选和 LUT6 槽 4 共址候选均通过 **67.9 MHz 完整 I/O** 验收；同网表共享/独占对照报告为 67.997 / 65.002 MHz，该阶段尚未提高主频。56 项相关检查通过，另有 3 项子集复验。[完整后端覆盖、对照与限制](docs/SHARED_WIDE_BACKEND_20261106.md)。

此前路由阶段加入按组合路径累计的 setup/hold 预算保护。深度候选经历史树恢复通过 65.2 MHz 全 I/O 验收；当时继续保留资源更少的五锥网表，后续优化从其衍生实现推进。[此前验证与复现](docs/PATH_BUDGET_ROUTING_20261105.md)。

后续已将经过核验的历史引脚关键性接入全局路由，并保护宏合法化时的近关键单元。同一网表/布局对照的报告 Fmax 从 53.056 提高到 63.452 MHz；保护布局后为 66.740 MHz，仍未超过最佳基线，因此全部拒绝替换。35 项相关检查通过；这不代表通用优势或 90 MHz 已闭合。[本轮完整对照与剩余瓶颈](docs/GLOBAL_FEEDBACK_20261106.md)。

## 为什么做这个项目

这个项目源于我在使用高云现有工具时的实际体验：P&R 太难用了，而我需要的时序驱动综合能力也没有得到满足。

我希望时序目标能够从综合阶段就参与优化决策，并贯穿后续的布局布线过程。遇到时序问题时，工具也应该提供足够清晰的反馈，让优化有依据，而不是反复尝试参数、等待结果。

NextGowin 就是为此开始的：为高云 FPGA 开发一套更好用、以时序优化为核心目标的综合与布局布线工具。

## 项目目标

- **综合与 P&R 协同优化**：以实际完整 I/O 时序为目标，让布线延迟、位置、扇出和时钟偏斜反向指导映射、逻辑重构与打包，再经自主 P&R 和原厂最终 STA 验收；不要求先把综合独立优化到极限。最新任务顺序见 [J0–J5](TASKS.md)。
- **时序驱动综合**：让时序约束参与综合与映射过程中的优化决策。
- **时序驱动布局布线**：围绕关键路径改进布局与布线结果。
- **清晰的诊断与报告**：让资源使用、关键路径和时序瓶颈更容易理解。
- **可复现的流程**：让配置、运行与结果比较更直接，方便持续迭代。

以上是开发目标，不代表当前已经实现的功能。

## 首版范围

首版聚焦 **138K**，暂不承诺支持其他容量或系列的高云器件。

具体器件型号、封装、速度等级，以及可用的逻辑、存储和专用资源支持情况，将随实现与验证进度补充。首版的重点是把单一目标做扎实，而不是尽早覆盖整个产品线。

## 当前状态

最新结果：**TMDS 112.2 MHz、VexRiscv 51.5 MHz 均通过完整 I/O STA**。本轮按终点时序预算重建并局部修复一个 CE 网络，使 CPU 从 51.3 MHz 小幅提高；网表、布局、时钟树和逻辑资源数不变，该网普通 PIP 数从 57 增到 99。仍低于原厂参考报告 79.8 MHz。固定引脚和接口周期不变，通用高频目标仍未完成。387 项核心回归及补充检查、压缩后审计通过。[结果与复跑](docs/TMDS_VEX_GENERALIZATION_20261105.md)，[最新 CPU 状态](results/cpu_tree_coopt_summary_001/summary.json)。下文保留阶段历史。

最新对照：已接通 STA 引导的整条进位链选择、等价重映射、核心 FF 位置锚定与时钟树复用。三种重映射和一次物理调整完成完整检查，均未超过成熟版本，**TMDS 仍选择 102.6 MHz，VexRiscv 50.6 MHz**。349 项回归通过，工作区约 9.97 GB。下一步核查独立 FF 数据入口和宽 MUX/FF 打包，相关能力尚不能宣称可用。[对照与诊断](docs/TMDS_VEX_GENERALIZATION_20261105.md#后续对照选择性进位链与固定-ff-布局)，[机器汇总](results/tmds_arithmetic_summary_001/summary.json)。下文保留历史结果。

最新续进：选择端驱动复制和完整宽宏复制使 **TMDS 102.6 MHz 完整 I/O STA 通过**（setup +0.006 ns、hold +0.201 ns），比 97.4 MHz 提高约 5.3%，增加 7 LUT / 3 MUX。更大的 102.667 MHz 报告峰值版本仅保留作对照，推荐较小版本。VexRiscv 保持 50.6 MHz；347 项核心回归通过，工作区约 9.93 GB。150/200 MHz 和通用领先仍未实现。[执行记录与复跑](docs/TMDS_VEX_GENERALIZATION_20261105.md#后续完成tmds-选择端与完整宽宏复制)，[机器汇总](results/tmds_replication_summary_001/summary.json)。以下保留阶段历史。

最新续进：关键链成组移动、合法化交换与关键驱动复制联合实测，将 VexRiscv 的完整 I/O 合格频率从 48.5 提高到 **50.6 MHz**（setup +0.004 ns、hold +0.132 ns），本轮仅增加 1 LUT，总计 4,156 LUT。一个退化候选已拒绝，TMDS 保持 97.4 MHz。150/200 MHz 与超过原厂的通用优势仍未实现。核心回归 339 项及新增宏保护后的相关 5 项复验通过，覆盖 340 个不同测试。[完整结果及复跑](docs/TMDS_VEX_GENERALIZATION_20261105.md#后续完成关键链合法化与驱动复制协同)，[机器汇总](results/vexriscv_physical_coopt_summary_001/summary.json)。下文保留阶段历史。

最新续进：VexRiscv 通过批量恒等缓冲修复全部输入 hold，新增 467 个缓冲 LUT，总 LUT 4,155。**48.5 MHz 完整 I/O STA 已闭合**：setup +0.023 ns、hold +0.132 ns，零违例；TMDS 保持 97.4 MHz。200 MHz 下 setup 和脉宽仍失败，报告 Fmax 48.555 MHz，没有超过原厂参照。335 项回归通过，工作区约 9.88 GB。详见[批量 hold 修复及复跑](docs/TMDS_VEX_GENERALIZATION_20261105.md#后续完成批量输入-hold-修复与-cpu-合格基线)和[当前机器汇总](results/vexriscv_hold_closure_summary_001/summary.json)。下段保留此前阶段状态。

最新推进（2026-11-05）：TMDS 已在 **97.4 MHz** 完整 I/O STA 闭合（报告 97.477 MHz）。VexRiscv 已完成当前校准 BRAM/DSP 模式的出口审计，包括 8,080 条硬资源时序弧和 3,092 个脉宽检查；完整自主布线可交给原厂 STA/编码。通用组合驱动复制累计增加 3 个 LUT，将报告 Fmax 从 41.607 提高到 **48.555 MHz（+16.7%）**。但 setup、hold、脉宽仍失败，CPU 尚无合格频率，低于同网表原厂 P&R 参照 79.800 MHz。其他硬资源模式、初始化载荷独立验收和硬件验证仍未完成。331 项核心回归通过。详见[本轮结果与后续任务](docs/TMDS_VEX_GENERALIZATION_20261105.md)和[最新机器汇总](results/hard_sta_replication_001/summary.json)。

以下为此前交接复核阶段状态。

2026-11-04 已完成对并行交接包的隔离复核与三个工程的自主 P&R/完整 I/O STA 实测：**UART 197 MHz、int2float 165.7 MHz、TMDS 53.5 MHz** 分别闭合，报告 Fmax 为 197.028、165.745、53.617 MHz。UART 组合方案用 153 LUT / 16 宽 MUX，较我方此前 167 / 30 更省资源；200 MHz 仍未闭合。对方 Stage 4 的 200.279 MHz 依赖 STA 内存约束改写，已排除，前三阶段保留并独立重测。

新增可泛化槽位搜索、进位级简化/输出物化、有效 I/O 延迟审计与等价 hold 缓冲修复。VexRiscv 已重新综合，但固定引脚/硬资源后端缺口尚未解决。**通用高频优势与硬件验收仍未完成**；不计算失真的几何均值。275 个不同回归测试通过。结果、失败项和复跑命令见[交接比较报告](docs/COOPT_HANDOFF_COMPARISON_20261104.md)，机器状态见[汇总](results/coopt_comparison_20261104/summary.json)。

以下保留各阶段历史结果，当前结果以上段为准；交接 ZIP 仍保持已交付的 183.106 MHz 冻结版本。

普通 VSS/VCC 常量网的原厂导入缺口已补齐：使用四个功能上确实依赖常量的 LUT，完成高、低常量各两源/两端的自主路由、原厂路径读回、配置位与完整 IO STA 回归。该[等价回归案例](results/ownpr_constant_route_probe_001/manifest.json)耗时 72.48 秒，时序仍为 195.017 MHz；它用于验证后端能力，未替换当前最佳实现。特殊 FF/ALU/IO 常量控制仍采用各自的校准规则。

最新已增加锁住其他资源的整网重布线，以及独占 LUT5/6/7 宏的位置移动与增量重布线。三次完整物理搜索均未改善最佳结果；两次宏移动只重布 7 条网、复用 245 条网，均因完整 STA 退化而拒绝。[五个导出的独立对照](results/ownpr_uart_physical_search_summary_001/summary.json)保留 193.805 MHz，未宣称新增搜索已经提频。距离排序忽略了竞争输入路径与离散线段组合，下一步需改进这一点。

自动协同循环已包含固定引脚输出寄存器候选，并在 I/O 打包后把新的时钟校准传给后续逻辑重映射。UART 全部 11 个输出 FF 已进入对应 I/O 单元，该阶段完整报告 **192.463 MHz、setup -0.196 ns / 25 违例、hold +0.037 ns / 零违例**。[混合候选对照](results/ownpr_uart_mixed_summary_001/summary.json)。

宽 LUT 输入分配现已枚举不同专用 MUX 层级。自动三轮闭环保留了中间层分配、布线 ECO 和一次 LUT5 合并，当前 **189.977 MHz、setup -0.264 ns / 26 违例、hold +0.037 ns / 零违例**。固定引脚、电气和接口周期保持不变，200 MHz 尚未达标；下一瓶颈是固定 P16 的 `tx_ready` 输出。[最新对照](results/ownpr_uart_selector_summary_001/summary.json)与[执行记录](docs/OWN_PNR_EXECUTION.md)。

最新已接通 DFFPE（默认 INIT=1）与恒 1 CE 的固定引脚输出 I/O 寄存器。UART 的 `tx` 从核心 FF 移到原 M19 对应的 I/O 单元，网表字节、引脚及接口周期不变；该输出 setup 裕量从 -0.422 ns 改为 **+0.304 ns**。完整设计目前 **185.824 MHz、setup -0.381 ns / 30 违例、hold +0.037 ns / 零违例**；200 MHz 尚未达到。十个 I/O FF 的 210 组时序向量、22992 配置位及完整端点通过原厂最终 STA/编码核验，169 项核心回归通过。[当前结果](results/ownpr_uart_tx_ioreg_summary_001/summary.json)与[详细记录](docs/OWN_PNR_EXECUTION.md)。

2026-11-04 恢复执行后，自动协同循环完成三轮，当前最佳为 **184.437 MHz**、setup -0.422 ns / 31 违例、hold +0.037 ns / 零违例。增量路由复用 246–252 条网络，仅重布 2–8 条；一次 LUT6 合并被接受，整次循环 425.66 秒。固定 IO、电气条件、接口周期和原 200 MHz 约束不变，200 MHz 尚未达标。下一瓶颈为固定 M19 的 `tx` 输出，需扩展 DFFPE 与常量 CE=1 的 I/O FF 支持。交接 ZIP 继续保持原 183.106 MHz 冻结版本，见[当前选择](results/ownpr_uart_joint_resume_summary_002/summary.json)与[执行记录](docs/OWN_PNR_EXECUTION.md)。

最终 STA 和位流生成已支持使用高云原厂工具链，布局和布线仍由本项目完成。新增统一[导出入口](scripts/export_ownpr.py)，在共享超时预算内完成路由核验、配置合并、原厂完整 STA/编码及读回检查；[实测导出](results/ownpr_uart_ce_wide_handoff_002/manifest.json)耗时 42.19 秒。生成位流与时序达标分开记录，当前未做硬件验证。UART 结合次级时钟、两处 CE 宽 LUT 合并、输入 ECO 和宽 LUT 重分解后，当前最佳完整报告为 **183.106 MHz、setup -0.461 ns（33 违例）、hold +0.037 ns（零违例）**，153 LUT、16 MUX、73 FF、42 ALU、24 I/O。22609 个配置位与完整端点核验通过；固定封装引脚、电气条件和原 200 MHz 全 I/O 约束不变。仅替换时钟、部分输入交换及另一处宽 LUT 重分解均出现退化，按完整 STA 拒绝。**200 MHz 和完整通用后端仍未完成。** 274 项全量回归通过，详见[联合优化记录](docs/OWN_PNR_EXECUTION.md)。

最新 I/O 寄存器进展：已接通动态 CE/CLEAR、默认初值 DFFCE 的独占输出打包、自主时钟/数据布线、原厂最终 STA/编码。UART 保持网表字节、24 个封装引脚和接口周期不变，9 个 FF 移入原有输出 IOB；报告 Fmax **171.107 → 179.031 MHz**，原 200 MHz、完整 I/O 下 setup -0.586 ns（44 违例）、hold +0.037 ns（零违例）。22042 个配置位、全部帧 CRC、189 组 I/O FF 时序向量及完整端点核对通过。新[有界入口](scripts/run_ownpr_ioreg_closure.py) 从已有自主网表/布局起，14 阶段 **282.75 秒**复现结果，见[运行清单](results/ownpr_uart_ioreg_closure_001/manifest.json)。**200 MHz、其他 I/O FF 模式和通用后端仍未完成**；当前最坏路径是 CE 链，输出路径仅稍好，详见[执行记录](docs/OWN_PNR_EXECUTION.md)。

最新宽 LUT 进展：已从本机高云 DLL/二进制恢复专用 LUT5/6/7 的几何、时序与配置，并接入自主布线、原厂最终 STA/位流编码；导出没有执行原厂 P&R。UART 的 [LUT6＋LUT7 候选](results/native_ownpr_uart_wide_export_007/handoff_audit.json) 保持固定 24 个 I/O、73 个 FF 和接口周期，报告 Fmax 从 167.336 提升至 **171.107 MHz**；完整 I/O 下 setup -0.844 ns（46 违例）、hold +0.037 ns（零违例）。147 个物理 LUT、10 个专用 MUX、42 个 ALU；328 个观察点直接 CEC、21189 个配置位、全部帧 CRC 及 MUX 内部时序边核对通过。**200 MHz、LUT8/共享打包、通用硬资源与硬件验收仍未完成。** 最坏路径现为 `rx_valid` 到固定 E26 引脚；六个候选及拒绝记录见 [汇总](results/ownpr_uart_wide_summary_001/summary.json)和[执行记录](docs/OWN_PNR_EXECUTION.md)。

最新 **263 项全量回归通过，174.46 秒**，见 [测试日志](results/ownpr_integrated_tests_020.log)。

上一阶段 UART 进展：等价 LUT 扩展与物理快输入重接已接入带全局验收/拒绝的有界闭环；该阶段位置、封装引脚、寄存器和物理 LUT 数量不变。[候选](results/ownpr_uart_pin_closure_002/round_005/export/handoff_audit.json) 在完整 I/O 下 setup -0.976 ns、hold +0.037 ns，报告 Fmax 167.336 MHz；**200 MHz 未通过**。直接对优化前网表 CEC、20132 个配置位、IO/GSR/帧 CRC 核对通过；当时 235 项全量及新增超时检查通过。局部改善却全局退化的候选已拒绝，见 [汇总](results/ownpr_uart_pin_optimization_summary_001.json)及[执行记录](docs/OWN_PNR_EXECUTION.md)。

最新导出修复：补齐专用 GSR 物理绑定，并通过 19 个 I/O 属性读回、27 项可恢复配置差分。正式出口使用新进程单次编码，避免重复编码改变未使用 I/O 的默认位；历史位流仅保留为中间证据。固定引脚的 157 MHz 全 I/O STA 仍通过，当前正式结果为 [export_020](results/native_ownpr_export_020/manifest.json)，[配置核验汇总](results/ownpr_io_verified_summary_001.json)。完整位流硬件验证尚未完成。

最新：自主物理感知 LUT 输入置换已让初始 int2float 在固定 I/O、157 MHz 约束下消除全部 setup/hold 违例；setup +246 ps、hold +5 ps，报告 Fmax 163.309 MHz，CEC 通过。改善来自关键 LUT 输入弧 0.511→0.260 ns，未改变封装引脚或接口周期。完整配置/硬件验收和通用后端仍在推进，见 [最新结果](results/native_ownpr_export_015/handoff_audit.json)。

2026-11-03 最新：已接通“自主打包/布局/布线 → 原厂最终 STA / 位流编码”，导出不调用原厂 P&R。固定 19 个 I/O 的完整 setup/hold 和脉宽报告已核对，自主布线 ECO 消除 4 个 hold 违例；仍有 1 个 setup −5 ps，报告 Fmax 156.881 MHz。已生成 `.fs` 并验证 11816 个配置位和全部 21872 帧 CRC；完整配置语义、硬件验证及通用后端仍未完成。详见 [最新验收](results/native_ownpr_export_014/handoff_audit.json) 和 [执行记录](docs/OWN_PNR_EXECUTION.md)。

2026-11-03：自研专用时钟候选已覆盖现有 18 个 FF，查询得到最大线延迟约 1.259 ns、接收端上升沿跨度约 61 ps；其 62 个编码变化位及 225 个配置约束位与原厂编码器全部吻合。与已有逻辑/数据布线合并的部分配置共 11755 位、0 冲突，167 项回归通过。内部时钟策略变更已单独记录，未重分配引脚。完整 I/O/源模式、GSR/default、setup/hold 和位流仍未验收，不能把该时钟结果当成 Fmax 或完整 P&R 成功，见 [执行记录](docs/OWN_PNR_EXECUTION.md)。

2026-11-02 自研后端方向更正：用户要求自行实现整套 P&R，并明确禁止开源器件数据库。已停止原厂参数扫描，改为从安装 SDK 的 DLL/二进制恢复站点、连接和时序。初版 LUT4/DFFRE 打包、布局及普通线跨 tile 路由已能运行，普通编码核查覆盖 624,102 个选择；器件图/时序查询已能独立初始化。补齐 FF 输入局部延迟后，参考设计 164 条 tNET 记录全部匹配。固定同一布局和引脚，自研延迟驱动路由把最慢数据网线延迟从 3.240 降到 1.545 ns，普通线时钟接收端跨度从 2.638 降到 1.277 ns，约 169 秒完成路由；161 项回归通过。完整 STA、专用时钟和可用位流仍未闭合，不能宣称完成 P&R 或取得频率优势。详细正负结果、初始化告警与验收边界见 [自研 P&R 记录](docs/OWN_PNR_EXECUTION.md)。

2026-11-02 最新研究与实测：已完成 Nextmap/Memo（PLDI 2026）、PigMap3、MappingEvolve 等技术筛选及明确型号的 Artix-7 完整 I/O 参考。新增 `pack_dsp_inverted_enable` 可选规则，把 CPU 的 132 个 FF 移入现有 4 个 DSP；同条件消融 LUT 3310→3133、FF 1526→1394，82 MHz 冷启动到位流 86.641 秒通过，portfolio 80.781 秒通过，125 项测试通过。但原厂 CPU 网表在 85 MHz 通过，新候选在该频点失败，通用优势仍未达成。固定引脚的 Artix-7 参考经 FAST I/O＋1:1 MMCM 后在 180 MHz 完整 STA 通过、200 MHz 失败；用户已确认“A9”指 Artix-7，具体型号/速度等级未提供；本参考不构成跨硅片同条件胜负结论。研究来源、工艺/时钟/I/O 差距、证明和所有负结果见 [研究与执行报告](docs/RESEARCH_138K_EXECUTION_20261102.md)，重验数据见 [结果汇总](results/research_20261102_verified_summary.json)。未执行 Gowin 引脚重分配。

2026-11-02 后续：**评测必须包含 I/O，且禁止重新分配引脚。** 在原引脚、3.3V/8mA 下，int2float 的 LUT4/输出 IOB/LOCAL_CLOCK 配方从 RTL 冷启动到位流用时 30.282 秒，157 MHz 全时序通过；新跑的同 RTL 原厂对照通过 149 MHz、在 157 MHz 失败。200 MHz 和通用显著优势仍未达成。新增 Sipeed UART、TMDS、VexRiscv、LCD 实际代码对照；修复桥接丢失内存属性的问题，CPU 的 BSRAM 从 8 到 10、FF 从 1849 降到 1526。完整条件、资源交换候选和负结果见 [实际工程与完整 I/O 优化](docs/PRACTICAL_138K_OPTIMIZATION.md)。仅内部路径的 248 MHz 已标为诊断，不计入验收。

2026-11-02 更新：公开 FF 配方从原始 RTL 冷启动达到 **77.100 MHz**，高于同条件原厂 **74.565 MHz** 约 3.4%；43 分 13 秒生成位流，setup 184、hold 0、LW 6/8。原始 80 MHz 仍未收敛，LUT/ALU 和耗时也高于原厂。**通用优于原厂的验收目标尚未达成**：新配方十一设计开发批次完成十项，Fmax 比值几何均值仅 0.837054，另保留一项原厂未布通失败。完整正负结果见 [任务与验收记录](TASKS.md)。

本轮算法、论文对应关系、正确性边界和复现命令集中在 [通用综合方法与验收](docs/GENERAL_SYNTHESIS_METHOD.md)。

针对低占用、150 MHz 以上的新目标，见 [高频诊断与验收路线](docs/LOW_UTILIZATION_HIGH_FREQUENCY.md)。新核对发现，当前开发集自主结果的十项中有八项最差报告路径由 FF→输出缓冲主导；0.837054 描述原封装整体时序，不能直接当作内部核心综合能力的比值。

布线优化的[任务计划已执行](docs/ROUTING_OPTIMIZATION_EXECUTION.md)：固定相同引脚，仅输出 IOB 打包使 ctrl/int2float/router 的自主报告 Fmax 分别提高约 19%/13%/21%，原 100 MHz setup/hold 均通过；再加入 LUT4 候选后，这三项对同设置原厂的几何均值为 1.008392，仍未达到通用高频优势。公开配置新增显式 `output_iob_registers`；约 33 秒冷启动位流与约 63 秒候选入口均已验证。[详细成因与正负结果](docs/ROUTING_DELAY_FINDINGS.md)同时保留 160 MHz 未闭合及全部 IOB 模式的六次原厂 DLL 崩溃。

已测冷启动入口：`python scripts/run_frozen_flow.py --config configs/fpgafly_rtl_fanout_v1.json --out results/new_fanout_run --total-timeout 3600`。串行候选入口另为 `scripts/nextgowin_portfolio.py`，所有候选复用自主资源推断，保留实际 JSON CEC 和原始 SDC/CST，以原生时序和资源审计选取。整个尝试共用一小时墙钟预算；组合入口可运行，不代表已证明通用性能优势。

项目已建立高云前端 → 开放 CPU 综合/映射 → 高云 P&R/位流的可运行混合流程；独立全栈工具链仍在开发。

当前按用户选择，以单核 CPU 作为自研映射和时序评估的默认路径，优先推进映射质量与布线后时序；GPU 和多核保留为可选实验。

完整流程入口、逐阶段文件、GAO 边界和复现命令见 [混合综合流程](docs/HYBRID_FLOW.md)。带 `.rao` 的非平凡示例已生成新位流并通过原生时序检查；优化前的 FPGAFly 全 RTL 路径（`fpgafly_cpu_flow_003`）在 P&R 一小时超时后判定失败。完整工程尚未通过原始 80 MHz 时序验收；当前每个完整尝试共用 3600 秒墙钟预算。

DSP、分布式 RAM、寄存器分支和专用 mux 优化现已接入；FPGAFly 已使用 139 个 DSP 原语并生成新位流。基本优化的阶段合计为 64 分 2 秒，ui_clk 为 64.317 MHz；比较链软化对照降到 44 分 26 秒，但 Fmax 退化为 60.691 MHz，因此保持独立开关默认关闭。两轮均未满足 80 MHz 时序目标。实现范围、测试和运行记录见 [硬资源优化](docs/RESOURCE_OPTIMIZATIONS.md)。

最新[124 项自动测试](results/practical_138k_tests_001.log)通过，新增内存属性恢复、短 ALU 链 CEC/反例和三种 ROM 寄存器位置的 SDK 验证。公开复制器默认关闭，配置显式启用；实际发射 JSON 另做状态折回和硬边界 CEC。联合小工程、RTL 冷启动、resume 与组合入口均有成功记录。DSP OREG 和整簇复制的整板对照分别退化到 68.846 与 60.716 MHz，未设为优胜配方；LUT 内部引脚真值重绑定实验也尚无通用收益证据，它不同于本轮明确禁止的封装引脚重分配。

- 固定版本 ABC 的完整 ACD 映射复现：19 个 EPFL 基准、76 组配置、380 次运行有等价证明覆盖；`hyp` 预处理仍未证明。
- 提取 Gowin 138K 原厂时序数据库，并核对运行时缩放；组合 mapper 已支持显式输入到达时间与端点要求。
- GPU 预计算已接入真实 ACD 搜索，adder 与 log2 保持相同映射结果；对照实验尚未证明端到端加速。
- GPU 固定图正反时序传播在 8 组真实图/批量配置上逐元素核对通过；深窄图和含全量拷贝的运行常退化，详见 [GPU 时序实验](docs/GPU_TIMING.md)。
- 独立实现 FAPO 的核心搜索与消融；扇出模型改善仍可能导致实际 Fmax 退化，物理收益需逐项验证。
- 将自定义 LUT4 网表直接送入高云 P&R；一个寄存器边界实验在相同约束下，两组网表均通过 setup/hold 检查。该结果不代表真实工程已完成验收。
- FPGAFly 的 42 模块候选通过切口等价与结构保留检查；首组相同约束物理对照中，ui_clk 报告 Fmax 从 72.125 改善为 79.520 MHz，仍未达到 80 MHz。单次样本与后续实验见 [接入记录](docs/FPGAFly_REMAP.md)。
- 后续 3 个 LUT 复制候选功能等价，但配对 P&R 从 77.823 退化到 75.389 MHz，未启用；详见 [物理反馈记录](docs/PHYSICAL_FEEDBACK.md)。

任务与完成标准见 [TASKS.md](TASKS.md)，构建、命令、数据和限制见 [复现记录](docs/REPRODUCTION.md) 与 [时序模块拆分记录](docs/GOWIN_TIMING.md)。当前实验环境为 Windows、MSVC、CUDA 12.1 及文档中固定的高云安装版本。

## 开发方向

- 明确 138K 目标器件的资源与约束模型。
- 建立综合、技术映射与布局布线的基础流程。
- 引入时序分析与时序驱动优化。
- 使用测试设计验证正确性，并评估资源占用、运行时间和时序表现。

## License

许可证尚未确定，将在正式发布前补充。

---

本项目为独立项目，并非高云官方产品，也不代表高云官方立场。
