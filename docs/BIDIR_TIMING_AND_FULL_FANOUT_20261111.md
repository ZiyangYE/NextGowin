# 全图建立/保持时序与完整扇出试验

本轮没有提高频率，合格交付仍为 **87.3 MHz**，110 MHz 目标未完成。完成了此前缺少的双向全图时序评价，并在一次真实改线后用高云完整 STA 验证其预测。结果和失败均保存在 `results/vexriscv_full_fanout_trial_001/engineering_summary.json`。

## 新增的核心能力

`ownpr_bidir_timing.py` 实现上升/下降方向的最晚与最早到达传播，以及反向要求时间传播：setup 取下游 `RT-delay` 的最小值，hold 取最大值。完整 DAG 包含竞争输入与隐藏分支；改边先构造候选，失败不改变原图。

`native_timing_state_probe.py` 从哈希固定的高云 DLL 读取同步时序状态；这是派生数据与接口观察，不是恢复的原厂 C++ 源码。便利接口 `getArrivalTimeForSyn` 返回最差裕量选中路径的到达时间，不能当作该引脚所有方向的最大到达值。本轮分别提取了 rise/fall 的 setup、hold 到达与要求时间，并与原生 slack 查询交叉验证。可复现的反汇编证据在 `binary_audit/native_time_states_001`。

`build_native_bidir_model.py` 将固定布局/时钟下的原生源时钟到达与终点约束接入独立传播图。内部弧使用已校准原语表和实际整树测量。它不是独立硅片签核：源边界和终点约束来自原厂，OBUF 的 fast 固定电气模式弧由观察得到，未推广为通用 I/O 库。

| 基线校核 | 到达方向值 | 要求时间方向值 | 最大到达误差 ns | 最大要求时间误差 ns |
|---|---:|---:|---:|---:|
| setup | 47,616 | 46,378 | 0.000151 | 0.000151 |
| hold | 47,616 | 46,378 | 0.00000240 | 0.00000206 |

源 seed 与终点 RT 是导入锚点，以上总数包含它们；另有测试明确排除锚点后核查内部传播。模型覆盖全部 **2,798 个同步终点**，setup 370 个违例、hold 零违例，与基线完整 STA 一致。已知常值、无同步终点的 OBUF 不参与时序接受判断。多时钟、改变 FF/硬核模式、移动状态或时钟、改变电气/时序约束均需重新准入；恢复/移除等检查仍交给最终原厂完整 STA。

单核连续三次全图 setup+hold 评价耗时约 0.52–0.65 秒，中位约 0.62 秒，不含文件读取、审计和路由。没有进行 GPU 性能比较。

## 真实候选与预测核查

`ownpr_full_fanout_alias.py` 联合考虑四组已有等价状态副本的 **337 个逻辑接收端**，重新分配 188 个。不增加 LUT/FF，不移动任何原单元，不改变时钟树或接口周期。向量硬核端口及其他不支持改接的端口保留原驱动，逐端计数；不存在报告之外的接收端被静默丢弃。签名仅用于发现，实际执行了变换前、变换后的联合归纳证明及严格折回观察相等检查。

这仍是按距离和负载数提出的试验候选。真正判断使用 `evaluate_fixed_clock_rewire.py`：在自研布线后更新全部实际 wire 弧，包括原接收端、改接端与第三方连带网络；锁定布局、时钟树、原语和同步边界，完整传播 setup/hold。IOFF 到 OBUF 的既有零延迟专用连接按精确绑定保留，不能误作缺失的普通路由。

| 110 MHz 下 | 全图测量反馈预测 | 原厂完整 STA |
|---|---:|---:|
| setup WNS ns | −4.106748 | −4.107 |
| setup TNS ns | −436.4364 | −436.426 |
| setup 违例终点 | 398 | 398 |
| hold WNS ns | +0.132700 | +0.133 |
| hold 违例终点 | 0 | 0 |

原厂报告保留三位小数，因此 TNS 的逐项舍入会累积。对改线后全部 2,798 个终点逐项比较，setup 裕量最大误差约 **0.63 ps**，hold 约 **0.50 ps**。详见 `rewire_model_validation.json`。该验证是第二份已布线网表的完整终点包络核对，未重新捕获其每一个内部状态方向，不能外推为任意器件模式精度。

候选已被模型提前拒绝；本轮仍完成原厂 STA 与编码，以验证首次变换后的预测可靠性。报告 Fmax 为 75.771 MHz，未做低频资格复核，也未采用该位流。原 87.3 MHz 检查点及其来源哈希已重新确认。

16 种按状态组保留/回退的时序投影也没有优于基线。它们只是整树时序组合投影，未证明混合后的资源占用合法，不能称为 16 次已执行的 P&R。

## 结论与下一步联合算子

均衡扇出数量不能保证消除布线延迟：不同 FF 槽位的接入资源、既有主干的共享关系和分支拥塞都重要。下一步按**已有实际路由树的分支**生成转移候选：整块接收子树从原驱动分离，利用已有等价副本连接子树切点，同时重测原树与新树、更新全局 setup/hold，再统一接受或回滚。现有 `ownpr_source_reconnect.py` 提供保留下游片段后重接源的基础能力，但跨逻辑网络的子树转移、所有权更新与联合事务尚未实现，不把这项计划写成完成。

当前完成的是统一评价的基础和一类改线验证，不是完整统一 synthesis+P&R 求解器。逻辑重映射、资源打包与拓扑感知路由仍需接入联合候选框架，不能仅封装阶段调用就宣称完成融合。

## 校验、预算与复现

8 项测试通过，覆盖非关键兄弟分支反超、短路径 hold 击穿、错误状态对解释、向量接收端完整计数、假别名拒绝和禁止复用移动后状态约束。首次状态捕获因工作进程过早加载 ctypes 失败，首次导出反馈因未识别既有 IOFF 专用连接失败；两项修复使用新目录或修复导出目录，并沿用同一原始 3,500 秒截止时间，没有重新计时。原始失败清单保留。

```powershell
runtime_bundle/python/python.exe -m pytest tests/test_bidir_timing.py tests/test_native_bidir_feedback.py -q
runtime_bundle/python/python.exe scripts/audit_native_time_state_abi.py --out binary_audit/<新证据目录>
runtime_bundle/python/python.exe scripts/evaluate_fixed_clock_rewire.py --model results/vexriscv_87_3_bidir_model_001 --placement results/vexriscv_full_fanout_place_002 --routes results/vexriscv_full_fanout_trial_001/route --wire results/vexriscv_full_fanout_trial_001/export/wire --out results/<新反馈文件>.json
```

模型、校核和试验均仅针对当前固定单主时钟、零输入延迟契约及已校准模式。其他三个工程没有应用新算子，本轮没有新跨设计收益结论。新导出反馈仅在显式指定 `--bidir-model` 时执行，原默认导出路径不增加该分析。Gemini 代码审查报告为根目录 `carbon-advice-20261111-time-model-07.md`；其同步器、控制端、完整树和模型独立性建议按实际实现范围采纳，未将所有 MUX 选择脚或普通 ALU 数据脚笼统认作不可路由资源。
