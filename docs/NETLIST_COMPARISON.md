# 优化前 CPU 综合网表与可布通原厂网表的差异

本页保留优化前 `fpgafly_cpu_flow_003` 的诊断快照。后续实现、验证及新运行结果见[硬资源与负载分散优化](RESOURCE_OPTIMIZATIONS.md)。

结论：当前网表的主要问题是硬资源映射不完整、普通逻辑明显膨胀，以及负载分散程度不足。DSP 完全缺失是首先应修复的实现缺口；不能把当前超时只归因于 long wire 不足。

## 对照对象与统计口径

基线为 [原厂归档网表](../results/fpgafly_source_001/source.vg)，SHA256 `95b84330c11c803ea06053cfd5494f3b2920ab5027ba60a1846414608fbabc47`。它已在本机 Gowin V1.9.12.04 上[完成 P&R](../results/fpgafly_native_baseline_001/physical_summary.json)。原运行清单的历史 `failed` 来自当时错误地要求未请求的位流产物；原生返回 0、无实现错误，独立审计确认 P&R 有效。ui_clk Fmax 为 72.125 MHz，未达到 80 MHz，不能称为时序收敛。此前 42 模块优化版同样能布通，报告 79.520 MHz，硬资源结构基本沿用此基线。

候选为 [CPU 流程 003](../results/fpgafly_cpu_flow_003/manifest.json) 的实际导出前 JSON；其原生 P&R 一小时超时，未生成新位流。两次实现采用同一 DLL、器件、SDC/CST 和主要 P&R 选项；候选额外请求 `-bit`。这里不将二者耗时作为受控速度比较，也尚未证明归档网表与当前全部 RTL 的顺序等价。

为了统一统计，使用[结构导入器](../scripts/import_vendor_comparison.py)读取原厂可见网表，保留硬原语，不执行重新综合或优化。原厂 DDR 内核受保护，因此**两边均将整个 `D3_320` 边界视为黑盒**，不把候选可见的 DDR 内部单元与基线不可见的单元混比。派生的可见网表仅用于分析，不能用于实现；原始文件保持不变。

[比较脚本](../scripts/compare_netlist_structure.py)与[比较汇总](../results/netlist_comparison_001/structure_003/comparison.json)均已保存。下表是相同边界下、打包前的结构计数，不是布局布线资源报告。

| 指标 | 可布通原厂网表 | 当前 CPU 网表 | 差异 |
| --- | ---: | ---: | --- |
| LUT | 35964 | 65695 | +82.7% |
| ALU | 5054 | 27157 | 5.37 倍 |
| LUT + ALU | 41018 | 92852 | 2.26 倍 |
| DFF 原语 | 42638 | 42326 | -0.73% |
| DSP 原语实例 | 139 | 0 | 全部缺失 |
| `MUX2_LUT5..8` | 3484 | 0 | 未使用专用宽多路选择结构 |
| BSRAM 原语 | 254 | 252 | 少 2 个 SDPB |
| 分布式 RAM 原语 | 12 | 0 | 未保留该类映射 |
| `LUT1 INIT=2` 直通节点 | 0 | 4373 | 需要审查输出别名的物化方式 |
| 全部叶单元 | 88076 | 135448 | +53.8% |

原厂完整物理报告的 DSP 用量是 **136 个资源单位**，对应上述 **139 个原语实例**，两种计数不可混用。当前完整候选的 BSRAM 是 **260 个 SDPX9B + 8 个 SDPB = 268 个原语**；表中的 252 是排除 DDR 后的用户侧计数。因此“当前没有使用 BRAM”并不成立。

## DSP 缺口与算术规模

当前 [综合入口](../scripts/nextgowin.py)写死了 `-nodsp`，同时本地固定版 Yosys 的 [help 输出](../results/netlist_comparison_001/tool_capabilities/help.log)显示，已有的 DSP 自动映射只适用于 `gw1n/gw2a`。[官方实现](https://github.com/YosysHQ/yosys/blob/main/techlibs/gowin/synth_gowin.cc)也在相应家族条件内调用 `mul2dsp/dsp_map`。所以只删除 `-nodsp` 不能补齐 GW5A 的 DSP 映射。

这是本流程的实现缺口。用户选择单核 CPU 只决定综合器在主机上的执行方式，不应限制 FPGA 内部 DSP 或存储硬块的使用。

原厂网表的 DSP 分布中，**42 个 `lif_pipeline` 每个使用 3 个 `MULTALU27X18`，合计 126 个实例**，已经占 139 个 DSP 实例的大部分。[原厂算术模块与实例列表](../results/netlist_comparison_001/structure_003/baseline.features.json)记录了其余乘法器，涉及 retinal 脉冲、输入相位、边解码和 spike cursor 等逻辑。

[lif_pipeline.v](../FPGAFLY_ISCAS2027/rtl/state/lif_pipeline.v)中的主要常数乘法是：

- 19 位有符号 `delta0 × 1307`；
- 18 位有符号 `g × 2588`；
- 18 位有符号 `g × (-5191)`。

原厂还将部分乘法、加法及流水寄存器映射到 DSP 的组合/级联结构；当前流程在通用算术降低阶段将其变为软逻辑，然后才让 ABC 处理剩余布尔图。后面的 LUT mapper 已经没有机会重新选择 DSP。

当前 ALU 的[源模块统计](../results/netlist_comparison_001/alu_origins.json)显示，仅以上三类常数乘法辅助模块就留下 5376 个 ALU；此外还有大量普通加法、比较和通用乘法展开结构。按同一模块边界计数，原厂有 326 条局部进位链，候选有 1766 条；两边均可能存在跨模块进位链分段，这不是全芯片物理进位链数。

候选 ALU 全使用通用 `ALU_MODE=2`，原厂则使用 0/1/3 等专门模式。模式差异本身不证明功能或时序错误，但说明当前算术映射没有达到原厂的特化程度。

## 高扇出比较：更少网络、更集中的负载

[原厂扇出清单](../results/netlist_comparison_001/structure_003/baseline.fanout.json)与[候选清单](../results/netlist_comparison_001/structure_003/candidate.fanout.json)采用相同 DDR 黑盒边界，统计实际叶单元引脚，不重复计算层次端口，排除常量。

| 非时钟网络的负载阈值 | 原厂 | 候选 |
| --- | ---: | ---: |
| ≥64 | 397 | 241 |
| ≥128 | 26 | 82 |
| ≥256 | 16 | 43 |
| ≥512 | 2 | 13 |
| ≥1024 | 0 | 3 |

所以不能用“≥64 的网络更少”推断候选更好布线。原厂有较多中等扇出分支，候选出现更重的集中负载。

对[指定信号的追踪](../results/netlist_comparison_001/structure/baseline.focus.json)找到以下具体区别：

- 原厂 `command_router` 中有 13 个 `core_selected_s*` DFFRE，其 D/CLK/CE/RESET、参数完全相同，Q 输出不同；原始 `core_selected_Z` 分支只有 9 个负载，多个相关分支分别有 64 个负载。
- 候选[对应信号](../results/netlist_comparison_001/structure/candidate.focus.json)由一个输出连接 701 个逻辑输入。原始 `CLOCK_LOC "core_selected_Z" LOCAL_CLOCK` 保留，因此同名约束现在作用于更重的物理网络；保持 CST 字节并不意味着约束对象具有原先相同的负载结构。
- 原厂 `pixel_pulses` 存在多个 DFF 输出分支，许多分支各带 64 个负载；候选每个 bit 集中到一个输出，负载为 567。
- `pulse_step[0]` 在可见原厂结构中连接 6 个输入引脚，候选为 95；其 bit 9 从 1 个输入引脚增至 82。两边 DSP 引脚和软逻辑引脚的含义不同，这些数字用于说明资源结构改变后的直接连线压力，不是独立性能预测。
- `warm_clear` 两边都是 1008 个复位负载，属于共同存在的重网络，不能将其当成候选独有问题。

同模块、同原语、同参数、同非 Q 连接及相同已知 Q 初值的 FF 分组，在原厂有 3378 个额外实例，候选为 483。这个统计还包含自然重复逻辑，不能把全部差额归因于某一个扇出优化 pass；`core_selected_s*` 是可以直接核对的具体实例。

另外，两边 LIF 的 **336 个局部使能 LUT2 均保留**。没有证据支持“这些显式局部使能门被全部合并”的解释，不应把删除或重建它们作为第一步。

## Long wire 证据的补充

[原厂完整报告](../results/fpgafly_native_baseline_001/mapped.rpt.txt)的 LW 项为 **5/8**。列出的网络是 `app_reset_9`、`core/n2695_5`、`warm_clear`、`core/sampler/n1518_4` 和 DDR `ddr_rst`。这说明可布通基线已经依赖专用 LW 网络；报告中的 8 不等于全芯片所有长距离布线段或 taps 的总数量。

候选的实际 LW 分配仍未知。软逻辑膨胀和负载集中为资源竞争提供了合理解释，但没有新的分配日志就不能断言“新网表需要超过 8 根，所以无解”。原生图构建及二进制证据另见[长线排查](GOWIN_LONGWIRE.md)。

## 优化顺序与验证方法

**第一优先级：补 GW5A DSP 映射。** 在通用算术被拆成位级逻辑之前识别乘法和乘加，先覆盖上述 126 个 LIF DSP 实例对应的模式，再扩展到其余变系数乘法器。首版可以保留外部流水 FF，只把组合算术移入硬 DSP，以减少同时修改 CE、初值和流水级数的风险；随后才评估寄存器吸收与 DSP 级联。每个映射都要核对符号扩展、输出截位、溢出、CE、reset 和周期对齐。LUT 切口 CEC 不能单独证明这些变化正确。

**第二优先级：恢复有依据的负载分散。** 优先从 `core_selected`、`pixel_pulses` 等明确存在原厂分支的网络着手。可用观察到的 64 负载作为实验分组起点，但它不是芯片的普遍硬上限。复制 FF 必须保持 D/CLK/CE/RESET/INIT，并避免被后续 merge 再合并；随后按负载所属模块或布局区域分配，检查上游 D/CE/时钟的新负载。以前任意增加少量 LUT 副本的物理反馈实验曾退化，不能因此默认启用全局复制。

**第三优先级：改善 LUT/ALU 和宽选择映射。** 审查 4373 个 `LUT1 INIT=2`：当前转换器把 BLIF 的单输入直通节点也实体化为 LUT1，部分可能应表达为网线别名。不能一概删除有 keep 意图或用于负载分散的节点，必须先证明连接与功能，再比较扇出。当前 `-nowidelut` 和 ACD LUT4 限制还使用户逻辑失去原厂 3484 个专用宽 mux，适合以寄存器/硬块边界为界保留或重建可识别的选择树，再让 ACD 优化周围逻辑。

**第四优先级：补齐小存储推断。** 原厂的 8 个 RAM16SDP1 位于 read-tags，4 个 RAM16SDP4 和额外 2 个 SDPB 位于 retinal 流水结构；候选未采用这些映射。可单独验证 RAM/SRL 识别，但必须尊重 RTL 中要求寄存器实现的属性，并核对读延迟和读写冲突语义。它的规模小于 DSP 缺口，不应先于 DSP 和重负载分散。

每步先通过资源/功能检查，再进行一次原始 SDC/CST 下的 P&R；保留 3600 秒失败上限。应同时看可布通性、LW 分配、setup/hold 与资源，而非仅优化 LUT 深度。完整 SDC 下的 80 MHz 和开发板功能仍需另外验收。

## 本轮已完成与未完成

已生成可复用的导入器、结构对比器、两侧资源/进位链/重复 FF/扇出清单及工具能力快照。本轮没有修改原始 RTL、默认综合策略或网表，也没有重启完整 P&R。DSP 自动映射、复制优化及宽 mux 重建仍是待实现项，不能将本次分析当作已修复或已收敛。

[73 项自动测试](../results/netlist_comparison_001/validation.log)通过；新增检查保证 FF 初值或控制不同的实例不会被分为同一复制组，并核对层次重复实例的进位链统计。比较记录中的派生 JSON 已成功导入并在两侧使用相同的结构分析器。
