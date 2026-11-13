# 进位链协同优化：本轮结果与架构判断

VexRiscv 110 MHz 目标未完成。最高已完成独立频率复核的实现仍为 **88.6 MHz**。本轮候选在 110 MHz 原厂完整 I/O STA 中得到 reported Fmax **88.811 MHz**；这不是已通过 88.8 MHz 约束的证明。

## 实测结论

| 110 MHz 完整 STA | 旧 88.6 MHz 实现 | 本轮候选 |
|---|---:|---:|
| setup WNS / ns | -2.195 | -2.169 |
| setup TNS / ns | -350.689 | -345.993 |
| setup 违例端点 | 370 | 368 |
| hold WNS / ns | +0.133 | +0.133 |
| hold 违例端点 | 0 | 0 |

候选新增 2 个普通 LUT，无新增 FF。获益来自在原进位链附近选择性复制 SUM 下游逻辑。最终接受事务的进位链位移为零，因此这轮不能声称“整链搬迁已取得原厂 STA 验证收益”。整链与邻近逻辑联迁候选大多因实际布线回弹而被拒绝。

已完成组合等价检查、6585 条网 / 50187 个 PIP 的连通性和冲突审计、独立时序图重建，以及原厂 STA/位流编码。2798 个 setup 和 hold 端点分别校验，最大 slack 误差约 0.000622 / 0.000500 ns。没有执行原厂布局布线，也没有下载硬件。

完整尝试的原截止时间保持不变。结束上述检查后，剩余预算不足以完成独立的 88.8 MHz 约束复核；收尾器明确返回失败。最终文档及源码快照收尾记录耗时 3530.57 秒，已超过预留的 3500 秒截止时间；再计入最多 120 秒的前期规划，无法证明完整尝试满足一小时要求。按规则，本轮计为**失败，不晋升候选**，保留 88.6 MHz 检查点。不得通过重置计时把候选计为合格结果。

## “一步完成 synthesis + place + route”的判断

值得保留的是统一物理时序目标和跨阶段反馈。现有证据不足以支持立即把全部映射、离散资源分配和路由选择改写为一个全局求解器；这样会同时扩大搜索空间与模型验证范围，不能保证在一小时内给出更优可复现结果。

本轮已经扩展了联合操作：保持寄存器、时钟、I/O 和硬资源边界不变，允许整条 mode-2 进位链合法平移、邻近 LUT 搬迁以及部分逻辑复制，随后实测受影响连接树，重建全图 setup/hold，再做等价与原厂终审。进位链准入仅限已标定的 type-17、同 slot/profile、六槽及相邻列结构，不能推广到任意列界或任意模式。

结果说明局部几何接近不足以预测延迟，且冻结状态单元和宽 MUX 宏会限制可行走廊。当前全局瓶颈转到 BRAM 地址路径：`ng_ownpr_region_18_0/Q -> ng_memory_0.1.0/ADB[7]`，诊断分解中布线约 **8.835 ns**、单元约 **2.157 ns**。这是路径分解，不应与 STA WNS 直接等同。

下一项高优先级工作是定位该地址网络的整棵扇出树和拥塞来源，比较靠近 BRAM 的逻辑复制、宽 MUX 与前后级逻辑联迁、按汇点裕量分配的分支重布。若必须移动寄存器，则先补齐时钟/控制集与 fast-corner hold 的原生校准，不能沿用当前固定边界时序模型。每项仍以实测全图时序决定去留，固定引脚和接口周期不变。

新 carry 操作尚未在 UART/int2float/TMDS 上完成独立泛化评测。先前普通 LUT 流程的回归结果不能作为本轮新操作的泛化证明。

## 可复现证据

- `results/vexriscv_carry_macro_trial_001/budget.json`：原始计时。
- `results/vexriscv_carry_macro_search_001`、`002`、`003`：保留被拒和接受的候选证据；003 为本轮最佳候选。
- `results/vexriscv_carry_macro_trial_001/export/manifest.json`：110 MHz 原厂终审与编码结果。
- `results/vexriscv_carry_macro_trial_001/native_model_validation.json`：全端点模型核对。
- `results/vexriscv_carry_macro_trial_001/engineering_summary.json`：未完成独立频率复核的明确失败状态。
- `results/joint_lut_structural_guards_001/qualified_replay_checkpoint.json`：继续保留的 88.6 MHz 合格检查点。
- `carbon-advice-20261112-carry-macro-12.md`：Gemini 咨询建议；建议与实测结果分开记录。

导出和模型大文件已做无损压缩，使用 `ownpr_image_storage` 按原始解压字节校验 SHA；固定的历史证据与交接 ZIP 保持不变。
