# 公平基线、统一组合与下一实验评估

## 1. 缺失门禁与非必要重造
- **已确立**：原厂STA/-bit获准，SYN+PR作标注基线。int(100MHz/core_inputs)已证RTL→.vo初态SAT+归纳、19引脚/GSR/CRC、全CFU/端点/7070父边位及164.545MHz四类STA(134.432,hold+.021)；UART/TMDS留12/86静态端点与纯LUT组未采；Vex≥75MHz败hold(63.729禁作分母)，缺硬核证明与两ERR核验。
- **缺失门禁(待定，禁豁免)**：①同周期SYN+PR及Vex hold闭合点(修scout TMDS路径)；②Vex 8→10 SDPB与MULTALU无界初态+归纳证明(仿真不代)及两ERR-bit电气/VCCIO核实(保284端口)；③合成账本：汇总.vo等价、全I/O零预算/延迟、-bit PIO/VCCIO/GSR、recorded_controls四类STA与库存守恒，禁改通用False。
- **非必要重造(推断)**：强求自研FSE先解出原厂内部进位/长线/全I/O位(如TMDS 86端点与all_io)，混淆了自研导出验证与分母公平性；应拆分基线契约与解码覆盖。

## 2. 通用组合与目标相对评估
- **证据与推断**：003复用200MHz网表；150MHz达143.1/134.8/139.2因setup败hold，Vex 75MHz差-.085ns留69违例，50MHz跌至63.729；scout输入IOREG致hold-1.389。**推断**：correct_hold在setup违例下失灵，100/50MHz过松压低Fmax，禁直接冻结134.432。
- **通用组合(无分支)**：以200MHz+P1探针取F0(非基线)；统一跑{0.88,0.93,0.97}F0含100MHz，交叉P1(core_inputs)与P2(-oreg_not_in_iob)，同周期跑gwsyn(map -t)+gowin(3/2,fan23,hold1,-bit)。同内存态验初周期与1.224F_rep，须四类位移/库存/物理态不变、Hold=0、非setup与零I/O延迟全过且|ΔF_rep|≤.0005；取过门禁**最高F_rep**为F_base，自研同按1.224F_base考最小比值≥1.20。

## 3. 位流语义范围界定
- **界定**：规范准许原厂STA/编码；013证无-bit则物理PIO全零。**全芯片位流全解码(头尾命令、未用模式与全熔丝)属独立自研目标，非冻结分母条件**。分母-bit仅需核实固定引脚PIO/VCCIO/GSR/CRC一致且STA物理态不变；FSE/CLS/SEL/时钟解码为**自研子集**强制门禁。

## 4. 1小时高优实验
- **待定与步骤(≤3300s,<40GB)**：测清同周期拐点是否超136.9/134.4/136.4、Vex 66-73MHz×{P1,P2}能否跨-.085ns得~70-75合格点及两ERR默认态。①预留空间(现39.36GB)即时清落选.pr，修TMDS路径(~3m)；②跑四设计{0.88,0.93,0.97}F0×{P1,P2}同周期SYN+固定布局-bit三阶段1.224F_rep校验(~25m)；③证前三新最高点初态+归纳至.vo，核Vex两ERR PIO，余缺收敛至SDPB/MULTALU证明(~10m)。
