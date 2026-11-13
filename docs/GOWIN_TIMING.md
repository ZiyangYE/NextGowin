# Gowin 138K 时序模块拆分记录

日期：2026-11-01。目标安装：Gowin V1.9.12.04 x64，目标器件 GW5AST-138C。

已将安装包中的器件时序数据解析为独立 JSON，并整理 STA 模块的导出接口和读取函数反汇编。安装包未发现 STA 实现的原始 C++ 源文件；另已拆出实际可读的原厂 Verilog 功能/时序仿真原语源码。交付同时包含这些原始仿真源码、独立数据库解析器、报告提取器和审计脚本。反汇编保留为二进制证据，不称为恢复的原始 C++ 源码。

## 代码与产物

| 内容 | 位置 |
| --- | --- |
| 器件数据库拆分源代码 | [split_gowin_timing.py](../scripts/split_gowin_timing.py) |
| Apicula 旧式表解析源代码 | [tm_parser.py](../third_party/apicula/tm_parser.py)，附 [MIT 许可证](../third_party/apicula/LICENSE) |
| STA 接口与函数审计源代码 | [audit_gowin_sta.py](../scripts/audit_gowin_sta.py) |
| 综合器对共用 STA 的依赖审计 | [audit_synthesis_dependency.py](../scripts/audit_synthesis_dependency.py)，[解析结果](../binary_audit/timing/synthesis_dependency.json) |
| 时序报告提取源代码 | [extract_timing.py](../scripts/extract_timing.py) |
| FF 查询路径抓取源代码 | [trace_ff_queries.py](../scripts/trace_ff_queries.py) |
| FF 数据约束扩展源代码 | [extend_ff_model.py](../scripts/extend_ff_model.py) |
| 原厂原语源码拆分脚本 | [split_primitive_models.py](../scripts/split_primitive_models.py) |
| 原语引脚方向提取脚本 | [extract_primitive_ports.py](../scripts/extract_primitive_ports.py)，[136 种方向表](../results/timing/primitive_ports.json) |
| 全部可读原语源码清单 | [primitive_sources_all_002/manifest.json](../results/timing/primitive_sources_all_002/manifest.json) |
| LUT4 时序仿真原始源码 | [module_LUT4.v](../results/timing/primitive_sources_002/tsim/module_LUT4.v) |
| DFFRE 时序仿真原始源码 | [module_DFFRE.v](../results/timing/primitive_sources_002/tsim/module_DFFRE.v) |
| DLL 接口、地址和哈希 | [manifest.json](../binary_audit/timing/manifest.json) |
| 数据库拆分清单 | [manifest.json](../results/timing/vendor_split/manifest.json) |
| 完整已解析数据 | [timing.json](../results/timing/vendor_split/timing.json) |
| 新流程探针 | [gowin_probe.py](../scripts/gowin_probe.py)，[运行清单](../results/gowin_probe_005/manifest.json) |
| 原生报告观测弧 | [observed_arcs.json](../results/timing/observed_arcs.json) |

原始输入为安装目录 `IDE/share/device/GW5AST-138C/GW5AST-138C.tm`，大小 3,139,786 字节，SHA256 为 `a3558f35685ebc58eefce834e82059ce651b9e2a6cbca2d6668094153ca0707f`。安装文件保持原样。

## 可直接阅读的原厂时序源文件

安装目录另有 `IDE/simlib/gw5a/prim_sim.v` 和 `prim_tsim.v`。前者描述功能，后者增加路径、setup/hold、period、width 等 Verilog `specify` 检查。它们是仿真原语源码，不是 DLL 中的 C++ STA 引擎。

当前按原语拆出 136 种源单元、268 个文件，覆盖可用的功能和时序版本，包含 LUT、FF、ALU、MUX 及硬资源。每个文件保留原始版权头与 timescale；清单保存原文件哈希、字节范围、原语体哈希和输出文件哈希。测试逐一确认 268 个文件及 36 个核心原语文件与安装源的对应字节一致。功能源 SHA256 为 `7109c2568188627465d218bc099cf72f12e90807c21d4d862d2335b499043544`，时序源为 `a45d0fe16080505081ea1252e71653dabd8910821176d628bea5bc32d78b4e0d`。

示例 DFFRE 的源码明确显示 RESET 为同步复位，而 DFFCE 的 CLEAR 为异步清零；前者在时钟沿处理 RESET，后者还对 CLEAR 变化响应。时序版本给出了 D、CE、RESET 相对 CLK 的检查关系，但示例 LUT/FF 的延时和检查值为 `0:0:0` 占位，不能作为器件延迟数值。仿真仍需相应全局实例、环境和回标数据；`sim` 与 `tsim` 两种库应分别使用。

```powershell
python scripts/split_primitive_models.py --out results/timing/primitive_core_new
python scripts/split_primitive_models.py --all --out results/timing/primitive_all_new
```

早期 `primitive_sources_001` 和 `primitive_sources_all_001` 因 Windows 不区分 `MUX2.v` 与 `mux2.v` 而覆盖文件，已被字节校验拒绝并标为 `invalid`。`002` 版本以 `module_`/`primitive_` 前缀区分源码单元，并检查文件名碰撞。

## 模块定位

在 `gowin.dll` 中识别出 235 个 STA/SDC 相关导出符号，包含 `STA::TmMonitor` 的时序数据读取、LUT/FF/ALU 延迟查询，以及 SDC 解析相关接口。它们与网表和器件对象存在依赖，不能仅复制几个函数就得到独立可用的 STA 引擎。

`gwsyn.dll` 从 `gowin.dll` 导入 573 个接口，其中 7 个属于 STA：`getTmMonitor`、`updateTiming`、`restartTiming`、`reportClocksForSyn`、两个 `reportMaxFreqForSyn` 重载和 `reportTimingByAnalysisTypeForSyn`。全部已解析到当前 DLL 的导出地址，证明综合器确实依赖共用时序模块。这是静态导入证据，不表示每条综合命令都会执行全部接口。

早期审计受 pefile 默认 8192 个导出上限影响，只记录了 224 个相关符号。当前显式提高上限，并核对解析出的 8275 个具名导出与 PE 表声明总数一致；因此修正为 235。该修正不改变已验证的数据库读取函数地址和 12 份反汇编。

与数据库结构直接相关的函数，以当前 DLL 哈希为准：

| 函数 | RVA | 用途 |
| --- | --- | --- |
| `TmMonitor::readTimingData` | `0x139dbb0` | 时序读取入口，跳转到内部实现 |
| 内部读取实现 | `0x139dbc0` | 选择器件文件，读取固定表与扩展表 |
| 扩展表读取器 | `0x148a3e0` | 解析 55、56、57 标记的结构 |
| 浮点行读取器 | `0x1489a40` | 读取时序值及选择相应槽位 |
| 关系表读取器 | `0x1489c40` | 读取按索引组织的关系记录 |
| 关系记录读取器 | `0x1489d70` | 读取源目标编号、属性与浮点值 |
| 附加映射读取器 | `0x14897b0` | 读取特殊组内的映射矩阵 |
| 通用单元弧查询 | `0x1444780` | pin row ID 重映射，再复制 selector 对应的 4 个浮点值 |
| FF 弧分派 | `0x1446fc0` | 按引脚、标志和配置选择 FF 时序行 |

RVA 是模块内相对地址，不能作为运行时绝对地址。上述辅助函数地址只对审计清单中的 DLL 版本成立。

## 数据结构与拆分范围

固定表有 3 个 15,552 字节块。其 LUT、ALU、DFF、SRAM、BRAM、fanout、clock 和 wire 等已知字段通过 Apicula 解析，分别输出为 `legacy_*.json`。上游对 ES 与 C1/I0 等名称存在别名处理；别名不构成当前器件 corner 正确性的独立证明。

后续扩展结构依据当前 DLL 读取路径解析：

- 标记 55：9 组、317 行浮点数据，另含编号映射和特殊组附加矩阵，分别保存为 `extended_group_0.json` 至 `extended_group_8.json`。
- 标记 56：40,221 条关系记录，保存为 `extended_relations_56.json`。
- 标记 57：13,745 条关系记录，保存为 `extended_relations_57.json`。
- 末尾 15 字节保存在 `unparsed_tail.bin`，未赋予语义。

每条浮点行或关系记录保留原始字节偏移、编号和数值。解析过程检查所有段落起止标记；测试将全部 317 行和 53,966 条关系记录重新编码，与原文件对应字节逐项比较。

这些检查证明当前数据结构的读取边界及数值保真度，不证明所有编号的含义。当前保留扩展组、关系端点及属性的数字 ID，尚未将其全部映射为 LUT 引脚、setup/hold、速度等级与 PVT corner。旧式表与当前报告有差异，不能直接将旧式表命名字段当作最终器件模型。

## 重现命令

在仓库根目录执行：

```powershell
python scripts/split_gowin_timing.py --out results/timing/vendor_split
python scripts/audit_gowin_sta.py --out binary_audit/timing
python scripts/audit_synthesis_dependency.py --out binary_audit/timing/synthesis_dependency.json
python -m unittest discover -s tests -v
```

拆分脚本需要本地已保存的 Apicula 解析器；其下载来源和内容哈希保存在拆分清单中。该源文件按内容哈希固定，不能仅靠上游 master 地址保证重现。

重跑受控探针时选择新的输出目录，避免旧报告造成假阳性：

```powershell
python scripts/gowin_probe.py --out results/gowin_probe_next
```

探针分别验证 `gwsyn.dll` 的分阶段 `map -t` 流程，以及外部 LUT/FF 网表直接进入 `gowin.dll` 的 P&R 路径。两者输入设计不同；该探针是接口验证，尚未证明完整自研综合器的正确性。P&R 输入哈希及实际产物已检查。

## 运行时模型验证进展

已增加 [运行时抓取脚本](../scripts/capture_runtime_tables.py) 和 [模型生成源代码](../scripts/build_timing_model.py)。抓取脚本校验 DLL 哈希后，在时序库初始化完成处暂停，导出全部 317 行，再主动结束调试进程。该模式不执行完整 P&R，不生成新的布线结果。

最新可复现抓取为 [gowin_snapshot_002 清单](../results/gowin_snapshot_002/manifest.json)。抓取到的每个浮点槽位都与原始数据库对应行核对，包括零值和负约束值。当前配置下，槽位 0 至 3 保持原值，4 至 7 乘 1.125，8 至 11 乘 0.9；317 行全部符合。该结论限定于当前器件、版本和探针配置。

初步 [综合模型](../results/timing/gowin138_model.json) 保留内部 LUT 输入 A/B/C/D 的延迟范围及 DFF clock-to-Q。模型包含的 LUT 单元延迟上界为 521 ps；逻辑引脚到物理输入的重排仍未恢复，完整 FF setup/hold 约束也未接入。

[报告交叉验证](../results/timing/model_validation.json) 中 126 条 LUT 和 DFFRE clock-to-Q 观测弧与模型值在报告精度内一致。LUT 检查比较内部输入的数值集合，并未证明逐个逻辑引脚的对应关系；这些观测弧存在多路径重复，不等于 126 条独立器件参数。

模型已接入小规模 ACD 参考实现。在输入到达时间为 5000 ps、端点要求为 6000 ps 的例子中，优化分解得到 5521 ps；将晚到输入置于前级的最佳候选为 6042 ps。该结果仅是单元延迟模型下的组合分解验证，未包括互连与完整布局布线。

使用模型导出的四个内部输入上界 521、517、457、287 ps，原生 CUDA 评分与 CPU 一致。最新一次百万候选的中位耗时为 CPU 2.1200 ms、GPU 驻留 0.2227 ms、包含传输 4.6257 ms，说明驻留迭代仍是必要方向。详见 [实验结果](../results/gpu/gowin_cut_scoring.json)，它不是完整 mapper 性能。

重现运行时模型和交叉验证：

```powershell
python scripts/capture_runtime_tables.py --out results/gowin_snapshot_next
python scripts/build_timing_model.py --snapshot results/gowin_snapshot_next/snapshot.log --out results/timing/gowin138_model_next.json
python scripts/validate_timing_model.py --model results/timing/gowin138_model_next.json --pair results/gowin_probe_005/probe.tr results/gowin_probe_005/probe.vo --out results/timing/validation_next.json
python scripts/acd_reference.py --model results/timing/gowin138_model_next.json --required-ps 6000 --out results/acd/gowin_next.json
```

`gowin_probe_006` 和 `gowin_probe_007` 是中间调试试验，未完成 P&R；不得将其报告完整性或运行时间作为实现验证。完成 P&R 的受控基线仍为 `gowin_probe_005`。

## FF 数据约束与路由分支

[FF 查询抓取](../results/gowin_ff_trace_004/manifest.json) 在原生查询入口记录 row ID、调用位置、源目标引脚 ID 与两个分派参数。受控 DFFRE 的 clock-to-Q 请求第 24 行；数据端的 setup/hold 请求第 4、6 行，时钟边沿标志为正沿。该抓取在获得查询证据后主动退出，不执行完整 P&R。

只用第 4、6 行建立的早期模型在小探针上通过，但对三组 priority 报告总计 389 条可比较记录仅匹配 368 条，见 [被拒绝的单分支验证](../results/timing/ff_single_branch_rejected.json)。不匹配包括 setup 调整 -0.262 ns 与 hold 调整 -0.048 ns，说明按原语类型固定一对时序行会遗漏物理配置。

进一步检查 [FF 分派反汇编](../binary_audit/timing/01446fc0.asm)，确认同一数据引脚在正沿时钟下具有 setup 行 0/4/8、hold 行 2/6/10 三组分支，受连接与配置状态影响。当前 [扩展模型](../results/timing/gowin138_model_ff_checked.json) 保留三组候选值并取保守上界：setup 为 263 ps、hold 为 23 ps；clock-to-Q 仍使用已核对的第 24 行。四份报告的 389 条 LUT、clock-to-Q 和数据约束记录均落入候选值集合。

这仍是包络模型：运行时直接抓取证明的是 4/6 分支，其他分支依据分派代码和物理报告交叉验证；未恢复逐实例分支选择或全部数据边沿对应关系。它不包含 CE/reset、负沿时钟、其他 FF 类型、布线和 skew。不能把 389 次集合匹配称为 389 个独立器件参数或完整 STA 验收。早期 `gowin138_model_ff.json` 是单探针模型，继续保留用于重现拒绝案例；后续研究应使用带 `ff_checked` 的版本并遵守范围限制。

报告提取器已补充识别原厂 `tHld` 拼写，并记录约束对应的数据输入引脚；原有 `tH` 拼写继续支持。这修复了部分报告的 hold 约束被遗漏的问题。最新提取产物见 [observed_arcs_002.json](../results/timing/observed_arcs_002.json)，旧提取文件保留作为历史记录。

```powershell
python scripts/trace_ff_queries.py --out results/gowin_ff_trace_next
python scripts/extend_ff_model.py --trace results/gowin_ff_trace_next/manifest.json --pair results/gowin_probe_005/probe.tr results/gowin_probe_005/probe.vo --out results/timing/gowin138_ff_next.json
```

`gowin_ff_trace_001` 为断点脚本语法错误后的超时调试产物；`002` 被主动中止且清单为失败。`003`、`004` 为完成的查询抓取，均不是完整布线结果。审计现保存 235 个 STA/SDC 导出与 12 个函数反汇编。

## 接下来要验证的语义

1. 沿 DLL 延迟查询调用链确认扩展 group/row ID 对应的资源与引脚。
2. 扩展到更多配置，确认每行浮点槽位的速度等级、corner 和 rise/fall 语义；当前配置的缩放已通过内存核对。
3. 将解码值与受控 LUT/FF 探针的原生 STA 逐弧比对，单独处理布线、时钟和负载效应。
4. 输出具有明确引脚名、约束方向和单位的综合时序模型，再接入 ACD 与 GPU 候选评分。

早期提取文件提供 543 条观测弧、42 个分组；最新文件增加了先前遗漏的 `tHld` 记录。报告仅包含被选中的路径，不能代替数据库完整性验证。
