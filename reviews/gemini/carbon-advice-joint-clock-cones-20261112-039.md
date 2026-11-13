# 时钟与锥迁移诊断039
## 1. 实测与诊断
- [实测] 标杆UART 197.0/int 167.6/TMDS 124.0/Vex 90.4MHz(未冻,禁63.7分母)。019展q1迁6IOREG降157.744MHz(WNS-0.369/hold+0.005)：0014->IOR87A逻辑2.241+线3.667ns；0015->IOB142A线4.615/偏斜+0.876ns；q1留核(+0.201/+1.126)反优。020拉40LUT跌至131.201MHz(WNS-1.652/TNS-3.864)，线升至6.089/4.914ns。
- [推断] 否定“单拉LUT+最小时钟”：①首级FF(C176)与宽MUX(C167..175)冻东侧，单拉LUT向南/东垫致21列折返(C160->C171->C161)；②缩时钟削IOB142A正偏斜反伤建立，受FF->IOREG(-0.164)与IOREG->OBUF(+0.377)夹击，019纯调钟上限+0.106ns(~170.5MHz)；③对UART(tNET=0)/TMDS(ng_pad_6->FF)/Vex(0新IOREG,SDPB/FF主导)不通用。
## 2. 源码缺陷
1. ownpr_ioreg_cone.py：depth=3截断使0051(东垫3级/南垫4级)漏南锚，external剔反向movable负载致0；target缺上游驱动拉力，无视保护窗与冻结宏/FF断崖。
2. 时钟模块：①clock_closure未接FullClockSearchModel；ClockSlackModel仅查已报路径(28/61端点,双报于arrival_graph:226崩)，130行对launch=None的IBUF->FF保持(+0.005ns)误扣全局min(0,*fast)，他汇快>5ps即拒；②ClockDataModel:20,36,68拒非默认INIT/SDPB且单沿代双沿，FullClock:9拒DFFCE/PE(阻UART/Vex)；③slack_search按最短线序且绑前缀，难探捕获增延。
## 3. 通用替代与1h实验
- [最高杠杆/推断] 全端点双角多级流水+宽宏协同布局+净益IOREG：以bidir_timing全图AT/RT驱动FF/宽宏/LUT迁移与IOREG筛选。实测019中int的IBUF->首级FF建立+5.54~+5.90ns而保持+0.005ns：携首级FF+宽MUX+锥西南迁10~15列，耗<1ns输入建立即自修hold并削FF->IOREG长线，负益垫留核；同法在TMDS/Vex携FF与宽宏锥向ng_pad_6或SDPB/ng_pad_135收缩。
- [推断] 全图有用偏斜时钟+分叉解耦：接入修复版FullClock(IBUF delta=0、放行INIT、补DFFCE/PE与SDPB弧及脉宽门禁)，双向搜发射减延(兼顾建立与输入hold)与捕获增延；分叉锥配独立FF(018)与小锥根/影子FF复制(过SDK CEC)。
- [实验<=2700s] 先修IBUF hold误扣与双端弹簧，禁盲搜：A组在019跑修复版有用偏斜时钟测~170MHz上限；B组对int/TMDS跑同一“可移FF+宽宏+锥重心迁移+增量布线+有用偏斜时钟”(<=4候选)，过四类STA/hold>=0/CEC且超167.6/124.0。[不确定性] 共享前缀离散度与拥塞非线性待实测证伪。
