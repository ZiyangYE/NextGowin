# 算术链保留、扩展单 FF 锚定与跨映射增量实现独立审计报告（#29）

## 一、119.034 MHz 谱系复审与 `checkpoint_008` 实测崩塌归因

1. **【实测事实】源/目标模式复审与精确 PIP 计数**：
   - [`source_mode_reaudit_001/manifest.json`](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/results/architecture_arithmetic_joint_trial_006/source_mode_reaudit_001/manifest.json) 验证 `existing_wide_batch_search_002`（`2626` PIPs / `205` nets）$\to$ `joint_macro_checkpoint_010/route`（`2633` PIPs，复用 `151`、重布 `54`，`wide_ff` `[101,167,OF0,DI0]` 迁至 `[103,167,OF0,DI0]`）通过；
   - [`source_mode_reaudit_002/manifest.json`](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/results/architecture_arithmetic_joint_trial_006/source_mode_reaudit_002/manifest.json) 验证 `joint_macro_checkpoint_010/route`（`2633` PIPs）$\to$ `post_state_trust_search_012` 实为 **`2637` PIPs**（非提示词所述 `2640`；复用 `197`、重布 `8`）通过，TMDS 维持 `119.034 MHz`（Hold `+0.048 ns`）。
2. **【实测事实】`arithmetic_mixed_checkpoint_008` 已完成且显著退化**：
   - 据 [`arithmetic_mixed_checkpoint_008/manifest.json`](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/results/architecture_arithmetic_joint_trial_006/arithmetic_mixed_checkpoint_008/manifest.json)、[`handoff_audit.json`](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/results/architecture_arithmetic_joint_trial_006/arithmetic_mixed_checkpoint_008/export/native/handoff_audit.json) 与 [`probe.tr`](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/results/architecture_arithmetic_joint_trial_006/arithmetic_mixed_checkpoint_008/export/native/probe.tr)，全流程耗时 `206.58s`，原生 STA 报 **`54.163 MHz`**（较 `119.034 MHz` 暴跌 `-54.5%`），`166.999 MHz` 下 Setup WNS `-12.475 ns` / TNS `-168.039 ns`（逻辑级数 `11 -> 25`），且 **Hold WNS `-0.196 ns`（违例 `1` 条，直接触发非 Setup 门禁失败）**。全部 `90` 条 ALU 单元弧与 `33` 条进位线弧与 [`ownpr_extended_timing_model.py`](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/scripts/ownpr_extended_timing_model.py) 误差 $\le 0.00051\text{ ns}$，排除模型失真。
3. **【实测事实 + 机制推断】客观原生证据区分“慢 ALU/进位首尾开销”与“常规线网/布局代理误差”**：
   - **关键路径拆解**（对比 [`post_state_export_012/native/probe.tr`](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/results/tmds_cofactor_joint_trial_005/post_state_export_012/native/probe.tr) 与本次 [`probe.tr`](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/results/architecture_arithmetic_joint_trial_006/arithmetic_mixed_checkpoint_008/export/native/probe.tr) Setup Path 1 `own_i_0261/I -> own_i_0052/D`）：数据到达从 `10.247 ns` 恶化至 `20.242 ns`（`+9.995 ns`），其中**单元延迟 `3.660 -> 11.071 ns`（`+7.411 ns`，占退化 `74.2%`）**，**线网延迟 `6.587 -> 9.171 ns`（`+2.584 ns`，占 `25.8%`）**。
   - **ALU 首尾开销铁证**：专用进位线 `tNET = 0.000 ns`（CFU 内及跨东邻桥），链中段 `CIN->COUT = 0.045 ns/bit` 极快；但每段进位入口 `I0/I1->COUT = 0.535~0.540 ns`、出口 `CIN->SUM = 0.219~0.267 ns`（2-bit 短链首尾即耗 `0.754~0.807 ns`），单比特 `I0->SUM = 0.705 ns`（`own_i_0028`）。TMDS 视差路径**串联 5 段短算术链**（`own_i_0080..81`、`0090..91`、`0003..04`、`0028`、`0020..23`），**仅 ALU 单元弧即耗 `3.917 ns`**，单靠算术硬核本身已超过 0-ALU 基线整条路径的总单元延迟（`3.660 ns`）。
   - **黑盒零到达盲区与禁宽映射叠加**：[`remap_ownpr_logic.py`](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/scripts/remap_ownpr_logic.py#L38-L94) 将 52 个 ALU 从 `chosen` 剔除并对全部 186 个边界输入写入 `zero_arrivals`（`0 ps`），使映射器对实际 `6.13~17.40 ns` 到达的 `SUM` 完全失明；叠加 `--recipe lut4` 禁用 `MUX2_LUT5..7` 余子式压缩与引脚置换，再加上 `20` 个 FF 前置适配 `ng_ownpr_pass_*`（`LUT1` `~0.52 ns`），在关键路径串入 `14` 级慢引脚 `I0/I1`（`0.493~0.521 ns` vs 快引脚 `I3` `0.260~0.287 ns`）LUT，贡献其余 `7.154 ns` 单元延迟。
   - **线网与 Hold 退化根因**：全逻辑重命名使增量布线仅保住 `39/251` 条网（[`route/manifest.json`](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/results/architecture_arithmetic_joint_trial_006/arithmetic_mixed_checkpoint_008/route/manifest.json) 重布 `212` 条，`reuse_branches` 仅救回 `4` 条即超时回退，且未开 `--pin-feedback`）。同时，原 `wide_ff` 站点 `(101,158,1)`（`R102C159[0][B]`，快角捕获时钟偏晚 `1.376 ns`）保留旧时钟树，却把原 3 级宽宏（快角单元 `0.297 ns`、`tHld=0.011 ns`）换作单级 `LUT4` 走快引脚 `I3`（`0.138 ns`、普通 FF `tHld=0.022 ns`），快角到达由 `1.485 ns` 跌至 `1.252 ns < 1.448 ns`，产生 **`-0.196 ns` Hold 违例**。

---

## 二、新机制静态正确性缺口（含精确反例）与未验证风险

1. **【静态精确缺陷 1】源暂存未绑定 `nets.json/routes.json` 与源端口派生一致性**：
   - **代码位置**：[`ownpr_source_configuration.py`](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/scripts/ownpr_source_configuration.py#L28-L80) 与 [`run_ownpr_timing_route.py`](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/scripts/run_ownpr_timing_route.py#L102-L107)。`stage()` 仅哈希绑定 `packed.json` 与 `placement.json`，未哈希 `nets.json/routes.json`，亦未调用 `bind(source_design, source_places, source_graph)` 校验 `old_nets` 确由源布局合法生成。
   - **精确反例**：设源布局中 `net_m` 合法占据 `wide_ff@(97,165,0)`，故 `source_validation_graph()` 在 `source_graph` 上打开 `(97,165,'OF0','DI0')`；若 `source/nets.json` 与 `routes.json` 将另一条 `net_k` 篡改/错配至终点 `DI0@(97,165)` 并走 `(97,165,'OF0','DI0')`（不改 `packed/placement/manifest`），`load()` 与 `validate()` 均放行；一旦目标布局恰好将 `net_k` 移至 `(97,165)`，`partition()` 因 `signature` 匹配直接冻结该串线路由。
2. **【静态精确缺陷 2（已在实测产物触发）】单 FF 扩展槽门禁漏检同 CLS/CFU 无 FF LUT 混装**：
   - **代码位置**：[`ownpr_ff_configuration.py`](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/scripts/ownpr_ff_configuration.py#L115-L131) `compile_initial()` 跳过 `ff=None` 簇，仅统计含 FF 簇 `len(ffs)==1`；[`ownpr_place.py`](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/scripts/ownpr_place.py#L155-L163) 锚定扩展槽时漏检 `L270` 的 `Q{slot}` 开关扇出检查，且 `L267` 仅当待放簇含 `ff` 时才检查 `ff_groups`（`slot//2`）。而校准集 [`ff_semantic_audit.json`](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/results/native_ff_attributes_paired_001/ff_semantic_audit.json) 仅验证单簇独占 CFU（`shared_control_legality_verified: false`、`full_bitstream_verified: false`）。此外 [`ownpr_shared_ff_packing.py`](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/scripts/ownpr_shared_ff_packing.py#L78-L80) 硬拒 `slot not in (0,2,4)`，与扩展单 FF 全局互斥。
   - **实测反例**：在 [`arithmetic_mixed_place_007/placement.json`](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/results/architecture_arithmetic_joint_trial_006/arithmetic_mixed_place_007/placement.json#L108-L114) 与 [`L276-L282`](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/results/architecture_arithmetic_joint_trial_006/arithmetic_mixed_place_007/placement.json#L276-L282) 中，`ng_coopt_116+slice$1642` 锚定于 `(98,164,slot=1)`，而无 FF 的 `ng_coopt_91` 被塞入**同一半片 CLS `group 0`** 的 `(98,164,slot=0)`（共享 `[9,12,...]` 控制帧，同 CFU 另有 `slot 2,3,4`），越过已验证校准边界却被静默放行。
3. **【不确定性与未验证风险】**：增量 `partition()` 对 `CEg/LSRg`/未用宽 MUX 输出的静态保留冲突、RRG 三节点有向别名链（`m0->m1->m2`）中段分支崩溃、单网 `negotiation_trees` 盲区及真实比特流上板等价性仍待独立单测闭环。

---

## 三、通用逐链等价选择机制与三条竞争路线

**通用逐链判据（无项目分支）**：在 GW5AST-138 上，$L$ 比特算术链走硬核的单元代价约为 $0.80 + 0.045(L-1) + \mathbb{I}_{\text{FF}}\cdot 0.52\text{ ns}$。仅当单级非串联链长 $L \ge 7\sim 8$（如 VexRiscv 32-bit 加减/PC 链）时硬 ALU 才显著优于宽多路复用树；串联短链（$L \le 6$）必输在首尾转换与适配 LUT。应以**统一等价选择图**自动决策，而非硬编码阈值或项目开关：

1. **路线 A：到达时间驱动的异构 Choice-DAG 联合映射 + 宽宏/引脚/Hold 协同恢复（首选）**
   - 借鉴 **EqMap**（e-graph 等价饱和保留多代数重写）与 **Mapping Fusion / FuseMap**（异构硬单元与 LUT 在同一选择图中竞争覆盖）思想（注：论文基于学术 LUT6/ASIC，不直接外推 Gowin 收益）：对每条候选算术链在全锥 AIG 中**同时挂载**“硬 `ALU(mode=2)` 实现”与“软布尔展开 + `MUX2_LUT5..7` 余子式实现”两支等价 choice 节点，按拓扑真实到达时间（废除 `zero_arrivals` 黑盒切割）统一做延迟最优覆盖；对 FF 扇入恢复 `wide_ff`/配对 LUT 吸收以消除 `ng_ownpr_pass_*`；对快角短路径（到达 $< t_{\text{skew}} + t_{\text{hold}} + 0.06\text{ ns}$）保留 `wide_ff` 或约束走慢引脚 `I0` 修复 Hold。
2. **路线 B：源/字级多操作数进位保留压缩（Carry-Save / 3:2 压缩树）**
   - 在 Yosys/e-graph 字级阶段对串联加减树（$A \pm B \pm C \dots$，涵盖 TMDS 视差统计、Vex 地址/分支偏移、`int2float` 前导零加权）施加结合律重写，用普通 `LUT4` 构成 `3:2` 全加压缩层，将 $K$ 段串联进位链压成对数级 LUT 深度 + **至多末端 1 条**最终进位链（或符号位 Carry-Select 多路选择），从源头消灭串联进位首尾税。
3. **路线 C：时序松弛门控的槽位 `6/7` 混装 + `--pin-feedback` 软走廊联合布局布线**
   - 审慎界定 **Double Duty**（Pun et al. 2025）适用性：学术架构靠绕过 LUT 的簇输入使同一 BLE 内 LUT 与加法器并发，而 Gowin `type-17` CFU 的 `slot 0..5` 被 ALU 独占输入，**唯有 `slot 6/7` 是真正的并发“Double-Duty”槽**。[`ownpr_mixed_carry_pack.py`](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/scripts/ownpr_mixed_carry_pack.py#L75-L120) 纯按未加权曼哈顿 `gain>0` 贪心塞满 `24` 个 LUT 会加剧进位 CFU 局部引脚拥塞；应仅允许 Setup Slack $> 0.8\text{ ns}$、Hold 安全且不推高 CFU 引脚密度的非关键 LUT 迁入 `slot 6/7`，并启用 `--pin-feedback` 软走廊重布。

---

## 四、有界决定性下一实验（$\le 1\text{ h}$，$< 40\text{ GB}$）与验收门禁

> **容量预警**：当前工作区已达 **`38.98 GB / 40.00 GB`**（[`checkpoint_008/manifest.json`](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/results/architecture_arithmetic_joint_trial_006/arithmetic_mixed_checkpoint_008/manifest.json)），执行前必须先用 [`workspace_budget.py`](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/scripts/workspace_budget.py) 预留并在依赖清单审计下清理已淘汰非冻结中间缓存。

- **有界双臂实验（总预算 $\le 45\text{ min}$）**：
  1. **前置门禁补丁（`5 min`）**：① 在 `ownpr_source_configuration` 中增补 `nets/routes` SHA256 与 `bind(source_design, source_places, source_graph)` 一致性断言及篡改负测；② 在 `ownpr_place` / `ownpr_ff_configuration` 中禁止扩展槽单 FF 所在 `slot//2`（未校准前扩展至整 CFU）混入无 FF LUT，并补齐锚点 `Q{slot}` 开关检查。
  2. **路线 A 统一流程双设验证（`40 min`，同一套无分支参数跑 TMDS + VexRiscv，附 UART/int2float 守卫）**：开启真实拓扑到达传播 + 逐链 `{硬 ALU ⊕ 宽 LUT4..7}` Choice-DAG + `wide_pins` + Hold 慢引脚/`wide_ff` 保护 + `--pin-feedback`。检验该通用策略能否在 TMDS 自动软化串联短链（恢复并冲击 $>119.034\text{ MHz}$），同时在 VexRiscv 自动保留 32-bit 进位链并宽映射控制锥（冲击 $>90.380\text{ MHz}$）。
- **硬性验收门禁**：① 固定状态全观测输入 CEC、源/目标双图模式审计、完整 I/O 覆盖与固定引脚/周期 100% 通过；② 四类 STA 守卫全过且 **Hold WNS $\ge +0.030\text{ ns}$**（零违例）；③ 仅当四设计最小相对公平基线比值非退化、且至少一设计创有效新高（TMDS $>119.034\text{ MHz}$ 或 VexRiscv $>90.380\text{ MHz}$）时晋级，否则回退并转入路线 B 字级压缩树。
