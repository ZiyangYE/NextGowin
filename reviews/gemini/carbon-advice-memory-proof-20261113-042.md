# Vex硬核形式模型与源码等价性评审
## 1. 库存与SDK忠实适配器规范
- **库存与规范**：①Gold含8初态x阵列；vg映10对称SDPB(W∈{8,16,32},BLK_SEL=0,SYNC,RESET=0：DCache4×8/READ_MODE=1并两级FF,1W2R复2×32,ICache拆2×16,双Tag扩32)，精确vo折半W=32成14块；4个MULTALU27X18全开OREG=CLK0,MUL_HH并开PREG=CLK0/OREG_CE=CE1与SOA→SIA级联(非全旁路)。②SDPB字阵列[16384/W][W]双射INIT_RAM_00..3F，显式bp=pl=0、DO[31:W]=0；写CEA&(BLKSELA==BLK_SEL_0)&GSRO，W=16/32取ADA[1:0]/[3:0]字节使能(忽[3:2]/[4]，W=8忽[2:0])；读忽ADB低位，RESET优先OCE/CEB双清bp/pl保mem，!RESET时OCE锁旧bp(2拍读，CEB=0仍推)，读使能锁旧mem(同拍同址Read-First)；超域generate报错禁黑盒/豁免；DSP全参可综合化。
## 2. 三路线对比与语义陷阱
- **路线与陷阱**：①A直降SDK(memory_map)：ezSAT拒$mem_v2，展~20万FF且8/10/14零切点致归纳败；B局部转移+组合对应(主推)：clk2fflogic+全14位$anyconst对任意合法时钟跳变无界证“SDK打包≡字阵列”，再按同步簇切点(双副本/2×16/零扩/双级读/DSP)闭环gold→vg→vo；C阵列SEC：整芯smtbmc 106.8MB易爆，但保Gate确定性用Skolem $anyconst址a*在SAT归纳不变量可合流B覆盖全址。②陷阱：透传/先写后读将Read-First翻Write-First；漏RESET>OCE/CEB或CEB=0/OCE=1毁2拍读；GSR.GSRO失配、DSP缺else锁存器与组合<=；DSP无initial=0，GSRO=1+set-init-undef留x经LUT重汇聚致ignore_gold_x伪败，未标定禁擅设0。
## 3. 保强状态匹配、前提假设、判别实验与排期
- **保强匹配**：保clk2fflogic、284口4步original_pair -ignore_gold_x基例与equiv基例+归纳；禁清零Gold、禁删初态SAT、禁Gate非确定、禁豁免。仅证明视图隐被吞并名(如入bp读FF)；引理(尤1W2R双副本∀a,s[a]==s0[a]==rf[a]防失步、2×16拼、低22位、bp及DSP态)须同过4步ignore_gold_x基例与归纳，零无证assume。
- **前提/假设/实验/排期**：①前提：GSRI===1、哈希锁、284口全测、零黑盒、超域拒。②假设：DSP上电态(0对x)、10→14折半等价、切点+a*收敛。③有界实验(≤120s)：E1用clk2fflogic任意交错时钟/复位跳变+全址$anyconst a*跑6步基例+4步归纳证sdk_SDPB≡适配器，6负控(Write-First/OCE越RESET/ADA错/BLKSEL错/INIT翻1位/DO高非0)全拒；E2切DCache(5/6块含READ_MODE 0/1)同测B与C(smtbmc --nomap)比耗时。④排期：S1适配器+E1+DSP初态核验；S2证gold↔vg；S3证vg↔精确vo。
