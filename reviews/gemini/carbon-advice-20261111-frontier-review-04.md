# 热点发射端负载分区与联合布局代码审查报告（04）

**基线与试验状态**：【观察】86.7 MHz 检查点已通过独立完整 I/O STA（setup `+0.018 ns`、hold `+0.133 ns`）并保留；当前 [manifest.json](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/results/vexriscv_frontier_partition_place_001/manifest.json) 仅处于已布局与等价证明完成状态（`status: "placed"`，`timing_signoff: false`，`physical_legality_complete: false`，`clock_replan_required: true`，耗时 `45.53 s`），自研布线与 110 MHz 完整最终 STA 仍在运行中，**尚未通过签核**。下文严格区分【观察】【推测】【需要实测】。

---

## 一、算法与证明审查：会导致错误采用的实质风险与退化隐患

1. **新增副本的参数、时钟与初态（Parameters / Clock / Init）**
   - 【观察】[ownpr_frontier_partition.py](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/scripts/ownpr_frontier_partition.py#L29-L86) 限制候选发射端与可移动 FF 的 `parameters` 仅允许 `INIT`；[ownpr_regional_replication.py](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/scripts/ownpr_regional_replication.py#L71-L83) 通过 `deepcopy` 保留原单元参数，保持前沿寄存器 `CLK/CE/RESET/SET` 原网连接不变，校验原 `Q` 初态必为确定 `'0'`/`'1'` 后写入 `netnames[clone+'_out']` 的 `init` 属性并在 L137 执行 `compile_initial`；[ownpr_register_equivalence.py](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/scripts/ownpr_register_equivalence.py#L38-L45) 再次核验父子同为 `DFFRE/DFFSE`、参数字典相等、`CLK` 同网且初态一致（本次 6 个副本初态均为 `"0"`）。唯 `replicate()` 独立 CLI 入口未预筛非 `INIT` 参数，虽会被 `prove_replicas` 拦截或校齐，建议与主流程统一。

2. **其他原状态、接口保持与归纳证明（CEC）**
   - 【观察】[ownpr_register_equivalence.py](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/scripts/ownpr_register_equivalence.py#L13-L68) 强制顶层模块集与黑盒不变、`hard(new)-hard(old)==set(replicas)` 且无原状态或硬核丢失、全网单驱动；折叠 `qb->qa`（含 L58 对候选端自反馈/跨副本 `aliases.get(b,b)` 映射）后分别验证一步归纳（`D/CE/SR` 输入相等）与全外部端口/原硬核输入观察相等，固定引脚与接口周期均未改变。L65 在结构严格相等（`reference==candidate` 或 `before==folded`）时允许空 CEC 列表在逻辑上完全严密，无需强制非空；本次 `_001` 两步均非结构相等，已由 ABC `cec -n` 实测证明等价（8710 与 8692 个观察点，`equivalent: true`）。

3. **共享物理控制组（CLS `slot // 2`）与合法化**
   - 【观察】主流程 L80-86/103-111、`regional_replication` L86/125-131 与 [ownpr_macro_legalize.py](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/scripts/ownpr_macro_legalize.py#L15-L25) 均将新增及移动 FF 限定于独立校准槽 `(0,2,4)`，禁止进入已有 FF 的 `slot//2` 控制组，并冻结原共享控制组 FF 与 `exclusive_tiles`（算术进位链、宽宏、硬核、I/O）。无 FF 普通 LUT 迁入同半簇奇数槽（如 [moves.json](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/results/vexriscv_frontier_partition_place_001/moves.json) 中 `[65,105,3/5]`）仅占用独立 LUT4，不触发 FF 控制冲突。唯一潜在漏洞是 `clear_site`（L69, L77）驱逐含 FF 簇时未调 `admissible` 校验 FF 类型与参数；本次因 `state_root=True` 仅走 `clear_register_slot` 未触发，后续启用普通逻辑锥复制前应补齐。

4. **`keep` / `ASYNC_REG` 属性盲区（唯一可能绕过 CEC+STA 导致错误采用的实质问题）**
   - 【观察】`replicate()` 与 `prove_replicas()` 均未检查单元及 `netnames` 上的 `ASYNC_REG`、`keep`、`syn_keep`、`dont_touch` 属性；主流程仅对 FF 单元字典调 `admissible(cells[ff])`，未传入常承载 Yosys 线网属性的 `netnames`，也未检查配对 D-LUT、独立移动 LUT（L85）与合法化驱逐 LUT（`clear_register_slot` L24）。
   - 【推测】若某源寄存器属跨时钟域首级同步器（`ASYNC_REG`），父子副本因共享同一异步输入与时钟，布尔一步归纳**必然判定等价且可通过 STA**，但硬件上亚稳态决断分叉会导致下游状态撕裂！本次 4 个源 FF（`$47248/$53408/$47184/$47265`）均驱动内部 ALU/宽选通/BSRAM 通路，【推测】属同步流水状态，但【需要实测】核查其单元与线网无 `ASYNC_REG/keep` 属性，并在四处代码补齐单元+线网属性硬拦截。

5. **虽会被完整 STA 拦截、但极易引发本次时序退化的三处算法缺陷**
   - 【观察】只要严格执行含 setup/hold 与时钟重规划的完整 STA 签核，时序退化不会导致静默错用，但 [partitions.json](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/results/vexriscv_frontier_partition_place_001/partitions.json) 与 [moves.json](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/results/vexriscv_frontier_partition_place_001/moves.json) 暴露了三处高危退化源：
     1) **点剥离而非完整算术扇出树分区**：`partitions()`（L26-38）仅收集出现在 `probe.tr` 窗口内的边；6 个副本合计仅改接 **8 个引脚**。其中 103 扇出的 `$47248` 两个副本各仅改接 2 个负载（且 `region_21_0` 因 6-tile 直径把馈入 `slice[5]` 的 `1541_leaf_3` 与馈入 `slice[29]` 的 `941_leaf_1` 混入同组），原 `$47248` 仍挂 **99 个扇出**，被同一终点最差路径掩盖的其余算术切片并未分流。
     2) **副本前驱/控制输入被严重拉长**：`regional_cost`（L102）给汇点最大距离赋权 `8`、外部输入均值仅赋权 `1`。`region_22_0`（`$53408` 副本，落于 `[65,105,2]`）在 `legalization`（`8A+E`）与 `placements`（`4A+E`）得分同为 `124.0`，代数上直接证明汇点距离 `A=0`、而**外部输入平均曼哈顿距离高达 `E=124.0` tile**；`region_23_0`（带局部控制网 `CE=2353/RESET=2355`）则从 `[77,46]` 东移 **48 tile** 至 `[70,87]`。【推测】副本自身的 `D/CE/RESET` 极易在 110 MHz 下产生新最差违例。
     3) **原高扇出源反向漂移与 Hold 裸奔**：`relax()` 按星型点对求和且非 `probe.tr` 边权为 `1.0`；剥离少量高权边后，原 `$47184`（仍挂 74 扇出）被西拉 **12 tile**（`[77,46]->[77,34]`），原 `$47265`（仍挂 99 扇出）被北拉 **7 tile**（`[73,100]->[66,101]`），且在 `+0.133 ns` hold 基线下无任何短路径与跨 tile 偏斜守卫。

---

## 二、若真实 STA 退化：最有信息量的分解与最少修改（不单调 I/O）

1. **零重跑四桶归因 + 单次消融对照**
   - 【需要实测】待 `_001` 最终 STA 完成后，先直接对违例路径做四桶互斥分解（零重跑成本）：① 新副本 `region_20..25_0` 的 `D/CE/RESET` 建立违例；② 原 `$47248/$47265/$47184` 剩余扇出（含被掩盖的同 ALU 切片）违例；③ `moves.json` 29 次移动牵连路径或局部布线拥塞；④ `hold_wns < 0 ns`。若复制与移动效应交织，直接用现成参数 `--move-count 0`（L136 已支持）跑一次纯分区复制对照（单次远低于 3500 秒预算），即可精确分离两阶段净收益。

2. **最少代码修改（约 20 行，不触碰单独 I/O 优化）**
   - **冻结已复制父 FF 与高扇出驱动**：在 `relax()`（L86）排除已生成副本的父簇（`aliases.values()` 配对簇）及扇出 `>8` 的驱动，立即阻止 `$47184/$47265` 抛弃剩余扇出反向漂移。
   - **同源全网表近邻几何归并**：在 `partitions()` 选出锚点组后，扫描全网表同一 `bit` 的所有合法接收端（`LUT I*`/`ALU I0,I1,I3`/`MUX2_LUT* S0`），凡距副本目标区直径 `<=6~8` tile 且严密近于原源者一并改接至该副本，零新增 FF 真正切开 103 扇出高低位算术锥并覆盖隐藏次关键路径。
   - **副本单输入最大拉伸硬上限**：在 `replicate()`（L102, L127）改用 `max` 输入距离惩罚，拒绝任一 `D/CE/RESET` 驱动曼哈顿增量超过 `8~10` tile 的选址，迫使 `region_22_0/23_0` 落在源汇折中走廊。
   - **Hold 最小守卫**：在 `relax()` 限制含 FF 簇移动半径（`<=4`），并禁止把非关键 1 级路径压入同 tile。

---

## 三、下一版目标升级：基于全负载树实测与时序图的联合目标

为避免把局部连线缩短误判为全路径收益，同时严守 3500 秒单次试验预算（Python 侧增量时序图评估仅需数秒），建议分三层升级：

1. **构建全网表静态时序图（DAG `AT/RT`），消除 `probe.tr` 单终点截断盲区**
   - 在顶层全连接图同时维护建立（`AT_max/RT_max`）与保持（`AT_min/RT_min`）时间。未改动线网直接注入 `--routes/--sta` 实测线延时；算术进位链（如 [critical_frontier.json](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/results/vexriscv_internal_state_trial_001/critical_frontier.json) 中 `R63C93..98` 的 `$45667.slice[2..31]`）直接融合 `ownpr_measured_carry_pack.py` 已校准的 `ALU` 数据与进位实测模型（`I0/I1/I3/CIN -> COUT/SUM`），使未进 `probe.tr` 的副本 `D/CE/RESET` 与全部 103 个扇出分支均具精确 `Slack_setup` 与 `Slack_hold`。

2. **以全负载树实测代理替代星型点对曼哈顿和**
   - 对多扇出网 $N$，按分区后的**完整接收集合** $S(N)$ 计算树延时：用方向性实测查表 $\Delta_{\text{cal}}(\Delta r,\Delta c)$ 叠加 Steiner 半周长（HPWL）与扇出电容项 $\beta(|S(N)|)$；对新增副本引入的 `D` 前驱及共享 `CE/RESET` 负载，按接入**现有已布线树最近分支点（Steiner tap）**的增量延时建模，而非到根驱动的星型距离，准确刻画“原树减负”与“前驱/控制树增负”的净平衡。

3. **跨时钟沿双向 Slack 联合目标（Log-Sum-Exp + Hold 硬壁垒）**
   - 将复制选址与 `relax()` 统一为最小化建立时间 `LSE + TNS` 的受约束优化：
     - **复制准入条件**：候选点 $x$ 必须满足 $\min_{p\in\{D,CE,SR\}}\text{Slack}_{\text{setup}}(\text{rep}/p,x) > \min_{s\in S_{\text{rep}}}\text{Slack}_{\text{setup}}^{\text{old}}(s)+\delta$，从机制上杜绝“救了 `Q` 却拉爆 `D/CE/RESET`”。
     - **移动接受条件**：每次试探仅在 2~3 级局部拓扑锥做毫秒级增量 `AT/RT` 传播，要求局部最差 `Slack_setup` 不退化、LSE 目标严格下降，且所有关联早到路径满足 `Slack_hold >= +0.08 ns`（计入跨 tile 时钟偏斜上界）。在此统一时序图下，后续还可直接量化 `R63` 进位链前后的馈入 LUT 与捕获 FF 耦合瓶颈，协同推进全路径收敛。
