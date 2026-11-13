# 端点分类纠偏与协同优化建议038
## 1. 观测事实与037纠偏
- 纠偏037(UART 197.0MHz)：037误归因未用IOREG且误列63.7MHz分母；实UART已有9个DFFCE IOREG(rx*离核拒重迁)，前10中9条由IOREG->OBUF发起(+0.001ns,tNET=0)，仅tx/tx_ready在核。
- 三设计与018：int(167.6,0IOREG)前10含7条FF->OBUF(+0.004ns)与3条FF->FF(+0.216ns)；4准入，q2/3卡tile 245/63(016仅读回)，q1卡动态RESET。TMDS(124.0,0IOREG,6准入/4卡tile)前10全为IBUF(ng_pad_6)->FF(-2.08ns)。Vex(90.4,1IOREG,#2 ng_pad_135->IOREG)混FF->FF/SDPB/输入，输出回核(违Q独占)或IOL*未标定致0准入。018独立FF均过(int 7-PIP修hold+0.002ns)Fmax未变；TMDS批119.5被拒。目标>=1.2x公平基线(禁63.7MHz)。
## 2. 机制评判(观测vs假设)
1. 同步展开+静态IOREG vs 动态复位：[观测] register_control_map单向折叠阻IOREG；CE=1时q1并RESET入私有LUT2成LUT3(RESET=0,INIT/级数不变)直用静态契约；CE动态需Q反馈，违Q独占OBUF。动态复位缺组合差分、SR[r,c,127]布线及同步setup/hold核验(现按recovery查)。[假设] CE=1展开更优，但外迁拉长上游(+0.216ns/IOB179A)且卡tile 245/63(q2+0.198ns)，须配上游。
2. 已IOREG化UART时钟/数据：[观测] IOREG->OBUF无捕获时钟且tNET=0，到达仅由T_clk+tC2Q+tOBUF定，无可缩逻辑；ClockDataModel拒DFFCE/PE与IO源。[假设] tx入IOR109A配短时钟分支可降到达，但前移时钟挤压D/CE建立、异步recovery与输入hold/removal。
3. TMDS/Vex可移FF映射：[观测] 冻结FF打包触几何上限；移TMDS输出时钟或迁dout无助ng_pad_6->FF反伤setup。[假设] 须建“可移FF+LUT/宽MUX/ALU重映射+增量带载布线+双角时钟/hold”：TMDS携FF($1650等)与锥向ng_pad_6收缩；Vex绕SDPB/ng_pad_135协同布局，回核输出仅在等价且减张力时复制影子IOREG。
## 3. 实验排序与采信门槛
- 排序：①int2float：CE=1展开(q1)+tile 245/63标定(q2/3)+上游迁移与增量布线，测+0.216ns上限；②TMDS/Vex：小步长可移FF+锥重映射+增量保树布线，经双角全图(含时钟重定标与修hold)选优导出；③UART：扩时钟模型纳recovery/removal与IOREG->OBUF，测tx入IOR109A配短时钟；④基线门禁：闭环母边/进位/核模式及Vex SDPB/DSP等价与ERR焊盘。
- 门槛：展开控制或复制FF须过SDK完整时序等价(含INIT/复位周期)及四类端点守恒(防018漏端点)；新tile/模式须具可逆FSE差分、自有PIP与时序查询全匹配(禁读回代准入)；定约束过四类STA/脉宽/全图/比特流核验，禁编造收益。