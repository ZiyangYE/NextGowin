# 四工程 1.20x 联合优化架构咨询报告

## 一、统一协同主路径与现状缺口（问题 1）

- **观察**：当前实现割裂为“粗糙冷启动 + 冻结边界热点微调”。冷启动阶段，`ownpr_place.py` 按每 CLS 单寄存器保守打包（VexRiscv 额外插入 427 个 `ng_ownpr_pass_*` LUT1），仅以组合层深加权松弛布局；`ownpr_route_design.bind` 给全部非时钟信号统一赋 `criticality=0.5`。后验阶段，`ownpr_joint_lut_search.py` 与 `ownpr_wide_mapping.py` 虽具备完整双角图反馈，但 `WideMappingFeedback.admit` 硬性冻结所有 FF、硬核、时钟树与既有宽宏；`vexriscv_wide_mapping_search_002` 净增 115 个 LUT 仅使模型 WNS 由热启动种子 -2.075 ns（源初值 -2.195 ns）改至 -1.973 ns，仍余 341 个违例端点，上轮原厂报告 Fmax 停留于 89.273~89.558 MHz。四工程瓶颈结构迥异：VexRiscv 约 75.1% 数据延迟在互连且受 `execute_to_memory_IS_DIV_s0` 等扇出 172~298 控制网牵制；UART 最差路径 5.076 ns 全在时钟线与输出缓冲（普通数据线为零）；int2float 受限于 3.405 ns 簇间数据互连；TMDS 受多段算术/控制锥与竞争输入 hold 夹击。
- **依据**：`AGENTS.md`、`docs/VEXRISCV_VENDOR_COMPARISON_20261105.md`、`docs/COOPT_HANDOFF_COMPARISON_20261104.md`、`results/vexriscv_wide_mapping_search_002/manifest.json`、`scripts/ownpr_place.py`、`scripts/ownpr_route_design.py`、`scripts/ownpr_wide_mapping_feedback.py`。
- **建议**：选定**“双角时序预算驱动的迭代重打包—全局解析布局—汇点分级协商路由（SB-CPPR）”**作为唯一通用主路径，严禁工程名分支、特殊 RTL 补丁与无约束夸张收益假设。同一流程执行 2~3 轮外层协同闭环：（1）综合与重打包层联合决策 LUT1–7 切口、算术进位链、同控制集双 FF CLS 及核心/IOB 竞争寄存器，消去已校准直连路径上的非必要直通 `LUT1`；（2）全局解析布局层用双角边裕量权重替代组合层深，联合合法化硬核、进位链、宽宏与寄存器簇；（3）全局路由层在 `ownpr_router.route` 每轮协商中按实测整树延迟动态更新逐汇点 `sink_criticalities`，全局收敛后再执行轻量宽切口收尾。
- **不确定性与推测**：推测固定寄存器、硬核与时钟边界的局部切口重映射已受次最差路径群钳制，无法令四工程同时越过官方 1.20 倍门槛；但 VexRiscv（6607 网）执行多轮外层重布局与全网协商路由的耗时未经实测，能否稳妥收敛于 3600 秒单次尝试硬限内仍具不确定性。

## 二、全边界与空间区域的候选、代价及合法性（问题 2）

- **观察**：`ownpr_wide_mapping.proposals` 仅截取 `wns+0.65 ns` 内前 40 个普通 LUT 根并要求放入完全空置的孤立 Type-17 CFU；`ownpr_path_cone.critical_segments` 仅提取固定原语间的普通 LUT 段，完全遗漏 FF `CE/RESET/CLEAR/PRESET`、BRAM 地址/使能、DSP 数据锥及 I/O 边界；`WideMappingFeedback` 亦禁止改动既有宽宏或混排槽位。
- **依据**：`scripts/ownpr_wide_mapping.py`（第 16、70–72 行）、`scripts/ownpr_path_cone.py`（第 18–46 行）、`docs/SHARED_DAG_AND_FMAX_POLICY_20261112.md`（宽叶输入旁路隔离未布通记录）。
- **建议**：（1）**全边界空间分层前沿**：将双角图全部汇点按 `FF_D`、`FF_CE`、`FF_SR_ASYNC`、`HARD_PORT`、`IO_OUT`、`IO_IN_HOLD` 六类边界与 `4x6` 芯片空间扇区交叉分桶，按桶内 TNS、违例密度与扇出负载分配保底候选配额，避免少数最差路径垄断搜索。（2）**多边界平滑代价**：用全体终点 `SmoothMax(setup_slack)` 加逐端点 hold 罚项、包围盒拥塞增量及 CLS 面积增量替代单根乐观到达差；对高扇出 `CE/SR`/停顿网采用接收端空间聚类分区与源支路负载联合计价，不按固定扇出阈值盲目复制。（3）**统一合法性契约**：路由前统一核验 CLS 四组控制集相容性、硬核 3 列独占、进位 6 槽连续、宽宏槽树混排校准集及 IOB 独占规则。
- **不确定性与推测**：推测覆盖控制集与硬核边界可同步压降 WNS 与 TNS；但在高密扇区并发提交多笔跨边界事务时，非局部布线拥塞串扰幅度仍待实测标定。

## 三、优先校准项与原生时序查询验证（问题 3）

- **观察**：当前 `model.json` 将发射时钟延迟与 `C2Q` 固化入 `seeds`，将捕获时钟延迟与 `tSu/tHld` 固化入 `setup_requirements/hold_requirements`；`ownpr_shared_ff_packing.py` 仅认证 Type-17 `DFFRE/INIT=0/RESET=0` 成对共享；`ownpr_ioreg_macros.py` 仅支持底/右边输出单向迁入；四个 `MULTALU27X18` 均为 `BYPASS` 而未用 `OREG_CLK="CLK0"`。同时，`NativeDeviceSession`、`ExtendedTimingModel`、`HardCellTiming` 与 `ClockGraph.retarget` 已具备节点线延、异步恢复/移除、硬核矩阵及时钟重定锚基础。
- **依据**：`scripts/ownpr_bidir_timing.py`、`scripts/ownpr_shared_ff_packing.py`、`scripts/ownpr_ioreg_macros.py`、`scripts/native_device_session.py`、`scripts/native_extended_timing.py`、`scripts/ownpr_hard_timing_model.py`、`scripts/ownpr_clock_graph.py`。
- **建议**：按优先级先补三项校准并用现有原生查询验证：（1）**时钟树与时序本征弧解耦（最优先）**：移动 FF/IOREG/硬核后，调用 `ClockGraph.retarget` 更新时钟汇点，在隔离进程以 `resolved_parent_tree` 配合 `delay_pointer`（RVA `0x14733b0`，selector 1/2）读取早/晚时钟延迟，结合 `NativeCellTiming` 第 2 组行表、`ffquery`（RVA `0x1446fc0`）与 `HardCellTiming.matrices` 动态合成 `seeds/requirements`，由 `compare_bidir_native_endpoints.py` 验证全端点误差 `<0.002 ns`。（2）**直入 DI 与通用控制集校准**：用 `isDirectConn/isViaLutbuf` 校准不经伴随 `LUT1` 的 FF `DI` 直连，补齐 `DFFRE/SE/CE/PE` 同 `(CLK,CE,SR)` 双槽编码回读。（3）**双向 IOREG 与硬核重绑**：打通四边核心 FF↔IOB FF 双向竞争移动（直接破除 UART 瓶颈），接通 `HardArchitecture.legalize` 选址后的独立原生端口捕获，并在综合阶段校准等价输出寄存器吸收进 DSP `OREG`。
- **不确定性与推测**：推测消去 427 个直通 `LUT1` 并释放核心/IOB 寄存器与 DSP 输出寄存器可显著缩短关键互连；但 `ClockGraph.retarget` 新增分支的熔丝编码差分、容量上限及顶/左边 IOREG 编码尚待实测验证。

## 四、公平基线审计、min-ratio 规则、接口与首轮实验（问题 4）

- **观察**：`results/four_design_joint_policy_20261112.json` 中 `official_baselines` 仍为 `null`；历史原厂数字存在 hold 违例（VexRiscv `-0.754 ns`）、物理 I/O 数缺失（282 对 284）或 `LOCAL_CLOCK` 策略差异。新口径允许 110 MHz 完整 STA 在 hold 合格后直接取原生 `reported_fmax_mhz`。
- **依据**：`docs/FOUR_DESIGN_JOINT_OBJECTIVE_20261112.md`、`results/four_design_joint_policy_20261112.json`、`results/frequency_reporting_policy_20261112.json`、`scripts/finalize_joint_lut_trial.py`、`scripts/native_project_synthesis.py`。
- **建议**：
  1. **公平基线审计与联合接受**：四工程统一锁定同源 RTL SHA256（保留未使用输入缓冲以对齐物理 I/O 数）、`C1/I0` 器件库、固定 `.cst`、同 `AUTO` 时钟许可、`0 ns` I/O 预算与 `0.050 ns` hold uncertainty，并对齐综合目标与 110 MHz STA 报告口径；原厂 `native_project_synthesis.py` + 原厂 P&R 须在 `hold_wns >= 0`、零 hold/异步违例与全 I/O 覆盖下冻结基准 `B_i`。主指标 `S = min_i(F_own,i / B_i)`；同一无分支流程（超参仅依赖图规模、扇出、硬核/进位密度与裕量分布）须四工程全过 CEC、连通、配置与 110 MHz hold 门控，迭代要求 `S` 不降且短板提升，终局要求 `S >= 1.20`，并单列披露 raw 与 fallback，禁用几何均值掩盖单项失败。
  2. **可实施接口**：
     - `SequentialClockFeedback.rebuild_boundaries(design, layout, clock_tree, native) -> BoundaryModel`：动态解耦时钟树早/晚到达与 FF/IOREG/硬核本征弧，输出 `seeds` 与 `setup/hold_requirements`。
     - `StratifiedFrontier.propose(design, layout, graphs, metrics, sectors=(4,6), budget=48) -> list[JointTx]`：跨六类端点与空间扇区生成重打包、状态/硬核移位与宽映射事务。
     - `JointMinRatioGate.evaluate_suite(flow_cfg, baseline_json, out_dir, limit_s=3600) -> SuiteReceipt`：先调 `workspace_budget.reserve` 预留空间，跑通四工程并核算 `min_ratio`。
  3. **首个低成本实验（<500 秒，<200 MB 预留）**：第一步在隔离新目录完成四工程同约束、hold 合格的原厂 SYN+PR 基线审计，固化 `B_i` 并定位真实最低比值工程；第二步在 UART 与 VexRiscv 小样本上移动 4~8 个核心/IOB FF、执行 `ClockGraph.retarget` 并消去对应直通 `LUT1`，用 `compare_bidir_native_endpoints.py` 验证 `SequentialClockFeedback` 与原厂 110 MHz STA 全端点误差 `<0.002 ns`。
- **不确定性与推测**：推测若公平原厂基线修复 hold 后 UART 高于 164.2 MHz，UART（现 197.028 MHz）将与 TMDS、VexRiscv 共同成为 `1.20x` 硬瓶颈；原厂在零外部延迟合同下使输入 hold 闭合的公平标准约束方式亦待基线审计确认。
