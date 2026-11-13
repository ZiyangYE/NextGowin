# 孤儿FF槽位迁移布线失败根因、奇槽校准与四设计协同建议（044）

## 一、9条孤儿FF网布线失败的独立根因判定

1. **证伪“物理Q/F复用争用、源前沿死锁与种子独占”假说**：核查`results/native_pip_encoding_011/native_pip_writes.json`与`scripts/ownpr_device.py`（L73-126），CFU（类型17）内`F0..F7`（32..39）与`Q0..Q7`（40..47）为独立源节点；`Q0..Q5`奇偶槽完全对称，均具23条出向开关盒弧（含12条1倍线、4条2倍线及4条`N8/S8/E8/W8`八倍长线，多为0比特隐式默认输入）。`nets.json`显示9对失败孤儿的伴随LUT网（`net_1632/1660/1673/1701/1715/1726/1962/2056/2066`）全系`source==sink==F0/F2`片内直连（0条开关盒PIP），零占用外部出向MUX；`route/manifest.json`亦证实`provably_trapped_roots=0`。同时，`run_ownpr_timing_route.py`第3阶段全可变回退已设`initial_trees=None`（`seed_owners={}`），`blocked_nodes`仅含379个时钟/IOREG点，第2轮过载节点已清零（`28→4→0`），但9条网仍在反向`uphill`与正向`downhill`回退中双双触顶`100001`，故并非种子硬锁或局部拓扑死路所致。
2. **真正根因：“源改名清空长跨度主干种子”叠加“漏传走廊引导与启发式欠权”**：`prepare_dsp_oreg_candidate.py`（L47-55）将18对孤儿`(lut,ff)`由`2g+1`移至`2g`，使源节点由`Q1/Q3/Q5`变为`Q0/Q2/Q4`。在`ownpr_incremental_routes.py`中，这既改变`signature`将其移出`frozen`，更触发`retained_branches`（L91）源集合相等检查，**彻底丢弃原Steiner树主干种子**（反观仅汇点变更的`net_1447/2257`仍保留`537/458`节点主干）。按`node=(r*182+c)*317+id`解码`nets.json`可见18条孤儿网的几何分化：失败的9条网（`net_1633/1661/1674/1702/1716/1727/1963/2057/2067`，对应`slice$46622..46948`，源处行`73..77`、列`73..82`）的3个LUT汇点均含0跳自反馈`A0/A2`（秒通）、中距汇点（14~39格）及**西侧远距`B0`汇点**（列`28..51`、行`77..94`，如`5439404=(94,51,B0)`跨48格、`5375053=(93,30,B0)`跨66格、`5086266=(88,29,B0)`跨62格）；其余9条成功孤儿网（如`net_1991`最远仅6格）皆为局部短跨度扇出。致命触发点在于路由调用策略漏配：Vex卫冕路由（`ownpr_joint_lut_search.py` L127）启用`heuristic_scale=0.03, root_arrival_weight=1.0, guided_corridor=True, guide_edge_delay=0.05`；而本轮两轮路由虽传`--guide-edge-delay 0.05`，却**漏传布尔开关`--guided-corridor`**（两份`manifest.json`均为`"guided_corridor": false`，`progress.jsonl`全程`"guide": null`，该延时参数完全空转），且`TimingRoutingGraph`基数`0.001`乘`--search-bias 5/12`后`heuristic_scale`仅`0.005/0.012`，远低于单跳实测代价（`0.2~0.6`外加6154条`warm_checkpoint`存量树的拥塞罚项）。缺乏几何走廊束缚的A*退化为近似Dijkstra球面盲搜，跨34~66格（覆盖逾20万线网节点）必触`100000`步上限；恢复轮`bias=12`下35格汇点`3783106`恰于`99792`步擦边连通，直接证实系搜索视野耗尽而非物理不可达。

## 二、保留奇槽的效果、现有校准边界与缺失的最小可逆测试

1. **保留原始奇槽可直接规避该失败**：9条失败网的3个汇点均未被移除。保持18对孤儿原位留守奇槽`2g+1`，其`A..D/F/Q`物理节点与卫冕布局完全一致，无汇点变更者凭相同`signature`直接留在`frozen`零重布复用；即便局部汇点变动，因源`Q{2g+1}`未变，`retained_branches`亦完整继承原长距主干。同时仍须补齐路由脚本的`--guided-corridor`参数，确保新增DSP/CE网正常收敛。
2. **现有校准尚不足以放行“非空CFU奇槽单住”**：`native_ff_attributes_paired_001`（`ownpr_ff_configuration.py` L17-68）仅校验内存态`convert(0x558700)`属性（`full_bitstream_verified=False`），单FF用例其余7槽全空，`multi_records`仅测偶槽`(0,2)/(0,4)/(2,4)`无配对LUT；`native_shared_ff_re_*`（`ownpr_shared_ff_packing.py` L14-38）虽具可逆`.fs`证据，但仅测类型17下`[2g,2g+1]`双槽同住且他组全空。尽管`DFFRE(INIT=0, RESET=0)`在`2g+1`单住与双住同出组向量`[9,12,15,5,7,...,17,17,...]`，但Trial017已揭示`generateFusemap`后置终结器`0x118ff40`会按槽位奇偶改写`CLS[11/12]`，且纯LUT组采用`CLS=[9,0,...]`，绝不可由孤立内存属性外推复合比特流合法性。
3. **缺失的最小原生属性+比特流可逆测试**：复用`native_shared_ff_encoding.py`单会话`warmup→baseline→variant→restored`框架（零厂商P&R），针对候选实际覆盖的CFU类型（17及若涉及的221..228）、奇槽`2g+1∈{1,3,5}`（避开含`[15]=21`/宽MUX语义的槽7）、`DFFRE(RESET=0, CE∈{one,signal})`，构造`[2g空, 2g+1配对LUT4+DFFRE]`与同CFU他组分别驻留**纯LUT4、偶槽单FF、共享FF对**的组合（若需支持同组`2g`驻留独立纯LUT4，增测同半组共存例），合计约12~18例。判据：`isDirectConn`正反校验通过、终结器后`4×19`属性吻合、`variant^baseline`严格等于`LUT(2g+1)∪CLS(g)`预期FSE位（`missing=unexpected=0`）、`restored`全载荷逐位复原，并附错槽与1比特翻转负控。

## 三、替代合法化方案对比

1. **路由可行型偶槽合法化（零新校准备份）**：除补齐卫冕路由参数（`--guided-corridor --guide-edge-delay 0.05 --search-bias 30 --root-arrival-weight 1.0`）或利用原同瓦片`Q{2g+1}`旧树瓦片序列做几何走廊引导外，可将孤儿并入邻近同控制偶槽单FF拼成已校准`[2g,2g+1]`共享对，或向3汇点质心西移（列`55..65`）落入空偶槽以减半西向长线跨度。
2. **保留可动FF+提取`0xCA`至`CE`+独立输入`SEL`**：硬核`OREG`锁死固定DSP列，易拉长后级`memory_MUL_LOW`加法链布线（类似Trial019固定IOREG致int2float退化9.9MHz）。若固定列STA无净增益，可将131路`LUT3(0xCA)+DFFRE(CE=1)`改写为`DOUT→DFFRE(D经SEL独立输入, CE=1447, RESET=0)`（复用已校准槽`{0,1,3}`后端），天然保留`INIT=0`、免除未定义初态SEC负担，并允许FF摆在DSP与52位ALU链最优中点。

## 四、3200s预算内可证伪有界实验与四设计/TMDS优先级

严禁盲目调大`max_expansions`（维持`100000`不变）、严禁弱化合法性或源级SEC门禁；执行前先核查40GB磁盘上限（当前`38.85GB`）：
- **E1（卫冕路由策略复核，`≤180s`）**：在现成`placement_002`上保持`100000`步上限不变，仅补齐`--guided-corridor --guide-edge-delay 0.05 --search-bias 30 --root-arrival-weight 1.0`重跑路由。若9网收敛，立即完成导出与原生四类STA；**判败**：若Vex未超越`90.380MHz`（全局`WNS=-1.973ns`，而bank0原仅`-1.335ns`）或Hold`<0`，即刻中止`OREG`候选，不追加全芯片SEC开销。
- **E2（非空CFU奇槽最小可逆校准，`≤120s`）**：运行上述12~18例`generateFusemap`可逆测试与负控。通过则准入奇槽、生成**0孤儿迁移**候选并在60s内完成冻结增量布线与STA对比；若检出`0x118ff40`跨槽耦合异动则维持拦截，改用E1或共享对拼合。
- **E3（条件触发的全CPU源级SEC，`≤240s`）**：**仅当**E1/E2实测STA净提速且Hold合规时，在`elaborated_sha256=1da48468...`边界做`SDPB/DSP`切点分解证明；未闭合`complete_source_transplant_equivalence_proved`门禁前严禁晋升。
- **四设计全局与TMDS主线守恒（保留`≥2400s`主预算）**：打通Vex `OREG`仅解除单设计物理阻塞，不改善其余零DSP三设计。当前四设计各自`>=120%`总目标的最大硬缺口仍是**TMDS（`124.004MHz`距100MHz参考`136.437MHz`的120%线`163.725MHz`差约40MHz）**及公平基线冻结（Vex硬核证明与最强官方候选收口）。须将E1+E2严格封顶在`300s`内，将主轮算力集中于结合`18.8ms`精确DAG评估器与`guided_corridor`增量布线的**可动FF+宽MUX/ALU/LUT联合重摆与时序协同优化**，优先攻坚TMDS长组合与布线瓶颈。
