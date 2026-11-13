# DSP输出寄存器吸收、原生校准与四设计优化独立咨询（043）

## 一、源初值核查（INIT0伪影）与源绑定证书

1. **观察与直接证据**：本方VexRiscv映射网表中132个DSP后级`DFFRE`的`SDK_DEFAULT`（`INIT=0`）**确系后端FF映射伪影，源RTL与综合前展开均完全未定义（`x`）**。核查保留源输入`results/coopt_vexriscv_rebuild_001/VexRiscv_defined_outputs.v`（L1178-1188, L2373-2376, L5069-5086），`execute_to_memory_MUL_LL[31:0]`、`LH[33:0]`、`HL[33:0]`、`HH[33:0]`（`17×17`有符号积独立有效位32位，合计`32+34+34+32=132`位）均为无初值`reg`，且在无`reset`分支的`always @(posedge clk)`块内仅受`!execute_arbitration_isStuck`使能更新；前端记录`results/coopt_vexriscv_rebuild_001/synthesis/manifest.json`（L49-55，`elaborated_sha256=1da48468...`）亦证实`verific_bridge.py`提取的全设计`initial_values: 0`。综合前Yosys状态位为`x`，但映射阶段将131位拆成`LUT3(0xCA)`自反馈MUX加`DFFRE(CE=1)`（仅`mult_9406`第28位`slice$48298`保留`CE=1447`），再经`gowin_netlist_format.py:legalize_gw5a_aliases`与`prim_sim.v`具象化为缺省`INIT=0`。该`x→0`细化在`-ignore_gold_x`下对正向综合合法，却抹除了`packed.json`中的未定义血统，导致仅凭`packed.json`做后置吸收会误判为`0→x`倒退。
2. **源绑定证书与显式初值判据**：必须在**FF工艺映射前的`verific_bridge`边界**签发哈希绑定的状态位未定义证书，并扩展`scripts/dsp_output_register_pack.py`以同时识别`$dffe`及`$dff+$mux(Q,DOUT,EN)`反馈形式。在不归零DSP且保留`original_pair`检查下，源端无初值`reg`与`sdk_MULTALU27X18.v`的`out_reg_sync`同为`x`，可用`sat -set-init-undef`配合`-ignore_gold_x`完成初始拍验证并衔接硬核切点归纳。**若源寄存器确有显式定义初值（如`INIT=0`）**：因`GSRI=1`下DSP上电态未知（任意$s_0\in\{0,1\}^{48}$），除非在原始对上对全称量词$\forall s_0$证明**初态不可观测不变式**（如具备真实上电`INIT=0`的有效位链在`OREG`首次经历`CE=1`定义写入前严格屏蔽所有通往主输出与架构存储的扇出锥），否则**必须拒绝吸收**。注意Vex的`writeBack_arbitration_isValid`仅含同步`reset`而无上电`INIT=0`，若源端真定义了`INIT=0`，无复位第0拍即可泄露差异，绝不可放行。

## 二、小型原生注册态DSP校准要点

1. **官方参数差异**：对比`hard_instances.json`与`dsp_sdk_lowering/result.json`，官方4颗DSP共用`C_SEL=0, CASI_SEL=0, ACC_SEL=0, P_SEL=0, PRE_LOAD=0, MULT_RESET_MODE="SYNC"`及全部`DYN_*="FALSE"`，差异在于级联与流水级：`MUL_LH/HL`设`A_SEL="0", PREG_CLK="BYPASS", OREG_CLK="CLK0", OREG_CE="CE0"`并以`SOA[26:0]`级联驱动伙伴`SIA`；`MUL_LL`用`A_SEL="1"`取`SIA`并开单级`OREG`；`MUL_HH`用`A_SEL="1"`且同时开启`PREG_CLK="CLK0"(CE0)`与`OREG_CLK="CLK0"(CE1)`，一次吸收execute与memory两级寄存器。本方4颗DSP独立馈入`A/B`（`A_SEL="0"`），单级吸收仅需新增校准1种模式：`OREG_CLK="CLK0", OREG_CE="CE0", OREG_RESET="RESET0"`。
2. **隐藏控制、位宽与扇出约束**：
   - **双位控制脚**：`native_dsp_full_timing_001`旁路态下`CLK[1:0](671/672)`、`CE[1:0](231/232)`、`RESET[1:0](234/235)`均返回`nodes=[]`。开启`OREG`后，`ownpr_native_netlist.py`须按位区分激活脚`CLK[0]/CE[0]/RESET[0]`的物理节点与未用脚`[1]`的静默投影，并将`new_dsp`接入时钟树。
   - **有符号45/48位与SDK陷阱**：`sdk_MULTALU27X18.v`以`a_in[26]`、`inb[17]`符号扩展产生45位有符号积（`gw5a_dsp.py`对`≤26×17`无符号操作数高位补0），再经`m_out0[47:0]`将第44位符号扩展至`[47:45]`（`dsp_output_register_pack.py`现限`index<45`，通用化需放宽至48并核对符号复制位）；`O_CLK`组合块无`else`分支，参数须严守`"CLK0"/"BYPASS"`；`FB_PREG_EN`与`OREG`共享时钟/使能/复位，须保持`"FALSE"`；`SYNC`下`RESET=1`优先于`CE`。
   - **DOUT/CASO/SOA消费者**：SDK中`CASO`与`DOUT`无条件同源（`assign CASO=d_out`），`OREG`同时延迟全部48位，故单颗DSP全部已用`DOUT`位须原子性同组吸收、无组合旁路消费者且`CASO`零负载（本方库存已满足，`SOA`仅透传`ina`且未连）。
   - **后端缺口**：须解除`gw5a_dsp.py`与`ownpr_timing_endpoints.py`(L41)拦截，校准248项`ushort`属性差分、可逆`.fs`位增量，以及`CLK[0]->A/B/CE/RESET`建立保持、`CLK[0]->DOUT`(kind 10)延迟与`ownpr_hard_sta.py`最小脉宽检查。

## 三、替代架构对比与四设计ROI建议

1. **四设计最短板现实**：总目标要求四设计**各自**`>=120%`公平基线。对照当前公开CLI参考：UART `197.028/133.493=147.6%`（对100MHz候选`136.878`为`143.9%`）、int2float `167.613/134.432=124.7%`、Vex `90.380/72.800=124.1%`，而**TMDS仅`124.004/136.437=90.9%`（缺口约40MHz）且DSP数为0**。在未证实DSP主导Vex最差路径前，优先投入重型注册态DSP校准对全局瓶颈TMDS收益为零。
2. **首选方案A（零硬核风险：`0xCA`反馈MUX提至`DFFRE.CE`+独立FF输入+中点摆位）**：将131路`LUT3(0xCA)+DFFRE(CE=1)`重写为`DOUT->DFFRE(D, CE=1447, RESET=0)`（复用已校准`ownpr_ff_configuration.py`与Trial017/018独立FF输入，消除`slice$48298`冗余`LUT1`）。它**100%保留`INIT=0`、零新硬核/时钟树/STA风险、秒级闭合SEC**；且硬核`OREG`会把发射点锁死在固定`new_dsp`列，易拉长后级52位加法链`memory_MUL_LOW`布线（Trial019固定IOREG曾因暴露`3.67~4.62ns`长线致int2float退化`9.9MHz`），而可动fabric `DFFRE`可摆在DSP列与52位ALU链之间的最优中点（仅`MUL_HH`含二级寄存器，官方才用`PREG+OREG`；前置输入寄存器映射则会改动流水级或受限于`decode_to_execute_RS1/RS2`多扇出分叉；算术拆解成LUT/ALU只会劣化`3.48ns`硬核乘积速度）。
3. **高ROI主线B（跨设计可动FF摆位+有用偏斜+带载布线协同）**：TMDS固定状态位搜索已饱和于`124.004MHz`（线延迟`6.10ns`），应结合Trial020的`18.8ms`快速DAG评估器与增量布线反馈，放开关键锥可动FF与ALU/LUT联合摆位及双角Hold守门下的时钟偏斜，同时提升TMDS与Vex。
4. **验证加速C（硬核切点组合SEC）**：弃用7981万变量单片展开，在`SDPB/DSP`边界做结构化切点分解：外围逻辑做常规SAT/归纳，硬核内部直接拼合`0.66s`全地址符号转移引理与端口参数双射审计。

## 四、最小可证伪有界实验与判败准则

严格区分**候选识别、局部转移引理、完整源级SEC、硬件编码回读、全IO布线STA**五层证据，严禁越级宣称完成：
- **E1（保留模型DSP关键性快筛，`≤30s`）**：读取`vex_bidir_model_004`/`probe.tr`，量化`mult_9406..9409`前级（`RS->DSP->FF`，硬核延迟`3.32/3.48ns`）与后级（`FF->memory_MUL_LOW`）最差Setup裕量并对比全芯片WNS（`-1.973ns@110MHz`），同时普查四设计`0xCA`使能锥。**判败**：若DSP前后级裕量优于全芯片WNS `≥1.0ns`，立即停止本轮`OREG`开发；若后级`memory_MUL_LOW`紧于前级，禁止单独做单级`OREG`固定列吸收。
- **E2（通用`0xCA`转`DFFRE.CE`+可动FF中点摆位，`≤300s`）**：在验证通过的反馈MUX上提取`CE`并直连独立FF输入，跑完源/网表SEC、增量P&R、全IO原生STA与比特流审计。**判败**：若`CE`扇出布线致Setup退化、Hold`<0ns`或编码失配则回滚。
- **E3（单DSP注册态最小夹具校准，`≤240s`，仅E1证实关键或导入官方Vex基线必需时执行）**：单颗DSP对照旁路与`OREG_CLK="CLK0"`（必要时含`PREG+OREG`、`A_SEL="1"`），采集控制脚节点、248项属性差分、可逆`.fs`位及序贯STA/MPW表，含3项负控（错配`CE0/CE1`、未吸收并行`DOUT/CASO`负载、源显式`INIT=1`）。**判败**：若`CLK[0]`需未校准专用时钟开关或单核STA重建误差`≥2ps`，中止并入生产后端。
- **E4（源初值血统与切点SEC微基准，`≤120s`）**：在小型DSP+有效位夹具上验证`verific_bridge`初值导出与`-set-init-undef`检查。**判败**：若源`INIT=0`无屏蔽输出负控漏检或出现无法消解的三值`x`误报则禁止上线。

## 五、关键不确定性

1. Vex现有341条违例路径中，DSP乘加锥相对Cache/MMU/CSR/分支锥的真实排名尚未剥离，E1前任何DSP提速预期均属未证实假设；
2. 四设计公平基线尚未冻结（Vex硬核证明与更强候选待收口），最终120%目标线仍可能上移；
3. Yosys `sat -set-init-undef`在重汇聚控制锥上可能产生`0&x→x`三值保守误报，需靠已证复位/有效位遮罩引理消解，不可放宽初态门禁。
