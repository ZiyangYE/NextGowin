# 结构搜索泛化守卫（UART / int2float / TMDS）审查报告

## 一、泛化门禁是否会掩盖失败

- **【观察】** `results/vexriscv_110_evaluation_policy_001.json` 与 `results/joint_lut_structural_guards_001` 显示，UART（197.0 MHz）、int2float（167.5 MHz）、TMDS（112.3 MHz）三项基线均为零违例、`TNS = 0.0`，setup WNS 为 `+0.001 / +0.004 / +0.005 ns`，hold WNS 为 `0.037 / 0.002 / 0.048 ns`。目前 `uart_model/manifest.json` 已完成双向图验证（最大误差 `0.00015 ns`），`int2float_arrival` 已建立到达图（无 ALU 查询、含 MUX 查询），而 `ownpr_joint_lut_search.py` 与 `evaluate_joint_lut_graph.py` 仍硬编码调用 `evaluate_fixed_clock_rewire.evaluate()`、无条件读取 `hard_endpoint_audit.json` 并以 `min(slacks) < -0.25` 选根。
- **【推断】** 改用相对全局最差 slack 的近关键窗口选根、按实际原语豁免硬核/ALU/MUX 查询并解除旧 alias 前置条件，能够消除“正 slack 基线零候选空跑”的直接假象；但**若不收紧配套门禁，仍会在三处掩盖失败**：一是 `TNS = 0` 使启动时的基线图复现校验对非最差端点完全失明；二是全局 `min(0.05, 当前 hold WNS)` 在 `int2float` 上把全芯片受改路径的 hold 底线放宽至噪声级 `2 ps`；三是试验异常被吞并叠加 `TNS = 0` 下的 `+10 ps` WNS 门槛，会把“候选全部报错崩溃”或“零提交未走导出与原厂 STA”混同为守卫通过。

## 二、最高优先级漏洞（区分观察与推断）

### 1. 【P0】`TNS = 0` 使搜索启动的基线复现门禁漏检非最差路径误差
- **【观察】** `ownpr_joint_lut_search.py:138-143` 仅在 `wns_error_ns > 1e-5` 或 `|actual.tns - expected.tns| > 0.002` 时报错；`edge_differences` 仅截断前 30 条写入 `baseline_diagnostic.json` 而不阻断。上轮 VexRiscv 配对数据线重复计入 `0.02925 ns` 时 WNS 完全不变，全靠负值 TNS 检出（`docs/JOINT_LUT_GRAPH_AND_BATCH_20261111.md:40`）。
- **【推断】** 三项守卫基线 `expected.tns == 0.0`。若 `rebuild()` 在非最差路径（如 TMDS `wide_ff_root` 的 `OF→DI` 实测线、`OBUF`、或 `int2float` 中 `slice$11012/RESET` 控制脚）存在建模偏差但未把正 slack 压穿 `-0.002 ns`，`actual.tns` 仍保持 `0.0`，失真图将静默通过启动检查进入搜索。

### 2. 【P0】全局 Hold 门槛 `min(0.05, 当前 hold WNS)` 撤除 `int2float` 改图保护
- **【观察】** `int2float_states/pin_feedback.json` 中全局最差 hold 位于远离关键锥的寄存器 `slice$6592/D`（`hold = +0.001975 ns`，`setup = +5.792 ns`），而关键 setup 锥 `slice$11009..11015/D` 的 hold 余量达 `+0.63 ~ +1.22 ns`。同时 `build_native_bidir_model.py:124` 模型准入容差为 `0.002 ns`，原厂 `.tr` 报告分辨率为 `0.001 ns`。
- **【推断】** 若将全局门槛降为 `min(0.05, 当前 hold WNS)`，在 `int2float` 上等于允许新算法把被改写逻辑锥的 hold 从 `>0.6 ns` 压低至 `+0.002 ns`，直接落入模型误差与报告舍入带，导出原厂 STA 极易触发真实 hold 违例（政策第 152 行规定降频至 95% 绝不掩盖 hold 失败）；多轮串行搜索若引用动态 `current` 而非冻结初始值，还会引发轮间阈值漂移。应改为**逐端点守卫**：冻结初始 hold `>= 0.05 ns` 的端点仍须 `>= 0.05 ns`；初始 `< 0.05 ns` 的端点须相对初始值零退化（若处于改写扇出锥内须额外保留模型误差安全余量），且全局零违例。

### 3. 【P0】异常吞并与 `admissible()` 分支退化可造成“静默零提交假通过”
- **【观察】** （1）`ownpr_alias_feedback.py:55` 中 `a.tns > b.tns + 0.1` 在 `b.tns == 0` 时恒假，候选必须使全局 WNS 净增 `> +0.01 ns`；而 `int2float_arrival/manifest.json:39-92` 前 5 条关键路径均受固定 `OBUF` 支配，距首条内部 FF 路径相差 `0.212 ns`，若相对窗口过窄（建议统一采用覆盖约 `0.5 ~ 1.0 ns` 并保留 `[:64]` 排序上限的纯 slack 策略，以兼容 `fairness.parameters`）易无解。（2）`ownpr_joint_lut_search.py:22-25,183-199` 中，5 输入分解根 LUT 与 `<4` 输入补零均强依赖既有物理 `GND` 单元；`ownpr_lut_graph_feedback.py:23,62` 未像 `ownpr_arrival_graph.py:155` 那样判别 `r.get('lut')`（而后者又漏排 `wide_ff_root`），遇纯 FF 簇或同槽独立 `DI` 寄存器即抛错；但 `worker()` 捕获所有 `ValueError/TimeoutError` 后仍返回 `0` 并跳过 CEC、导出、`evaluate_joint_lut_graph.py` 与原厂 STA。
- **【推断】** 若不强约束“有效测时试验数 > 0”与“非预期结构/模型异常 = 0”，100% 试验崩溃会被回退机制掩盖；若三工程在公共策略下均零成交，类似 VexRiscv `search_009` 在原厂导入阶段才暴露的 `PA2126` 悬空常量缺陷也无法在异构工程上被检出。

### 4. 【P1】解除 `evaluate()` 与原语豁免时的跨目录哈希及集合失绑
- **【观察】** `build_native_bidir_model.py` 不校验 `routes` 与 `wire_delays.json`，而 UART/TMDS 的 `placement/routes/wire/sta` 分处不同历史目录，且搜索入口未调用 `checked_wire()`；`int2float_reference.json` 的 `selected_source_sha256` 漏列 `fixed.cst` 与 `clock.sdc`；硬核与 IOREG 仅查 `layout` 未查 `packed.json` 的 `cells`，ALU/MUX 仅判非空未核对实例集合相等，`model/manifest.json` 漏记 `name_map / sta_endpoint_audit / extended / mux / ioreg / hard` 哈希，且未强制 `unconstrained_io_arcs == []`。
- **【推断】** 移除 `evaluate()` 后若不补齐上述闭环校验，错配线延迟、未约束输出弧或残缺原语查询将直接混入搜索与重建模型。

## 三、模型变换绑定与实际 Native 对照要求

1. **输入与原语强绑定**：`model/manifest.json` 须完整锁定 `packed`、`placement`、`cst`、`sdc`、`sta/manifest`、`handoff_audit`、`name_map`、`sta_endpoint_audit`、`pin_feedback`、`arrival/graph` 及实际存在的原语查询文件 SHA256，并断言 `unconstrained_io_arcs == []`。搜索与独立重建须执行 `checked_wire(wire, routes)` 并核对 `sta` 路由/时钟树哈希一致。硬核豁免须同时满足 `cells` 无硬核原语且 `hard_clusters` 为空；ALU/MUX/IOREG 豁免须对应 `cells` 集合为空，否则查询实例集须与网表集合精确全等。
2. **启动与终态 Native 对照**：启动时基线 `rebuild()` 须与 `model.json` 满足 `edge_differences == []` 且**全部**节点早/晚到达时间与 slack 偏差 `< 1e-5 ns`。终态成交候选须通过全状态边界 CEC、`evaluate_joint_lut_graph.py` 整树重建、原厂完整 I/O STA（先跑 100% 基线频率；仅当 setup 失败且 hold 零违例时改主时钟周期复测 95% 频率），并用 `compare_bidir_native_endpoints.py --rebuilt-model` 逐端点核对原厂 setup/hold slack（最大误差 `< 0.002 ns`）。若正式运行在公共策略下零成交，须如实标记“原始无改动”，并在预留预算前提下隔离对最佳合法非退化测时候选做一次诊断性导出与原厂逐端点对照（不作正式交付），以实证导出/STA 链路畅通。

## 四、完成判据

1. **模型准入**：三工程原生状态对与早晚图逐 pin 最大误差 `< 0.002 ns`、`unconstrained_io_arcs == []`，`int2float` 补齐 `cst/sdc` 哈希链。
2. **搜索健康度**：在统一无工程名特判的策略下，三工程近关键根数与完成增量布线/整树测时的有效试验数均 `> 0`，非预期模型/结构异常（缺 `GND`、簇/配对断言崩溃、基线边差异）严格为 `0`。
3. **签核与报告**：逐端点 hold 门禁生效且原厂完整 I/O STA **零 hold 违例**；分别披露原始结果与回退结果，原始实现通过 95% 实际 STA 守卫（`187.15 / 159.125 / 106.685 MHz`），固定引脚、电气标准、接口周期与 CEC 不变，单次完整尝试满足 3500 秒与 40 GB 容量限额。
