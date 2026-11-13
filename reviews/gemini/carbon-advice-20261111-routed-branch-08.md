# 路由接收子树转移独立审查报告（2026-11-11）

**范围说明**：已审阅 `scripts/ownpr_route_branch.py`、`scripts/ownpr_measured_branch_transfer.py`、`scripts/ownpr_alias_feedback.py`、`scripts/ownpr_source_reconnect.py`、`scripts/ownpr_full_fanout_alias.py`、`tests/test_route_branch_transfer.py` 及 `results/vexriscv_routed_branch_search_001/{manifest,selected}.json`；`results/vexriscv_routed_branch_search_002/manifest.json` 尚未生成。未读过程日志，未执行任何命令。

## 1. 五项机制漏洞审查

- **所有权释放与重连**：
  - **【观察】** `prune()` 在严格有向单父树上回溯剩余 sink，死前缀与 `recipient` 旧树一并从 `owners` 释放；`reconnect_source(..., min_depth=depth, max_depth=depth)` 首阶段以 `blocked = foreign | (nodes - roots) | {old['source']}` 强制仅接 `anchor` 根、禁入子树内点，阻断双父与环。
  - **【观察】** 次阶段补连 `recipient` 原有 sink（`ownpr_source_reconnect.py:59`）会拆毁其既有树并以新子树为 `seed` 重连（允许从子树内点分叉）；同一 `recipient` 二次接收子树时会拆重连前次迁入的关键分支。
  - **【待验证】** `owners` 仅含 `routes['nets']` 与时钟/IO `reserved`，未并入 `bind()` 的 `static` 及常数网节点，需确认 `configure_graph()` 已在底层永久屏蔽。
- **同物理资源多逻辑 sink**：
  - **【观察】** `byresource` 汇总同物理节点的全部逻辑引脚，要求全属 `eligible` 且整组改接，并校核节点数等于 `branch['sinks']`（漏记特殊端会自动拦截）。
  - **【观察】** 两处缺口：`ownpr_measured_branch_transfer.py:130` 仅遍历 `byname` 查签名，未断言 `set(byname)==set(nb)`（缺键会抛未捕获 `KeyError`）；`AliasFeedback.update()` 未自检两网共用同一物理接收节点。
- **硬核/控制/私有端口限制**：
  - **【观察】** 双重白名单仅放行 `LUT1..4/I*`、`ALU/I0,I1,I3`、`MUX2_LUT*/S0`，禁改 `CIN`、级联 `I0/I1`、触发器全部端口及硬核；`binding()` 强制 `hard_clusters` 单元字典完全不变。
  - **【推测/待验证】** 若 `recipient` 原有扇出含硬核端，拆树重连会改变硬核入口上游 PIP（多节点捕获端会因 `len(nodes)!=1` 报错回滚），终态须经原厂 import/readback 重验。
- **物理 alias 方向性**：
  - **【观察】** `ownpr_route_branch.py` 与 `ownpr_source_reconnect.py` 仅在折叠后 canonical RRG 节点（`graph.node`）判连通；`anchor` 若跨多 tile 物理线段，新源可能从另一 tile 坐标注入并反转线段内传播方向与延时。
  - **【待验证】** 需确认 `expand(..., native.fanouts)` 严格按新驱动 PIP 物理端口做有向可达展开（不可达即抛 `ValueError`），而非回退无向等价类。
- **模型回滚与源时钟条件**：
  - **【观察】** 候选在内存副本评估，仅达标才在轮末提交状态与 `cache`；`AliasFeedback.update()` 在多轮提交间对前序已改源边保持一致。
  - **【推测/待验证】** 端点 `requirements` 为单源标量，不含源-目的对动态 CPPR/CRPR 重算；最远 24 tile 换源的时钟偏斜残差依赖 `hold >= 0.05ns` 裕量吸收，不可替代原厂 STA。另需确认 `collapse` 已校核父子 FF 同 `CLK` 网络/极性（`manifest.json` 摘要仅列 `D/CE/RESET`），且 `native.parent_tree` 异常退栈无状态残留。

## 2. 联合归纳证书对串行 Commit 的支持性

- **【观察】** **在固定状态单元且仅做同族 alias 组合输入改接时，完全支持任意有限次串行 commit。** `aliases` 为星形根映射，多步同族迁移后 `fold_many(after, aliases)` 仍将所有副本输出折回同一代表元，必满足 `folded[0] == folded[1]`；终态 `certify(original, final_design, aliases)` 对累积网表重跑 `collapse` 与 `cec -n`，即便迁移进入反馈锥使转移结构不再对称（如 `001`），终态一步归纳仍严格闭合，由传递性保证首尾等价。
- **【观察】** **扩展边界**：`ownpr_full_fanout_alias.py:39` 要求 `folded[0] == folded[1]` 字典全等；一旦引入 LUT 引脚置换、`INIT` 修改或组合复制即会触发异常，届时须改为对 `folded[0]` 与 `folded[1]` 做 CEC。

## 3. `+0.105ns` 瓶颈归因与有约束、可证伪的下一步

- **归因**：
  - **【观察】** `001` 仅耗时 143.8s、24 事务中第 0 轮成交 1 笔（裁去 7 级/`1.378ns` 前缀改走 5 PIP），模型 WNS 由 `-2.357ns` 升至 `-2.252ns`，但违例数仍为 370、第 1 轮零成交退出，表明单分支修复后即撞上 `-2.25ns` 次临界墙。
  - **【推测】** 受**局部算子覆盖**与**纯首跳上限**双重限制：算子上，`min_sinks=2` 漏单叶关键端、`max_depth=12` 漏远端深子树、子树须 100% `eligible` 令混有单个 `DFFRE/CE,D` 的控制/数据分支全数连坐禁迁、`max_families=4/min_fanout=32` 过窄，且同 `recipient` 二次迁入拆毁旧树；上限上，VexRiscv 大量违例在多级 `LUT->ALU->LUT` 组合内层及非副本 FF，纯首跳换源难消 `>2.2ns` 缺口。但立刻混入组合重映射会破坏 `AliasFeedback` 的固定原语契约。
- **下一步（有约束、可证伪）**：
  1. **原厂验收门禁**：待 `002` 结束后，先对 `001`/`002` 优胜解跑完整原厂 I/O STA 与编码读回，核验真实收益（绝不把反馈预测当签核）。
  2. **单次有界覆盖/嫁接消融（<=64 事务、<=1800s）**：固定原语与布局，仅改三点：① `recipient` 保留既有路由树做增量嫁接，不再拆树重连；② 对负裕量端放开 `min_sinks=1`、`max_depth=20`，扩至 `max_families=8, min_fanout=12`；③ 对 `sta_endpoint_audit` 具单节点捕获的合法 FF `CE/D` 评估准入。
  3. **证伪判据**：若原厂完整 STA 实测累计 `ΔWNS < +0.25ns`（或成交饱和后仍卡在 `<-2.0ns`），即证伪“仅靠既有副本路由重分配可逼近 110MHz”；再单立新阶段引入关键锥复制/重映射 + 局部布局路由 + 原生时序重捕获 + `folded` CEC。

## 4. 最应补充的一个负例测试

建议在 `tests/test_route_branch_transfer.py` 新增 **`test_shared_physical_sink_cotenant_and_exact_depth_conflict`**（补齐当前对 `reconnect_source` 精确切深模式的零测试覆盖）：构造含两物理接收端 `{R, S}` 的子树，令 `R` 同时绑定合法端 `lut_ok/I0` 与非法端 `alu_bad/CIN`（或保留端 `lut_keep/I0`），断言：
1. 整组改接时，`AliasFeedback.update()` 抛 `Non-alias input or fixed-state change`；
2. 仅改接 `lut_ok/I0` 时，`prune()` 在 `donor_record` 保留 `R`，`reconnect_source(..., min_depth=d, max_depth=d)` 因 `R` 属 `foreign` 返回 `fragment_occupied` 拒绝接管（并建议在 `AliasFeedback.update()` 补加“禁止异网共用同一物理 sink 节点”断言一并验证）。
