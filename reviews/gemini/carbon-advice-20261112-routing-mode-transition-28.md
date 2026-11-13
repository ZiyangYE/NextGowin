# 双图增量复用审查、路由模式跃迁与非增量协同架构评估

面向四设计（UART/int2float/TMDS/VexRiscv）单一通用综合+自研P&R各超公平原厂基线 `≥1.20×`（评估频率 `= 基线×1.20×1.02`，优化四者最小比值；严禁项目分支、RTL补丁、开源芯片库及以 Vex `63.729MHz` 放宽校准值作分母；单次完整尝试 `≤1h`、工作区 `<40GB`），严格区分实测事实与推断，独立评估如下。

## 一、分离源/新配置图增量复用验证的正确性风险与未覆盖负例

- **【代码事实】**：[`ownpr_incremental_routes.py`](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/scripts/ownpr_incremental_routes.py#L34-L116) 通过 `source_validation_graph()` 临时开启旧 `OF0/1/3->DI0/1/3` 校验历史树，并在 `partition()`/`retained_branches()` 中按新图 `graph.check_pip()` 释放失效终端而不在新搜索图启用旧终端。
- **【正确性风险与4个未覆盖聚焦负例（推断/静态缺陷）】**：
  1. **源图自举授信与 `not missing` 漏检**：[`source_validation_graph()`](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/scripts/ownpr_incremental_routes.py#L36-L44) 未读取源 `packed.json`/`placement.json` 调用 `configure_graph()`，而是直接采信 `old_routes` 自身携带的 `OF->DI` 注入 `select_terminal_pips()`；且当 `not missing`（L39）时直接返回新图 `graph`。*负例1*：旧布局在 `(r,c)` 并无 `wide_ff`，但篡改/错配的 `old_routes` 含 `(r,c,'OF0','DI0')` 且新布局恰好将合法 `wide_ff` 迁入 `(r,c)`（或旧路由同 Tile 并存 `OF0->DI0` 与 `OF1->DI1`，违背 [`ownpr_wide_ff_packing.py`](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/scripts/ownpr_wide_ff_packing.py#L28) 单站唯一宽 FF 约束），现逻辑会误判源路由合法。
  2. **新迁入宏的静态/内部专用端口漏入 `reserved`**：[`bind()`](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/scripts/ownpr_route_design.py#L15-L64) 将常量控制脚（`CE=1`/`RESET=0` 对应 `CEg`/`LSRg`）、真值无关常量 LUT 脚（`A..D`）、`alu_constant_fold` 及无外扇出的 `mux_skip` 内部输出（`F0..7`/`OF0..2`）排除在 `new_nets` 之外，而 [`run_ownpr_timing_route.py`](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/scripts/run_ownpr_timing_route.py#L33-L37) 的 `reserved` 仅含时钟树与 `ioreg`。*负例2*：将 `RESET=0` 寄存器或带无关常量脚的宽叶迁入某冻结树/保留分支占用的 `LSRg`/`As` 节点（或与 `implicit_zero`/CLS 模式位潜在冲突），`partition()` 未释放，须待后端配置合并才报错。
  3. **有向等价类（Alias Class）中游入口引发 `expand()` 延迟崩溃**：`native_rrg_001` 将最多 3 个单向物理点 `m0->m1->m2` 聚为单一规范节点 `U`。*负例3*：`retained_branches()` 保留从下游 `m1` 进入 `U` 的旧支路后，`route(initial_trees=seeds)` 允许新汇点从 `U` 的上游成员 `m0` 引出 PIP；`validate()` 与 `audit_retention()` 均放行，直至导出阶段 [`ownpr_expand_routes.py`](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/scripts/ownpr_expand_routes.py#L37) 因 `m1 ↛ m0` 抛异常失败。
  4. **回退检查点单网盲区与迭代偏置膨胀**：[`negotiation_trees()`](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/scripts/ownpr_route_checkpoint.py#L42) 向 `safe_seeds()` 仅传单网列表致 `endpoints[node]-{name}` 恒空（且 `retained_branches()` 独立调用时不跨 `changed` 网累加 `blocked`）；同时 [`run_ownpr_timing_route.py`](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/scripts/run_ownpr_timing_route.py#L110-L146) 两级增量失败后全网回退继承 `iteration_offset=24`（`present=49`）与不对称 `margins`，首轮拥塞代价放大 49 倍压垮时序权重。*负例4*：部分树侵占零 PIP 新网端点且触发两级回退，验证重置迭代偏置与立判端点冲突。

## 二、实测瓶颈归因：定宽切口枯竭与全量重布毁树的本质

- **【实测事实】**：余子式映射修补 `LUT7`、`I3` 快脚（`.260` vs `I0` `.521ns`）与常量叶定时假脚后，四设计各 3 宏 CEC 通过，但 TMDS 24 个宏内候选与仅有的 4 个跨宏候选（2 根）全未接受；单未配对宽宏局移 4 CFU 达 `115.463MHz`（`cell 4.047ns + wire 6.460ns`，`166.999MHz` 下 setup `-2.673ns`、hold `+.048ns`，66 端点模型误差 `<.000624ns`）；2 个含寄存器宽宏全量重布对照在捕获时钟不变（`.614+1.222ns`）下退化至 `90.256MHz`（`wire 7.117->9.232ns`）。Vex 自研捕获 284 IO（含未用 `ERR` `IBUF`）与原厂捕获 282 垫片仍存物理对齐缺口。
- **【机制推断】**：① **多锥算术低收敛突破 `k≤7`**：TMDS 关键锥（`IBUF->计1/视差比较加减->DFF`，13~15 级、`0 ALU`）跨宏展开后真支撑跃升至 `8~14` 变量，单 CFU `MUX2_LUT5..7` 无法吞并，且 74 个孤立宽宏锁死整块 CFU 加剧绕行；② **路由模式断层**：硬冻结增量路由锁死通道，而全量重布丢弃优质旧树，且无反馈时数据网一律 `criticality=0.5`（按扇出降序先布大扇出非关键网）或改结构后未知脚全置 `1.0` 丧失区分度，空载边延搜索导致关键线暴涨 `+2.115ns`。

## 三、两类非增量竞争方向对比与路由模式跃迁

1. **方向A：通用前端算术保留（`ALU mode-2`）+ 低效宽宏降阶与混合 CFU 打包**
   - **【机制与边界】**：前端统一保留加减/比较/计数为 `mode-2 ALU`（配已校准动态头尾适配），将非净赚孤立 `MUX2_LUT6/7` 逆向拆回共享 `LUT4` DAG，并统一启用已校准 `ALU`（槽 6..7）、`LUT5/6`（槽 2..7/4..7）及同控制组双 FF 混排。**不假设进位零延迟**：真实物理代价由 `ExtendedTimingModel` 的 `ALU` 单元弧、`within_cfu`/`east_cfu_bridge` 延迟及头尾 `ALU+LUT1` 开销决定。直击 TMDS `0 ALU` 与空槽稀释，但对已有 `382 ALU` 的 Vex 单独提频有限。
2. **方向B：全局多选择共享 DAG + 直通 `LUT1` 消除与受控状态迁移/复制**
   - **【机制与边界】**：消除 [`ownpr_place.py`](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/scripts/ownpr_place.py#L109-L115) 强插的 `ng_ownpr_pass` 直通 `LUT1`，结合跨锥前缀 Choice-DAG、时钟预算内内部 FF/宏成组迁移与关键发射态复制。注：**跨逻辑重定时（Retiming）受限明显**——固定接口周期禁止跨 `IBUF`/`OBUF` 移位（无法切短 TMDS `own_i_0528/I->own_i_0012/D` 主输入单拍路径与视差单拍自环），且 [`ownpr_ff_configuration.py`](file:///C:/Users/JackC/Documents/ChatGPT/NextGowin/scripts/ownpr_ff_configuration.py#L100-L101) 仅校准默认 `INIT`。
3. **共通枢纽——走廊软冻结协商路由（Routing Mode Transition）**：两方向均须废除“全冻 vs 全拆”二元对立，建立中间态：保留改动包围盒外扩走廊之外的合法旧树（经物理源/汇签名跨网名迁移），仅解锁走廊内与阻塞网以 `iteration_offset=0` 进入协商，每轮用已验证 `<.000624ns` 双角模型与带载线延重算 `sink_criticalities`，若旧关键树退化则加权回护。**独立结论**：须以该路由过渡层为底座，融合**方向A（破 TMDS 算术膨胀）+ 方向B 的直通消除与受控状态/宏联移（破 Vex 长互连）**，方能共抬四设计最小比值。

## 四、决定性有界实验（≤1h，<40GB，严守全门控）

预留空间后在单次 `≤3600s` 预算内（不预设实测耗时）执行两阶证伪实验：
1. **阶段1（路由过渡验证）**：补齐源布局 `configure_graph` 校验与静态端口 `reserved` 后，在已生成的“2含态宽宏移动+新时钟树”控制样本上运行**走廊软冻结+在线双角裕量重加权路由**。若最坏线延仍较 `7.117ns` 恶化 `>0.3ns`，即证伪纯走廊软冻结，须在 A* 搜索内直接引入多分支带载增量惩罚；若恢复并超越 `115.463MHz`（hold `≥+0.048ns`），即确证状态移动不毁树。
2. **阶段2（四设计统一非增量消融）**：无项目分支统一开启“原生 `ALU` 保留 + 孤立宽宏降阶/混合 CFU 打包 + 冗余 `ng_ownpr_pass` 消除 + 走廊协商路由”，完成初态+归纳等价证明、配置编码及原厂四类 STA（同步核查原厂未使用输入垫片默认配置以对齐 Vex 284/282 IO，推进 Vex 高频 hold 合规公平基线与 `SDPB/MULTALU` 状态映射归纳证明）。若 TMDS 总到达未能从 `10.506ns` 压降 `≥1.5ns` 或任一设计出现 hold 违例/比值退化，即证伪前端算术+混排假设并转向视差环路代数展开（Carry-Select/状态前瞻复制）。
