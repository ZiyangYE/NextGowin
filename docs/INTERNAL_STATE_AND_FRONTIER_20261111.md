# 内部状态函数缓存与内部关键路径接管

本轮最高完整验收频率为 **86.7 MHz**，此前为 86.6 MHz。110 MHz 尚未达到；没有硬件运行验证。

唯一主检查点：`results/vexriscv_internal_state_trial_001/qualified_replay_checkpoint.json`。86.7 MHz 独立完整 I/O STA 的 setup 为 +0.018 ns、hold 为 +0.133 ns，零违例，其他原生检查通过。位流接口及哈希在同目录 `bitstream_interface.json`。封装引脚、电气约束和接口周期保持不变；布局、布线由本项目完成，高云仅完成最终 STA 和编码。

## 已执行的变换与证明

`prepare_internal_state_cache.py` 将有界同步状态函数 F(Q) 缓存至新增 FF。复用已经存在并穷举验证的 F(T(Q)) 次态锥，在其 LUT 空闲配对位置添加一个 FF，改接五个 LUT/OBUF 负载，包括同步控制反馈。旧状态保留，增加 **0 LUT、1 FF**。本例初态 F(Qinit)=0，共验证 128 组抽象状态/数据/使能组合。

归纳不变式为新增 C=F(Q)。折回 C 后，原状态转移、输出和被观测的次态项与原网表严格相同。折回本身不能检验新增 FF；本轮补充 `verify_materialized`，对落盘网表、布局和证书重新构造并逐项比较，检查新增 FF 的 INIT、D/Q/CLK/CE/RESET 及物理控制组。实际产物复核及源码哈希在 `functional_revalidation.json`，验证器快照为 `cache_verifier_source.py.txt`。

测试 `tests/test_internal_state_cache.py` 两项通过，包含新增 FF 五个引脚及 INIT 的错误注入。适用范围收紧：拒绝未建模的 DSP/BRAM 消费者，避免把存在组合旁路的硬核误当同步边界；要求明确常量网、同一主时钟、已知初值，最多六个源状态和十八个抽象变量。不声称异步波形、CDC 或任意硬核模式等价，也未证明连续叠加缓存的联合归纳。

当折回图逐字段完全相同时，CEC 返回空任务列表是允许的；必须同时证明严格结构相等，且独立校验新增寄存器。强求此时返回非空 SAT 任务不会补足寄存器本体证明。

## 实测收益为何仍小

共同 110 MHz 约束下，WNS 从 −2.446 ns 改善为 −2.425 ns，TNS 从 −378.411 ns 改善为 −373.395 ns。报告 Fmax 为 86.833 MHz；已独立验收的是 86.7 MHz。

原最差 I/O `ng_pad_215/O` 从 −2.446 ns 改善到 −0.746 ns，改善 **1.700 ns**；`ng_pad_280/O` 从 −1.262 ns 改善到 −0.046 ns。新增 FF D 端 setup 裕量为 +1.810 ns。原有 2,790 个 setup 终点均未退化，平均改善约 20 ps；新增 FF 另受完整 STA 覆盖。证据为 `endpoint_comparison.json` 和 `cut_boundary_diagnostic.json`，不同源/路径的终点比较不能解释成同一条路径的因果延迟分解。

内部路径接管：最差算术路径 cell 4.307 ns、wire 7.129 ns；深层控制路径 cell 3.342 ns、wire 8.141 ns；BRAM 地址路径 cell 1.786 ns、wire 9.661 ns。三个竞争簇最差裕量仅差约 77 ps，因此只处理一个簇难以显著提高整机频率。

新增通用诊断 `ownpr_critical_frontier.py` 对已审计的 setup 终点路径聚合共享布线边界，不修改设计。本例最差 0.3 ns 窗口有 13 条路径，其中 9 条来自同一 103 扇出的寄存器。该寄存器至两个关键逻辑入口的实测布线分别为 2.155 ns 和 2.491 ns。没有发现可直接复用的结构等价寄存器。排名中的 wire exposure 只是重复路径上的布线暴露量，不能作为预计可恢复延迟。

## Gemini 讨论及下一次试验

已完成两轮指定模型咨询：根目录 `carbon-advice-20261111-internal-cache-02.md` 与 `carbon-advice-20261111-coopt-next-03.md`。第二轮按更新后的配置使用无调用超时，未读取过程日志。报告为建议，以下计划结合现有实现作了修正：项目已有 `ownpr_measured_carry_pack.py`，无需因另一个文件名不存在而重建相同功能；状态预计算仍维持六源状态/十八抽象变量的已证明边界。

下一次独立尝试采用一个共享的 3,500 秒截止时间，包含准备、证明、布线、导出和频率资格复核：

1. 先释放非冻结产物的可压缩空间并预留最坏输出，始终小于 40,000,000,000 字节。重新读取当前检查点并验证哈希。
2. 按共同关键路径覆盖选择发射寄存器和接收端簇，尝试给算术低位/高位分支及 BRAM 地址锥分配局部副本；同时计算新增 D/CE/RESET/CLK 负载，不能只减少 Q 扇出。
3. 扩展现有算术邻接实测模块，联合处理前锥、宽宏、算术段与后级结果选择。优先探索保持已标定 carry 编码的布局和重映射；整条 carry 迁移、配对 FF 移动须先补足合法性能力。宽宏拆回 LUT4 必须比较真实到达时间，不能按级数直接决定。
4. 对三个竞争簇共同检查。若代表路径均改善至少 0.6 ns 且无新内部端点跌破 −1.8 ns，才支持联合优化方向；几何距离减少而实测延迟不降时，转查路由树负载和开关盒绕行。这些阈值是实验判据，不是保证。
5. 组合变换、状态复制分别完成等价性证明，再做完整 I/O setup/hold、配置连接审计和编码。110 MHz 不通过时，单独复核可通过频率；超时不能采纳部分结果。UART/int2float/TMDS 按冻结评估政策作 95% 实际 STA 回归守卫。

本轮未修改其他三个工程，未证明新算子在其他设计上有性能收益；不能把此前固定检查点的后端回放称为新算法的泛化验证。

## 复现入口

```powershell
runtime_bundle/python/python.exe -m pytest tests/test_internal_state_cache.py -q
runtime_bundle/python/python.exe scripts/replay_coopt_checkpoint.py --case vexriscv --report results/vexriscv_internal_state_trial_001/qualified_replay_checkpoint.json --target-mhz 86.7 --seconds 600 --out results/<新复核目录>
runtime_bundle/python/python.exe scripts/ownpr_critical_frontier.py --sta results/vexriscv_internal_state_trial_001/export/native --window 0.3 --out results/<新诊断文件>.json
```

第一遍频率复核在导出前被磁盘预留检查拒绝；无损压缩并校验解压 SHA 后，在同一原始截止时间内重试成功。详见 `qualification/manifest.json`、`qualification_retry_001/manifest.json`、`cleanup/internal_state_space_003.json` 与 `_004.json`。未覆盖冻结证据。
