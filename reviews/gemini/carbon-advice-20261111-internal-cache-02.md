# 内部状态缓存只读审查报告

审查文件：`scripts/prepare_internal_state_cache.py`、`tests/test_internal_state_cache.py`。

## 一、证明是否有效：数学归纳有效，但代码“折回CEC”对新增寄存器属重言式

在给定约束下（同正沿同源 `clk` 的 `DFFRE`、禁时钟/异步复位消费者与跨域、原网表无组合环、且满足 $C(0)=\text{INIT}=F(Q_{\text{init}})=0$），**同步周期级等价证明在数学上严格成立，含经旧 `CE` 逻辑的反馈**：

1. **无组合环**：改接仅断开 `bit`（$F(Q)$）至5个 LUT/OBUF 消费者的边，改接寄存器输出 `fresh`（$C.Q$），并将 `next_bit` 接入 $C.D$。在原 DAG 上以触发器切分重连不会产生组合环。
2. **归纳基（$t=0$）**：原寄存器未改动，$Q_{\text{new}}(0)=Q_{\text{init}}$，且 $C(0)=0=F(Q_{\text{init}})$，联合不变式 $Q_{\text{new}}=Q_{\text{ref}}\land C=F(Q_{\text{ref}})$ 成立。
3. **归纳步（$t\to t+1$，含 `CE` 反馈）**：设时刻 $t$ 有 $Q_{\text{new}}=q,\;C=F(q)$。折回网表 `folded` 将 `fresh` 替回 `bit` 后与含 `next_bit` 观察端口的 `reference` 完全同构，且 `reference` 中 `bit` 恰为 $F(q)=C(t)$。按拓扑序归纳，`new` 在 $(q,C=F(q))$ 下**全部组合节点**（含受 `C` 反馈的旧 `CE` 锥、原状态 `D/CE/RESET`、`OBUF` 及 `next_bit`）取值与 `reference` 完全相同，故当拍输出与 $Q_{\text{new}}(t+1)=T(q,x)$ 不变。
4. **缓存闭合**：128组 `info['variables']` 穷举覆盖了真实可达 `(q, d, ce, reset)` 子集，保证 $\text{next\_bit}_{\text{ref}}(q,x)=F(T(q,x))$，故 $C(t+1)=F(Q_{\text{new}}(t+1))$。

**证明盲区**：第79–80行构造 `folded` 时先删除 `nc[name]` 再把 `fresh` 替回 `bit`，仅是第74行改接的**语法逆操作**，恒有 `reference == folded`。它完全未检验新增 `DFFRE` 的 `INIT/CLK/CE/RESET/D` 连接，若 `nc[name]` 参数或引脚接错，`folded` 比较与 CEC 仍会通过。

## 二、实现漏洞

1. **未本地校验 $F(Q_{\text{init}})=0$ 与源复位极性（第55、60、77、84行）**：`transform()` 硬编码 `INIT='0'` 与 `initial_value=0`，未按 `info['states']` 初值校验 `((word >> init_addr) & 1) == 0`；第60行亦固定复位值为 `0`。若 `plan(..., True)` 未拦截 $F(Q_{\text{init}})=1$，128向量检查与折回比对均会放行，导致第0拍及 GSR 后失配。
2. **硬核组合路径与常值时钟漏检（第38–40行）**：`synchronous_fanout()` 对 `MULTALU27X18`/`DPX9B`/`SDPX9B` 允许 `*CLK*` 接常值（`v not in model.constants`）并直接终止扇出遍历。若 `MULTALU27X18` 处于纯组合/寄存器旁路模式（如 `CLK='0'`），检查在此截断，漏检下游异步复位或跨域消费者；复位名过滤仅含 `'RST'/'RESET'`，遗漏 `'CLR'/'CLEAR'/'PRE'`。
3. **缺同 CLS 控制集检查且校验顺序污染目录（第50、77–78、98–101行）**：`transform()` 仅查偶槽位空闲，未校验同 CLS 配对槽已有 FF 的 `CLK/CE/RESET` 是否兼容 `CE=1, RESET=0`；`main()` 先建 `a.out` 并对恒相等的 `reference/folded` 跑 CEC，最后才调 `validate()`，失败时残留输出目录且浪费运行时间。
4. **死 LUT 残留与边界检查缺失（第57、65、70–76行）**：改接后 `root` 零扇出仍留在网表与布局中，占用槽位并维持扇入布线负载；第65行 `model.functions[n]` 解包丢弃输出位 `_`，未断言 `_ == b`；第76行缺整型 `GND/VCC` 时静默退化为字符串 `'0'/'1'`；第57行 `1 << len(info['variables'])` 无指数爆炸上限保护。
5. **测试未覆盖新增寄存器本体（`test_internal_state_cache.py` 第11–18行）**：仅断言 `a == b` 与旧 `DFF` 不变，未校验 `new_ff` 引脚/`INIT`，亦无多拍逻辑仿真验证。

## 三、最值得做的一项时序诊断

**执行完整 Gowin STA 下的“切点双侧裕量分解对比（Cut-Boundary Dual-Side Slack Audit）”。**

新增寄存器 `C` 为复用片内 `LUT.F -> DFF.D` 直连而被固定在 `next_driver`（`ng_output_state_5`）位置，并非位于原 `ng_coopt_78` 或5个消费者的中心，核心风险在于**省去 $F(Q)$ 级数收益与跨区布线/反馈回环延迟的抵消**。应在完整 I/O STA 下成对对比缓存前后的两类路径（含 Setup 与 Hold）：

1. **发射侧（`C.Q -> 5个消费者 -> 同步终点/OBUF` 对比原 `Q -> ng_coopt_78 -> 5个消费者`）**：分解“省去 $F(Q)$ 组合锥延迟”与“`ng_output_state_5` 至旧 `CE` 树及 `OBUF` 新增线延迟”的净差值，并检查移除多级 LUT 后近端终点或 I/O 是否产生 Hold 违例。
2. **捕获侧（`* -> ng_output_state_5 -> C.D`，单列 `C.Q -> 旧CE -> ng_output_state_5 -> C.D` 自反馈环）**：确认新闭合的经旧 `CE` 往返路径是否突破 9.09 ns（110 MHz）成为新关键路径，直接决定应保持原位、将 `next_driver+C` 向消费者质心协同移位，还是拆分 `OBUF` 与内部控制副本。
