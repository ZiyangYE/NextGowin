# UserCode差异、命令复现与基线评审
## 1. 观测与推断（零Fmax晋升）
- **观测**：①前三设计公开与重建fs的33245440帧位、21872帧CRC/尾及余头尾全同，唯21886公开0x0A0000000000{4215,94EB,ABCB}(同CheckSum/UserCode)对重建低16位0(UserCode=0,CheckSum同)。②SUG100§4.3.3载下载校验、默认全0；公开-user_code default，cfg无该行，重建cmd.do(16行)漏-cfg。③Vex新core_all报73.248MHz(公开72.800)，较公开cmd.do(24行)还漏-convert_sdp32_36_to_sdp16_18及-ph/裸-o/-init_all/-cst_error/-retiming_resource/-clock_route_order/-global_freq与原sdc。零Fmax晋升。
- **推断**：①0x0A系UserCode字；传-cfg缺省时DLL以CheckSum回填，裸DLL未调留0(合GWTE双分支串)。②仅Vex含8/10 SDPB，漏SDP折半项直改BSRAM布局布线，系73.248偏72.800的首要确定性嫌疑。
## 2. 物理同一性边界与必排例外
- **界定**：全帧位/CRC及余头尾全同时，0x0A属下载校验寄存器而非SRAM配置位，可与物理同一性分离，禁豁免尾/源/物理门禁或掩码代全字节复现。必排例外：①回读耦合：vg/vo零实例USERCODE/JTAG/GOCONFIG/SEU回读原语；②cfg巧合：SUG100示vcc改延时且FixedPIO仅采16/81项，裸DLL不代显式-cfg；③格式别名：限固定头SHA与21872未压缩帧，21886高32位须0x0A000000且余6尾行全同；④漂移：去注释vo与初态tr包络须同公开CLI。
## 3. 最小原生受控实验与负对照
- **实验(锁vg/cst/sdc/DLL哈希，禁改位流，保CRC/源到vo/快照)**：①A裸DLL复现0；B单加公开cfg验缺省翻为CheckSum且前三全文件0位差；C按公开24行cmd.do+原sdc同内存跑初周期与1.224*F_rep四类STA；D副本cfg加set user_code = 0x12345678，验帧/CRC/CheckSum/余头尾不变，唯21886低32位与注释变该值。②负控：N1翻余6尾行任1位或21886高32位拒；N2缺省!=CheckSum或显式!=声明值拒(禁通配)；N3翻1帧位或移单胞触发帧/CRC/CheckSum三拒。
## 4. Vex归因与公平基线冻结门禁
- **归因与冻结**：①禁先归因随机性：Vex跑两次独立进程C组(含SDP折半等24行+原sdc+cfg)比对公开fs/vo/tr；两次0位差复现72.800即证确定性(73.248因漏参)，同命令/路径多进程仍异方凭N>=3判非确定性。②全满前禁冻：统一无分支组合保100MHz core_inputs强候选(136.878/134.432/136.437)取合格最高分母；全字节同原布局过1.224*F_base四类位移/Hold=0/库存/零IO/快照不变；补Vex 8->10 SDPB(含折半)及硬核模型过展开→vg→vo初态SAT+归纳；闭环两ERR垫、iobuf81项及源时钟/GSR；自研Fmax不变。
