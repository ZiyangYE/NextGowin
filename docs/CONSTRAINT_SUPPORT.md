# 当前约束支持边界

时序约束已经实际参与 ACD 映射；输入晚到端口的改变会改变被选择的分解，原始 deadline 不满足时会返回失败状态。当前组合映射接口、GPU 图传播和原生高云后端的支持范围不同。

| 能力 | 自研组合映射接口 | GPU 固定图传播 | 原生高云 P&R 路径 |
| --- | --- | --- | --- |
| 输入 arrival | JSON `input_arrivals_ps` 或默认值 | 每个快照的 PI 值 | 交给原始 SDC，检查激活状态 |
| 输出 required | JSON `output_required_ps` 或默认值 | 每个快照的端点值 | 由原生时钟与 I/O 约束建立 |
| 时钟周期 | `clock_period_ps` 可作为默认输出要求，不建立时钟对象 | 不建立时钟 | 已验证 FPGAFly 五时钟及 generated clocks |
| I/O max/min delay | 由调用者转换成组合边界，未自动解析 SDC | 仅接受给定 arrival/required | 工程 SDC 原样输入；小探针已检查 max/min |
| clock uncertainty | 未解释 | 未解释 | 已验证 hold uncertainty；工程原约束保留 |
| false path、异步 clock groups | 未解释，不自动越过时钟边界 | 未解释 | FPGAFly 原始例外必须在报告中处于激活状态 |
| multicycle、max/min delay | 未自动转换 | 未自动转换 | 审计器能核对命令激活；当前工程未专门验证每种语义 |
| setup/hold、recovery/removal、脉宽 | 组合模型只计算 setup | 组合 arrival/required，不代表这些检查全覆盖 | 独立读取原生端点/TNS 和已报告路径/脉宽 |
| DSP、BRAM、carry 内部时序 | 保留为硬块切口，未作完整数值建模 | 不跨硬块传播 | 由原生器件库计算，原始连接保留 |
| 布线、skew、引脚模式 | LUT 521 ps 包络；物理反馈另列启发式 | 当前为均匀解析权重 | 读取器件真实模式和本次布线结果 |

组合接口只接受已定义的 JSON 字段和 ps 单位。未知字段、未知端口、缺少 required、非有限值和没有时间原点模型的负 arrival 会报错。它不接受任意 SDC 脚本后假装已经解释；工程 SDC 由原生后端执行，不是该 JSON 接口的通用转换器。

ABC ACD 内部把时序量当作整数 LUT 层处理：输入 arrival 向上、required 向下量化到 LUT 单位。读取使用保留时序注释的 `read_blif -n`，输出后再检查注释，最后用原始 ps 约束独立计算 WNS/TNS。原生 mapper 放宽不可满足 deadline 的情况单独记录，不用于替代用户原始要求。

验收例见 [late a](../results/constrained_late_a_004/manifest.json)、[late b](../results/constrained_late_b_004/manifest.json)、[不可满足要求](../results/constrained_impossible_004/manifest.json)。这些是组合接口证据，不是布线后时序。器件表范围见 [高云时序记录](GOWIN_TIMING.md)，真实工程约束核对见 [FPGAFly 记录](FPGAFly_REMAP.md)。
