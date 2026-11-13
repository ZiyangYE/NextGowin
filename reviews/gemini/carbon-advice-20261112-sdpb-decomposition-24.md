# VexRiscv SDPB 等价分解与联合架构建议

## 一、已观察事实与待核实假设

- **已观察事实**（据所读两份文件）：现有源码等价脚本校验综合清单与 SDK 模型哈希，对组合原语直接取用 SDK 模型，对 `mux2` 与 `DFFRE/SE/CE/PE` 采用保留 `INIT` 及控制优先级的显式二值适配器，要求 `GSR` 恒无效、顶层接口一致且拒绝一切黑盒；经 `memory_map` 与 `clk2fflogic` 后，先在独立 `original_pair` 上做 4 步初态 SAT（拦截 `equiv_make` 漏检的 `INIT` 与异步复位极性错误），再做 4 步基例与归纳。UART、int2float、TMDS 已通过；VexRiscv 因不含 `SDPB` 模型被拒。另已观察 own VexRiscv 为 90.380 MHz（原厂 63.729 MHz 系放宽约束校准），own TMDS 为 0 ALU。
- **待核实假设**（未读其它文件）：SDK 中 `SDPB` 精确引脚与参数编码（`BIT_WIDTH_0/1`、`READ_MODE`、`WRITE_MODE`、`RESET_MODE`、`BLKSELA/B`、`INIT_RAM_00..3F`）及碰撞 `X` 细节；VexRiscv 网表中 `SDPB` 位宽拼接与深度级联形态；任务所述 own TMDS 265 LUT4/156 宽 MUX、原厂 37 ALU/105 LUT 统计及 own 尚无串行进位链现状。

## 二、SDPB 壳核解耦与核心语义核对

绝不能将 `SDPB` 黑盒化或仅比对端口连接：黑盒切断写后读数据流，会放过参数配置错、地址移位、字节掩码错接与碰撞失配；而全量 `memory_map` 将 RAM 展开为数万 FF 易超 1 小时时限。健全方案是将哈希锁定的 SDK `SDPB` 拆为**显式时序/译码壳层（Shell）**与**规范存储核（Core）**：壳层留在网表参与完整证明，仅对存储核做按地址归约，须逐项核实：

1. **地址/数据打包**：按 `BIT_WIDTH_0/1` 解析 `ADA/ADB` 有效字地址高位偏移、子字选择及未用低位常量绑定，核对 `DI/DO` 位切片重排与多片 `BLKSELA/B` 片选拼接。
2. **写使能/byteenable**：区分窄模式下作低位地址的 `ADA[3:0]` 与 32/36 位宽模式下作字节写使能的 `ADA[3:0]`，与 `CEA` 及 `BLKSELA==3'b000` 合成为字节/位写掩码。
3. **双时钟**：严禁假定同源时钟；保留独立 `CLKA/CLKB`（含潜在极性参数），经 `clk2fflogic` 降阶覆盖任意边沿交错。
4. **读写碰撞**：同拍同址读写采用二轨编码 `(val, def)`，执行单向精化 `def_gold -> (def_gate && val_gold == val_gate)`；若 Gold 确定而 Gate 因碰撞产出 `X`，必须判败。
5. **INIT 初态**：解析 `INIT_RAM_00..3F` 到规范字表，在独立 `original_pair` 上校验初态（含 RTL 未定初值的二轨精化）。
6. **复位与读出周期**：校验 `GSR` 恒无效；`RESETA/B` 按 `RESET_MODE`（同步/异步）仅复位壳层输出寄存器，绝不清零存储阵列；按 `READ_MODE` 建模 1 拍读锁存（`CEB`）或 2 拍流水输出（`OCE`）及复位优先级。

## 三、全设计等价组合证明：两条可实施路线

先用小深度实例有界 SAT 证明“二轨可综合 `SDPB` 适配器 == SDK 原始 `SDPB`”，再实施以下任一路线：

- **路线一：全称符号单地址见证归约（纯 Yosys 位级 SAT/归纳，首选）**
  在 `memory -nomap` 后配对 Gold 数组与 Gate 级联 `SDPB` 存储核（深 $D$、宽 $W$），引入自由常量符号地址 $a^*$（`$anyconst`）：① **初态全表核查**：遍历全部 $a \in [0, D-1]$ 校验 $I_{\text{gold}}(a) \sqsupseteq I_{\text{gate}}(a)$，并在 `original_pair` 上跑 4+ 步初态 SAT；② **按地址状态不变量**：仅用 1 对双轨字寄存器 $(V_{\text{gold}}, V_{\text{gate}})$ 跟踪 $a^*$ 在 `clk2fflogic` 下的按字节条件写，每拍断言 $def_{V,\text{gold}} \to (def_{V,\text{gate}} \land V_{\text{gold}} = V_{\text{gate}})$；③ **读边界闭合**：读地址等于 $a^*$ 时返回 $V_{\text{gold}}/V_{\text{gate}}$；两端有效读地址相等且不为 $a^*$ 时，供给满足同拍多端口一致（Ackermann 公理）及可选 $k$ 步写前递缓冲的受约共享符号值；**一旦 $ra_{\text{gold}} \neq ra_{\text{gate}}$，向两端注入彼此独立的自由变量**。仅增加数十个 FF 即可凭时间良基归纳严密闭合全电路反馈环。
- **路线二：保留 `$mem_v2` + SMT 数组理论证明（`write_smt2` + Z3/Bitwuzla）**
  跳过 `memory_map`，将 `SDPB` 核建模为连接壳层的双轨 `$mem_v2`，经 `clk2fflogic` 后导出 `QF_AUFBV` SMT2。用 `yosys-smtbmc` 先从显式初始数组做 4+ 步初态 BMC，再显式注入 Gold 数组与多片分条 `SDPB` 数组的按地址精化耦合不变量做 $k$ 步归纳（不可省略显式数组耦合不变量直接做端口 miter 归纳，否则未耦合初态数组必致虚警）。

## 四、错接拦截机理与强制反例测试

若地址错位（偏移、位序颠倒、字节使能误作低位地址）：写路径存在见证 $a^*$ 仅命中单侧令 $V_{\text{gold}} \neq V_{\text{gate}}$；读路径在有效采样拍触发 $ra_{\text{gold}} \neq ra_{\text{gate}}$，独立自由读值直接击穿输出断言。若时钟错接（`CLKA/CLKB` 互换、极性反相或使能漏接）：`clk2fflogic` 步中仅单侧发生写更新或读锁存，1~2 拍即产反例。上线前须通过 6 项负向变异测试（均须被拒）：① 翻转 `ADA/ADB` 一位或错移位宽偏移；② 交换宽模式 `ADA[3:0]` 字节使能；③ 互换或反相 `CLKA/CLKB`；④ 篡改 `INIT_RAM` 单比特；⑤ 翻转 `READ_MODE`（1/2 拍）或 `RESET_MODE`；⑥ 构造同拍同址读写碰撞令 Gate 产出错误旧值或 `X`。

## 五、突破局部 LUT 优化的通用联合架构方向

面向四设计最低比值 $\ge 1.20\times$ 公平原厂基线目标（固定 I/O 与外部周期、无项目分支、1 小时时限），局部 LUT 重写已趋饱和；own TMDS 呈 0 ALU/265 LUT4/156 宽 MUX（原厂历史 37 ALU/105 LUT），暴露出算术打散与簇装箱失配。建议推进**物理代价驱动的 Syn+Packing+Placement 联合流**：

1. **先验打通并校验串行进位链闭环**：不能假设 own 已有串行进位链；先在微基准上打通并校验 `ALU` 模式位编码、CLS 内外 `CIN->COUT` 专用连线方向合法性及原生四类 STA 弧识别，未验证前对进位映射保持失败关闭。
2. **延迟/拥塞双代价综合保留与刚性簇装箱**：综合阶段按实测 `CIN->COUT` 与普通布线延迟比，对超阈值多比特加减/比较链通用保留 `$alu` 而非拆成 LUT4+宽 MUX；装箱将连续 `ALU` 锁为刚性相对坐标条宏，按共享 `(CLK, CE, SR)` 控制集同簇绑定关键 LUT+DFF，并限制每 CLS 宽 MUX（`MUX2_LUT5..8`）密度以消除开关盒引脚争用。
3. **带进位条合法化的解析布局与单轮拥塞回传**：全局布局联合求解固定 I/O 张力与刚性进位条合法化，提取按汇点布线负裕量/拥塞热点做单轮回传，自适应收紧高拥塞锥的宽 MUX 与算术切分阈值，以四设计比值下界为唯一判据。
