# 通用时序驱动综合：实现与验收

更新：2026-11-02。**完整冷启动在 FPGAFly 上达到 77.100 MHz，高于当前原厂对照 74.565 MHz；通用优于原厂与原始 80 MHz 收敛目标仍未达成。**

## 比较合同

候选必须从同一 RTL 独立综合，保留器件、原始 SDC/CST、IP、初值、接口、状态机延迟和后端选项。原厂完成技术映射后的用户逻辑只能作为基线，不作为自主候选的种子。每个冷启动尝试从前端到位流共用不超过 3600 秒的实际墙钟预算，超时即失败。

验收需要同时记录 setup/hold、原始约束下的各时钟频率与 TNS、未布通网络、LUT/ALU/FF/BRAM/DSP/LW、位流以及耗时。生成位流仅表示后端完成，不表示时序收敛。一次工程或一个小基准获胜，也不构成通用优势；固定多设计批次、未参与调参的验证集和重复实验仍是最终要求。

## 已实现的方法

1. **资源优先**：使用高云 RTL 展开前端，自主进行资源推断与逻辑映射，保留 DDR/IP 和硬资源接口。已支持经过核实的乘法 helper 到 DSP、分布式 RAM/BRAM 等路径；工程中的 `read_tags` 数组恢复另有专用适配，不计作通用存储器反编译器。
2. **保留等价实现**：ABC 延迟导向 AIG 重构后，重新绑定并证明边界，重载到达/要求时间。按 Gowin LUT4 和私有 MUX 结构选择 cut，进行有上限的等价 cut 恢复、精确 Shannon 分解和最终活跃面积约束。
3. **联合选择寄存器控制逻辑**：保留原 FF 与完整次态的等价实现，选择时计入 FF 端口成本和最终 LUT 面积。该步骤不跨寄存器移动逻辑，不改变原始延迟。
4. **合法性与功能证明**：读取实际映射 JSON 中的 LUT INIT 再做 CEC，观察硬资源输入、接口以及 `keep` 内部信号；校验 FF 初值、时钟、控制优先级、专用 MUX 所有权与硬资源配置。
5. **受限物理修复**：可选普通 LUT、确定初值的同步 FF 与完整专用 MUX 簇复制，分别受面积/数量上限和保护规则约束。原生后端决定其实际效果；目前逻辑层次仍只是局部性启发式，尚无经过验证的 Gowin 预布局/拥塞模型。
6. **物理反馈选择**：在共享预算内串行执行候选，以同约束的原生 P&R 审计结果决定保留项，失败和退化均保留。公开入口没有按基准名称选择算法。

目前综合成本模型覆盖 LUT、部分 FF 端口与估计线延迟；它不是完整多时钟 SDC STA。零到达时间的默认组合映射先最小化全局延迟再恢复面积；不能声称所有 SDC 例外已进入这一步的优化目标。原始 SDC 的完整物理验收由高云后端执行。

## DSP 输出寄存器吸收

入口为 `pack_dsp_registers=true`，实现见 [dsp_output_register_pack.py](../scripts/dsp_output_register_pack.py)。适用于独占纯乘法输出的正沿 FF 组，要求时钟/使能一致，无已知初值、无复位和保留状态，不使用级联输出，只覆盖已证明的低 45 位。其他情形保持原实现。

新增显式开关 `pack_dsp_inverted_enable=true`：在上述条件不变时支持同组低有效 CE，共享一次使能取反后接入正有效 DSP CE，并要求单独的 SDK 低有效使能状态证明。默认关闭；checkpoint 重建候选和 portfolio 复制证明均已接入。VexRiscv 实测减少 132 FF/177 LUT、82 MHz 完整冷启动通过，但未超过原厂在 85 MHz 的合格点；不能将其面积收益解释为通用频率优势。详见 [本轮研究及消融](RESEARCH_138K_EXECUTION_20261102.md)。

证明使用安装 SDK 中固定哈希的 MULTALU27X18 语义，保留两组独立状态和符号乘法；在不活动全局 GSR 的前提下证明状态关系的保持。未定义的源初态允许实现选择；用户明确指定的 INIT 不会被忽略。反转使能必须成为反例。生产资源、实际网表、发射与 resume 均核对相同的授权单元和证明文件哈希。

整板资源阶段已吸收 **126 个 DSP 周围的 3108 个 FF**。微型完整流程与 resume 已通过原生时序审计；整板结果见下表。该步骤不是一般 retiming、MAC 或 PREG/OREG 多级融合。

[同步 FF 复制](../scripts/native_register_replication.py) 仅处理具有确定初值的同步 FF，采用 SDK 初态与归纳证明、精确图折回和发射后边界 CEC。整板 19 个复制的检查点得到 75.208 MHz，随后公开配方完整冷启动达到 77.100 MHz；两者仍未满足 80 MHz。公开开关为 `register_replication`；它补充已有 CE 扇出修复，覆盖满足条件的同步 RESET 等混合负载。

[完整专用簇复制](../scripts/native_cluster_replication.py) 保持私有 LUT/MUX 数据树的独占连接。整板 35 个簇复制加 FF 修复退化至 60.716 MHz，故公开开关 `dedicated_cluster_replication` 保持默认关闭。两项公开变换的小工程已通过实际发射 CEC、原生时序与位流；RTL 冷启动、resume 和组合入口也有单独成功记录。

`register_control_penalty_ps=206` 为控制路径添加合成惩罚，审核后的 D 端 263 ps 和 C2Q 345 ps 保持独立。该值按旧/新数据代价差构造，在旧检查点上得到完全相同的 1823 个寄存器选择；它是开发配方，不是实测控制端 STA 弧，不能替代完整物理验收。

## 论文对应关系

| 工作 | 在本项目中的位置与边界 |
| --- | --- |
| [ACD，TCAD 2024](https://people.eecs.berkeley.edu/~alanmi/publications/2024/tcad24_acd.pdf) | 固定 ABC 版本的分解/映射复现；器件适配单独验收 |
| [EqMap，ICCAD 2025](https://www.csl.cornell.edu/~zhiruz/pdfs/eqmap-iccad2025.pdf) | 已编译作者代码并适配实际 LUT 输出；十设计开发对照总体退化，未设为默认 |
| [MappingEvolve，DAC 2026](https://arxiv.org/html/2604.26591v1) | 借鉴阶段化延迟/面积恢复；未复现作者完整演化系统 |
| [Nextmap，PLDI 2026](https://zsisco.net/papers/nextmap-pldi26.pdf) | 已取得源码，研究字/位语义和 DSP 联合映射；尚未完成 Gowin 适配 |
| [Lakeroad，ASPLOS 2024](https://homes.cs.washington.edu/~rjust/publ/lakeroad_asplos_2024.pdf) | 采用原厂硬核语义与独立证明的原则；当前 OREG 规则是受限独立实现，不是作者工具复现 |
| [Memo，PLDI 2026](https://hardekbc.github.io/files/sisco26fungible.pdf) | 通用状态聚合、读写端口恢复的后续依据；尚未接入 |
| [PhyMap，ASP-DAC 2026](https://www.aspdac.com/aspdac2026/archive/pdf/5F-4.pdf) | 物理反馈与增量关键路径研究依据；ASIC 预布局不能直接视为 Gowin 物理预言器 |
| [FAPO，2026 预印本](https://arxiv.org/abs/2610.06937) | 已有独立核心搜索与消融；当前原生复制启发式不等同完整联合 min-cut 优化 |
| [PACT，作者标注接收 FPT 2026](https://arxiv.org/html/2608.23602v2) | 布线证据、隔离候选与验证后接受的流程参考；Vivado/RapidWright 后端方法，尚未移植 |

完整资料和来源边界见 [TASKS.md](../TASKS.md)。不把预印本当作已经核实的顶会发表，也不把其他 FPGA 架构的 LUT6/DSP 配置直接套用到 GW5A。

## 当前实测

| 同 RTL / 原始约束的 FPGAFly 结果 | UI Fmax | setup / hold 违例 | 范围 |
| --- | ---: | ---: | --- |
| 全新原厂基线 | 74.565 MHz | 432 / 0 | 完整原厂综合与 P&R；未穷举原厂设置 |
| 自主完整综合第二版 | 72.610 MHz | 829 / 0 | 冷启动实际墙钟 2977.410 秒 |
| 自主 LUT 复制第三版 | 73.465 MHz | 519 / 0 | 检查点实验，不能当作冷启动耗时 |
| 展平 LUT4 冷启动 | 54.092 MHz | 3509 / 0 | 实际墙钟 2080.844 秒；退化 |
| DSP OREG 冷启动第二次 | 68.846 MHz | 2486 / 0 | 3186.328 秒生成位流，LW 3/8、FF 44833；配对审计通过但退化 |
| 同步 FF 复制检查点 | 75.208 MHz | 167 / 0 | 19 个复制，LW 7/8，位流与配对审计通过；含准备/等待 3220.878 秒，非 RTL 冷启动 |
| 专用簇＋同步 FF 检查点 | 60.716 MHz | 1249 / 0 | 35 个簇、23 个 FF 复制，LW 7/8，1517.720 秒完成；退化 |
| 公开 FF 配方冷启动第一次 | 无 | 无 | 563.140 秒后变换前 JSON 重载失败；修复与整板发射探针通过，失败保留 |
| 公开 FF 配方冷启动第二次 | 77.100 MHz | 184 / 0 | 从原始 RTL 到位流 2592.563 秒，LW 6/8，严格配对审计通过；未启用簇复制和 OREG |

原厂与现有最佳自主实现都未满足原始 80 MHz 目标。新配方十一设计开发批次完成十项，Fmax 比值几何均值 **0.837054**，仅一项有效提升，并保留 `log2` 原厂未布通失败；未证明通用优势。早期固定批次为 0.87734，EqMap 作者实现适配对照为 0.931765，均不能替代新方法的验收。

新冷启动综合/证明/发射约 9 分 33 秒，完整墙钟 43 分 13 秒。LUT 40814、ALU 9287、FF 48001、BSRAM 268；原厂分别为 36633、5375、47744、270。该工程获得约 3.4% 的单次频率收益，但逻辑资源与耗时更高，不能称为总体 Pareto 优势。最新 119 项测试和全部正负物理结果见任务记录。

直接 DLL 路径的综合阶段报告另有约束绑定缺口：37 ns 探针仍显示默认 10 ns，整板 JTAG 与异步组也不匹配。因此该诊断结果不能充当原始约束下的候选 oracle；不能把综合阶段约 197 MHz 的估算当作实际实现。完整对照的原生 P&R 另行审计了全部五个时钟、例外命令与 SDC/CST 哈希。

## 复现入口

```powershell
python scripts/run_frozen_flow.py --config configs/fpgafly_rtl_fanout_v1.json --out results/new_fanout_run --total-timeout 3600
python -m unittest discover -s tests
```

输出目录必须新建。冻结入口保存代码、配置和模型快照，检查运行期间方法未变；可选的候选组合入口为 `scripts/nextgowin_portfolio.py`。自研综合/时序评估按用户选择默认使用单核 CPU，GPU 不作为本轮 QoR 改进的前置条件。
