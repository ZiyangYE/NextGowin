# ACD 论文映射复现与真实 cut CUDA 实验

日期：2026-11-01。首批 CPU/CUDA 研究原型与高云时序拆分已交付；完整工具链及扩展验证仍按 [TASKS.md](../TASKS.md) 推进。固定图的 GPU 正反传播另见 [GPU 实验](GPU_TIMING.md)，约束边界见 [支持矩阵](CONSTRAINT_SUPPORT.md)。

从高云前端导出开始的完整 CPU 综合、映射与位流入口见 [混合流程](HYBRID_FLOW.md)。它与本页以 EPFL AIG 为输入的论文实验分别记录。

## 固定实现与基准

已编译 Berkeley ABC 的完整 ACD 映射实现，直接使用论文作者合入上游的代码。原有 Python 精确分解器继续作为小规模验证工具。

| 依赖 | 固定版本 | 用途 |
| --- | --- | --- |
| [Berkeley ABC](https://github.com/berkeley-abc/abc) | `9e246bec22844ecc54c9459538ccffda951776b9` | priority cuts、ACD、structural choices、重映射、SAT 分解、CEC |
| [EPFL benchmarks](https://github.com/lsils/benchmarks) | `82d8cc6910419298e713a46644ed59fd3df53038` | 20 个算术与控制组合基准 |
| 原厂 Gowin 时序库及 STA | 见 [时序记录](GOWIN_TIMING.md) | 138K 单元时序与 P&R 验证 |

ABC 使用本机 MSVC v143 构建为 Win32，遵循上游 Windows 构建方案。上游旧 DSP 清单缺少部分新拆分的命令文件，构建脚本补齐 `src/base/abcc`，并为这些同名源文件指定独立 object 名称。生成的工程和构建结果均放在 `build/abc`，上游源码不修改。ABC 和 EPFL 的许可证保留在各自源码目录中。

## 论文流程与当前复现差异

[R1 论文](https://people.eecs.berkeley.edu/~alanmi/publications/2024/tcad24_acd.pdf) 的基准预处理为 `dfraig; resyn; resyn2; resyn2rs; if -y -K 6; resyn2rs`。实验采用以下四条映射序列，分别从同一预处理 AIG 开始：

| 配置 | LUT6 命令 |
| --- | --- |
| baseline | `dch; if -K 6` |
| acd | `dch; if -Z 6 -K 8` |
| acd_remap | `dch; if -Z 6 -K 8; strash; dch -f; if -K 6` |
| sat_cascade | `dch; if -s -S 66 -K 8` |

历史论文的 LMS 结构库未随论文输入提供。当前版本的 `if -y` 要求先初始化 LMS；实验使用该版本内置的生成库，先在固定 ctrl seed 上运行 `&if -y -K 6` 初始化，然后重新读取实验输入，执行预处理。因此这是固定现代版本上的算法与流程复现，不声称与论文原表数值完全相同。该差异写入每次运行清单。

完整运行入口为 [run_abc_reproduction.py](../scripts/run_abc_reproduction.py)。它保存输入、工具和网表哈希，统计每次映射的 LUT 数、边数、深度、CPU 时间和包含进程启动与文件输出的 wall time。每个配置运行 5 次。原始 AIG 到输出必须有完整的组合等价证明：可以直接 CEC，也可以先证明原始输入到预处理 AIG，再证明预处理 AIG 到映射输出。后一种路径利用等价关系的传递性，仍覆盖完整功能；预处理未证明时不能采用。

ABC 将时间戳写入 BLIF 注释，曾导致相同逻辑重复做昂贵的 CEC。运行器现保留原始文件哈希，另对删除整行注释和空行后的完整 BLIF 文本计算哈希；只有文本相同且已有正面 CEC 证据时复用检查。恢复执行会校验已有文件哈希及证明日志。失败、超时、未证明结果分别保留。

## 当前已有的映射结果

LUT6 的 adder 基准已经完成 5 次运行及等价检查：

| 配置 | LUT 数 | 深度 |
| --- | --- | --- |
| baseline | 358 | 22 |
| acd | 380 | 16 |
| acd_remap | 361 | 8 |
| sat_cascade | 340 | 19 |

这些是逻辑映射指标，不是布线后 Fmax。20 个基准的运行现已结束，见 [完整清单](../results/abc_paper_001/manifest.json)、[汇总 JSON](../results/abc_paper_001/summary.json) 和 [逐项 CSV](../results/abc_paper_001/summary.csv)。其中 19 个完成全部四种配置与五次重复，共 76 组配置、380 次映射运行具有等价证明覆盖。`hyp` 的原始输入到预处理 AIG 检查在三种尝试中均未完成证明，因此没有纳入有效 QoR 配对统计，也不表示已发现反例。

在这 19 个有效配对基准上，相对 baseline 的几何平均比例为：

| 方法 | LUT 数比例 | 逻辑深度比例 |
| --- | --- | --- |
| acd | 1.0841 | 0.9127 |
| acd_remap | 0.9583 | 0.8588 |
| sat_cascade | 0.8552 | 0.9863 |

ACD 重映射的深度与 LUT 数平均改善不代表每个设计都改善，例如 sin 的重映射深度从 baseline 的 29 层变为 32 层。后续默认流程需要保留基线并按约束选择结果。初期 K4 与 K6 任务曾短时重叠运行，CPU 时间和 wall time可作流程诊断，严格性能比较还需在独占条件下进行。

逐基准对比图提供 [PNG](../results/abc_paper_001/qor_comparison.png) 与 [SVG](../results/abc_paper_001/qor_comparison.svg)。图中仅使用上述 19 个已证明的配对基准，明确保留 sin 等退化情况。

R1 论文对 `hyp` 明确不使用结构 choices。运行器已为后续 hyp 重试去掉 `dch`，但本次 hyp 在预处理证明阶段即被阻止，未运行映射配置。其 formal 障碍仍待解决，不能将本次称为全部 20 个基准通过。

为 Gowin 另运行了 LUT4 配置，将 `-K 6` 替换为 `-K 4`、`-Z 6 -K 8` 替换为 `-Z 4 -K 6`。四个基准各三种配置、每种 5 次已全部通过 CEC，见 [abc_gowin_k4_001](../results/abc_gowin_k4_001/manifest.json)。例如 adder 深度为 35、23、20；priority 深度为 9、8、8。

## 外部映射到高云 P&R

[gowin_blif.py](../scripts/gowin_blif.py) 将 LUT4 BLIF 映射成 Gowin 原语，并在所有输入输出处添加同一时钟的 DFFRE。它支持正、负极性 cover、常量与直接信号输出，拒绝 latch、未知指令、多重驱动和组合环。

独立 INIT 读回会从生成的原语网表重建组合 BLIF，然后使用 ABC 与原始 AIG 做 CEC。测试还主动改坏 INIT，确认错误可被检查捕获。寄存器边界固定为 CE=1、RESET=0、INIT=0；该验证范围是组合功能及固定生成的边界结构，不是任意顺序逻辑的形式验证。

[run_gowin_mapped.py](../scripts/run_gowin_mapped.py) 直接调用已审计的 P&R 接口，检查外部网表输入哈希保持一致，避免工程调度器重新综合。当前 priority 的一次对照如下：

| 映射 | 报告 Fmax MHz | setup 违例端点 | hold 违例端点 |
| --- | --- | --- | --- |
| baseline | 107.373 | 0 | 0 |
| acd_remap | 117.330 | 0 | 1 |

以上是最初的零 uncertainty 运行，证据见 [baseline 清单](../results/gowin_priority_baseline_003/manifest.json) 和 [优化清单](../results/gowin_priority_acd_001/manifest.json)。优化网表的一条输入边界路径 hold slack 为 -0.001 ns，该历史结果不能视为完整时序收敛。

后续对两组网表同时施加更严格的 50 ps hold uncertainty，并保持原有时钟和 I/O 延迟要求，重新运行原厂 hold 修复。原生报告包含 `tUnc 0.050`，两组均为 setup=0、hold=0 违例端点。Fmax 分别仍为 107.373 和 117.330 MHz，见 [baseline 新清单](../results/gowin_priority_baseline_margin_002/manifest.json) 和 [优化新清单](../results/gowin_priority_acd_margin_002/manifest.json)。优化后的最差已报告 hold slack 为 +0.012 ns。

这些报告中，寄存器到寄存器的最差已报告 setup 路径所需周期从 8.327 ns 降至 8.015 ns。它与整体报告 Fmax 不同，因为后者还包含人为添加的边界与 I/O 路径。报告解析器区分流程结束与 `timing_closed`，并拒绝把缺失的违例统计视为通过。

这些小实验使用自动引脚分配与人为添加的寄存器边界；报告 Fmax 也受到 I/O 路径影响，不代表板级验收。另已使用原始板级约束完成 FPGAFly 全工程映射网表的 P&R 对照，见 [接入记录](FPGAFly_REMAP.md)；尚未时序收敛或烧录验证，单次对照不能代替多设计、多种子物理性能结论。

## 显式到达时间与端点要求

[constrained_mapping.py](../scripts/constrained_mapping.py) 已将原厂 521 ps LUT 延迟范围接入完整 ABC ACD mapper。输入为组合 BLIF/AIG 与显式 ps 单位的端口约束 JSON；支持输入到达时间、逐输出 required time 及默认值，未知字段、未知端口和缺失端点要求会报错。该接口当前执行组合 setup 优化，不解释通用 SDC 或时钟域例外。

接入时发现两个需要处理的实现细节：默认 BLIF 层次读取器会跳过部分时序指令，因此流程使用 `read_blif -n`；R1 的 `If_LutDecEval` 使用整数层数，因此 arrival 向上、required 向下取整到 LUT 单位。映射完成后再次核对所有原生时间注释，再用原始 ps 数值独立计算到达时间、required time、WNS、TNS 与关键路径。ABC 放宽不可满足 deadline 的行为会单独记录，不能代替原始目标的验收。

对于 [同一组合函数](../examples/late_input.blif)，将晚到端口从 a 改为 b，会让 a 与 b 分别进入只有一级 LUT 的输出侧，另一端口经过两级。两组输出到达时间均为 5521 ps，满足 6000 ps 要求，且均通过 CEC。将要求收紧至 5500 ps 后，独立验证给出 -21 ps slack，并明确判为不满足；CLI 返回码为 2。证据见 [late a](../results/constrained_late_a_004/manifest.json)、[late b](../results/constrained_late_b_004/manifest.json)、[不可满足要求](../results/constrained_impossible_004/manifest.json)。

[timing_portfolio.py](../scripts/timing_portfolio.py) 在同一约束下分别运行 baseline、ACD 和 ACD 重映射。所有候选必须通过 CEC 和注释保真检查，再按原始 ps 约束下的 WNS、TNS 与 LUT 数选择，避免默认采用可能退化的最后一轮重映射。可指定相对 baseline 的最终面积预算，baseline 始终可选。该选择仍属于单元模型优化，不替代后续 P&R。一次完整运行见 [选择记录](../results/constrained_portfolio_001/manifest.json)。

更早的 `constrained_*_001` 与 `_002` 是定位读取器和整数层假设的调试产物，不作为上述约束驱动能力的验收依据。回归测试覆盖字段静默丢失、fractional 时间误用、实际映射选择变化、不可满足目标，以及 1500 层组合链的正反向时序分析。

组合接口、GPU 固定图和原生后端的约束支持范围分别列于 [支持矩阵](CONSTRAINT_SUPPORT.md)。新增 [GPU 正反传播实验](GPU_TIMING.md) 使用真实映射图，证明部分驻留批量工作有收益，也保留传输与深窄图的退化结果；它尚不是完整 mapper 的 GPU 迭代流程。

```powershell
python scripts/constrained_mapping.py examples/late_input.blif --constraints configs/late_a.json --out results/constrained_a_new
python scripts/constrained_mapping.py examples/late_input.blif --constraints configs/late_b.json --out results/constrained_b_new
python scripts/constrained_mapping.py examples/late_input.blif --constraints configs/impossible.json --out results/constrained_bad_new
python scripts/timing_portfolio.py examples/late_input.blif --constraints configs/late_b.json --area-allowance 0.05 --out results/portfolio_new
```

使用 `configs/impossible.json` 的命令预期返回 2，并保留完整报告。此处的 ps 单元模型不包含互连、clock skew 或 hold，最终物理时序继续由高云报告验收。

## 真实 ACD 工作量的 CUDA 实验

[build_acd_profiler.py](../scripts/build_acd_profiler.py) 在生成目录内复制并插桩 `ac_wrapper.cpp`，复用普通 ABC 静态库构建独立分析版本。普通 ABC 二进制保持独立。分析版本只计时并采集 `acd_evaluate` 调用，不作为性能基线。

adder 的实际映射捕获了 7,216 次 ACD 调用，其中 6,995 次返回可行，原函数评估累计约 11.34 ms。按真值表与变量数计有 2,519 种输入，连同 late mask 等参数计有 2,629 种调用键。分析版本仍得到 380 LUT、16 层，并通过原始输入 CEC，见 [采集清单](../results/acd_corpus_adder_001/manifest.json)。

[prepare_acd_gpu.py](../scripts/prepare_acd_gpu.py) 根据真实真值表与 late mask 生成分区，得到 252 种分区索引模式和 359,210 个任务。任务包含所有满足当前枚举规则的分区，不复现原 mapper 的提前终止、剪枝和编码优化。

[CUDA 内核](../cuda/acd_partition_bench.cu) 每个 block 计算一个分区的列重数，CPU 用独立的排序去重实现计算同一数学量。全部任务结果一致，5 次运行中位数见 [结果](../results/acd_gpu_adder_001/benchmark.json)：

| 范围 | 时间 ms |
| --- | --- |
| CPU 独立分区计算 | 88.149 |
| GPU 驻留计算 | 2.497 |
| GPU 包含输入输出传输 | 3.333 |

约 35 倍只适用于同一批独立分区的计算，与原始 ABC 完整 ACD 评估的 11.34 ms 不可直接等同。后者有不同的算法、剪枝与成本目标。Python 任务展开、设备初始化和分配也没有包含在上述 GPU 稳态时间中；完整流程尚未获得端到端加速证明。

### GPU 预计算已接入真实 mapper

[build_mu_mapper.py](../scripts/build_mu_mapper.py) 在生成目录内插入三个列重数查询点。GPU 对捕获到的真值表预计算全部合法 free set，导出精确查表文件；[mu_cache.hpp](../src/mu_cache.hpp) 在真实 ACD 搜索中按真值表、变量数、LUT 大小和当前原始变量分区查询。late mask 继续由原 mapper 处理。未命中执行原 CPU 函数，验证模式对每次命中再次执行原函数并要求完全一致。GPU 工作发生在预计算进程，mapper 本身消费 CPU 内存中的结果表；当前不是多轮 GPU 驻留实现。

[run_mu_experiment.py](../scripts/run_mu_experiment.py) 比较普通 ABC、带查询钩子的 CPU 回退、GPU 缓存三个模式，每个模式交错运行 5 次，另运行一次逐次验证模式。所有模式的规范化 BLIF 哈希必须一致，再由原始参考函数的 CEC 覆盖。GPU 表先与独立 CPU 实现核对，再用单次 GPU 生产模式重建并要求文件哈希相同。

| 设计 | 普通 ABC 进程中位秒 | CPU 回退版秒 | GPU 缓存版秒 | 普通/缓存映射阶段 CPU 秒 |
| --- | --- | --- | --- | --- |
| adder | 0.189691 | 0.195630 | 0.191580 | 0.064 / 0.059 |
| log2 | 8.819500 | 8.695253 | 8.782943 | 8.578 / 8.522 |

证据见 [adder 实验](../results/mu_mapper_adder_001/manifest.json) 与 [log2 实验](../results/mu_mapper_log2_001/manifest.json)。adder 验证了 115,353 次命中且没有未命中；log2 验证了 1,270,580 次命中，另有 5,207,187 次未命中。log2 仅预计算前 20,000 次捕获调用对应的 5,121 种真值表，不能把这一覆盖率称为完整缓存或代表性抽样。

这些数据尚不能证明端到端加速：adder 的映射阶段减少约 5 ms，但进程耗时没有减少；log2 的小差异没有超过 CPU 回退版表现。只计一次 GPU 生产进程加一次缓存 mapper 的冷启动耗时，adder 为 0.387881 秒、log2 为 9.000105 秒，且还未计入首次 CPU 调用捕获和 Python 任务展开。缓存复用假设必须单独说明，不能把约 35 倍的分区内核加速推广为综合加速。

回归测试覆盖空缓存回退、截断文件拒绝，以及把合法数值范围内的重数故意改错后由逐次验证器拒绝。尚需研究更低开销的数据结构、批处理与驻留，当前结果支持继续实验，也支持保留 CPU 路径作为默认。

## FAPO 核心思路的独立实现

[fapo_reference.py](../scripts/fapo_reference.py) 根据 [FAPO 原文](https://arxiv.org/html/2610.06937v1) 实现独立的有界参考搜索。采用论文的 PI 扇出代价、LUT 单位延迟加扇出代价、最终存活 LUT 面积预算及 ATP 目标；实现复制、均衡 sink 分配、LUT cut 内联切换、同根恢复，以及完整关键 DAG 的最小顶点割。复制后重新计算全网络扇出，保留原始种子。常量按物理 VCC/GND 处理，不计 LUT 延迟或面积。

搜索上限是本项目的选择：默认 4 轮、每个状态优先 12 个根、每轮最多 128 个展开提案、frontier 4 项、archive 32 项、每轮至多一个最小割提案且最多 4 轮复制。它未覆盖作者未公开的全部启发式，也未提供原始 subject graph 上的全部替代 cut；因此不声称复现论文的数值表或收益。

[小套运行器](../scripts/run_fapo_suite.py) 对 baseline 和 ACD-remap 的相同输入分别运行混合搜索、关闭最小割、零面积增长、关闭扇出项四组消融。每个输出均需 CEC，并检查 BLIF 序列化后面积与时序仍与内部状态相同。功能测试覆盖并列关键路径的联合覆盖、上游负载抵消复制收益、真值表组合、种子保护及面积预算。

在 [K4 小套](../results/fapo_k4_suite_001/manifest.json) 中，priority baseline 从 236 LUT、模型延迟 21 变为 238 LUT、20，ATP 从 4956 降为 4760；关闭最小割时保留原种子。ACD-remap 加后优化从 230/18 变为 232/17。router baseline 从 87/17 变为 88/15。这些都是无量纲扇出模型，不能当作 Gowin 的 ns 延迟。

该 priority baseline 后优化网表另做了 [高云 P&R](../results/gowin_priority_fapo_margin_001/manifest.json)，与原 baseline 使用相同 50 ps hold uncertainty。两者 setup/hold 违例均为零，但报告 Fmax 从 107.373 降为 105.369 MHz。最差已报告寄存器到寄存器路径所需周期从 8.327 变为 8.306 ns。整体 Fmax 与核心路径指标方向不一致，且模型 ATP 改善未带来整体 Fmax 改善；后续需要单独评估物理结果，不能默认启用此模型的最优候选。

[K6 小套](../results/fapo_k6_suite_001/manifest.json) 对 ctrl、priority、router、adder 的两个父映射完成了同样四组消融，共 32 个输出全部通过 CEC。ACD-remap 的 adder 从 361 LUT、模型延迟 44 变为 366 LUT、31；仅 greedy 为 365/32。ctrl 未改善，priority 的 ACD-remap 未改善。这是固定小套上的独立实现结果，未用于宣称全 EPFL 平均收益，也没有把 K6 网表送入只接受 LUT4 的当前 Gowin 接口。

## 重现入口

在仓库根目录执行：

```powershell
.\scripts\build_abc.ps1
python scripts/run_abc_reproduction.py --out results/abc_new --repeats 5 --timeout 180
python scripts/run_abc_reproduction.py --out results/abc_new --repeats 5 --timeout 180 --resume
python scripts/summarize_abc.py results/abc_new/manifest.json --out results/abc_new/summary.json
python scripts/build_acd_profiler.py
python scripts/collect_acd_corpus.py results/abc_new/adder/normalized.aig --reference third_party/epfl/arithmetic/adder.aig --out results/corpus_new
python scripts/prepare_acd_gpu.py results/corpus_new/cuts.bin --out results/gpu_new
.\scripts\build_acd_cuda.cmd
.\build\cuda\acd_partition_bench.exe results/gpu_new/partitions.bin
python scripts/build_mu_mapper.py
python scripts/prepare_acd_gpu.py results/corpus_new/cuts.bin --all-partitions --out results/gpu_cache_new
python scripts/run_mu_experiment.py results/abc_new/adder/normalized.aig --reference third_party/epfl/arithmetic/adder.aig --partitions results/gpu_cache_new/partitions.bin --out results/mu_new
python scripts/run_fapo_suite.py --parents results/abc_gowin_k4_001 --out results/fapo_k4_new
python -m unittest discover -s tests -v
```

运行输出目录需为新目录；只有复现实验运行器支持带配置和产物校验的 `--resume`。源代码获取与构建要求见固定版本清单，下载上游最新版本不能保证得到这里的结果。
