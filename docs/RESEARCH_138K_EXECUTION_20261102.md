# 最新研究、器件差距与固定引脚优化执行任务

日期：2026-11-02。状态：本轮研究、实现和物理消融已完成；通用显著优于原厂、完整 I/O 的 200 MHz 目标仍未达成。

## 目标与边界

1. 核查 2025–2026 年 FPGA 综合、映射、时序/物理优化相关论文，区分正式会议、作者标注接收、预印本，以及 ASIC/FPGA 结果。
2. 核实 GW5AST-138 与 Xilinx 参考器件的工艺、LUT、DSP、存储和 I/O/时钟差别。用户已于 2026-11-02 确认此前“A9”指 Artix-7；具体器件、封装和速度等级尚未提供。本报告以 XC7A100T-CSG324-1 为明确的参考器件。
3. 使用已安装 Vivado 2022.2，尝试同一 int2float RTL、完整 I/O 约束的 XC7A100T-CSG324-1 参考实现。新参考采用一次预先声明的官方板卡连接器引脚顺序；绝不改变 Gowin 已冻结引脚，也不做引脚搜索。不同封装、时序角和默认 I/O 负载不能被当作等价硅片条件。
4. 从研究中选取能落地到 Gowin 的技术，优先核查 DSP 内部寄存器利用及全关键路径物理反馈，完成原语语义证明、实际网表检查和同条件 P&R。不能把相关思想称为整篇作者系统已复现。
5. 保存可复现命令、代码、任务结果、全部失败；所有比较包含 I/O，保持接口周期。每次完整自主尝试不超过一小时，不烧录板卡，不修改 SDK 或工具许可。

## 本轮成功判据

完成研究证据表和器件差距分析；实现至少一项有依据的 Gowin 候选并进行功能证明及实际物理消融；取得或明确诊断跨平台参考结果；结果必须区分真实闭合、报告频率、面积收益和失败。200 MHz/通用显著优势只能在实测达成后标为完成，不能因任务文档写完而宣称达成。

现有基线：固定引脚完整 I/O 下 int2float 157 MHz 冷启动位流通过，原厂同条件 149 MHz 通过；UART 143/136、TMDS 136/136、VexRiscv 82/82、LCD 78/78 MHz 为已核对频点。详见前轮 [完整 I/O 记录](PRACTICAL_138K_OPTIMIZATION.md)。

## 执行结果

- [x] 核查近期正式论文、作者系统/代码和会议议程，区分适用架构与出版状态。
- [x] 核实工艺节点；建立明确型号的 Artix-7 参考，保持全部外部 I/O 约束，并测量时钟和 I/O 配置差别。
- [x] 实现可选的 Gowin DSP 低有效使能规范化与输出寄存器吸收，接入资源推断、checkpoint 和 portfolio。
- [x] SDK 原语状态关系证明、错误使能负例、实际映射 CEC、完整冷启动、固定引脚审计、实际工作频点消融。
- [x] 检查 Gowin 自动 PLL 时钟推导，保存失败和无收益结果。
- [x] 保存方法快照、输入/约束/模型哈希和可复现命令，未改封装引脚，未烧录硬件。
- [ ] 多数低占用设计在 >150 MHz 下显著优于原厂：尚无证据。
- [ ] Gowin int2float 完整 I/O 200 MHz：仍未闭合。

## 最新论文中什么值得采用

检索截止 2026-11-02。以下是与本问题直接相关的优先项，不是所有会议论文的穷举。

| 工作与状态 | 可借鉴的技术 | 对本项目的实际意义与限制 |
|---|---|---|
| [Nextmap，PLDI 2026](https://zsisco.net/papers/nextmap-pldi26.pdf)，DOI 10.1145/3808299 | 语义 e-graph 中联合字级与位级优化，推迟过早的表示提取，联合 DSP 选择 | 优先保留乘法/加法/寄存器关系，再匹配硬核。作者代码的目标包括 UltraScale+ DSP48E2，不是 Gowin；本地 `third_party/nextmap` 已检查，尚未完整移植。不能把其资源收益称为本项目的布线后 Fmax 收益。 |
| [Memo，PLDI 2026](https://hardekbc.github.io/files/sisco26fungible.pdf)，DOI 10.1145/3808294 | 可转换的存储器表示、门级存储识别和结构感知等式饱和 | 有助于避免 BRAM 在中间表示中丢失。本项目已修复显式 `ram_style` / `no_rw_check` 属性丢失，但这只是属性保真修复，不是 Memo 复现。 |
| [PigMap3，ASP-DAC 2026 官方资料](https://www.aspdac.com/aspdac2026/archive/pdf/5F-4.pdf) | 增量映射，在线跟踪布局布线后的关键路径 | 对本项目最重要的物理优化思路：使用实际端点、位置和线延迟反馈，不只优化抽象 LUT 深度。论文展示的 OpenROAD/ASIC 流程不能直接操作 Gowin 私有路由资源。 |
| [MappingEvolve，DAC 2026 作者代码](https://github.com/Flians/MappingEvolve)，[论文](https://arxiv.org/abs/2604.26591)，DOI 10.1145/3770743.3803988 | 用 LLM 搜索/改进映射算子，再以可执行评测筛选 | 可以用于离线规则探索；其 ASAP7 标准单元实验不是 Gowin FPGA 证据。当前优先修正目标函数和硬核语义，避免搜索过拟合小测试集。 |
| [PACT，作者标注接收 FPT 2026](https://arxiv.org/html/2608.23602v2) | 布线后 ECO，带类型的编辑、时序/正确性检查和回滚 | 借鉴“提出修改—证明—物理验证—只保留合格结果”。依赖 Vivado DCP/RapidWright/UltraScale+；截至本轮仅按作者接收声明记录，会议尚未举行，不能移用其提升百分比。 |
| [EqMap，ICCAD 2025](https://www.csl.cornell.edu/~zhiruz/pdfs/eqmap-iccad2025.pdf) | 等式饱和保留重写选择，改善映射 | 本项目已有作者代码运行和固定物理批次；此前未证明通用优势。更多等价选择必须结合真实物理代价，否则仍会选到难布线的共享逻辑。 |
| [Lakeroad，ASPLOS 2024](https://homes.cs.washington.edu/~rjust/publ/lakeroad_asplos_2024.pdf) | 从硬核模型合成并验证映射 | 并非最新一届，但直接指导本轮的做法：用实际 Gowin SDK 原语证明 CE/OREG 语义，拒绝仅凭名称相似进行打包。没有声称移植了完整 Lakeroad。 |
| [FPGA 2026 官方议程](https://isfpga.org/program/) | RND 混合粒度并行路由；硬件加速的分治放置；Module-Context Modeling | 放置/路由加速需要可控制的后端和资源图；当前原生 Gowin 后端不具备这些开放接口。模块上下文建模值得采用，但不得以内部模块 Fmax 替代完整 I/O 验收。议程中的封装引脚分配技术因用户限制不采用。 |
| [FAPO，2026-10 预印本](https://arxiv.org/abs/2610.06937) | 针对关键路径的复制与映射约束，控制最终面积 | 作为研究候选，不能列为已经核实的顶会论文。本项目已有独立启发式，不能称为完整作者系统复现；后续要避免修复单一路径却恶化其他关键路径或 hold。 |

[Mapping Fusion](https://arxiv.org/abs/2507.10912) 的作者页面标注 MLCAD 2025，不应误写为 ICCAD。ICCAD 2026 在本次检索日尚未召开；会议征稿或教程页面不能充当某篇论文已发表的证据。

本轮选择先做硬核寄存器映射，原因是它有明确的真实工程遗漏、可用的 SDK 模型、可证明的局部规则和很短的物理验证周期。没有启动整个 e-graph 框架移植后再等待不确定收益。

## 为什么制程相近不能推出频率相近

用户已确认比较对象是 **Artix-7**，不再按 Cortex-A9/Zynq 解释。Artix-7 采用 **28 nm**；本轮 GW5AST-138 为 **22 nm**。因此 Gowin 的名义工艺节点更小，但这并不保证具体 RTL 的布线后频率更高。具体 Artix-7 型号、封装和速度等级仍未确认，XC7A100T-CSG324-1 是本轮实际测试的参考型号。[Gowin 数据手册](https://cdn.gowinsemi.com.cn/DS1239E.pdf)、[AMD 工艺表](https://docs.amd.com/r/en-US/ug116/Process-Technology-Family)。

实际性能还取决于：

1. **逻辑组织和宽 LUT 映射。** GW5AST 以 LUT4 数量标示容量，但并非只有 LUT4。DS1239 §2.2 明确写有“真 LUT5、真 LUT6”；[Arora V CFU 手册 UG303](https://cdn.gowinsemi.com.cn/UG303E.pdf) §2.1.1、§3.1.5 描述一个 CLS 形成 LUT5、两个 CLS 形成 LUT6，以及 LUT4 与专用 MUX2_LUT5/MUX2_LUT6 的宽 LUT 结构。7 系列也支持 LUT6，具体资源组织、打包约束和输入脚时序需分别建模。[7 系列原语手册](https://download.amd.com/docnav/documents/aem/xilinx14_7-7series_hdl.pdf)。此前以“Gowin LUT4 对比 Xilinx LUT6”直接解释路径深度差距不充分，现予以更正；不能把某个 LUT4-only 配方的结果归因于 Gowin 没有 LUT6。
2. **硬核配置与寄存器位置。** Gowin 的 27×18 DSP 与 7 系列 25×18 DSP48E1 不是同一原语；内部寄存器、使能、复位和级联规则需要分别适配。[DSP48E1 手册](https://docs.amd.com/v/u/en-US/ug479_7Series_DSP48E1)。本轮正是因为未支持低有效 CE，4 个 Gowin DSP 的 132 位输出状态留在了 fabric。
3. **全局和局部时钟、PLL 补偿、封装引脚到 IOB 的路径。** 完整 I/O 时序包含这些代价，不能用两个内部寄存器之间的 Fmax 替代。
4. **3.3 V I/O 电路和负载。** OBUF 并不按逻辑核心的工艺缩小比例变快；slew、驱动强度、bank 类型和默认负载模型都会影响报告。
5. **位置与布线。** 低 LUT 占用不等于近距离。CPU 新网表的最差路径有 7.027 ns 数据布线和 5.054 ns 数据单元延迟；一个扇出 3 的连接也跨 43 个 Manhattan tile、耗时 2.013 ns，单纯限制高扇出并不足够。
6. **工具和比较口径。** 我们当前是 Gowin 1.9.12.04 与 Vivado 2022.2，器件、速度角、封装、引脚几何和 I/O 负载模型不相同。没有执行足够严格的跨硅片归一化实验，不能由下表推断哪家制程或芯片固有更快。

## 同 RTL 的 Artix-7 参考实测

参考器件：XC7A100T-CSG324-1。输入为同一注册版 `int2float`，11 位输入、7 位输出，**不是完整 IEEE 32 位 int-to-float 单元**。顶层周期数保持不变。外部输入/输出 max/min delay 均为 0，hold user uncertainty 50 ps，3.3 V、8 mA，输出 IOB、输入 fabric FF。Vivado 自身还计入器件抖动，不能假称两边全部 uncertainty 数值完全一致。

Artix 参考引脚只按 [Digilent 官方 Arty A7-100 引脚文件](https://raw.githubusercontent.com/Digilent/digilent-xdc/master/Arty-A7-100-Master.xdc) 中 E3 时钟及 JA/JB/JC 顺序一次声明，之后所有版本完全一致。用户已经确认目标系列为 Artix-7，但尚未确认具体开发板是否与本参考相同；它也不是 Gowin 的可互换封装；没有进行引脚搜索或改动 Gowin 引脚。

| 参考实现 | 目标 MHz | 最差 setup ns | 最差 hold ns | 最差脉宽裕量 ns | setup / hold / pulse 违规端点 | 结果 |
|---|---:|---:|---:|---:|---|---|
| direct BUFG，SLOW I/O | 200 | -4.855 | +0.110 | +2.000 | 10 / 0 / 0 | 不通过 |
| direct BUFG，FAST I/O / 非低功耗输入模式 | 200 | -4.074 | +0.179 | +2.000 | 11 / 0 / 0 | 不通过 |
| FAST I/O + 1:1 MMCM | 200 | -0.279 | +0.755 | +1.100 | 2 / 0 / 0 | 不通过 |
| FAST I/O + 1:1 MMCM | 180 | +0.012 | +0.747 | +1.278 | 0 / 0 / 0 | 完整 I/O STA 通过 |

表中 MMCM 变体保持数据流水级，但依赖锁定后的稳定时钟；没有证明上电瞬态与无 PLL 原设计完全等价，**不会作为 Gowin 综合自动改写发布**。180 MHz 只有 12 ps setup 裕量，是工具报告通过点，不是板级测量或频率上限。全部路由完成，`check_timing` 的无时钟、无输入/输出延迟、部分约束、组合环计数为 0。DRC 仅保留 CFGBVS/CONFIG_VOLTAGE 未设置警告：这是实现诊断参考，未配置成可上板工程，未生成/烧录参考位流。

direct/SLOW 最差输出路径包含 **5.331 ns 时钟到达 + 0.472 ns C2Q + 0.001 ns 数据线 + 4.015 ns OBUF**。修改 I/O 和时钟后，200 MHz 的最差路径转成两层 LUT5/LUT6，数据逻辑 0.704 ns、数据布线 3.560 ns。该变化支持“先定位路径类别，再做对应优化”，不支持“再加一个综合重写就一定能超过 200 MHz”。

结果目录：

- `results/artix7_fullio200_reference_004`（SLOW）；
- `results/artix7_fastio200_reference_001`；
- `results/artix7_fastio_mmcm200_reference_001`；
- `results/artix7_fastio_mmcm180_reference_001`。

每次约 63–68 秒。最初 `_001`–`_003` 的 Tcl 临时路径/权限失败已保留；以正常用户执行权限运行同一 Vivado 批处理后解决，未修改安装目录、系统 ACL 或许可证。

## Gowin 的 PLL 与 I/O 为什么没有同样改善

前轮固定引脚、LOCAL_CLOCK 的最佳 int2float 在 157 MHz 全检查通过，最差路径为 **3.483 ns 时钟到达 + 2.859 ns C2Q/OBUF，数据布线 0 ns**。因此这条路径不能靠继续减少数据布线获得 200 MHz。也不能从这一条路径推出整颗芯片的 Fmax 上限。

本轮检查了旧 PLL 实验是否因手动 generated-clock 覆盖原生推导而失真：`results/gowin_auto_pll_clock_003` 删除手写 generated-clock，分别运行内部/外部反馈，原生生成 `ng_pll/CLKOUT0.default_gen_clk`。两者依旧 **setup 7、hold 11 违规**；LUT4 最差输出路径仍有 4.089 ns 启动时钟延迟、1.529 ns 数据线和 2.859 ns 数据单元延迟。自动时钟报告约 200.970 MHz 的内部频率并不代表外部 I/O 通过。

此探针属于诊断：自动时钟在 SDC 解析后创建，不能按此前写法为其加 50 ps hold uncertainty；因此它不作为新的合格候选。尝试通配符和提前引用自动时钟名的两次失败保留于 `_001`、`_002`。原本完整约束 PLL 负结果仍是正式比较依据。

结论只到这里：**手写 generated-clock 不是该失败的唯一原因，当前原生报告没有出现 Vivado MMCM 那样的负插入延迟补偿。** 不能进一步断言 Gowin 硅片没有补偿能力，或自行用负 clock latency 抹掉真实代价。要推进这一路线，需要核对 PLL 反馈实际布线、器件时钟结构及签核模型；相位关系必须与硬件参数一致，并重新检查输入 setup、输出 setup 和双向 hold。

## 本轮落地的 Gowin 优化

新开关：`pack_dsp_registers=true` 且 `pack_dsp_inverted_enable=true`。默认仍关闭低有效使能扩展。

代码：[DSP 打包](../scripts/dsp_output_register_pack.py)、[公开综合入口](../scripts/nextgowin.py)、[检查点验证](../scripts/check_flow_checkpoint.py)、[候选组合](../scripts/nextgowin_portfolio.py)、[测试](../tests/test_dsp_output_pack.py)。

发现：VexRiscv 的四组乘法结果状态使用 `$dffe EN_POLARITY=0`。旧规则只接受正有效 CE，跳过全部四个 DSP。新规则在整个输出组时钟和 CE 一致、且满足原有状态保护条件时，将共享使能取反一次，连接 DSP OREG 的正有效 CE，并把原 FF 的输出接到对应 DSP OREG 位。没有增加流水级。

仍严格拒绝：已知 INIT、复位、负沿时钟、混合控制、被保留状态、非独占乘积输出、级联和超出已证明低 45 位范围的状态。没有把具有已知初值的 DSP 输入寄存器直接复制进 AREG/BREG；这种扩展涉及初态及多个消费者之间的状态关系，未建立证明前不采用。

正确性证据：实际 SDK `MULTALU27X18`，独立源/目标状态、符号乘法，两步归纳状态关系保持；低有效 CE 另有独立证书，故意接反 CE 的负例必须失败。源状态没有指定 INIT 时使用实现细化关系，不把两组独立任意初态假设成天然相等。生产流校验证书、资源图、实际映射 CEC 和 checkpoint，后续发射不能悄悄换成未经证明的 DSP 配置。

### CPU 固定引脚消融

`results/cpu_dsp_inverted_cold_001`：从原始已适配 RTL 到位流，**86.641 秒，82 MHz 完整时序通过**，所有实际非时钟引脚均有 max/min 约束，原封装引脚/电气设置相同。原始项目中无定义输出的统一处理与前轮一致，没有额外改变功能。

`results/cpu_dsp_enable_ablation_001`：同一公开配置，仅切换低有效 CE 开关；旧分支也启用原有 OREG 路径，但没有符合正有效 CE 的组。完整批次 357.578 秒。

| 本轮网表 | LUT | ALU | fabric/IO FF 合计 | BSRAM | DSP | 82 MHz | 85 MHz | 90 MHz |
|---|---:|---:|---:|---:|---:|---|---|---|
| 原厂既有 RTL 综合网表 | 2466 | 390 | 1319 | 10 | 4 | 前轮通过 | 通过 | 失败，setup 2 / hold 68 |
| own，关闭低有效扩展 | 3310 | 384 | 1526 | 10 | 4 | 失败，9 / 53 | 失败，10 / 53 | 失败，10 / 55 |
| own，开启低有效扩展 | 3133 | 384 | 1394 | 10 | 4 | 通过，0 / 0 | 失败，2 / 64 | 失败，8 / 64 |

本轮明确收益：**FF -132（-8.65%），LUT -177（-5.35%），无新增 DSP/BRAM**。减少 fabric 状态及其控制映射的结果已由原生资源报告验证。90 MHz 压力目标下，旧/新报告频率为 78.585/85.724 MHz，但两者都不闭合，**不能报告为 90 MHz 性能成功或用两者比值宣称工作频率提升**。

此前“不走打包 JSON 往返”的最佳 LUT4 网表已经能在 82 MHz 通过；本轮关闭新开关的消融与它的中间表示流程、命名及布局不同，尚未隔离这些差异对时序的各自影响，不能替代那条最佳基线。因此新规则证明了面积收益和可用的 82 MHz 流程，尚未提高此前最佳合格频点。原厂本轮实际通过 85 MHz，故 CPU 仍然落后，不能宣称全面获胜。

CPU 的 284/312 外部 I/O 占用约 91%，**不属于“各项资源 <40%”集合**，即使 LUT、BRAM 和 DSP 占用很低。

新网表 82 MHz 的关键路径从 `decode_to_execute_SRC2_CTRL[0]` 到 `decode_to_execute_RS1[14]`，穿过控制/运算/旁路组合逻辑，数据线约占数据路径的 58%。这是寄存器边界间的真实组合路径，不能仅靠 DSP OREG 消除。90 MHz 下又转为寄存器到外部输出路径，其中一条线横跨 94 个 tile、耗时 3.848 ns；这说明必须同时考虑旁路逻辑和固定 I/O 位置。

## 改进优先级

### 补充：Artix-7 确认后的逐路径差距分析

本节重新读取已有实现报告，没有新增引脚或放置实验。不同器件的端点、速度角和 I/O 默认负载不完全相同，以下用于定位瓶颈，不作为原语本征速度比。

**宽 LUT 更正与后续核查：** 当前 157 MHz 候选的配置是 `architecture_recipe="lut4"`，即主动采用 LUT4 映射，不能代表器件宽 LUT 能力。项目已有 `wide` / `wide_no_recovery` 候选及 MUX2_LUT5/MUX2_LUT6 适配；应检查关键逻辑锥的宽 cut、专用连线、输入脚分配和成本估计是否合理，进行局部宽 LUT 与 LUT4 的同条件消融，而非假设硬件不支持。此前宽映射的负结果不证明所有局部宽 LUT 选择均无收益。安装 SDK 中新应用手册 TN735 的 MAP_LUT5/MAP_LUT6 配置明确针对 GW3A-20，不能未经验证直接当成 GW5AST-138 的可用开关。该更正增加了 synthesis 可改进的范围，但不消除报告中已测得的时钟偏斜和输出时钟到达问题。

**输出路径差异主要体现在时钟相位/补偿，而非已经证实 Gowin 的输出单元更慢。**

| 已实测路径配置 | 启动时钟到达 ns | C2Q ns | 数据线 ns | OBUF ns | 输出到达 ns |
|---|---:|---:|---:|---:|---:|
| Gowin LOCAL_CLOCK，157 MHz 最差输出路径 | +3.483 | 0.331 | 0.000 | 2.528 | 6.342 |
| Artix-7 FAST I/O，200 MHz，无 MMCM，q[5] | +5.331 | 0.472 | 0.001 | 3.234 | 9.038 |
| Artix-7 FAST I/O，200 MHz，有 MMCM，q[5] | -0.811 | 0.472 | 0.001 | 3.234 | 2.896 |

Artix 后两项使用同一组封装引脚；改善来自实际时钟结构及相应签核模型，不是 OBUF 变快。负时钟到达是稳定周期时钟相对于参考边沿的相位关系，不是负的物理传播时间。MMCM 的反馈与补偿模式有明确硬件条件，参见 [AMD UG472](https://www.amd.com/content/dam/xilinx/support/documents/user_guides/ug472_7Series_Clocking.pdf)。Gowin 是否能在当前冻结引脚和拓扑下取得对应补偿，仍须证明，不能直接减去 3 ns 时钟延迟。

**内部路径也几乎同样临界，不能只修输出路径。** Gowin 157 MHz 的第三条 setup 路径为 `r[6] → q[2]`：

| 组成 | 报告值 |
|---|---:|
| 发送 FF 时钟到达 | 2.350 ns |
| 接收 IOB FF 时钟到达 | 0.959 ns |
| 不利时钟偏斜 | 1.391 ns |
| C2Q | 0.331 ns |
| 组合单元 | 约 2.259 ns，5 层 LUT |
| 数据布线 | 2.284 ns |
| setup 要求 | 0.057 ns |
| 最小周期静态估算 | 约 6.322 ns |
| 6.369 ns 目标下报告 slack | +0.049 ns |

逐项按显示精度相加有数 ps 舍入差异。Artix-7 的 MMCM/200 MHz 最差内部路径为另一对端点 `r[3] → q[5]`，经过 LUT5+LUT6 两层：C2Q 0.456 ns、组合单元 0.248 ns、布线 3.560 ns、有效不利 skew 0.121 ns、setup 0.834 ns、uncertainty 0.060 ns，合计约 5.279 ns，仍违规 0.279 ns。不能用不同路径的单项数字断言 Gowin 每根线更慢；这里反而是 Gowin 所选内部路径的数据线总延迟较小，而逻辑级数及不利 skew 较大。

同一 int2float 实现资源：Gowin 自主候选为 85 LUT、18 FF，其中 6 个输出 FF 在 IOB；既有 Gowin 原厂 149 MHz 合格点为 70 LUT、18 FF；Artix MMCM/180 MHz 为 33 个 Slice LUT、18 FF，其中 7 个输出 FF 在 IOB。这里同时混合了架构和映射策略差异，不能将 85/33 全部归因于 LUT4，也不能直接作为面积倍率。自主候选使用更多 LUT 却比 70 LUT 的原厂候选通过更高频点，说明 LUT 数量并非充分的时序目标。Gowin 目前 `q[1]` 的冻结引脚 AB15 位于配置相关 bank，未成功吸收其输出 FF；已有纯 D / 全 IOB 实验也未解决，但尚未证明这是该 bank 的绝对硬件限制。当前最差外部路径来自其他已放入 IOB 的 FF，所以补齐第 7 个 IOB FF 也不能单独解除瓶颈。

**优化必须满足每个输出寄存器的双向时间窗口。** 例如保持上述发送时钟及数据路径不变，在 5 ns 周期下，q[2] 的接收时钟至少需约 `2.350 + 4.874 + 0.057 - 5 = 2.281 ns` 才满足内部 setup；若其 C2Q/输出缓冲沿用约 2.859 ns，则输出要求又限制它不晚于约 `5 - 2.859 = 2.141 ns`。这只是忽略附加裕量、hold 和硬件可实现性的预算示例，但已显示窗口为空：单独推迟接收时钟并不能解决全部问题，还须同时改变数据路径或发送时钟。实际验收必须重新跑完整 STA。

这也解释了为何 LOCAL_CLOCK 在本例改善外部输出之后，内部 setup 仍可能受损。下一轮的目标应是联合约束的时钟/数据路径优化，而非最小化一个时钟插入延迟或一条数据线。

此外，当前 int2float 与 CPU 新候选的原生报告均为 **LW 0/8**。普通数据线很长与专用 LONG WIRE 资源耗尽是两个不同问题；本例没有 LW 耗尽的证据，不应通过盲目提升全局长线使用量来修复。

以下按本轮证据排列，未完成项没有包装成已实现：

1. **把端点与真实到达时间带回映射。** 当前 own 映射主要采用统一组合预算和估计线代价，完整 SDC 由后端验收；它还不是完整多时钟、路径例外和 I/O 预算驱动的综合 STA。下一阶段应导入每个 FF/I/O/硬核边界的 arrival/required 与物理代价，避免为已达标的内部逻辑省级数，却牺牲固定 I/O 端点。验收仍是完整 I/O。
2. **采用受限的物理反馈窗口，而非全局盲复制。** 以原生报告里的几条长线/晚到选择信号为中心，尝试局部重映射、选择性复制和内部实例位置约束，保持封装引脚不变。每个候选检查所有原生违规总数及 hold；仅有前 25 条路径时不能声称覆盖全部关键路径。以前全局复制和单 FF 复制的负结果保留，不重复把 fanout 阈值当成通用答案。
3. **继续做具有状态证明的硬核联合映射。** 支持更多合法 DSP CE/OREG、算术与存储器模式，保留字级结构与属性；但实际资源和时序共同选优。Nextmap/Memo 是有价值的长期路径，不能只拿一个面积数字替代路由验证。
4. **时钟/I/O 作为单独工程优化域。** int2float 当前边界路径首先需要解决时钟到达和 IOB 条件，不能指望 LUT 算法消除 pad 延迟。保持原引脚，合法检查 clock region、反馈和必要的物理配置。采用 PLL、相位调整或源同步协议时，必须单列接口/锁定条件；本轮没有授权改变外部协议或增加延迟，因此未这样替换验收设计。
5. **最终以冻结验证集证明通用性。** 当前多设计合格点仍不足以支持“大多数低占用项目显著获胜”。应在未参与调参的工程、同一约束/电气条件、相同后端预算下，报告全部设计及失败率，再计算几何均值；不能只选择获胜项。

这些优先级不会因接入 GPU 自动解决。当前原生放置/布线接口不支持把关键内核替换为 CUDA；GPU 论文更多是在有资源图的自有后端加速搜索。按用户选择，自己的综合继续单核执行，资源映射与目标函数质量优先于运行时间优化。

## 复现

工作区根目录，Python 为 `D:/Conda3/python.exe`，结果目录必须是新目录：

```powershell
D:/Conda3/python.exe -m unittest discover -s tests
D:/Conda3/python.exe scripts/run_frozen_flow.py --config results/cpu_dsp_inverted_cold_001/config.json --out results/cpu_dsp_inverted_reproduction --total-timeout 1800
D:/Conda3/python.exe scripts/nextgowin_portfolio.py --config results/cpu_dsp_inverted_cold_001/config.json --out results/cpu_dsp_inverted_portfolio_reproduction --recipes lut4 --total-timeout 300
D:/Conda3/python.exe experiments/run_vivado_fullio_reference.py --out results/artix7_mmcm180_reproduction --frequency 180 --fastio --mmcm
D:/Conda3/python.exe experiments/probe_gowin_auto_pll_clock.py --out results/gowin_auto_pll_reproduction
```

完整消融脚本 [run_cpu_dsp_enable_ablation.py](../experiments/run_cpu_dsp_enable_ablation.py) 固定结果目录以保留本轮证据；如重跑应先改为新的结果目录，不能覆盖旧证据。Vivado 参考在当前受限运行令牌下可能发生 Tcl 路径失败，需正常用户权限运行；这不需要修改系统环境或许可证。

测试记录 `results/research_optimization_tests_001.log`：**125 项通过，30.628 秒**；低有效 DSP 专项 5 项通过。`results/cpu_dsp_inverted_cold_001/run/method_source` 保存本轮生产脚本/配置/模型快照，checkpoint 对实际资源和证明的重验也通过。`results/cpu_dsp_inverted_portfolio_001` 用公开候选入口重新完成同一低有效使能配置，82 MHz 全时序和位流通过，80.781 秒；独立证明目录的传递已实际验证。

最终重验命令为 `D:/Conda3/python.exe experiments/summarize_research_20261102.py`，生成 [机器可读汇总](../results/research_20261102_verified_summary.json)。它重新检查 4 个 Artix 参考和 9 个 CPU 结果的完整 I/O、固定引脚、违规计数及报告哈希；Vivado 同时检查完整路由、脉宽和缺失约束计数。主要失败均在各结果目录保留，未用超时、工具异常或未收敛网表替代成功结果。
