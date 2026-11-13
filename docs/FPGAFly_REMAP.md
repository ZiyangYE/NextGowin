# FPGAFly 真实映射网表接入

当前入口直接处理归档的 Gowin 结构化映射网表。它不是重新综合 RTL，也没有执行开发板烧录或神经网络功能验收。

从原始 RTL 开始的开放 CPU 综合流程另见 [混合流程](HYBRID_FLOW.md)；本页结果仍限定于归档映射网表的局部优化。

## 输入与约束

输入为 `FPGAFLY_ISCAS2027/firmware/bitstream.zip` 内的 `build/revc_02/impl/gwsynthesis/revc_02.vg`，约 23 MB、212 个模块定义，原始工具为 Gowin 1.9.12.03。[提取清单](../results/fpgafly_source_001/manifest.json) 保存归档与成员哈希；源网表未被修改。

物理对照统一使用本地已审计的 Gowin 1.9.12.04 DLL，保留工程自带的 [SDC](../FPGAFLY_ISCAS2027/constraints/tang_mega_pro.sdc) 和 [CST](../FPGAFLY_ISCAS2027/constraints/tang_mega_pro.cst)，不与旧版本归档的 Fmax 直接比较。预期物理时钟为 board_clk 20 ns、ddr_ref_clk 15.625 ns、memory_clk 3.125 ns、ui_clk 12.5 ns、jtag_clk 1000 ns。

## 替换与证明范围

[gowin_cones.py](../scripts/gowin_cones.py) 接受受限的原厂结构化 Verilog 语法，遇到未知语法、属性、隐式网线、非法 INIT 或接口变化会拒绝。它将模块端口、非 LUT 单元的所有连接及 assign 连接视为边界，仅把 LUT1–LUT4 网络送往 ABC。FF、ALU、RAM、DSP、PLL、DDR 和层次模块实例均保留，状态初值等非 LUT defparam 语句也保留。

首轮 42 模块实验保留了连接时钟、复位等命名控制引脚的 LUT 及其前驱锥。后续按已拆出的原厂原语源码，明确将 DFFRE RESET、DFFSE SET 作为同步控制处理，允许其组合逻辑参与优化；时钟与异步 CLEAR/PRESET 等仍保护。该规则不等于通用的跨层次时钟网络识别，也不构成门级波形或异步冒险证明。首轮清单中的 50 → 48 个可替换 LUT 是按当时的保守保护范围统计，不能与后续扩大范围的计数混算。

[remap_gowin_project.py](../scripts/remap_gowin_project.py) 对原始种子和 baseline、ACD、ACD-remap 三种候选计算同一局部单元模型时序。候选必须通过 CEC，不得增加原始边界输入到输出的结构依赖，以避免在被切开的硬块连接之间引入新环。默认按 WNS、TNS、LUT 数排序，保留原种子和 5% 面积上限；与种子打平时优先保留种子。

这里的 arrival=0、required=12500 ps 是显式的局部实验边界，LUT 采用已验证的 521 ps 包络。它未包含模块外部路径、ALU/硬块内部延迟、布线、skew 和多时钟关系，因此局部 WNS 不能视作真实 ui_clk WNS。工程原始的全部 SDC 由后续高云 P&R 处理。

选中候选后，生成新 LUT INIT，再从实际生成的模块反向读取，重新做边界 CEC。[audit_project_remap.py](../scripts/audit_project_remap.py) 进一步针对最终组合后的整个文件重新检查：修改范围外逐字节不变、原始非 LUT 语句保留、新增语句范围合法、边界依赖未增加，以及每个修改模块的 CEC。这些证据支持切口组合功能等价与结构保留，不替代 RTL 到网表的证明、完整顺序形式验证或开发板验收。

## 当前结果

[四模块初试](../results/fpgafly_remap_001/manifest.json) 中，`state_decode`、`state_encode` 和 `row_integrator` 的新候选没有超过种子，均保留原样；`lif_pipeline` 选择 ACD，其可替换部分从 50 个 LUT、3 级路径变为 48 个 LUT、2 级路径。

随后对全部 42 个 `lif_pipeline` 及其专门化模块应用同一策略，见 [替换清单](../results/fpgafly_remap_lanes_001/manifest.json)。所有模块均选择 ACD，可替换部分合计 2100 → 2016 个 LUT；462 个受保护 LUT 及其他单元保留。84 个 LUT 是模块定义层面的统计，最终物理资源以新 P&R 报告为准。

[最终网表审计](../results/fpgafly_remap_lanes_audit_001/manifest.json) 已完成：42 个修改模块全部通过重新提取后的 CEC，170 个未选中模块及修改范围外文本保持逐字节一致。

完整基线 P&R 已完成，原生返回码为 0、没有实现错误，输入哈希未变。独立 [物理审计](../results/fpgafly_native_baseline_001/physical_summary.json) 确认五个时钟和全部原始 SDC 均已激活，BSRAM 为 270，但该次运行未达到 ui_clk 80 MHz：报告 Fmax 为 72.125 MHz，最差已报告 setup slack 为 -1.365 ns、TNS 为 -67.728 ns，180 个 setup 端点违例，hold 违例为 0。不能称为时序收敛，也不能据此断言 Gowin 两个版本的整体优劣。

对最差路径的 [延迟分解](../results/fpgafly_native_baseline_001/path_accounting.json) 显示，数据路径约 13.693 ns，其中 tNET 为 12.074 ns，约占 88.18%。该路径经过全局握手控制与 FF CE 端，当前局部 LUT 级数模型未覆盖其主要布线瓶颈。这是后续物理反馈优化的依据，不是对全设计平均布线占比的估计。

首轮 [串行物理对照](../results/fpgafly_physical_pair_002/manifest.json) 已完成，独立 [重新审计后的比较](../results/fpgafly_physical_pair_002/comparison.json) 核对 DLL、工具版本、delay model、时钟、SDC/CST 内容及实现选项一致：

| 指标 | 原始映射 | 42 模块 ACD 候选 |
| --- | --- | --- |
| ui_clk 报告 Fmax / MHz | 72.125 | 79.520 |
| 最差已报告 setup slack / ns | -1.365 | -0.076 |
| ui_clk setup TNS / ns | -67.728 | -0.109 |
| setup 违例端点 | 180 | 4 |
| hold 违例端点 | 0 | 0 |
| 物理 LUT | 39035 | 38951 |
| 物理寄存器 / BSRAM | 48093 / 270 | 48093 / 270 |

候选的这次物理结果改善，但仍未满足 80 MHz。它只是一组配对样本，不能证明稳定平均收益，也不能把整体改善全部归因于某个局部路径减少一级 LUT。原生报告记录的时间为 1284 秒与 930 秒；期间存在其他主机工作，不将其作为受控运行时间加速结论。候选最差路径的布线占比仍约 88.05%，见 [候选路径分解](../results/fpgafly_physical_pair_002/optimized/path_accounting.json)。

后续针对跨模块控制网络的展平、布线反馈和复制实验见 [物理反馈记录](PHYSICAL_FEEDBACK.md)。

## 原生物理报告验收

[run_gowin_netlist.py](../scripts/run_gowin_netlist.py) 直接送入外部网表和原始板级约束，独立新进程调用 P&R，避免工程调度器重新综合。它记录输入、SDC、CST 和产物哈希，并检查输入未被覆盖。默认只要求 P&R 网表与时序报告；`--bitstream` 才请求配置文件。

[project_timing.py](../scripts/project_timing.py) 独立核验原生完成日志、产物新鲜度、五个时钟的周期、每条 SDC 约束的激活状态、每时钟 setup/hold TNS、全局违例端点，以及已报告的 setup/hold/recovery/removal/pulse 路径。它区分全局端点统计与有限条详细路径，不把未出现在路径表中的时钟正 slack 当作已知 WNS，也不把某一个时钟 Fmax 当作全工程频率。BSRAM 上限为 270，规则见 [物理验收配置](../configs/fpgafly_native.json)。

首次基线运行启动时，运行器误将未请求的 `.fs` 也列为必需产物；已修正为按 `-bit` 选项判断，原运行清单仍保留 `failed` 的旧检查结果。独立审计确认其 P&R 产物有效，同时如实判定时序不收敛。首次配对调度 `fpgafly_physical_pair_001` 被资源报告格式差异阻止，未启动优化版；解析器现已支持 `BSRAM 270/340` 及分类型子行，后续 `002` 复用已完成基线，不重跑基线，也不使用旧固件充当新产物。

## 重现

以下命令使用新的输出目录，在仓库根目录运行。先按提取清单从归档恢复原网表；不需要恢复 MNIST 数据或烧录固件。

```powershell
python scripts/remap_gowin_project.py results/fpgafly_source_001/source.vg --module-pattern 'lif_pipeline(?:_[0-9]+)?' --out results/fpgafly_remap_new
python scripts/audit_project_remap.py results/fpgafly_source_001/source.vg results/fpgafly_remap_new/optimized.vg --manifest results/fpgafly_remap_new/manifest.json --out results/fpgafly_audit_new
python scripts/run_gowin_netlist.py results/fpgafly_source_001/source.vg --sdc FPGAFLY_ISCAS2027/constraints/tang_mega_pro.sdc --cst FPGAFLY_ISCAS2027/constraints/tang_mega_pro.cst --out results/fpgafly_baseline_new
python scripts/project_timing.py results/fpgafly_baseline_new --profile configs/fpgafly_native.json --out results/fpgafly_baseline_new/physical_summary.json
python scripts/run_gowin_netlist.py results/fpgafly_remap_new/optimized.vg --sdc FPGAFLY_ISCAS2027/constraints/tang_mega_pro.sdc --cst FPGAFLY_ISCAS2027/constraints/tang_mega_pro.cst --out results/fpgafly_optimized_new
python scripts/project_timing.py results/fpgafly_optimized_new --profile configs/fpgafly_native.json --out results/fpgafly_optimized_new/physical_summary.json
```

物理报告存在违例时审计 CLI 返回 2；缺失约束、时钟或产物时拒绝验收。一次局部模型改善、一次布线成功或一次时序通过均不代表多次运行的稳定收益。
