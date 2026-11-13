# 证明观测器掩码构造与 `equiv_make` 切点污染独立评审（049）

## 一、根因复核与最小决定性反例

复核 `docs/COMPOSITIONAL_MEMORY_PROOF_20261113.md`、`scripts/compositional_memory_cut.py`、`scripts/restore_equiv_miter_init.py` 及 `results/memory_match_causality_trial_030/matched_write_cones.json` 后确认：未合并抽象控制器对的 4 步基例已通过全部原始 IO 与新增存储器命令义务；`equiv_miter` 确实丢失线网 `init` 属性，`restore_equiv_miter_init.py` 仅凭同名 `$ff` 单元、双射端口拓扑与同名等宽线网位偏移见证精确搬运 4363 个状态单元的 5015 个已定义初值位（未虚构初值），消除了 `padding_write` 及后续帧违例，仅余第 0 帧 `cmp_ng_mem_{3,4,5,6}_part_0_write_high` 四个失败点。

根因并非硬件或门级实现失配，而是 **`equiv_make` 的对称三值切点扇出重写破坏了单向 Gold-$X$ 精化（refinement）与 Gate 内部使能—地址相关性**：
1. `compositional_memory_cut.py` 将输出端口 `write_mask` 直接绑定在内部写使能线 `en` 上，而 `en` 同时继续扇出驱动内部 `active` 与 `write_high = highwr & active`。
2. 在 `matched_write_cones.json` 中，`ng_mem_3_part_0_write_mask`（位 `607..614`）触发 `equiv_make.cc:260` 插入对称等价单元 `bit 614 = $equiv(A=18598, B=18599)`，并将 Gate 侧 `write_high` 锥（`18639 = 32215 & 614`）中的原始使能 `18599 = mux(0,1,27490)` **重写替换为切点 `614`**。
3. 第 0 帧中，Gold 使能 `18598` 受未声明初值的源寄存器驱动而为 `X`，Gate 对应物理寄存器具合法确定初值，使 `27490 = 0`、`18599 = 0`（满足 $0 \sqsubseteq X$）。未合并时，Gate 地址多路器（`$procmux$14295_gate`，`S=27490`）虽在失能分支选通 `A="x"` 使高位或归约 `32215 = X`，但 `18639 = X & 0 = 0`，与 Gold 常量 `"0"` 一致通过。经 `equiv_make` 合并后，`$equiv(X, 0)` 输出 `614 = X`：顶层 `write_mask` 因 Gold 为 `X` 被 `-undef` 规则豁免，但该 `X` 借切点 `614` 反向注入 Gate 内部，令 `18639 = X & X = X`，面对 Gold 已定义常量 `"0"`（`bit 643`）触发伪反例。

**最小决定性反例（1 状态位、2 观测输出 `m, h`）**：
- **Gold**：未初始化寄存器 `q_G`（`init=x`），`m = q_G`，`h = 1'b0`。
- **Gate**：确定化寄存器 `q_T`（`init=0`），`a = q_T ? 1'b0 : 1'bx`（甚至无 `x` 常量的纯二值 `a = ~q_T` 亦成立），`m = q_T`，`h = a & q_T`。
- **判定**：第 0 帧 $q_G=X, q_T=0$。未合并时 $m_T=0 \sqsubseteq X$，$h_T = X \land 0 = 0 = h_G$，全部通过；经 `equiv_make` 合并端口 `m` 后，`y_m = $equiv(X,0) = X` 替换 `h_T` 中的 `q_T`，`m` 仍因 $m_G=X$ 豁免，但 $h_T' = X \land X = X \neq 0 = h_G$（若 `a = ~q_T` 则 $1 \land X = X \neq 0$），第 0 帧必败。

## 二、`raw_output XOR mask` 构造的可靠性与五大隐患

当代数掩码 $k$ **严格限死在已定义集合 $\{0,1\}$** 时，Kleene 三值异或保持精化偏序不变：$(t \oplus k) \sqsubseteq (g \oplus k) \iff t \sqsubseteq g$（$0 \mapsto k, 1 \mapsto \bar{k}, X \mapsto X$）。纯汇点异或观测器确能阻止输出端口名被 `equiv_make` 回灌至功能锥。但置于完整 `-undef` 比较与时序归纳流程中，该构造**并非无条件可靠**，存在五项关键隐患：

1. **`-enable_undef` 下掩码取 `X` 导致时序归纳假设空真（核心隐患）**：在 Yosys `sat -enable_undef` 与 `equiv_induct -undef` 中，新增主输入默认可取 `X`。若未在**每一展开帧**对 `mask` 强制施加 `-set-def`，求解器可在归纳假设帧 $0\dots k-1$ 令 `mask = X`，使所有 `raw ^ X = X`（Gold 观测恒为 `X`，违例触发器恒为 `0`），令输出归纳假设彻底退化为空真，再在第 $k$ 帧令 `mask = 0` 产生伪归纳反例；而 `equiv_simple/equiv_induct -undef` 并无仅约束 `mask` 为二值却保留其余预言机输入三值能力的原生粒度。
2. **常量折叠与公共子表达式消除（CSE）制造组合环或跨端口串扰**：Gold 的 `write_high`、`read_high`、`padding_write` 大量为常量 `'0'`。若插入 XOR 后执行 `opt_expr`，`'0' ^ mask` 会被化简回输入线 `mask`；随后 `equiv_make` 将 Gold 端口线 `mask` 的扇出重写至 `$equiv` 输出 `Y`，会把 Gate 侧 `raw_gate ^ mask` 改写为 `raw_gate ^ Y`，直接构成**组合逻辑环**并污染所有共享该 `mask` 的端口。即便不跑 `opt_expr`，`opt_merge` 也会将多个 `'0' ^ mask` 或同字节 8 条重复使能 `18598 ^ mask` 合并为同一线网，致使 `equiv_make` 将多个 `$equiv` 级联串接，单点 `X` 跨端口传染。
3. **隐藏别名与 `restore_equiv_miter_init.py` 的耦合冲突**：若将原始信号改为 `hide_name=1` 隐藏线网并在其上挂靠 `init`，`opt_clean` 会清理冗余隐藏别名从而丢弃 `init` 属性；同时 `restore_equiv_miter_init.py` 第 44 行严格校验拷贝单元所有非字面量引脚位必须具备同名等宽 `netnames` 见证，不当隐藏或增删线网会触发搬运拒绝；反之若保留同名公开线网，`equiv_make` 仍会按内部同名线网匹配。
4. **残余内部同名切点仍会注入 `X`（实证已存在）**：`matched_write_cones.json` 第 687 行已出现 `bit 4630 = $auto$equiv_make.cc:297:find_same_wires$21806`（`A=18237, B=13833`），证实 `equiv_make` 除端口（260 行）外还会匹配内部公开线网与状态位（297 行）。仅对输出端口加 XOR 无法阻止内部未初始化 Gold 状态借内部 `$equiv` 向 Gate 确定态锥注入 `X`。
5. **源端未声明状态相关性与共享读预言机局限**：当同一未初始化 Gold 寄存器在 Gate 中映射为多个物理副本（初值均为 `0`）时，对称 `$equiv` 因 $X \neq 0$ 无法建立，归纳步会丢失 Gate 副本间相等不变式；此外无记忆共享输入 `ng_mem_*_read_*` 既未覆盖“Gold 读 `X`、Gate 物理阵列读确定值”的非对称精化，也未约束未写地址重复读取的跨拍一致性。

## 三、完整保留全部证明义务的替代方案

必须保留未合并基例与精确 `init` 搬运，严禁删除任何失败端点、添加复位假设或将 Gold 初值置零。推荐以下替代构造：

1. **异名保活汇点配对（无掩码输入替代 XOR）**：在进入 `equiv_make` 前，将 Gold 与 Gate 的待观测输出改为互异公开保活线（如 `\ng_obs_gold$...` 与 `\ng_obs_gate$...`，加 `keep` 属性且不暴露为同名端口），使 `equiv_make` 完全不改写观测源锥；随后在 `equiv_miter` 前逐一挂接**纯汇点** `$equiv(A=g_obs, B=t_obs, Y=port)`。此法无需任何 `mask` 输入，从根源上消除 `mask=X` 归纳空真、`'0'^mask` 组合环与 CSE 级联，且零侵入兼容 `restore_equiv_miter_init.py`。
2. **非侵入式单向精化乘积系统与双轨归纳（根治内部 297 行切点）**：停止用 `equiv_make` 重写内部功能扇出，直接由 `gold_abs` 与 `gate_abs` 构造保持两侧转移逻辑完全独立的乘积系统（天然保留全部原始 `init` 并可与 `restore_equiv_miter_init.py` 交叉核验）。将内部状态候选 $(q_G, q_T)$ 表述为纯旁路单向精化断言 $\neg\mathrm{undef}(q_G) \Rightarrow (\neg\mathrm{undef}(q_T) \land q_T = q_G)$，辅以 Gate 复制寄存器相等不变式 $q_{T,1} = q_{T,2}$；对 Gold 采用显式双轨 `(val, def)` 编码后，用 Houdini 仅淘汰不可归纳的内部辅助候选，**100% 强制保留原始 IO 与全部存储器命令义务**进行 $k$-归纳或 IC3 证明。

## 四、证明边界声明

即便控制器接口切点与归纳问题解决，完整的存储器及源级等价证明**仍未完成**：条件读预言机控制器证明、局部存储阵列状态转移归纳、原始初值及副本一致性组合良基性、以及 SDK 原语准入仍属各自独立的未闭环门禁；公平官方基线尚未冻结，四设计 20% 频率提升目标亦未完成。
