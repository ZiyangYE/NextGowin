# 固定边界联合 LUT 拓扑重映射与增量路由独立审阅报告（2026-11-11）

**审阅范围**：仅静态审阅 `scripts/ownpr_lut_graph_feedback.py`、`ownpr_joint_lut_search.py`、`ownpr_arrival_graph.py`、`build_native_bidir_model.py`、`ownpr_route_design.py`、`ownpr_multilevel_cut.py`、`tests/test_lut_graph_feedback.py`、`docs/ROUTED_SUBTREE_AND_ARCHITECTURAL_BOUND_20261111.md` 及直接关联辅助模块与校准数据，未执行任何命令或脚本。

## 一、新类与搜索实现正确性严审

- **已闭合机制**：
  - **【观察】** `implement()`（`ownpr_joint_lut_search.py:8-34`）调用 `Cuts.function()` 复核切集证书，将最晚信号 `late` 置于 `boundary[4]`，按 Shannon 展开为两个 `LUT4` 叶与根 `LUT3(INIT=0xCA)`（`I2=0` 选 `I0`、`I2=1` 选 `I1`），布尔代数正确。`FixedBoundaryLutFeedback.rebuild()`（`ownpr_lut_graph_feedback.py:77-96`）丢弃参考图进入普通 `LUT1..4` 的旧边，复用 `build_native_bidir_model.py` 冻结的边界 `seeds`/`requirements` 与非 LUT 单元弧（含 `ownpr_arrival_graph.py` 的 `io_observed_mode` 弧），按整树测量重建普通 LUT 的 setup/hold 连线弧与四象限非单调单元弧，并刷新未改非 LUT 兄弟接收端的负载树延迟；`worker()` 启动即校核基线 WNS/TNS 复现误差（`<=1e-5 / 2e-3 ns`），终态执行 `native_boundary_cec.prove()` 与路由连通性校验。
- **关键缺陷与漏洞**：
  1. **【观察】跨检查点复用阻断**：`worker()`（`L98`）调用 `evaluate_fixed_clock_rewire.evaluate()`，而后者（`L24-26`）硬编码要求 `placement/manifest.json` 含 `joint_existing_alias_assignment_proved`，导致新算子无法直接在原始基线或 UART/int2float/TMDS 检查点上启动。
  2. **【观察】`admit()` 漏检同槽冲突与配对数据链篡改**：`admit()`（`L30-45`）未校验两个独立 LUT 占用同一 `(row, column, slot)`，亦未校验坐标越界与 tile 类型；若两同槽 LUT 共享同一输入网，`rebuild()` 的 `owners`（`L60-63`）因网络名相同不会报错。同时 `admit()` 仅比对带 `ff` 的 cluster 字典（不含网表 bit），未显式断言配对 `LUT/F` 仍驱动对应 `FF/D`。
  3. **【观察】`validate_placement` 单点检查被空参架空**：`worker()`（`L138`）向 `ownpr_critical_chain_move.apply()` 传入 `dict(sites={})`，使其 `L130-135` 的 `exclusive_tiles` 与 `cal['tile_types']` 循环零次执行，且 `implement()` 未断言带 `ff` 的 `root` 严禁移位。
  4. **【观察】自废最快物理引脚 `I3`**：核查 `native_cell_library.json`，槽 `0..5` 慢角延迟极不对称：`I0=0.521ns`、`I1=0.511ns`、`I2=0.457ns`、**`I3=0.260ns`**。`implement()` 将 `width=1..3` 及 5 输入根映射为 `LUT1..3`，使 `late` 落在慢脚 `I0..I2`（根 `I2` 比 `I3` 慢 `0.197ns`），且叶 `LUT4` 的 `boundary[:4]` 仅按 bit 号而非到达时间排序，次晚信号常落入 `I0/I1`，每级白损 `0.20~0.26ns`。
  5. **【观察】切集截断偏置、悬空死 LUT 与单测缺口**：`ownpr_multilevel_cut.py:61-62` 按 `(len(c), sorted(c))[:limit]` 截断，`limit=48` 时浅层小切集与跨越 `MUX2_LUT5/6` 私有边的废切集会挤占深层 5 输入关键切集配额；`implement()` 不清理零负载旧内部 LUT，白占槽位并污染面积评分；`tests/test_lut_graph_feedback.py` 仅 2 个窄用例，未测 5 输入分解、同槽同网冲突、配对 FF 篡改及 hold 拦截。

## 二、必须额外拦截的专用链路、静态常数与共享资源

1. **专用物理链路**：
   - **【观察】** `proposals()` 虽过滤 `MUX2_LUT*` 的 `I0/I1` 私有边，但 `admit()` 未拦截对 `wide_macros` 叶 LUT 的降宽（如 `LUT4->LUT3`）或改接；且 `ALU/COUT`、`ioreg/Q` 及 `wide_ff_root`（`OF_s->DI_s`）未列入 `admit()` 与 `implement()` 的显式黑名单。建议在 `admit()` 硬性断言：配对 `FF/D == LUT/F`、`MUX2_LUT*` 私有叶不变、禁止普通 LUT 采样 `ALU/COUT` 或 `ioreg/Q`。
2. **静态常数与保留信号**：
   - **【观察】** `admit()` 仅豁免 `lut_truth_independent_constant_input`，但若新 LUT 引入真值表**依赖**的 `GND/VCC` 引脚，`ownpr_route_design.py:117-129` 会改写全芯片多源常数网 `constant_0/1` 的 `source_sites`，而 `rebuild()`（`L90`）因 `bit in constants` 跳过该脚连线测量校验；另 `LUT1(INIT='01')` 在 `ownpr_native_netlist.py:63-76` 会被隐式改写为接 `GND` 的 `LUT4`，且 `keep/syn_keep` 网络在 `ownpr_state_safety.py:13` 被豁免保护但终态 CEC 严格核验。建议禁止新增真值表依赖常数脚，并在清理死 LUT 时保护驱动 `keep/syn_keep` 网络的单元。
3. **共享 CFU 资源**：
   - **【观察】** `admit()` 未核查 `mixed_carry_lut_calibration`（仅槽 `6,7` 可放无 FF 普通 LUT）、`mixed_wide_lut_calibration` / `wide_ff_ordinary_sharing`（受限槽位与准入哈希）及 `shared_ff_calibration` 奇偶槽约束；且 5 输入两叶同占一 tile 会令 `boundary[:4]` 在同一 CFU 形成 8 脚同源局部拥塞。

## 三、模型是否足以准入新增组合 LUT

- **【观察】搜索内环探索准入基本具备基础**：在固定状态/时钟/IO/硬核与单时钟零 IO 延迟契约下，`NativeCellTiming` 已覆盖全部 `4 脚×2 槽组×3 selector` 原生弧并保持非单调传播，新增连线经 `expand(..., native.fanouts)` 有向排他展开并在 `parent_tree` 下重测整树负载，足以支撑有界搜索排序。
- **【推断】尚不足以替代签核，且缺改图实测闭环**：① 前序 `<0.0007ns` 精度仅在固定原语重连上验证，尚未对含新增 `LUT4+LUT3` 的候选做原厂 2,798 端点全量比对；② `measure()`（`L118-119`）仅装入**单网** `parents` 查询，而 `measure_ownpr_wire_delays.py:57-60` 装入**全芯片** `parents`，需实测排除跨网上下文差异；③ 终点 `requirements` 为固定标量，跨源重组逻辑锥切换最差激励 FF 时无法重算源-宿对动态 CPPR 偏斜残差，须保留 `hold>=0.05ns` 裕量并以原厂完整 STA 签核。

## 四、试点可能根本瓶颈与可证伪下一步

- **可能根本瓶颈**：
  1. **【观察】+【推断】软拼 5 输入本征延迟偏高**：`LUT4 -> 普通路由 -> LUT3(I0/I1)` 两级单元弧即需 `0.78~1.03ns`，叠加局部布线常达 `1.1~1.5ns`（高于 `proposals()` L54 的 `1.1ns` 乐观估计，亦劣于高云 0ns 级联 `MUX2_LUT5`），且令 `boundary[:4]` 每网净增 2 个物理 sink 推高扇出负载。
  2. **【观察】+【推断】受困于固定宏/状态分段墙**：VexRiscv 在 110 MHz 下仍差 `2.252ns`（370 个违例端点），关键路径横跨宽 MUX、ALU 前后及 BRAM 地址长互连（`0.8~1.45ns/跳`，BRAM 线延时 `8.835ns`）；仅在被 ALU/FF/BRAM 切碎的局部普通 LUT 段重映射且锁死配对尾级 LUT 位置，难以消去跨宏长线。
  3. **【观察】+【推断】单锥串行贪心屏障**：面对 370 条近并列关键路径，每轮仅提交单根且要求单步改善全局 WNS/TNS（`rounds<=3` 最多改 3 锥），加之增量路由失败未接入 `mutable_fallback` 协商，极易停滞。
- **可证伪下一步建议（单次完整预算 `<=3,500s`）**：
  1. **补齐防线与快脚规范化**：解耦 `evaluate()` 的 alias 证明前置依赖；补全 `admit()` 专用链/同槽/混合 CFU/常数脚拦截并修复 `validate_placement` 传参；在 `Cuts` 中优先保留高时序收益 5 输入切集，将叶输入按到达升序绑定，并用真值表无关 `GND` 将 `LUT1..3` 抬升为 `LUT4` 使最晚信号走 `I3(0.260ns)`；补齐上述负例单测。
  2. **改图模型准入证伪门禁**：取首个改图成交候选对比单网与全网 `parent_tree` 测量及原厂全端点 STA。**判据 A**：若端点最大偏差 `>0.05ns` 或单网查询失配，立即停机修模型，不推进多轮搜索。
  3. **批量事务消融与架构升级判据**：支持无冲突多锥批量提交与 `mutable_fallback` 协商路由（达 `+0.15ns` 里程碑时同步在 UART/int2float/TMDS 跑真实新算子守卫）。**判据 B**：若原厂 110 MHz 完整 STA 实测 `ΔWNS < +0.50ns`，即证伪“固定非 LUT 单元仅靠普通 `LUT1..4` 局部重映射可逼近 110 MHz”，下一步须转向**准入硬件 `MUX2_LUT5/6` 生成并与局部关键 FF/ALU/宽 MUX 协同移位（配受控时钟重捕获）**。
