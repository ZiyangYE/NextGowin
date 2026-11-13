# 四设计1.20x联合目标：从零质疑与开放架构探索（18）

## 一、从零质疑现有路线与#17前案、无望方向及物理上限

- **【观察】局部切口逼近超时且覆盖受限**：`results/vexriscv_wide_joint_trial_001`（`completion_audit.json`、`engineering_summary.json`）冻结FF/硬核/时钟净增115 LUT，原生Fmax仅由`89.273`升至`90.380MHz`（`+1.24%`），110MHz setup `-1.973ns`/TNS`-275.506ns`/341违例，hold`+0.133ns`，2798+2798端点误差`<0.000626ns`，全流程耗`3436.4s/3600s`（工作区`39.11/40GB`）。`scripts/audit_joint_boundary_coverage.py`统计setup违例：FF `D` 188/`-157.30ns`、`CE` 58/`-38.03ns`、`reset` 11/`-11.35ns`、BRAM地址/数据/控制`38+26+2`/`-58.61ns`、IO 18/`-10.22ns`（非D端占`153/341=44.9%`）。`scripts/ownpr_global_timing_frontier.py`仅按静态扇入从4123个普通LUT选40根覆盖`93/104`组（11组零可达），不保关键路径与收益。
- **【观察】四工程异构瓶颈与原厂物理剖面**：据`results/coopt_comparison_20261104/summary.json`、`results/vexriscv_comparison_001/analysis.json`及`probe.tr`：① **TMDS表示坍塌**：历史原厂hold闭合于`136.437MHz`（`105 LUT+37 ALU+0 MUX`），自研`tmds_wide_ff_hold_002`为`0 ALU+311 LUT+156 MUX`，仅`112.368MHz`（`0.82x`）；② **UART零数据线墙**：前8路径`IOB->OBUF`数据线`0.000ns`，总延`5.075ns`全在`clk(2.216)+C2Q(0.331)+OBUF(2.528)`，邻核`R108C181`（Path 9）亦需`5.015ns`；③ **int2float输出留核**：前5路径FF留`R102C162`多付`1.290ns`线延致`5.966ns`（`167.613MHz`）；④ **原厂VexRiscv得失**：原厂凭`375~389 ALU`、`SDPB`、`MULTALU27X18`寄存及`59`个I/O FF少用207个FF，且无自研`427`个`ng_ownpr_pass_*` LUT1与`467`个`ng_hold_buffer_*` LUT4；但将停顿/PC网`n19403`（扇出298）与`decode_to_execute_PC`（扇出172）两级串联升入全局`LW`脊线，净耗`3.814+3.677=7.491ns`自毁至`72.52~75.46MHz`，且输入直连核内FF致`-0.754ns` hold违例。公平原厂基线`B_i`（`evaluation MHz=B_i*1.20*1.02`）仍为空（`four_design_joint_policy_20261112.json`）。
- **【推测】前案批判、无望方向与物理上限**：① **否定#17唯一主线**：未消解4294-LUT冗余前跑2~3轮全局重布局与6607网Python协商路由极易超`3600s`失败，且纯P&R迭代救不了`0ns`数据线的UART与`0 ALU`的TMDS；#17称“核/IOB移动破UART瓶颈”已被Path 9（`5.015ns`）证伪。② **三条无望方向**：继续固定FF/硬核/时钟做LUT/宽切口搜索或`frontier`调度；盲目效仿原厂把级联关键控制锥升入`LW`（单跳`~3.7ns`串联必恶化）；保留直通LUT1与hold缓冲LUT4直接硬跑多轮全网重布。③ **物理上限与不确定性**：`0ns`外部I/O契约与`PRIMARY`时钟下，底边输出被`tCLK_launch+tC2Q+tOBUF`硬锁在`197~205MHz`；若公平原厂UART超`166MHz`，不压发射时钟延迟则`1.20x`物理不可达。`B_i`锁定前，原厂修输入hold后的真实分母仍不确定。

## 二、独立发散的四条替代架构与可证伪实验（不承诺20%收益）

> **前置零步（公平基线锁定）**：先用`scripts/native_project_synthesis.py`+官方P&R在同源RTL/固定引脚/`0ns`全I/O/`hold>=0`门控下审计四工程（`<300s`），固化`B_i`与真实`min-ratio`短板。

### 1. 表示层算术进位保护与顺序/硬核边界吸收（零外环网表减法架构）
- **【观察】**：VexRiscv已证专用`ALU`链仅`0.045ns/bit`（`tNET=0`），但综合将TMDS算术打散为`0 ALU+156 MUX`（`S0->O 0.227ns`+普通线）；`ownpr_place.py`单FF保守打包插入`427`个直通`LUT1`；4个`MULTALU27X18`全处`BYPASS`（`ownpr_hard_timing_model.py`未验寄存模式）。
- **【推测】**：布局前做通用结构减法：① 对`>=3bit`加减/计数锥禁打散为MUX树，强制走`ALU`链并将晚到控制信号Shannon提升至顶层`I3`快脚；② 校准CLS全8槽独立`DI`直入以删除`427`个直通`LUT1`，并将同控制集后级FF吸收进DSP `OREG_CLK="CLK0"`与双FF CLS，先缩网表再单次布线。
- **可证伪实验与失败判据**：单次`<400s`在TMDS启用进位保留，并在小切片验证`DI`直入与DSP寄存吸收。**失败判据**：TMDS原生Fmax未从`112.368`升至`>=135MHz`，或引发CEC失败、原生STA误差`>0.002ns`。
- **缺失数据与风险**：缺全槽无伴生LUT之`DI`熔丝图及DSP寄存模式248项属性/时序表；风险为进位链列向占位挤压局部槽位及CEC流水网名映射失配。

### 2. 关键汇快速影子树 + 大宗/`LW`单跳分流与硬核锚定（高扇出拓扑解耦架构）
- **【观察】**：VexRiscv扇出172~298停顿/控制网驱动大批`CE/reset/BRAM`端；自研单棵普通树支路达`2.7~3.8ns`，原厂两级串联`LW`则耗`7.491ns`。
- **【推测】**：放弃按扇出阈值克隆与串联`LW`，按裕量二分负载：① 同一高扇出源仅对最差`k<=12`个setup关键汇（如`BSRAM ADB`前级）克隆1个低载影子驱动走最短局部线（`<0.9ns`），其余200+非关键`CE/reset`汇剥离至缓冲慢树或非串联单跳`LW`脊线（卸除拥塞且不串联吃延迟）；② 布局单次引入`(CLK,CE,SR)`与`BSRAM/DSP`扇入锚定引力。
- **可证伪实验与失败判据**：固定布局仅对VexRiscv最差5条`fanout>80`控制网做“关键影子树+大宗隔离”增量重布（`<500s`）。**失败判据**：`CE+reset+BRAM`子集TNS（现`-107.99ns`）改善不足`25%`，或克隆拖慢上游源点setup。
- **缺失数据与风险**：若用单跳`LW`分流大宗负载，缺`LW`入口MUX/脊线熔丝及`resolved_parent_tree`模型；风险为中段克隆放大前级扇出及`BSRAM`列周边局部拥塞。

### 3. 合法时钟分支有用偏斜与四边双向`IOLOGIC`共优化（时钟—引脚边界架构）
- **【观察】**：`ownpr_ioreg_macros.py`仅支持底/右边`OBUF`单向打包；VexRiscv靠`467`个`ng_hold_buffer_*` LUT4硬顶输入hold；int2float输出留核多付`1.290ns`线延；UART卡在`5.015~5.075ns`时钟+缓冲硬底限。
- **【推测】**：协同调度时钟树与四边`IOLOGIC`：① 补齐四边输入及顶/左输出`IOLOGIC`，输入FF打入引脚侧直接消去`467`个hold缓冲LUT4，输出端竞争`IOB FF`与邻核FF；② 基于`ownpr_clock_graph.py`多分支引入受控有用偏斜：为输出发射或关键源选快时钟分支（唯一能压缩UART `tCLK_launch`破`200MHz`墙的合法机制），为输入或关键汇选慢分支。
- **可证伪实验与失败判据**：`<300s`在int2float/UART实测邻核/IOB竞争加时钟快分支重定锚，并探针校准1组输入`IOLOGIC`。**失败判据**：int2float最差输出未从`5.966`降至`<=5.25ns`，且UART在`hold>=0`下仍`>=5.00ns`；或跨分支诱发新hold违例。
- **缺失数据与风险**：缺四边输入及顶/左输出`IOLOGIC`熔丝与setup/hold表、各时钟分支双角延迟谱；风险为双角偏斜比失配引发核内FF间hold违例。

### 4. 单通零外环ZSA时序预算分配与原生批查询（反多轮迭代编译提速架构）
- **【观察】**：`ownpr_route_design.bind`对全网赋常数`criticality=0.5`；而`NativeDeviceSession`逐树Python查询令单次全流程逼近`3600s`红线。
- **【推测】**：弃用#17高超时风险的多轮外环，改走单通零裕量预算（ZSA）+原生批查询：布局前由本征延迟与曼哈顿下界一次解算边预算`B(e)`，单通驱动预算布局与逐汇点`sink_criticalities`路由，并将`delay_pointer`改为进程内批查询，压回`<1500s`安全区。
- **可证伪实验与失败判据**：固定VexRiscv当前布局，仅把常数`0.5`换为单通逐汇点裕量权重重布并测批查询耗时（`<600s`）。**失败判据**：原生setup WNS改善`<0.20ns`且路由+查询耗时降幅`<35%`。
- **缺失数据与风险**：缺静态线延下界拟合参数与批查询驻留接口；风险为静态预算高估局部绕行能力导致非关键网挤压关键通道。
