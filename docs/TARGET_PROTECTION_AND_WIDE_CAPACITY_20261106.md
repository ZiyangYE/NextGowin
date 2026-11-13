# 目标周期保护与宽宏共享容量校准

后续更新：共享后端已接通，实际 LUT5 共址及 LUT6 槽 4 共址通过完整 I/O 验收；见 [后续实现与资格证据](SHARED_WIDE_BACKEND_20261106.md)。下文保留隔离校准阶段的范围和负结果，不将旧捕获中的生产启用标记改写为新状态。

主结果仍为 **67.9 MHz 完整 I/O 合格**，检查点 `results/vexriscv_state_frontier_002/qualified_replay_checkpoint.json` 不变。本轮新增完整候选报告 Fmax 为 65.002 MHz，已拒绝；不宣称达到 90/150/200 MHz 或获得通用优势。固定封装引脚、电气约束、接口周期不变，未使用原厂 P&R、开源器件数据库或编程硬件。

## 目标频率必须参与合法化保护

此前 `--protect-slack 0.5` 根据原基线频率的 pin slack 禁止 incidental displacement。67.9 MHz 的周期为 14.727540501 ns，90 MHz 为 11.111111111 ns，减少 3.616429390 ns。因此，在当前验证过的单时钟完整周期合同下，保护目标频率附近 0.5 ns 的旧单元，应使用 **4.116429390 ns** 的原始 setup slack 阈值。hold 阈值不变。

新增 `ownpr_target_protection.py` 验证单一上升沿主时钟、简单 I/O 合同及报告中所有 setup 关系/终点数量。拒绝多时钟、生成时钟、时序例外、半周期和不完整覆盖。通过后将周期差加入保护余量；降低目标频率时不放松原保护。这个换算是旧实现的腾位保护过滤器，不预测变换后逻辑和负载的当前 slack。

`run_ownpr_region_coopt.py --target-aware-protection` 已接到 regions 和 multilevel 两个结构入口，要求同时指定 checked pin feedback 与保护 margin，并在候选中保存换算、源 SDC/报告哈希及保护单元。17 项相关检查通过，包括实际 2,775 终点合同、多/生成时钟与例外拒绝；初次测试因未容许原厂附加的纯报告命令而失败，修正后通过，失败日志保留。

## 完整物理结果及限制

`results/vexriscv_target_protection_001` 从 67.9 MHz 基线出发，选择 8 个 cut，保护 1,534 个原单元。净增 23 LUT / 23 宽 MUX，额外移动 6 个原普通 cluster，其中 5 个带 FF。逻辑等价、连通性、配置、完整 STA 与编码导出完成，总时间 **516.437 秒**，小于该次 2,400 秒截止时间。

90 MHz 下 setup WNS −4.273 ns、TNS −320.095 ns、294 个 setup 违例；hold WNS +0.134 ns、零 hold 违例。报告 Fmax **65.002 MHz**，所以没有进行较低频资格替换。它与上一轮七输入候选的基线、cut 选择都不同，不能当作严格同候选的单变量收益对照。

新最坏路径布线 **11.872 ns**、单元含 C2Q **3.475 ns**。一条新输入连接从原寄存器 R48C131 到新 `ng_coopt_1136` 叶 LUT 的 R80C95，跨 68 个曼哈顿 tile，实测 **4.940 ns**。这说明保护原位置只能避免部分副作用，无法保证新合并逻辑的输入负载和宏位置合理。另一组 CE 支路也有退化。原终点按周期归一化后，143 个改善超过 10 ps、131 个退化超过 10 ps。

下一步不能继续只扩大禁止移动的范围。需要利用部分 CFU 容量，使宽宏与附近逻辑共存，并将所有新输入/输出和竞争路径的实际延迟带入候选选择。

## 原厂来源的宽宏/普通 LUT 共享证据

当前后端把每个宽宏的整块 CFU 视为独占。实际 67.9 MHz 网表有 10 个 LUT6 宏和 8 个 LUT5 宏，因该限制保留了 **88 个未使用的 LUT 槽位**；排除已有宽宏/FF 配对的 CFU，仍有 **80 个**。这些是结构空位，不代表全部都能改善关键路径。

新增 `experiments/native_wide_lut_sharing.py` 在隔离子进程中直接调用已锁定高云 DLL，未执行 vendor placement/routing。在原厂一基坐标 [50,90]、[51,90] 两处 type-17 CFU，对 LUT5、LUT6 分别捕获 wide-only、ordinary-only、mixed 三种配置，共 12 个编码场景。所有场景均校验预期 LUT/CLS 位并恢复原始完整 payload，耗时 **183.969 秒**。

`scripts/audit_wide_lut_sharing.py` 再独立解码原始压缩配置图，验证来源哈希、器件表、12 场景完整覆盖、每个组合 199 项配置要求、位图精确组合、引脚绑定不变及外部输入/输出节点无交叠。结果：

| type-17 宏 | 宏根槽位 | 叶 LUT 槽位 | 可共存的普通 LUT 槽位 |
|---|---:|---|---|
| LUT5 | 0 | 0–1 | 2–7 |
| LUT6 | 1 | 0–3 | 4–7 |

证据在 `results/native_wide_lut_sharing_001/capture.json`、`audit.json`、`independent_audit.json` 和压缩配置图。**production_enabled=false**；尚未开放正式共享打包，未证明混合 FF/ALU、任意根槽位、实际普通线连接、加载后延迟或硬件行为。不能把隔离编码成功等同于完整 P&R 成功。

下一阶段实现范围是：允许经过校准的部分宽宏占用模式；只腾出叶槽位，保留其他槽位的普通 LUT；更新容量检查、配置编译和合法化；再执行等价、自研路由、完整 I/O STA/导出。现有整 CFU 独占规则在这些验证完成前继续生效，原 67.9 MHz 主结果不变。

## 复现与容量

```powershell
runtime_bundle/python/python.exe scripts/run_ownpr_region_coopt.py --report results/vexriscv_state_frontier_002/qualified_replay_checkpoint.json --transform multilevel --timing-feedback --protect-slack 0.5 --target-aware-protection --target-mhz 90 --cut-count 8 --cut-width 7 --min-cut-cells 2 --cut-window 4 --cut-growth 48 --gain-target-ns 4 --legalize-macros --seconds 2400 --out results/<新的候选目录>

runtime_bundle/python/python.exe experiments/native_wide_lut_sharing.py --out results/<新的隔离校准目录>
runtime_bundle/python/python.exe scripts/audit_wide_lut_sharing.py --capture results/<校准目录> --out results/<校准目录>/independent_audit.json
```

候选结果和同终点比较见 `results/vexriscv_target_protection_summary_001/summary.json`，频率障碍诊断在候选的 `frequency_diagnosis.json`。测试日志 `results/target_protection_tests_002.log`：17 passed。

四份已完成 capture 的重复输入经哈希核验后转为透明引用，节省 **528,204,900 字节**，逐份重验完整 pin 反馈读取；清单 `cleanup/native_capture_input_dedup_20261106_002.json`。原始来源、主检查点和冻结 ZIP 不变。本轮审计时工作区约 **37.923 GB**，唯一上限仍为 40,000,000,000 字节。
