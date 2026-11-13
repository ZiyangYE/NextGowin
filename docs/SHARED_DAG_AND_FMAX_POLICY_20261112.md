# 共享 DAG 协同优化与 Fmax 新验收口径

本轮原厂 110 MHz 完整 I/O STA 报告的 **Fmax 为 89.273 MHz、hold 为 +0.133 ns**。按用户新要求直接采用这个 Fmax，**没有另跑频率确认**。这与前轮原厂报告的 Fmax 相同，不能将历史独立复核的 89.2 MHz 改报为 89.273 MHz 当作算法提速。

110 MHz 下 setup WNS 仍为 **-2.111 ns**，因此 **110 MHz 目标未完成**。本轮实际收益是总欠时和违例数量下降。

| 同为 110 MHz 原厂 STA | 前轮 | 本轮 |
|---|---:|---:|
| reported Fmax | 89.273 MHz | 89.273 MHz |
| setup WNS | -2.111 ns | -2.111 ns |
| setup TNS | -309.781 ns | -285.332 ns |
| setup 违例端点 | 348 | 341 |
| hold WNS | +0.133 ns | +0.133 ns |
| hold 违例端点 | 0 | 0 |

TNS 改善 **24.449 ns**，但全局最差路径没有改善。最终候选相对旧 88.6 MHz 源网表增加 25 个普通 LUT，相对前轮源状态净增 12 个，FF 数量不变。所有结果均来自固定输入状态的 warm 协同优化，不能当作从 RTL 重新综合的完整运行时间比较。

## 用户指定的新规则

`AGENTS.md` 与 `results/frequency_reporting_policy_20261112.json` 记录用户原话及覆盖范围。`scripts/finalize_joint_lut_trial.py` 默认行为已改为：

1. 完成原厂 110 MHz 全 I/O STA/编码、等价、连通/资源/配置及独立时序模型核对。
2. hold 通过且其它检查通过后，直接使用报告的 `reported_fmax_mhz`，不降为一位小数，不为该频率重复 STA。
3. 保存原始 110 MHz 的 setup/hold 指标及实际时钟约束；不把这个 Fmax 当作 110 MHz 已闭合的证明。

本轮没有 `qualification/` 目录，收尾记录 `frequency_replayed=false`。历史独立复核、冻结基线和交接 ZIP 未改写。旧的独立频率复核只保留为显式指定的历史复现模式，不是后续默认流程。

## 新增实现与实际覆盖

`ownpr_shared_dag.py` 实现多个输出根共享一份新前缀 DAG，每个内部节点只复制一次；各根保留原输出 bit。可在原区域保留等价根副本接管旁路负载，使逻辑复制、根选址、合法化和完整接收树重布成为一笔共同评价的事务。

`ownpr_shared_feedback.py` 增加了受限的宽 LUT 外部输入源替换准入：宽叶的位置、INIT、输出 bit 和专用连接不变；新驱动必须与原驱动为结构完全相同的普通 LUT 函数。归一化只用于准入审计视图，实际时序图仍根据真实候选及测得的线延重建。

`ownpr_clone_route_seeds.py` 支持按物理源节点复用公共分支，解决克隆后逻辑网名变了而旧物理接收路径仍有效的情况。它不承诺保留原时序，仍重新测整树并守住逐端点 hold。

四次有界搜索共记录 **57 次提案、45 次实测、12 次失败/超时**，全部保留。按全局 WNS、TNS、资源的固定排序选择 `vexriscv_shared_dag_search_003`。另一个候选的违例端点数更少，但 TNS 较差，未事后改换排序选择它。

必须区分能力代码与验证结果：

- 最终候选实际包含共享 DAG、普通旁路隔离和布局调整，通过原厂终审。
- 最终候选实际改动的宽叶输入数为 **0**。最差路径根 `ng_coopt_2603` 的接收端全在宽 LUT 叶上，相关输入隔离提案未能在本轮预算内布通。因此这项扩展只有准入/等价约束测试及失败路由证据，**尚不能称原厂验证了实际改过宽叶输入的实现**。
- 分支复用在最后一版守卫的部分提案中触发，但提案未被采用；不能将其说成本轮最终候选收益来源。
- FF、时钟、硬核位置/模式仍在原已校准边界内，没有实现任意移动状态或任意硬核模式的通用反馈。

## 验证与守卫

最终设计通过全网表固定状态组合 CEC、独立物理连通/冲突检查、配置合成和原厂编码。全部 **2798 个 setup、2798 个 hold** 端点与独立模型核对，最大 slack 误差约 **0.000622 / 0.000500 ns**。固定引脚、I/O 电气属性和接口周期保持，未运行原厂布局布线，未烧录硬件。

主候选与 `shared_dag_structural_guards_001` 的核心操作源文件 SHA 精确一致。UART/int2float/TMDS 的 raw 搜索都选择不改变原实现，按精确物理实现 SHA 复用原厂 STA，保留 **197 / 167.5 / 112.3 MHz**。这是无退化守卫通过，不是三项工程获得提速。

`shared_dag_structural_guards_002` 另外检查加入分支复用后的入口；其结果单列，不替代主候选对应的第一组守卫。

49 项针对性测试通过，包括共享菱形 DAG、保持硬核输入、旧区域旁路隔离、配对 FF 不移动、快脚置换与 GND 填充、宽叶源替换的拒绝条件、物理源相同的网名变换分支复用，以及 Fmax 新口径。收尾器还检查没有频率复跑目录。

## 为什么还没有推动 Fmax

最差 FF/宽 MUX 路径仍包含多段较长普通互连；改善其它共享输出簇只能减少 TNS。候选队列对同一根的多种锥宽覆盖仍不足，最关键的宽叶旁路隔离又没有成功布通。这是搜索覆盖与物理求解的具体限制，不能视为固定状态边界的理论极限。

Gemini 的审查保存在 `carbon-advice-20261112-shared-implementation-15.md`。已吸收数值布尔误判防护、快脚/GND 集成测试及候选覆盖问题。其关于任意含 FF Tile 都不合法的推断并未直接作为禁用规则；现有所选候选通过了独立配置和原厂回读，但这同样不证明全部混排模式已认证。

额外的只读切口枚举保存在 `wider_cut_opportunities.json`：

- 当前最差锥根 `ng_coopt_2603`：K5 可覆盖 2 个内部单元，K7 可覆盖 3 个。
- `ng_coopt_3056`：K6 可覆盖 3 个，K7 可覆盖 4 个。
- BRAM 地址根 `ng_coopt_2730`：发现 K6、2 个内部单元的切口；本次有界枚举未给出 K5 的对应结果。

这些不是完整枚举或物理增益证明。下一轮值得比较同根不同锥宽的均衡搜索，以及真正改变 LUT5/6/7 宏拓扑和位置的映射/打包/路由共同搜索。现有 CEC 已将这些 MUX 当作组合逻辑展开；当前主要软件缺口在允许新宏集合的双角时序图重建、合法化和原厂校准终审，不能只调用已有 `Cuts.replace` 就宣称完成。LUT8 的跨 CFU 几何不在现有认证范围。

## 复现与证据

本轮主检查点为：

`results/vexriscv_shared_dag_trial_001/all_designs_reported_fmax_checkpoint.json`

主记录包括 `candidate_selection.json`、`export/manifest.json`、`native_model_validation.json`、`reported_fmax_checkpoint.json` 和 `completion_audit.json`。实际执行版本保存在各搜索的 `sources/`；后续源代码更新不能冒充旧版本逐位复现。

如日后显式要求完整复验，可对这个检查点在 **110 MHz** 重放一次；不自动改成 89.273 MHz 再确认：

```powershell
runtime_bundle/python/python.exe scripts/replay_coopt_checkpoint.py --case vexriscv --report results/vexriscv_shared_dag_trial_001/all_designs_reported_fmax_checkpoint.json --target-mhz 110 --seconds 420 --out results/shared_dag_replay_NEW
```

该命令会忠实报告 110 MHz 的负 setup 裕量，同时报告 Fmax；目标未闭合不等于位流/连通性检查失败。本轮未执行这个额外重放。运行前仍必须通过容量预留，目录总上限保持 40,000,000,000 字节。
