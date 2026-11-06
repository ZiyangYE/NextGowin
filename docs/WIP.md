# WIP：综合与自研 P&R 源码及小设计复现

日期：2026-11-06。优化工作已按用户要求暂停；本次整理已有算法，补充最小 RTL 的复现入口和运行依赖。完整安装与运行步骤见 [repro/README.md](../repro/README.md)。

## 内容与边界

本包按当前入口的本地 Python import 和脚本文件名引用取传递依赖，并选取不需要大型实验 fixture 的测试。`source_provenance.json` 记录复制来源及 SHA-256。本次新增小例子入口，并修复最终 I/O 报告检查器对分组标题的识别，附回归测试。旧冻结交接包保持不变。

| 入口/模块 | 作用 |
|---|---|
| `scripts/nextgowin.py`、`map_resource_json.py` | 前端/资源推断与技术映射；前者历史完整 CLI 的后端为原厂 P&R，不能冒充自研 |
| `scripts/ownpr_multilevel_cut.py`、`ownpr_pin_cut_selection.py` | 多级 Boolean cut 与实际引脚反馈选择 |
| `scripts/ownpr_shared_ff_packing.py`、`ownpr_pack_shared_ff.py` | 严格控制身份的 LUT/FF 共享打包 |
| `scripts/ownpr_global_relayout.py` | 宏/共享寄存器整体布局、密度约束及合法化 |
| `scripts/ownpr_router.py`、`ownpr_incremental_routes.py` | 拥塞协商、时序权重、分支保留及有界搜索回退 |
| `scripts/ownpr_critical_tree_rebuild.py` | 整树负载测量与受限负 WNS 探索 |
| `scripts/run_ownpr_region_coopt.py` | 从已核验 checkpoint 开始的一次有界协同优化 |
| `scripts/refine_ownpr_attempt.py`、`merge_ownpr_refinement.py` | 沿用原截止时间的 hold 修复及路线合并 |
| `scripts/native_boundary_cec.py`、`ownpr_register_equivalence.py` | 组合边界等价及寄存器副本归纳检查 |
| `experiments/native_import_ownpr.py`、`scripts/audit_ownpr_native_export.py` | 将自研物理结果交给原厂最终 STA/编码并核验 |

Git 源码提交不包含大型运行时或实验数据。对应 Release 单独提供小例子所需 DLL/EXE、器件数据、路由图与校准参考，按 SHA-256 校验；不含许可证密钥、大型 CPU 网表或原始大型报告。原仓库 Apache-2.0 `LICENSE` 保留不变，第三方组件仍适用其原有许可。

## 可以立即验证的部分

使用 Python 3.11，安装 `requirements-dev.txt` 后执行 README 的校验和 pytest 命令。测试覆盖纯图路由/冲突检查、增量分支、搜索方向、候选选择、目标周期保护，以及 Boolean cut、共享控制身份和负 WNS 取舍的轻量检查。

轻量测试不加载厂商 DLL，也不证明 74 MHz 或硬件功能。小例子的独立运行验证另见 `repro/validation.json`，实际包含完整 I/O STA 和位流编码；没有烧录或验证板卡。依赖大型 CPU 实验的集成测试没有纳入此包。

## 真实 GW5AST-138 流程的外部依赖

- Windows x64、本地 Gowin V1.9.12.04 安装；`NEXTGOWIN_SDK` 可指定其 `IDE` 目录。
- ABC/Yosys 及相应运行时。当前部分 WIP 脚本仍使用 `binary_audit/flow/runtime/bin/python.exe`、`build/`、`third_party/` 等原开发布局，尚未统一为可安装命令。
- 前端调用的旧 `binary_audit/dll_steps.py` 源码单独保留，仍含历史 Conda/SDK 路径；它不是随包提供的运行时，也未在本次提交中重新执行。
- 由工具自身生成或从可信本地实验恢复的 `results/native_*` 校准、路由资源图及经过审查的 checkpoint。源码保留 DLL/时序版本和产物 SHA-256 的拒绝条件；SDK 版本不同不能复用私有函数地址。
- 尚未提供从零生成所有校准数据的统一 bootstrap。提取/校准程序仅是源码，不能替代它们需要的原始工具链和捕获记录。

小例子的上述依赖已由 Release 提供，下载后可从 RTL 运行到完整 I/O 时序验收。通用 `run_ownpr_region_coopt.py` 仍需要适合目标设计的检查点和输入报告；小例子不等于 VexRiscv 数据集，也不代表任意工程的冷 RTL 闭环均已完成。

## 最近的本地结果与未完成项

最近接受的 VexRiscv 结果为 74.0 MHz，setup +0.028 ns、hold +0.133 ns，完整 I/O 零违例。256 对共享 FF 和全局重布局后，加入 3 LUT 修复输入 hold，并重接一棵 BRAM 地址树；从该次尝试开始至最终验收 3,218.064 秒，未重置 3,500 秒期限。相对前一项 71.4 MHz 仅提高 3.641%，仍未达到显著通用优势。

90 MHz 目标下仍有 135 个 setup 违例。最坏路径来自固定 V14 的 reset 输入：121 个负载，一条支路 14.997 ns。后续应处理高扇出网络分区/缓冲及整树延迟，并继续覆盖全部 I/O。不得移动固定引脚、改变接口周期、略去 hold/复位输入时序或自动烧录硬件。

共享 FF 后端当前只覆盖经校准的 type-17、DFFRE、默认初值、RESET=0、CE=1/动态 CE 和 0/1、2/3、4/5 槽位组合。任意控制模式、宽宏/ALU 与 FF 混合、更多硬资源模式、多时钟、冷 RTL 自动闭环和多工程优于原厂都未完成。

## 研究定位

当前实现是实验性自研流程，不是某篇论文的完整复现。[TD-Placer 作者论文](https://arxiv.org/html/2512.00038v1)及[作者实现说明](https://github.com/yyiloe/TD-Placer/blob/main/README.md)提供路径权重和延迟模型方向；本代码尚未具备其完整动态模型。真实器件数据来自用户本地 Gowin 工具，未采用开源芯片数据库或 nextpnr 算法作为自研结果。
