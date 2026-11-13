# GW5AST-138C 长线与布线超时排查

当前结论：LW 资源及其接入点是需要显式检查的物理约束。新综合流程目前只优化逻辑切口和 LUT 延迟，没有把原生 LW 分配与空间可达性纳入代价，因此满足逻辑时序预算仍可能难以布线。但本次 FPGAFly 的日志不足以证明 LW 已耗尽，超时继续按失败记录。

与可布通原厂网表的[同口径对比](NETLIST_COMPARISON.md)进一步确认：原厂使用 DSP 并对部分重负载信号提供多个寄存器分支，当前流程则缺少 DSP 映射，且有更重的集中负载。原厂物理报告的 LW 项为 5/8，但候选实际占用仍未知。

## 官方资料与器件差异

[高云 SUG100](https://cdn.gowinsemi.com.cn/SUG100E.pdf) 明确区分普通布线、长线和时钟布线：`Route Maxfan` 调整普通布线的扇出控制，不控制 LW 或时钟路径；过小的值还可能造成布线失败。当前工程使用的值 23 并不是 LW 配额，也不能通过简单修改此值增加长线资源。

本机随 V1.9.12.04 安装的 SUG1541-1.0E（PDF 第 29 页）规定：`BUFS` 请求 LW 时钟资源，但只支持 LittleBee、Arora I 和 Arora II；`LOCAL_CLOCK` 改用一般布线。目标 GW5AST-138C 属于 Arora V，不能直接套用旧系列添加 `BUFS` 的处理方式。[安装文档路径、版本及哈希](../binary_audit/longwire_004/manuals.json) 保留了核验来源。

[较旧的 SUG935](https://cdn.gowinsemi.com.cn/SUG935-1.4.1_Gowin%E8%AE%BE%E8%AE%A1%E7%89%A9%E7%90%86%E7%BA%A6%E6%9D%9F%E7%94%A8%E6%88%B7%E6%8C%87%E5%8D%97.pdf) 中可以看到 `BUFS`、`LOCAL_CLOCK` 和 CLK/CE/SR/LOGIC 的约束区分，但语法存在不意味着当前器件支持其中所有资源类型。

## P&R 二进制中可以确认的内容

检查对象为安装中的 `gowin.dll`，SHA256 为 `bc9472a5a22cf4a94e39e07439f261f3354c668f92ef9abc735e6678bea53b7b`。未修改安装文件。这里保留的是导出接口、机器码及引用证据，不是恢复的 C++ 源码。

[静态核验入口](../scripts/audit_gowin_longwire.py) 校验全部 8275 个具名导出，对字符串引用和直接调用检查实际指令边界，并输出[证据清单](../binary_audit/longwire_004/manifest.json)。所有地址均为 RVA，不是某个进程的绝对地址。

| 观察 | 二进制证据 | 能说明什么 |
| --- | --- | --- |
| 候选与实际使用是不同状态 | `getLongWireCandidate` / `setLongWireCandidate` 位于 `0x89ac0` / `0x8c070`，访问 Net 的 `+0x74` bit 1；`getLwUsed` / `setLwUsed` 位于 `0x89ad0` / `0x8c080`，访问 bit 3 | 高扇出、被考虑使用 LW、实际占用 LW 不能混为同一个计数 |
| 有独立剩余量字段 | `getLongwire_remain` / `setLongwire_remain` 位于 `0x10a140` / `0x1301b0`，访问 group 对象的 `+0x430` | 二进制包含资源预算管理；尚未据此取得这次运行的实际容量和余量 |
| 138K 有专门的长线图 | [`buildLwRRGraph_5AT138`](../binary_audit/longwire_004/00387640.asm) 位于 `0x387640`，依次构建 SegSpine、Span、Tap、Branch、Sec 图 | 资源是带连接关系的网络，不是任意负载都能使用的统一数量池 |
| 逻辑和次级时钟分别统计 taps | [`0x10673c0`](../binary_audit/longwire_004/010673c0.asm) 的诊断输出包含逻辑、sclk 和总 taps 用量，并枚举行列覆盖范围 | 应检查接入点、空间范围及不同用途间的竞争；不能只看 LUT 利用率 |
| 有逐请求选择和失败处理 | [`0x107efa0`](../binary_audit/longwire_004/0107efa0.asm) 与 [`0x107f880`](../binary_audit/longwire_004/0107f880.asm) 遍历候选/tap 数据，并比较坐标距离；失败后存在清理与重新处理路径 | 可达性和选择策略影响能否落到物理长线上；尚未完整还原所有分配规则 |
| 一般布线有迭代路径 | [`0x107ccb0`](../binary_audit/longwire_004/0107ccb0.asm) 中有 50 轮计数边界、重复网络处理及代价调整；上层 [`0x1097d80`](../binary_audit/longwire_004/01097d80.asm) 还会处理返回状态并再次调用 | 长时间无新进度可能来自反复搜索；这个计数不是整个 P&R 的总轮数或时间上限 |

`LongwireAssignment*` 的旧分组函数也存在于导出表，但本次直接调用扫描未发现调用者，不能仅凭其名字断定当前 Arora V 路径一定使用它们。

从以上连接结构可推断：即便芯片尚有空闲长线，某条网络也可能缺少可用且可达的入口、分支或 tap；高扇出数据、复位和使能若大量跨区分布，会增加这种压力。若未选中长线的网络转到一般布线，还会增加普通布线的拥塞。这里是依据结构作出的机制解释，不是对本次超时的已证实归因。

## 失败网表中的具体风险

[层次扇出分析器](../scripts/analyze_route_fanout.py) 在层次边界合并同一电气网络，只统计实际叶单元引脚，排除常量网络。对本次 `with_pads.json` 展开 144492 个叶单元，得到[完整清单](../results/fpgafly_cpu_flow_003/route_fanout.json)：扇出至少 64 的网络有 255 条，其中 252 条不连接时钟输入。

| 信号或来源 | 叶单元输入负载 | 主要用途 |
| --- | ---: | --- |
| `app_clk` | 46096 | 46095 个时钟引脚，另有 1 个逻辑输入 |
| DDR `ddr_rst` | 5181 | 5179 个复位引脚，另有 2 个逻辑输入 |
| `core/retina.LessThan_229.a[0]` 等别名 | 1658 | 数据/逻辑 |
| 同一数据总线 bit 1 | 1167 | 数据/逻辑 |
| `core/ng_acd_2304` 的 LUT 输出 | 1037 | 数据/逻辑 |
| `core/ng_acd_2303` 的 LUT 输出 | 1017 | 数据/逻辑 |
| `core/boot.neurons.warm_clear` | 1008 | 复位 |
| `core_selected_Z` | 701 | 数据/逻辑，原 CST 显式指定 `LOCAL_CLOCK` |

这不是“需要 252 根 LW”的结论。阈值 64 是诊断筛选值，不是已确认的原生提升阈值；一条高扇出网络也未必使用 LW。分析器未取得布局位置和实际 tap 占用，不能判定资源超额或不可布通。

原始 [`core_selected_Z` 约束](../FPGAFLY_ISCAS2027/constraints/tang_mega_pro.cst) 及其连接保持不变。它可能在一般布线中形成压力，但直接删除约束可能改变资源分配和其他路径，不能将删约束后的结果冒充相同约束下的修复。

## 当前流程增加的检查

[主入口](../scripts/nextgowin.py) 在每次导出时生成 `05_netlist/route_fanout.json`，报告各网络的驱动源、引脚负载、不同负载单元数及用途分类。报告只用于诊断，不用未经校准的 LW 容量阈值拒绝网表。

[导出核验](../results/longwire_emit_smoke_002/manifest.json) 确认加入诊断后的示例用户网表与此前已验证网表逐字节相同。[71 项测试](../results/longwire_validation_001.log) 通过，新增检查保证层次端口不会被重复算作物理负载，以及跨层次常量不会被误报为高扇出网络。

```powershell
python scripts/audit_gowin_longwire.py --out binary_audit/longwire_new
python scripts/analyze_route_fanout.py results/fpgafly_cpu_flow_003/05_netlist/with_pads.json --top thefly_top --out results/fanout_new.json
python scripts/probe_gowin_longwire.py --out binary_audit/longwire_probe_new
```

[动态探针](../scripts/probe_gowin_longwire.py) 使用人工寄存器链和共享 CE/RESET，限时不超过 300 秒，默认在观察到 138K 长线图入口后立即取调用栈并结束。它与完整工程验收分开记录；它不修改 DLL、不烧录，也不将未完成的探针当作成功布线。

[入口观察已完成](../binary_audit/longwire_entry_001/manifest.json)：128 个寄存器的人工探针在约 97.97 秒到达目标入口，随后按诊断脚本停止。其[调用栈](../binary_audit/longwire_entry_001/trace.log) 确认当前器件从 `executePnrTask`、`detailedRouting` 和内部初始化进入 `chip::buildRRGraph`、`chip::buildClockRRGraph`，最终调用 `buildLwRRGraph_5AT138`。这是运行中实际命中的路径，不只是导出表中的名字。

此前[尝试完成该人工探针](../binary_audit/longwire_stress_004/manifest.json) 在 300 秒上限内未结束，只观察到图构建入口，未取得 LW 分配成功/失败清单；未将其作为性能比较或资源耗尽证明。它也不能说明真实工程的一小时超时停在图构建内：真实工程已经完成了 Routing Phase 1，两者的进度不同。

## 后续有依据的修复方向

首先需要取得失败网表中实际 LW 候选、已分配 tap、分配失败及回退网络的清单，再与原厂综合网表在相同约束下比较。现存超时日志只到 Routing Phase 1 完成，没有输出这些统计，因此现阶段不能把超时直接标记为 LW 耗尽。

映射器随后应将扇出与物理跨度纳入代价，优先处理清单中的数据广播和控制网络。可评估保持顺序语义的寄存器复制、局部译码和按负载区域分组；纯组合切口 CEC 不足以证明寄存器复制的初值、复位和使能语义，不能未经另外验证默认启用。复制也可能引入更多连线和时钟负载，必须用实际布线结果决定是否接受。

P&R 超过 3600 秒仍按用户标准判定失败。上述排查没有放宽原始 SDC/CST。

后续[硬资源优化](RESOURCE_OPTIMIZATIONS.md)的首轮完整网表已在 3461.30 秒内生成新位流。其[原生资源报告](../results/fpgafly_cpu_opt_001/06_pnr/mapped.rpt.txt)显示 LW 为 5/8，分配到 app_reset、一个 core 内部控制信号、warm_clear、tile[0].local_reset 和一个 memory 内部信号。本轮没有耗尽 LW，但 ui_clk 仅 64.317 MHz，5126 个 setup 违例；它证明本轮可布通，不能证明旧网表的超时原因。完整阶段耗时合计 3841.72 秒，仍超过一小时，见[验收记录](../results/fpgafly_cpu_opt_001/acceptance.json)。

比较链软化的[第二轮验收](../results/fpgafly_cpu_opt_002/acceptance.json)确认 LW 同样为 5/8，阶段合计 2665.98 秒并生成新位流；Fmax 退化到 60.691 MHz。减少逻辑和缩短布线耗时没有自动带来时序改善，LW 用量也不能代表控制路径的实际连线延迟。
