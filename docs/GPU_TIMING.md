# GPU 正反向时序传播实验

这是固定组合 LUT 图上的传播实验，不是完整 mapper，也不是高云全器件 STA。节点数包含 PI、常量及逻辑节点；层数包含输入层。权重为用户选择的均匀解析延迟，K6 图不代表高云存在物理 LUT6 原语。布线、FF 内部、时钟、例外和多时钟分析继续由后端处理。

## 实现与正确性

[图准备器](../scripts/prepare_timing_graph.py) 从真实 BLIF 生成 fanin/fanout CSR、拓扑层、输入到达时间和端点要求。实验使用 64 位整数 ps，显式边界的 arrival 向上、required 向下量化到 1 ps；无约束节点保留 INF，反向传播不对 INF 做减法。常量按零延迟处理，去掉真值表中无功能依赖的输入。

[原生 CPU/CUDA 实现](../cuda/timing_bench.cu) 正向读取 fanin 最大值，反向读取 fanout 最小值，每个节点和快照只有一个写入者，不依赖原子更新。CPU 使用线性拓扑遍历，GPU 使用逐层 kernel 和 CUDA Graph 复用。所有节点的 arrival/required 数值均需与独立的 Python 向量化参考结果完全一致，不能只核对最大输出时间。

验证另包括：在同一 CUDA Graph 上改变输入到达时间，先放宽、再收紧 deadline，随后恢复原边界；每一步都重做精确核对。这会检测仅重复相同输入时难以暴露的旧 required 值残留。测试还覆盖 reconvergent fanout、多端点要求、常量、不可达逻辑、1500 级窄链、损坏 oracle、截断文件及隐藏 CUDA 设备后的 CPU 回退。

GPU 不可用、分配失败或安全预算不满足时，程序保留已验证的 CPU 路径并给出明确原因。当前设备数据缓冲区预算不超过初始化后空闲设备内存的 70%，捕获层数上限为 4096；这些是安全限额，不是测量所得的通用性能分界。

## 真实图与测量范围

[首套实验](../results/timing_gpu_suite_001/manifest.json) 使用 ctrl、log2、sqrt 的 EPFL 映射结果，以及 FPGAFly 整个 core 的 LUT 切口，分别运行 1 和 64 个独立边界快照，共 8 组。CPU 提供单线程与最多 8 线程的快照并行版本；小图使用自适应内部重复次数以避免计时分辨率不足。每种模式保存 5 个样本。

GPU 驻留 wall time 包含图启动和同步；另测量完整边界数组上传及所有 arrival/required 结果下载。拓扑上传、分配、CUDA Graph 捕获和首次调用单列；Python 准备、文件读取与 CPU 参考验证不包含在 `cold_gpu_call_ms` 中。整进程 wall time 包含重复性能/正确性检查，不能当作一次查询延迟。

实验期间有原厂 P&R 等主机活动，GPU 独占也未建立。每次运行保存前后 `nvidia-smi` 采样与显存空闲值，因此以下是并发环境诊断，不是受控硬件速度结论。设备显式缓冲区最大约 119 MiB；该值未包含所有 CUDA 上下文或驱动内部分配，不称为进程峰值显存。

## 已观测结果

下表为一次完整批次的中位耗时，单位 ms。CPU 栏使用最多 8 线程；单快照时实际为 1 线程。所有 8 组均通过完整数值核对及边界变更检查。

| 图 | 节点 / 层 | 快照数 | CPU | GPU 驻留 | GPU 含边界/结果拷贝 |
| --- | --- | --- | --- | --- | --- |
| ctrl | 37 / 3 | 1 | 0.000263 | 0.0579 | 0.0882 |
| ctrl | 37 / 3 | 64 | 0.010585 | 0.0618 | 0.1117 |
| log2 | 10371 / 58 | 1 | 0.116626 | 0.5922 | 0.7728 |
| log2 | 10371 / 58 | 64 | 2.0123 | 1.6043 | 5.4967 |
| sqrt | 10701 / 638 | 1 | 0.113589 | 16.6090 | 14.3648 |
| sqrt | 10701 / 638 | 64 | 2.5983 | 12.4974 | 17.1843 |
| core | 60009 / 8 | 1 | 0.365423 | 0.1493 | 0.4707 |
| core | 60009 / 8 | 64 | 8.1940 | 0.9454 | 19.5024 |

core 的 64 快照驻留批次约快于该次 8 线程 CPU 的 8.7 倍，但拷贝后比 CPU 慢；冷 GPU 调用约 245 ms。sqrt 的深窄图即使批处理也更慢。GPU 的部分样本抖动明显，且传输模式偶尔快于驻留模式的同组中位数，不能把它解释为传输有负成本；原始样本及并发环境需一并考虑。

图表提供 [PNG](../results/timing_gpu_suite_001/timing_comparison.png)、[SVG](../results/timing_gpu_suite_001/timing_comparison.svg)，完整数值见 [CSV](../results/timing_gpu_suite_001/summary.csv)。图中点为中位数，误差线为 5 次的最小/最大值。

当前证据支持对浅而宽、可长期驻留的重复快照继续研究 GPU。小图、深窄图及需要频繁完整主机拷贝的查询继续保留 CPU 默认路径。尚未把这些数值当作 RTL 综合或完整映射加速，也未证明图结构不断变化时能摊平重建和捕获成本。

## 重现

```powershell
python scripts/build_timing_cuda.py
python scripts/run_timing_suite.py --out results/timing_gpu_new --context "Describe concurrent host and GPU activity here"
python scripts/plot_timing_gpu.py results/timing_gpu_new
python -m unittest discover -s tests -v
```

[构建入口](../scripts/build_timing_cuda.py) 固定源文件和二进制哈希，[单图运行器](../scripts/run_timing_bench.py) 检查图及构建产物未变，并保存原始输出、环境采样、分项时间和所有样本。它支持 `--cpu-only`；自动 GPU 回退会明确标记 `gpu_correct=false`，不能被计为 GPU 验证通过。
