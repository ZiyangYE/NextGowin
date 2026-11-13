# 22号咨询：Vex反相器门禁审计与四设计通用提速建议

## 一、问题1：两处被删 Lut4 的真伪判定与证伪方案

- **观察**：`probe.vg` 含 4 个 `INV`。P&R 后 `probe.rpt.txt` 报告 `2466 LUT`，与 `inverter_normalization.json` 的 `before` 物理 `Lut4@NETLIST` 计数 `2466` 严格一致；调 `sepInvInst` 后跌至 `2464`。`probe.vo` 显示驱动 `DFFRE.CE` 的 2 个反相器导出为非物理 `INV`，而 `n18265_s2`（`INIT=0x00FF`）与 `n20295_s2`（`INIT=0x3333`）均导出为 `LUT1`，其中 `n18265_6` 直接驱动触发器数据端 `IBusCachedPlugin_fetchPc_booted_s0.D`。门禁拒绝完全正确。
- **推测**：二者均为**实际功能物理实例**，绝非导出回填虚拟对象。GW5AST-138 的 `DFFRE` 仅 `CLK/CE/LSR` 控制端具可编程反相多路器，数据端 `.D` 无硬件反相器，`~reset_d` 馈入 `DFFRE.D` 必占真实 `LUT4` 槽位。前端将无法并入控制端的 `INV` 转为 `Lut4` 并记入 `Design::getInvLutMap`（`0x89910`）；布线后越相调用 `sepInvInst`（`0x92f570`），首调 `0x8611d0` 清理中间表时误删该二真实 `Lut4`。时序极值未变仅因其不在最差关键路径或 `TmNode` 未失效，放行将致 CPU 启动标志断路。
- **建议（证伪方法）**：不凭 STA 判据，用四步只读/离线证伪：①调导出接口 `Lut4::getVirtualFlag`（`0x8ab20`）验证非虚拟；②调 `Instance::getInstanceSite`（`0x2aef60`）与 `phyInst->getLogicInst`（`0x23750`）验证其独占合法 CLS 槽位而非挂靠下游 LUT；③只读核对 `getInvLutMap` 与 `getRealMiddleInsDataMap`（`0x8a4c0`）归属；④隔离沙盒对比前后锥级真值表（CEC）与比特流帧，证实 `booted_s0.D` 逻辑失真。

## 二、问题2：原厂更窄例程可用性与 ABI 边界

- **观察**：反汇编 `separate_inverters.asm` 与 `gowin_exports.txt` 显示，`sepInvInst` 依次调 `0x8611d0`（前处理）、遍历 `Design+0x1f0`（`getInvList`）调 `0x907e50`（可合并判定）、`0x91bbc0`（引脚反相折叠并写 `MergedInvs`）及 `0x7278a0`（删实例）。上述四个内部子例程均未导出；而导出的 `Vlgreader::deleteInvConvertedLut`（`0x8ec5a0`）与 `restoreMergeInv2Lut`（`0x92c610`）强依赖解析期瞬态 `Vlgreader*` 实例，不接受裸 `Design*`。
- **推测**：按裸 RVA 强调 `0x907e50/0x91bbc0` 存在跨版本偏移漂移与内部账本失步风险；若直调导出 `Pin::setInvertInPin` 改网表却不联动刷新 `TmNode` 与 `ckt_info`，则属盲补内存，极易制造陈旧时序假象。
- **建议**：判定**不可**调用未导出窄例程或手工补丁内存。导出符号仅用于**只读审计**（`getInvList`、`getMergedInvs`、`getInvLutMap`、`getRestoreFlag`、`hasInvertInPin`）。

## 三、问题3：按网表特征决策与校准时序根治

- **观察**：`experiments/native_fixed_layout_sta.py` 在 `routing` 与 `runTimingAnalysis` 后先调了 `generateOutputFile`，随后才采样 `before`、调 `sepInvInst` 与 `restartTiming`。UART、int2float、TMDS 因无 `InvLutMap` 实体未触发误删；Vex 同时含 2 个控制 `Inv` 与 2 个转换 `Lut4` 而被拒。
- **推测**：`before` 中非物理控制 `Inv` 并非布线残留，而是 `generateOutputFile` 内部调 `restoreTheMiddleInsInNetlist`（置位 `getRestoreFlag`）为输出 `.vo/.sdf` 原地展开所致。
- **建议**：①正面回答问题3：**应当跳过**。严禁按工程名分支，一律由只读网表特征分派：统计未绑物理槽位（`getPhyInst()==null`）且扇出含时序/宏控制脚（`Pin::isCtrlPin`）的 `Inv` 数 `C_ctrl`。当 `C_ctrl == 0`（仅有被合并 LUT 反相、无待折叠控制反相）时，直接跳过 `sepInvInst`，用原始无修改状态执行 `restartTiming` 校准。②连同 Vex 一并根治：调整校准管线顺序，将 `before` 快照及同周期/变周期 `restartTiming` 校验**前移至 `runTimingAnalysis` 之后、`generateOutputFile` 之前**（此时 `getRestoreFlag()==0`、`C_ctrl==0`，控制反相保持原生引脚折叠态，真实 `Lut4` 零损），全部验收后再调 `generateOutputFile`，四设计可无分支、零 `sepInvInst` 统一闭环。

## 四、问题4：一小时内可完整验证的三条通用架构突围路径

- **观察**：四设计联合目标要求单套无工程名分支流程下各自达到公平官方基准 `1.20x`（评估约束 `×1.20×1.02`），而 `vexriscv_shared_dag_trial_001`（`89.273 MHz` 对 `90.380 MHz`）已证伪无差别宽切提频。
- **推测**：核心架构瓶颈集中在三处：①TMDS 等算术密集锥将多操作数位计数/加减与符号分支编译成多级串行 `ALU` 进位链加 `LUT4` 选择，进位进出惩罚重；②宽原语（`MUX2_LUT5/6`）盲目占用邻近槽位并抬升 CLS 引脚密度，且未与 `IOREG` 及高扇出 `CE/RESET` 控制集协同；③多汇点关键扇出树与硬核列锚点间的互连延迟主导了 Vex 的高负 TNS（`-275 ns` 级）。
- **建议（三条通用路径，四设计全流程单轮均可在 25~45 分钟内跑完）**：
  1. **通用进位保留压缩（CSA）+ 投机分支后选合成变换**：在合成层基于算术有向图自动识别 ≥3 加数累加/Popcount 锥（覆盖 TMDS 1计数与视差更新、Vex/int2float 多项加法），用 `LUT4` `(3,2)` 压缩树折叠中间进位，仅在末级发射单条连续 `ALU` 链；对“比较判号→条件加减”锥实施投机求值，并行计算双分支和再由 `MUX2_LUT5` 末级选通，将串行“进位+逻辑+进位”压为单次进位传播，保持外部周期与接口不变。
  2. **负裕量门控宽原语映射 + 时序边界（IOREG/控制集）联合装箱**：改全局宽切为门控发射——仅当路径预测裕量为负、可净减 ≥2 级 LUT 深度且目标 CLS 唯一输入数不超布线端口上限时才生成 `MUX2_LUT5/6`；同步引入通用垫片到时钟边沿时序模型，按实测建立/保持裕量自动裁决端口寄存器落入 `IOREG` 或近邻首级 CLS（修复输入默认 IOREG 恶化内核路径问题），并对驱动关键锥的高扇出 `CE/RESET` 源实施拓扑聚类寄存器复制，降低跨列控制集牵制。
  3. **TNS 临界度加权簇级解析布局 + 引脚置换与主干隔离布线**：将连续 `ALU` 链、紧耦合 `MUX2_LUT5/6+DFF` 视为刚性复合宏，与 `SDPB/MULTALU27X18` 列锚点一起纳入路径计数与负裕量（TNS）加权解析全局布局，避免局部交换陷入单路径震荡；详布阶段结合 `LUT4` 四输入物理延迟差做等效真值表引脚重排（最晚到达信号绑最快输入端），并在高扇出 Steiner 树上执行关键汇点直连短线（`X1/X2`）、非关键汇点旁路分叉的缓冲隔离布线。

## 五、尚缺的完整验收

任何校准修复与架构候选在认列达标前，均须补齐以下完整门禁：①完成四设计官方综合+官方 P&R 基准的源码、固定引脚、I/O 电气及异步时序审计，冻结公平基准并统一按 `baseline × 1.20 × 1.02` 设定 STA 评估频率，同口径考核原厂与自研；②四设计全量通过 `ownpr_sta_invariants` 四类时序（Setup/Hold/Recovery/Removal）同周期及变周期平移不变量、物理实例集合守恒及异步覆盖复审，严防陈旧 `Required Time` 虚高 Fmax；③通过全端口 I/O 时序覆盖、功能等价性、物理合法性与比特流编码校验，单轮全流程耗时 `<3600s` 且经 `workspace_budget.py` 预留后总空间绝不超 `40 GB`。
