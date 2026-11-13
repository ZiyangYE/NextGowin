# 高云前端、CPU 综合映射与高云实现的混合流程

本次按“流程梳理”会话中的交接目标实现：高云负责解析 RTL/IP 和合并 RTL GAO；开放综合后端与本项目的 ACD 接口完成用户逻辑的资源推断和技术映射；高云接收新网表，仅执行 P&R 和位流生成。默认单核 CPU。

开放综合使用固定版本 Yosys，LUT 映射使用已固定的 ABC ACD 实现。这是一条可运行的混合工具链，不声称所有综合算法都是本项目原创。

## 运行入口与中间文件

主入口是 [nextgowin.py](../scripts/nextgowin.py)。每次选择新的输出目录，状态、配置、阶段耗时和哈希保存在 `manifest.json`。

| 阶段 | 执行者 | 输出目录与文件 |
| --- | --- | --- |
| 解析、展开与可选 GAO 合并 | 高云 `dev`、`extract -o`，不调用 `cvt/infer/map` | `01_frontend/elaborated.v`、前端 Tcl、日志、源文件与 include 快照哈希 |
| 导出格式适配 | 本项目 | `02_bridge/design.v`、`vendor_cells.v`、原语参数、初值和 RAM 还原清单 |
| 优化与资源推断 | Yosys，普通逻辑展开，保留指定 IP/时钟层次 | `03_resources/inferred.il`、`resources.json`、`resources.v`、执行脚本与日志 |
| 时序驱动 LUT4 映射 | 本项目接口调用 ABC ACD；单核串行 | `04_mapping/mapped.json`，每个切口的 BLIF、约束、映射及读回 CEC |
| 原语合法化与网表输出 | 本项目与 Yosys | `05_netlist/mapped.vg`、最终 JSON、I/O/FF 适配记录、参数格式记录 |
| 布局、布线、位流与时序审计 | 直接调用高云实现 DLL | `06_pnr/mapped.vo`、`.tr`、实现报告、`.fs`、约束副本及独立物理审计 |

`--stop-after frontend`、`resources`、`mapping` 可停在对应边界。`mapping` 包括映射后网表导出；默认执行到 `bitstream`。程序不会烧录开发板。

前端输入为描述源文件与选项的综合 `.prj`，不是已映射 `.vg`。真实工程的输入元数据可以直接从仓库文件清单生成，不要求先对用户逻辑执行一次高云综合：

当前工程导入器接受 Verilog 文件和已生成的 GAO 输入，并校验目标 part；尚未接入混合 VHDL 工程导入。

```powershell
.\scripts\setup_gowin_runtime.ps1
python scripts/setup_yosys.py --download
python scripts/prepare_fpgafly_cpu_project.py
python scripts/nextgowin.py --config configs/fpgafly_rtl_flow.json --out results/fpgafly_rtl_new
```

`setup_yosys.py --download` 在缺少包时获取官方固定发布包；本机已下载时只校验并提取项目内运行库，不修改系统 PATH 或高云安装。官方包为 `2026-10-31`，SHA256 为 `4e845291d99e1711e4c3a005661132deef8f8cc0afaf1e3fd0f4811cc103e783`；Yosys 自报 `0.70+286`、`63eecf3dd`。来源为 [官方 OSS CAD Suite](https://github.com/YosysHQ/oss-cad-suite-build/releases/tag/2026-10-31)，许可证保留在提取目录。

Gowin 运行时准备器默认使用 `C:/Gowin/Gowin_V1.9.12.04_x64` 与 `D:/Conda3` 的 Python 3.11。它校验 DLL 和已有运行时，复制解释器到项目内并建立只用于读取 SDK 资源的目录联接；不同版本会拒绝复用。当前工作区的运行时已准备好。

## 格式与语义适配

[verific_bridge.py](../scripts/verific_bridge.py) 恢复 `Init Val` 注释中的寄存器初值，保留 `syn_keep/syn_preserve`，并将参数专门化的原语名还原成实际单元与参数。空原语声明不会作为没有功能的普通模块参与优化。

[RAM 适配器](../scripts/restore_verific_memories.py) 核对读写辅助模块的实际寻址公式，将受支持的位向量表达还原成数组。当前支持本工程出现的一维数组、整字读写和具有常量列号的单比特写。未知公式、额外可见用途或未支持的动态列号会报错。测试比较原导出表达与还原数组的读写结果。

普通逻辑必须允许跨模块常量传播，否则常数乘法等算术模块会保留不必要的通用运算器。`keep_hierarchy` 配置保护 DDR 内核及 SDC 引用的 PLL 路径；它不是禁止所有逻辑层次展开的开关。原始 `-noflatten` 全层次保留试验显著膨胀，已作为失败实验保留。

[网表适配器](../scripts/gowin_netlist_format.py) 按当前器件处理 DFFE 等非物理别名、原语参数类型和未连接输出。I/O 插入会检查层次内部已有的 PAD，避免给 DDR 或 GAO 再插一层缓冲。原生读取器不支持的 sliced assign 会先转换为标量连接。RAM 未使用数据通道等 synthesis don't-care 常量明确置零；空的原厂字符串属性继承原生默认值，不误转成数值零。

导出时清除无用途的别名和源码位置注释，保留必要连接、端口和显式 keep 对象，降低原生读取器处理庞大调试网表的开销。这不删除功能寄存器或改变 LUT 真值表。

## GAO 与 IP 边界

带 `.rao` 的入口保留用户已有的高云生成配置。GAO 内核采用已验证的原厂映射快照，用户逻辑进入开放综合流程；[宏绑定器](../scripts/gowin_macros.py) 校验位流配套 `.rao`、生成文件与内核网表哈希，连接导出前后的端口，并保留受保护内容。它不依靠仅保留信号名字来推断探针连接。

当前示例的固定快照采样 `a/q`，时钟为 `clk`，深度 1024，GAO_ID 为 `1110111101100001`。更改 `.rao` 时，需要重新由高云生成配套文件并重新验证宏绑定；当前入口不包含任意 `.rao` 到调试内核的独立生成器，也不保证所有加密 IP 都可直接导出。未知 IP 必须另行验证其边界和网表。

重新映射 GAO 内部逻辑的探索运行在原生布线阶段发生异常，未作为成功证据。交付路径保留原厂内核，并使两个时钟域的 hold uncertainty 分别作用于各自时钟，用户寄存器保留在核心逻辑区。

```powershell
python scripts/prepare_cpu_gao_example.py
python scripts/nextgowin.py --config build/cpu_gao_example/config.json --out results/cpu_gao_new
python scripts/verify_cpu_datapath.py results/cpu_gao_new --out results/cpu_gao_sim_new
```

## 已完成的验证

[非平凡 GAO 示例](../results/cpu_gao_flow_012/manifest.json) 包含带初值的 8 位状态、加法器、同步复位、使能和组合逻辑，不再限定为单触发器。该次流程约 32.41 秒，生成新的 `ao_0.fs`，P&R 前后输入网表哈希一致。[完整物理审计](../results/cpu_gao_flow_012/06_pnr/physical_summary.json) 确认两个时钟、I/O 延迟、异步分组和各自 50 ps hold uncertainty 全部激活；setup/hold 违例均为 0。报告 sample_clk Fmax 为 107.367 MHz；它是这次示例的物理结果。

[初值与顺序行为回归](../results/cpu_gao_sim_003/manifest.json) 使用安装的 GW5A 功能模型，对最终用户网表与原始 RTL 比较 2000 个周期。此测试对 GAO 模块使用 stub，只验证用户数据通路；它不能代替调试内核的上板采样或一般顺序等价证明。

当前 [69 项自动测试](../results/hybrid_validation_001.log) 全部通过，包含 RAM 还原、FF 别名、原语参数格式、层次 I/O、约束对象别名、初值，以及篡改后重算哈希仍不能复用证明的恢复测试。

FPGAFly 的全 RTL 路径 [fpgafly_cpu_flow_003](../results/fpgafly_cpu_flow_003/manifest.json) 判定为失败。前端、资源推断和全组合切口 CEC 已完成，但[原生 P&R](../results/fpgafly_cpu_flow_003/06_pnr_failed_2/manifest.json) 在 3602.76 秒超时，未生成新位流。随后安排的两小时上限重试已按用户要求停止；[验收记录](../results/fpgafly_cpu_flow_003/acceptance.json) 保留原因。已完成的综合结果可以复用，但不足以证明真实工程全流程成功。先前针对归档映射网表得到的 79.520 MHz 不作为这条新综合流程的结果。

接入 DSP、存储和负载分散后的 [fpgafly_cpu_opt_001](../results/fpgafly_cpu_opt_001/acceptance.json)已生成新位流：P&R 外部计时 57 分 41 秒，完整阶段合计 64 分 2 秒，LW 为 5/8。原始 5 个时钟与 SDC/CST 已核对，但 ui_clk 为 64.317 MHz、5126 个 setup 违例、0 个 hold 违例，时序验收仍失败。`status=complete` 在主流程清单中只表示各阶段执行完毕，必须同时检查 `timing_closed` 和端到端耗时。

比较链软化的 [fpgafly_cpu_opt_002](../results/fpgafly_cpu_opt_002/acceptance.json)也已生成新位流：P&R 为 37 分 28 秒，有效阶段合计 44 分 26 秒，满足一小时耗时要求；Fmax 为 60.691 MHz，5086 个 setup 违例、0 个 hold 违例，时序反而退化。该开关默认关闭，作为面积/耗时对照保留。两轮完整文件和约束核验均通过；两轮都不能视为 80 MHz 达标。

## 时序约束与结论边界

CPU 映射当前使用 `period_ps` 指定的组合切口预算和已验证的 521 ps LUT 包络；该预算实际进入 ABC 的 arrival/required 注释及独立分析。它不是完整的 SDC 解释器，也不包含所有跨层次到达时间、carry/硬块时序与布线。完整工程的原始 SDC/CST 交给高云实现，并在报告中逐条核对激活状态。模型预算满足不等于物理时序收敛。

每次网表导出还生成 `05_netlist/route_fanout.json`，检查展开到原语后的高扇出数据、复位、使能和时钟网络。它不等于实际 LW 用量；原生长线的候选、tap 可达性、器件差异和本次超时证据见[长线排查](GOWIN_LONGWIRE.md)。

当前已接入 GW5A DSP 乘法、read-tags 小存储恢复、寄存器分支、直通 LUT 消除和局部专用 mux 打包；验证及新运行状态见[硬资源优化](RESOURCE_OPTIMIZATIONS.md)。优化前的[结构对比](NETLIST_COMPARISON.md)保留为历史快照。单核 CPU 选择不限制 FPGA 内部硬资源的使用。

每个组合输出须通过 CEC，且从实际生成的 JSON LUT INIT 独立读回再验证；寄存器与硬资源由资源阶段和适配记录另外约束。完整 RTL 顺序等价、板级功能和 GAO 实机采样不在现有证明范围内。

## 失败后恢复

```powershell
python scripts/nextgowin.py --config configs/fpgafly_rtl_flow.json --out results/fpgafly_rtl_new --resume
```

恢复仅支持已完成映射后的导出/P&R 重试。程序重新核对输入、前端导出、格式适配、工具和模型哈希，并对实际映射 JSON 读回切口、核对保留的 CEC 证据与硬资源，随后归档失败的实现目录。约束配置改变或上游产物无法证明时拒绝复用。失败记录保留，不以旧位流补齐新运行。

按用户验收标准，一小时仍未完成即判定失败。入口对 P&R 强制设置不超过 3600 秒的等待上限，`--pnr-timeout` 只允许缩短该时限；超时不能通过延长等待转成成功。恢复时继承已记录的内部对象别名绑定。下一轮应先改善网表或实现策略，再启动有依据的重试。

`net_aliases` 为原 CST 中依赖旧综合命名的内部对象提供明确绑定。FPGAFly 的 `core_selected_Z` 绑定到同一个 `host.core_selected` 信号，连接和原 CST 字节保持不变；别名不存在、冲突或试图改写外部端口会报错。实现审计拒绝 `CT1135` 这类未找到物理约束对象的结果。
