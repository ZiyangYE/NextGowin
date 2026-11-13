# 双向时序模型与现有副本扇出重分配审查（07）

## 1. Setup/Hold语义、边界重用与独立性限制

核查 [native_timing_state_probe.py](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/experiments/native_timing_state_probe.py)、[build_native_bidir_model.py](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/scripts/build_native_bidir_model.py)、[ownpr_bidir_timing.py](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/scripts/ownpr_bidir_timing.py) 与 [validation.json](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/results/vexriscv_87_3_bidir_model_001/validation.json)，单时钟图代数自洽：`flags` `16/17` 按 `flags&1` 映射 `0=rise/1=fall`，`pair0`（setup，`R-A`）与 `pair1`（hold，`A-R`）同 `getSinkSlack` 一致，反向传播 [`analyze`](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/scripts/ownpr_bidir_timing.py#L28-L49) 中 setup 取 `min(RT-d)`、hold 取 `max(RT-d)` 正确。用于全扇出重分配须锁死四项前提：

1. **`C2Q`/`seed` 重用与 `CLOCK` 不变条件**：普通 `DFFRE/DFFSE` 的 `seeds[FF/Q]`（源时钟到达+`C2Q`）可重用，当且仅当：① 单元位置、槽位 `slot` 与原语参数不变；② 路由锁定全部时钟网，数据网重布零时钟扰动；③ `replica/Q` 在基线 `pin_feedback.json` 中已有有限到达值（`<1e9`，0扇出悬空副本若为哨兵值则禁直接复用）；④ 非模式相关的 `hard_c2q`。切片级负载变化体现在下游 `wire` 延迟而非 `C2Q`。
2. **终点 `RT` 重用与 CPPR 限制**：`pin_feedback` 为图级（GBA）状态。重接数据脚不改捕获时钟与门限，`2,798` 个终点 `RT` 可作反馈重用；但跨时钟支路的 `parent/replica` 在最终路径级 STA 中若有公共时钟悲观消除（CPPR）差异，会有微小残差。若 `parent/Q` 扇出清空但保留单元，`parent/D` 仍在 `terminals` 中，须防导出裁剪空载 FF 致终点集失配。
3. **完整图变化假设**：改接时 [`changed_graph`](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/scripts/ownpr_bidir_timing.py#L52-L56) 必须重测并替换：① `replica/Q` **整棵新负载树**（含旧 sink 与新迁 sink，原同簇 `[0,0]` 边迁出后须更新为实测走线边）；② `parent/Q` **全部残留 sink**；③ 拆线波及的任何**第三方连带网** wire 边。同时禁路由期交换 LUT 输入脚（`I0..I3` 延迟与单调性不同），否则 `cell` 弧失效。
4. **校验与观测独立性边界**：`47,616` 个 arrival 与 `46,378` 个 required/slack（差 `1,238` 源于 `42` 条无约束 `OBUF` 及其纯无约束扇入链 `required>=1e9`）含直接拷入的 `seeds` 与终点 `RT`；受约 `OBUF` 的 `io_observed_mode` 由同源 `OAT-IAT` 相减所得（非独立 IO 库）；内部弧亦取自同一 DLL 同样本查询。故 `0.151ps/0.00243ps` 仅证明**单样本 GBA 传播律与槽位语义一致**，不可外推为全器件签核。

## 2. 多现有副本别名合并的易错条件

核查 [ownpr_existing_state_alias.py](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/scripts/ownpr_existing_state_alias.py) 与 [ownpr_register_equivalence.py](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/scripts/ownpr_register_equivalence.py)，须防四处陷阱：

1. **顺序与星型映射**：[`collapse`](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/scripts/ownpr_register_equivalence.py#L12-L61) 要求 `parent not in replicas` 且 `aliases` 仅单跳替换。禁链式（`R2->R1->R0`）或逐对折叠；每组须选唯一定点 `R0` 构造扁平星型字典 `{R1:R0, R2:R0, ...}` **一次性联合 `collapse`**（组内 `R1->R2` 互迁亦可直接覆盖）。
2. **反馈耦合与三步 CEC**：互馈/自馈副本（[`alias_groups`](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/scripts/ownpr_existing_state_alias.py#L21-L42) 将 `Q` 标为不同 `boundary:bit` 会漏检自馈，扩展发现时必遇耦合）单独折叠会归纳失败，须联合归纳。且 [`certify`](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/scripts/ownpr_existing_state_alias.py#L67-L84) 中 `before` 归纳、`after` 归纳、`fold(before)==fold(after)` 观察 CEC 缺一不可：漏 `before` 会使错接不等价副本在同向折叠下假阳性通过；漏 `after` 会放过改线对次态/控制等价性的破坏。
3. **`keep` 与隐式 CDC**：[`admissible`](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/scripts/ownpr_existing_state_alias.py#L14-L18) 仅查 `cell` 属性，须补查 `netnames` 的保留/调试属性及直驱顶层 `ports`。更须拒绝 **`D/CE/RESET` 直采异步或无约束 `IBUF` 的双物理 FF**（即使未标 `async_reg`）：布尔 CEC 视主输入单拍确定而放行，实片亚稳态独立决断会致逻辑分裂。
4. **103 扇出簇合法性**：[`rewire`](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/scripts/ownpr_existing_state_alias.py#L54-L64) 现仅许 `LUT1..4`。全枚举时，同一 CLS/切片双 FF 共享 `CLK/CE/LSR`，单改其一的 `CE/RESET/SET` 或改动 `MUX2_LUT*` 选择脚、`ALU` 专用链、`OBUF`/硬核脚会破坏簇合法性，须原位保留并分类记录。

## 3. 最小保守接受准则与核心负例测试

### 最小保守接受准则（仅作候选筛选，以原生全量 STA 签核为准）
1. **等价与合法门**：扁平星型联合 `collapse` 通过 `before/after` 归纳与折叠观察三项 CEC；单元与网名无保留/调试/异步属性且非无约束 `IBUF` 直采；簇控制集未分裂；时钟网零改动。
2. **全树重测与双端门**：实测 `parent/Q` 残留树、`replica/Q` 合并树及所有连带重布网的全部 late/early wire 延迟（禁几何估算、禁只更改接边、禁未同步 LUT 换脚）。全图 `2,798` 终点满足：`hold` `violations==0` 且 `WNS_hold >= +0.050ns`；`WNS_setup` 较 `-2.356528ns` 净升 `>= +0.020ns`。
3. **签核与回滚门**：总耗时 `<=3500s` 内完成固定引脚（含 `A5/C17`）全 I/O 原生 STA 与编码；仅当原生 STA 实测 `WNS_setup` 优于 `87.3MHz` 基线、`hold` 零违例且接口周期不变时接受，否则原子回滚至 `87.3MHz` 基线。

### 最有意义负例测试：旧兄弟分支恶化叠加短路径 Hold 违例
针对 [test_bidir_timing.py](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/tests/test_bidir_timing.py) 补双源负例：`parent/Q`（`seed=[1.0,1.0]`）驱关键点 `s_crit`、短径点 `s_short` 与保留点 `s_keep`；早发射的 `replica/Q`（`seed=[0.85,0.85]`）原驱次关键点 `s_sib`。将 `s_crit`、`s_short` 迁至 `replica/Q`：
- **防漏测旧负载**：`s_crit` 改善，但 `replica/Q` 负载激增令旧 `s_sib` late 延迟劣化并反超恶化全局 `WNS_setup`——仅更改接边会误收，全树更新必拒；
- **防短径击穿**：即使 `WNS_setup` 净改善，`s_short` 改接早发射源且走线缩短致 `early` 到达跌破 `RT_hold`（`violations=1`）——门控必须原子拒绝并保持原图不变。
