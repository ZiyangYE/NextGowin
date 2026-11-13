# 实际工程与完整 I/O 高频优化任务

日期：2026-11-02。状态：本轮实现、实测与复核已完成；多数设计显著优于原厂及完整 I/O 200 MHz 目标仍未达成。

**固定引脚是硬约束。用户已明确禁止重新分配引脚的实验。** 曾读取引脚表以检查 Bank 10，但没有生成或运行重新分配引脚方案。以下采纳结果均校验实际引脚与各设计冻结的原厂迁移基线一致，使用原 3.3V/8mA 设置。

用户明确要求评测包含 I/O。因此所有验收保留输入/输出 max/min 延迟、setup/hold 和完整时钟约束；不使用 false path 排除数据 I/O。`int2float_core200_probe_001` 的 248.085 MHz 仅属诊断，不作为性能达标或提升证据。原约束下的历史结果保留。

执行计划：

1. 固定 Sipeed TangPrimer-25K-example 提交 `27c1737b687369f46baff9cf621cd7499a1b7fd4`，选择 UART 收发核心、HDMI TMDS 编码器、LiteX 的完整 VexRiscv CPU 模块及完整 LCD 控制器迁移到 GW5AST-138。原 25K 引脚不复用；各工程初次迁移的 138K 引脚由原厂首次实现确定，此后所有候选固定一致。UART 核心不包含演示字符串发送器和调试振荡器；TMDS 不冒充完整 HDMI 系统。
2. 对实际工程建立 100/200 MHz 完整 I/O 约束并比较原厂、自主宽 cut/LUT4，保留所有失败。异步接口的板级协议与外部器件额定速率不以内部 STA 替代。CPU 模块不冒充完整 LiteX SoC：仓库未提供 SoC 所需三个初始化文件。
3. 针对低占用场景，加入可穷举的小输入函数到 BSRAM ROM 的候选；保持原接口周期、初值，使用实际 SDK 原语验证，再进行原生 P&R。不是按 int2float 名称硬编码。
4. 检查 Gowin 时钟输入/全局时钟/PLL 补偿及 IOB 的实际代价。时钟与 I/O 电气设置变更单独标注，并给原厂相同条件；不得通过 SDC 相位虚构、删掉 I/O 或 hold 检查制造 200 MHz。
5. 汇总可用改进、限制及新工程结果。每个完整自主尝试仍共用一小时预算；不烧录硬件。

已修复 Windows Icarus 运行环境：子进程 PATH 同时包含套件 bin/lib，`iverilog -B` 使用 Windows 原生路径，`vvp -M - -M <lib/ivl>` 显式指定 VPI 目录，绕开短路径查找失败；禁用本工具子进程的系统模态错误框，失败进入日志。没有修改系统 PATH 或 SDK。

## 已验证结果：包含全部 I/O，固定引脚

以下是实际通过的测试频率，不是精确最大频率。每个点均复核 setup、hold、recovery/removal、pulse、所有实际非时钟 I/O 的 max/min 约束、引脚及电气设置；没有数据 false path 或 multicycle。原厂与自主结果在同一设计内使用相同时钟/IOB 策略。

| 设计 | 原厂通过 MHz | 自主通过 MHz | 自主方案 | 判断 |
| --- | ---: | ---: | --- | --- |
| int2float | 149 | **157** | LUT4、输出 IOB、LOCAL_CLOCK | 小幅优势，未到 200 MHz |
| UART 收发核心 | 136 | **143** | LUT4、输出 IOB | 小幅优势 |
| TMDS 编码器 | 136 | **136** | 短进位链布尔化 + LUT4，保留原 FF 控制 | 追平当前对照，面积仍更大 |
| VexRiscv CPU 模块 | 82 | **82** | 内存属性修复 + LUT4 | 同频闭合，面积仍更大 |
| LCD 控制器 | 78 | **78** | 宽 cut、FF 留核心 | 统一全端口压力约束下持平 |

这里保持同一 RTL 扫描频率，未同步改变 UART 分频、LCD 等待时间等协议参数。因此这是 FPGA 时序实现测试，不是 UART 波特率、显示器接口额定速率或板卡运行验收。LCD 的转发时钟端口也纳入了保守的统一端口约束；本轮没有另外建立接收器的源同步时序模型。VexRiscv 使用 284/312 个 I/O（约 91%），不属于“各项资源低于 40%”集合，作为实际 CPU 挑战项单列。其余小设计的低逻辑占用不意味着 I/O 延迟可忽略。

证据：[完整 I/O 覆盖复核与通过点](../results/practical138_verified_summary_001.json)、[首次闭合检查](../results/fullio_closure_001/manifest.json)、[较高工作点](../results/fullio_closure_002/manifest.json)、[接近报告上限的复核](../results/fullio_closure_003/manifest.json)。旧十项几何均值没有被这些开发结果替换；不能据此宣称通用显著优势。

[同频补测](../results/fullio_challengers_001/manifest.json)另外确认：UART 原厂在 143 MHz 有 1 个 setup 违例，自主方案通过；CPU 修复后 LUT4 在 82 MHz 也通过，宽 cut 虽 setup 通过却仍有 1 个 hold 违例，不能采纳。CPU 在 75 MHz 下曾报告 Fmax 75.056 MHz，但重新实现到 82 MHz 后仍闭合（报告 82.179 MHz）：hold 修复和路由会随目标改变，因此不能把一次低约束下的报告频率当作设计上限。

## int2float：157 MHz 是完整 RTL 冷启动结果

[冻结配置](../results/int2float_fullio157_cold_001/config.json)从原始 RTL 到位流完成，墙钟 **30.282 秒**；157 MHz 全时序通过，报告 Fmax 157.689 MHz。原厂重新从同一 RTL 执行 `map -t`：157 MHz 下 setup 2、hold 8；149 MHz 下全部通过，报告 Fmax 149.620 MHz。两侧均通过完整 registered-core next-state CEC，包括初值、CE/reset 和原有周期延迟。

这次原厂对照是新跑的，不仅是复用原先 100 MHz 综合网表。原厂基线范围仍是固定 SDK 的直接综合流程，不是穷尽原厂所有 GUI/实现选项后的最优值。

- [冷启动与位流记录](../results/int2float_fullio157_cold_001/run/manifest.json)
- [新原厂配对及双侧功能证明](../results/int2float_fresh_pair_001/manifest.json)

复现：

```powershell
D:/Conda3/python.exe scripts/run_frozen_flow.py --config results/int2float_fullio157_cold_001/config.json --out results/new_int2float157_run --total-timeout 240
```

## 两个通用实现方向及一个实际流程缺陷

**保留内存映射意图。** `restore_verific_memories.py` 原来只恢复 `syn_ramstyle="registers"`，丢掉了 Verific 注释中的 `ram_style="block"` 和 `no_rw_check=1`。原 CPU RTL 明确给寄存器堆设置了这些属性，桥接后却被选成 LUTRAM。现已恢复显式属性，记录到转换清单，并拒绝矛盾配置；不会自行给未标记的 RAM 添加 `no_rw_check`。

修复后，VexRiscv 的寄存器堆实际改用 BSRAM：总 BSRAM **8→10**，FF **1849→1526（减少约 17.5%）**；宽 cut 的 LUT **4332→3933**。200 MHz 压力目标下报告 Fmax 从 **78.655→83.271 MHz**，但两者均未闭合，不能当作可用运行频率。LUT4 已在 75 MHz、82 MHz 实际通过。该缺陷修复已经进入公共桥接流程，不是设计名特判。

证据：[修复后 CPU 候选](../results/tangprimer138_cpu_attributes_003/manifest.json)。原厂同源 CPU 为 10 BSRAM、1319 FF，说明仍有状态/逻辑优化差距。

**短进位链重新参与布尔映射。** 新增 `scripts/lower_short_carry.py`，按链长选择整个 ALU 链，保留长链和保护属性；以已审计的 Gowin ALU 模式 0–8 方程生成布尔逻辑，经过 CEC 后交给 LUT mapper。TMDS 候选去掉 60 个 ALU；物理 ALU 从旧方案 95 降到 33，LUT4 版本通过 136 MHz，旧自主版本在 200 MHz 压力目标下的报告 Fmax 仅 83.155 MHz。此候选还关闭了 FF 控制重写，不能将全部差值只归因于 ALU 降低。原厂面积仍更小：105 LUT/37 ALU，对比该候选 192 LUT/33 ALU。该功能保持显式实验候选，未推广为所有工程默认。

证据：[短进位链证明及物理结果](../results/tmds_short_carry_001/manifest.json)。

**小输入函数转 BSRAM 并移动现有寄存器。** 新增 `scripts/registered_rom_mapper.py`，对能装进一个 16-Kbit pROM 的给定组合核心生成精确表；提供输入侧、输出侧、ROM 内部寄存器三个候选，保持原两周期合同。移动寄存器的版本要求 `F(0)=0`，避免破坏零初值。实际发射网表使用高云原始 SDK 原语遍历全部输入验证。

int2float 的 2048×7 表由一个 BSRAM 实现。输出侧寄存器版本在固定原引脚、完整 100 MHz I/O 下通过：**0 LUT、7 FF、1 BSRAM**，对比 LUT4 版本 85 LUT、18 FF；200 MHz 仍失败。它是明确的面积/硬资源交换，不是已经获得 200 MHz 的证据。当前入口接收 BLIF 与二级寄存器合同，尚未自动从任意 RTL 抽取此类锥。

证据：[实际 SDK 功能验证](../results/int2float_rom_output_001/manifest.json)、[完整 I/O 物理审计](../results/fullio_closure_001/int_rom100/physical_audit.json)。

## 为什么固定引脚下仍没有 200 MHz

157 MHz 最差路径已位于同一个 IOB：数据布线 **0 ns**，C2Q 与输出缓冲合计 **2.859 ns**，发射时钟路径 **3.483 ns**，合计约 **6.342 ns**。157 MHz 的报告 slack 仅约 0.028 ns。把数据线再缩短不能修复这一条路径；200 MHz 的 5 ns 预算需要进一步降低时钟插入或真实 I/O 单元代价，而不是仅减少 LUT 层数。

Bank 10 的 CFGBVS 输出引脚 AB15 对应 `q[1]`，在原厂与自主网表中均未打包进 IOB FF。将控制改成完整 D 输入后也仍未打包；该观察与配置 Bank 的限制相符，但不是整条时序失败的唯一原因。**按用户要求，未做换引脚试验。**

以下负结果全部保留：

- [普通布线时钟](../results/int2float_clock_ablation_001/manifest.json)：改善后在较低目标闭合，但 200 MHz 仍失败。
- [PLL 内部反馈](../results/int2float_pll_ablation_002/manifest.json)、[外部反馈](../results/int2float_pll_external_001/manifest.json)：有较高内部 Fmax，完整 I/O setup/hold 均失败，未采纳。初次 PLL 参数的 PFD 频率超限也保留，后续按 50 MHz PFD/800 MHz VCO 修正。
- [HCLK/CLKDIV 除以 1](../results/int2float_hclk_002/manifest.json)、[纯 D 输入补充候选](../results/int2float_hclk_plain_001/manifest.json)：可实现，但仍使用 PRIMARY 时钟网络，17/18 FF 进入 IOB；200 MHz 失败。
- [16/24 mA 驱动对照](../results/int2float_drive_ablation_001/manifest.json)：本组报告没有时序收益。最终采纳点回到原 8 mA；不推断所有电气条件均无作用。
- [CPU 路径指导 FF 复制](../results/cpu_route_guided_001/manifest.json)：只找到一个符合安全条件的 >2 ns 临界 FF→LUT 边，完成状态证明和发射后 CEC；报告仅从 78.661 到 79.046 MHz，90 MHz 仍失败，未采纳。
- [仅内部路径的 248.085 MHz](../results/int2float_core200_probe_001/NOT_ACCEPTED.json)：明确取消验收资格，因为该诊断排除了 I/O。

## 工程适配、验证与后续重点

代码来源为 [Sipeed 固定提交](https://github.com/sipeed/TangPrimer-25K-example/tree/27c1737b687369f46baff9cf621cd7499a1b7fd4)，原仓库文件保持未修改。VexRiscv 的显式全 X 指令写数据端口、LCD 的未驱动串口输出，在迁移副本中固定为零；只确定未定义输出，双方使用同一副本并保存哈希。没有虚构缺失的 LiteX SoC 初始化文件，也没有声称完成 CPU 启动或外设硬件验证。

[124 项回归测试](../results/practical_138k_tests_001.log)通过，包含带属性内存恢复的行为仿真、ALU 各模式 CEC 与故意损坏的反例、三种 ROM 寄存器位置的 SDK 仿真及初值拒绝检查。所有完整自主尝试均在各自一小时预算内；未烧录硬件。

后续要获得明显优势，应优先研究：保留字级算术后再选择进位链/LUT；让映射代价包含实际 IOB 可打包条件与时钟路径；对已报告的关键锥做有界重映射与布局反馈；在不改变接口周期的前提下进行有证明的寄存器移动。已有复制、PLL、HCLK 和驱动尝试说明，仅扩大参数搜索不能保证跨过当前 I/O 瓶颈。固定引脚、完整 I/O、原接口周期继续作为验收条件；200 MHz 和多数设计显著胜出仍是未完成目标。
