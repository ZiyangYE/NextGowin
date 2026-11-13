# VexRiscv 共享多输出 DAG 实现漏洞审查与收敛修正建议

## 一、实测现状、频率新口径审查与非理论极限判定

- **【观察】`vexriscv_shared_dag_search_001` 完结实测**：读取时尚存完整 `manifest.json`、`selected.json` 与 `trials.json`。本轮基于 warm seed（`WNS -2.1107 ns`、`TNS -309.789 ns`、违例 `348`、`hold +0.1327 ns`）运行 `505.16 s` 完成 `24` 次试验（`22` 次测时、`2` 次路由超时），提交两笔事务（第 0 轮 `3136|3140|3142` 三根共享 `7` 内节点，`changed_nets=31`；第 1 轮单根 `1078` 建 `legacy` 卸载 `40` 个旁路并驱逐 `1094`），全图 `CEC`（`0.269 s`）与连通审计通过，`lut_delta +21`（较 warm seed 净增 `8`）。最终内部测时 `TNS` 改善至 `-295.854 ns`、违例降至 `345`，但全局 `WNS` 停留在 `-2.1107 ns`（增益 `0.000 ns`，`timing_signoff: false`）。当前最高已完成原厂完整 STA 仍为 `89.2 MHz`，110 MHz 完整 STA 报告 `Fmax` 为 `89.273 MHz`，未达 110 MHz 闭合目标。
- **【推断】局部未动 `WNS` 绝非理论极限**：两轮未推进顶层 `WNS` 源于下文定位的搜索束替换、同根队列队首垄断与盲目驱逐等具体实现缺陷（顶层关键多级锥根本未被完整克隆评估），绝不能将两次局部搜索受阻视为固定边界或共享 DAG 架构的理论极限。
- **【观察】新频率报告口径核查**：`frequency_reporting_policy_20261112.json` 与 `ownpr_frequency_policy.py` 已落实“完整 110 MHz STA 且 hold 通过（`hold_violations==0`、`hold_wns_ns>=0`）及 `other_checks_passed` 时直接取原生 `reported_fmax_mhz`，不重跑确认，且 `full_timing_pass_at_110mhz` 仍绑定 `native_constraints_satisfied`”。**【推断】**`ownpr_frequency_policy.py:8` 用 `isinstance(..., (int, float))` 未排除 `bool` 子类（误传 `True` 会当 `1.0` 放行），且未显式强制校验 `cec_equivalent`、`io_coverage_complete`、`configuration_consistent` 与 `not vendor_pnr_executed` 字段，建议补齐布尔排除与显式门禁键。

## 二、五大具体实现漏洞定位

1. **`roots/internal` 拓扑与测试盲区（`ownpr_shared_dag.py:27-33, 47-50, 58-71`）**
   - **【观察】**`ordered_nodes` 与 `L50` 仅检查 `members` 内 1-hop 直接边；未校验 `members - roots` 每个内节点在子图内有向可达 `roots`；`tests/test_shared_dag.py` 仅含 4 个测试函数且全设 `fast=False`、无 `GND` 单元与真实 `metrics`。
   - **【推断】**若存在经外部节点 `ext` 的路径 `r1 -> ext -> m -> r2`（`r1, r2 in roots, m in members, ext not in members`），`L50` 漏检跨根祖先依赖；传入断链内节点会产生 `0` 扇出死克隆 `ng_shared_*`；原内节点扇出被完全接管且非 `keep` 网时未清理原位 `0` 负载僵尸 LUT；`fast=True` 真值表置换与升格路径在单测中处于零覆盖状态。

2. **`legacy` 反向依赖、全卸载悬空与抢线污染（`L35-41, 72-83, 104`）**
   - **【观察】**`ordinary_side_receivers` 将 `root` 在 `members` 外全部非保护非排他普通 LUT 无差别改接至旧位 `ng_legacy`（`L80-82`），且 `L79` 写入 `origins[ng_legacy] = n`。
   - **【推断】**① 若 `r1 -> ext -> r2` 且 `unload=True`，`ext` 改接旧位 `ng_legacy_r1` 会造成迁往新区的 `r2` 反向依赖旧区 `legacy` 副本，且 `displacements` 可能将驱动 `legacy` 的上游 LUT 反向驱离；② 若 `root` 外部负载全为普通 LUT，100% 负载被卸至 `ng_legacy`，保留原 bit 的新 `root` 沦为 `0` 汇点悬空死逻辑（在 `ownpr_route_design.py:133` 被静默丢弃）或拉爆靠近新址的次关键普通接收端；③ `origins[ng_legacy] = n` 使 `prioritize()`（`ownpr_path_cone.py:113-116`）将关键根 `WNS` 赋给旧位旁路输入脚，诱发新旧两区最高优先级抢线（实测 Trial 4/11 单 LUT 卸载即耗时 `67 s` 路由超时）。

3. **`INIT/slot` 与 `pairedFF` 控制组冲突（`L51-55, 91-102, 153-160, 179`）**
   - **【观察】**`proposals()` 与 `clone_shared()` 允许新副本或被驱逐 LUT 落入含现存 `paired FF` 的 `type-17` Tile 任意空槽；`compile_initial`（`ownpr_ff_configuration.py:95`）跳过 `ff=None` 簇，但导出时 `ownpr_configuration.py:62-64` 对含 FF 的 Tile 编码全部 4 个 `cls` 组（无 FF 组 `attributes[0]=0`，而 `ownpr_wide_ff_packing.py:48` 须显式置 `attributes[slot//2][0]=9`）。同时 `L166` 对 `slot>=6` 慢槽仅罚 `3`（约合 `1` 格线距），多根 `groups()`（`L117-130`）亦未过滤 `paired FF` 根。
   - **【推断】**将无配对 LUT 塞入含 `paired FF` Tile 的同半切片奇槽（`1/3/5`）缺乏混合控制校准，落入该 Tile 无 FF 组则会因 CLS `attributes[0]=0` 引发导出期模式位失配；慢槽罚项过轻导致实测 Trial 2/4/6/20 频繁将关键根排入 `slot 6/7`。

4. **位分配缺陷（信号 `bit` 与物理槽位分配，`L56-60, 78, 129, 155-182`）**
   - **【观察】**`L56-58` 分配 `nextbit` 仅扫 `top` 且 `max(...)` 无 `default=1` 下界；物理选址中 `occupied` 始终锁死已迁根释放的旧槽，多根簇硬编码 `unload=False`（`L129`）却在 `L164` 仅按单一 `anchor` 计费。
   - **【推断】**未卸载的多根迁址完全漏算其余外部负载坐标，直接拉断旁路；静态锁死旧槽也浪费了原位空出的合法低槽位。

5. **共享负载增量与单跳到达失真（`L98-101, 119-125, 163-174`）**
   - **【观察】**克隆保留原内节点使外部边界网净增汇点（实测 3 根事务 `changed_nets=31`），`proposals()` 对外部网新增汇点数（`boundary_fanout_delta`）与内部共享副本 `1->m` 扇出膨胀零罚项；`estimate(i)`（`L98-101`）仅做单跳曼哈顿修正。
   - **【推断】**同一紧裕量外部网被多节点重复引接会招致严重树分支回弹；多级 DAG 未按拓扑序累加前级副本新到达时间，使下游快脚置换仍按失真旧序排列。

## 三、造成候选无效或严重回弹的选择与合法化机制

- **【观察】搜索束替换与同根队首裸迁垄断**：`ownpr_joint_lut_search.py:190-193` 在 `--shared-dag` 下完全替换 `cone_proposals`，丢弃全部无普通旁路的单根多级关键锥（如 `ADB[5]` 根 `2730`）；`proposals()`（`L188`）按 `tuple(roots)` 建队轮询，将同根 `width=1,3,5` 混入同一队列并只弹出队首，致实测全部 `unload=True` 试验 100% 退化为 `interior_count=1` 单 LUT 裸迁，其中 Trial 18 甚至将 `ng_hold_buffer_482` 从 `[37,138,0]` 跨芯片搬至 `[88,60,1]`（曼哈顿距 `129`），使 `WNS` 崩至 `-5.5228 ns`！
- **【观察】无视连线与 hold 护栏的盲目驱逐**：`L157` 仅凭 `setup slack > 0.05 ns` 即允许驱逐（未排除 `ng_hold_buffer_*` 等保 hold 单元、未检 hold 裕量），`L179-181` 在 `±8` 格内仅按距旧位最近落子且完全不计被驱逐单元自身扇入扇出拉长；实测 Trial 21 将 `ng_coopt_1632` 驱离 `10` 格至 `[74,88,5]`，`WNS` 暴跌至 `-3.6770 ns`（违例激增至 `391`）。
- **【观察】实例名死切与中点选址失锚**：`by_receiver`（`L112`）按单一实例名分组，无法聚合相邻异名 FF/ALU 切片；`center=(anchor+launch)//2` 配 `radius=4` 提前截断（`L146-158`），使 Trial 8 三根落在 `(98,113)` 距汇点逾 `35` 格，回弹至 `-2.4955 ns`。

## 四、最高优先级可执行修正（限 3 项）

1. **封堵拓扑/卸载漏洞与含 FF Tile 的 CLS 槽位门禁**：在 `clone_shared` 中用全图传递可达性禁止 `roots` 经外部节点互为上下游并校验内节点全可达 `roots`；`ordinary_side_receivers` 仅卸载非关键（`slack > WNS + 0.35 ns`）且离旧位更近的旁路，确保原根保留至少一个关键汇点，并从 `change['input_origins']` 移除 `ng_legacy` 防止抢线；在未显式补齐 `cls` `attributes[slot//2][0]=9` 与奇偶混装校准前，禁止向含 `paired FF` 的 Tile（或至少禁止同 `slot//2` 半切片及无 FF 组）放入新/驱逐普通 LUT，大幅提高 `slot>=6` 罚项。
2. **修复多级锥覆盖、位移跨度预算与安全驱逐合法化**：在 `ownpr_joint_lut_search.py` 中令 `shared_proposals` 与普通多级 `cone_proposals` 合并竞争而非互斥替换；`proposals()` 队列键改为 `(tuple(roots), len(members))` 并优先多级锥（`len>=3`），硬拒 `ng_hold_buffer_*` 入锥或被驱逐及根迁后输入总曼哈顿恶化超预算的跨区裸迁；驱逐门槛提至 `setup_slack > 0.6 ns` 且 `hold_slack > 0.08 ns`，重放半径缩至 `≤3` 并按被驱逐单元全关联边曼哈顿增量选址。
3. **计入共享负载增量、拓扑链式快脚估计与邻近汇点聚类**：将 `groups()` 聚类键由单一 `receiver` 名改为汇点物理坐标簇（曼哈顿距 `≤3`），并为多根簇按根独立开启安全 `unload`；对同一紧裕量外部边界网限制单事务新增汇点 `≤1`、在选址代价中计入未卸载旁路坐标与 `1->m` 扇出罚项；按 `order` 正向递推累加内节点副本的新到达时间与槽位延迟后再单次执行 `fast_inputs`，并在 `test_shared_dag.py` 补齐 `GND + fast=True` 及跨外部节点反向依赖回归单测。
