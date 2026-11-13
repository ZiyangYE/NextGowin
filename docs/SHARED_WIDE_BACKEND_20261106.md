# 宽宏与普通 LUT 共用 CFU 的后端

本轮把上一阶段的原厂 DLL 隔离校准接入真实打包、合法化、配置编译和自研路由。固定封装引脚、电气约束、接口周期及完整 I/O 覆盖不变；原厂仅用于器件查询、最终 STA 和编码。未调用原厂 P&R、开源器件数据库或自动编程硬件。

## 已接通的能力与边界

`ownpr_wide_lut_sharing.py` 根据源代码、fixture、DLL/FSE、原始捕获及独立解码图像哈希准入。type-17、默认根槽位的 LUT5 使用叶槽 0–1，普通 LUT 可用 2–7；LUT6 使用叶槽 0–3，普通 LUT 可用 4–7。布局必须显式携带校准摘要和共享标记。拒绝叶槽重叠、共享 FF、ALU、其他宏根槽位及 LUT7 的部分共享。

`ownpr_macro_legalize.clear_site(..., wide_leaf_slots=...)` 只腾出必须占用的叶槽位。其余槽位中已有的未配对普通 LUT 可以保持；已有 FF 仍需移出，不能暗中使用未校准的混合模式。受保护且必须移出的单元会使该候选站点不可选。`ownpr_timing_wide_remap.place(..., share_ordinary=True)` 允许这种部分占用，并经独立容量检查。

配置编译依据宽宏叶 LUT 和保留普通 LUT 的全部活动槽位生成 CLS 模式。测试逐项核对完整控制属性，而不只检查模式编号。其他局部移动算法仍把宽宏 CFU 作为整体保护，不自动拆动内部关系。

两个入口：

- 多层逻辑重构使用 `--share-wide-luts`，允许新 LUT5/6 宏与原普通逻辑共存。
- `--transform wide_neighbors` 利用已存在宽宏的空槽，选择实际报告路径中的普通 LUT 邻居。按历史时序权重和全部关联连接的几何代价排序，保持网表字节、FF/硬资源/I/O 位置及原时钟不变。这个排序尚不是加载后时序预测，仍须完整 P&R/STA 接受。

## 同网表完整对照

两个候选从同一 67.9 MHz 检查点出发，使用相同固定 I/O 和 90 MHz 目标。所选 8 个 cut 及最终逻辑网表逐字节相同，净增资源均为 23 LUT / 23 宽 MUX；差别来自共享容量下的合法化、布局、时钟重接和路由。

| 候选 | 报告 Fmax MHz | 90 MHz setup WNS ns | TNS ns | setup 违例 | 移动原 cluster / FF |
|---|---:|---:|---:|---:|---:|
| `vexriscv_target_protection_001`，整 CFU 独占 | 65.002 | −4.273 | −320.095 | 294 | 6 / 5 |
| `vexriscv_shared_wide_001`，允许部分共享 | 67.997 | −3.595 | −317.653 | 296 | 11 / 7 |

报告频率提高约 4.61%，但这不等于超过现有最佳已验收频率。部分共享允许选择更近且原来有逻辑的 CFU，所以原单元移动数反而增加；不能笼统声称它总能减少移动。

五个新宏采用共享准入，其中三处实际保留了 16 个普通 LUT，另外两处没有普通 LUT。实际有共址逻辑的三处都是 LUT5，因此不能用这次实验冒充 LUT6 的完整共址覆盖。两个代表宏的零基布局坐标：`ng_coopt_1348` 从 [60,101] 改为 [67,103]，`ng_coopt_1136` 从 [79,94] 改为 [77,99]。

自研路由用时 46.281 秒，6,546 网、50,583 PIP 的连通/资源冲突检查通过；57 网的 3,194 个保留 PIP 通过审计，没有整网或全局回退。同引脚同 RR 转换方向的 `ng_coopt_3193/F → slice$47812/CE` 从 2.013 ns 恢复到 0.216 ns，也有其他弧退化；不能把独立弧差值相加作为整路径收益。新的最坏路径布线 9.130 ns、单元含 C2Q 5.515 ns。

完整增量尝试 517.422 秒；另用 207.937 秒按 **67.9 MHz** 重放，setup **+0.021 ns**、hold **+0.134 ns**、零违例，其他检查与编码导出通过。审核时从本次开始计时的上界为 1,141.077 秒，含中间等待，低于 2,400 秒总限制。`backend_qualified_checkpoint.json` 是合格后端对照，**不替换主性能结果**：它没有提高已验收频率，且多出 23 LUT / 23 MUX。

来源：`results/vexriscv_shared_wide_summary_001/summary.json`、`wire_comparison.json`、`backend_audit.json`。隔离校准文件中的 `production_enabled=false` 保留当时的历史范围；当前能力以新后端代码及实际资格检查点为依据，不篡改旧校准声明。

## LUT6 的实际共址补充验收

`results/vexriscv_wide_neighbors_001` 从原主检查点出发，将 `ng_coopt_1612` 从零基 [69,96,3] 移到 `ng_coopt_1106` 的 LUT6 CFU [66,98,4]。网表逐字节相同，资源数量、全部 FF/硬资源/I/O 位置与完整时钟树不变。这次实际占用 LUT6 空槽 4，不把上一项空槽未使用的宏当作共址证明。

只重布 4 网，其他 6,545 网复用；6,549 网、50,287 PIP 的连通及冲突检查通过，路由 35.219 秒。90 MHz 报告 Fmax 67.972 MHz、WNS −3.601 ns、276 个 setup 违例，TNS 为 −280.325 ns，相对原主结果 −280.459 ns 仅有很小改善。

完整增量尝试 439.641 秒；67.9 MHz 重放 204.656 秒，setup **+0.016 ns**、hold **+0.134 ns**、零违例，其他检查与编码导出通过。资格 SDC 与原主结果逐字节相同。它同样保留为后端合格对照，未替换主结果；不能把模式可用或 TNS 的微小变化称为巨大性能提升。

合格对照为 `results/vexriscv_wide_neighbors_001/backend_qualified_checkpoint.json`。最终覆盖清单与限时审计在 `results/vexriscv_shared_wide_summary_001/lut6_backend_audit.json`、`final_audit.json`。当前完整路由证据覆盖实际有普通逻辑的 LUT5 宏及 LUT6 槽 4；LUT6 其他空槽有隔离绑定/配置证据，尚无逐槽整设计资格案例。

下一步应把新宽宏的所有输入、状态前沿复制和空槽打包纳入同一个候选，并实测新增 D/CE/输出负载及竞争路径。现有共享接口解决了容量缺口，几何排序尚不能预测这些联合时序效果。90/150/200 MHz 与通用优势仍未完成。

## 检查、容量与复现

共享及相关后端 23 项检查、既有宽宏/移动/FF 兼容性 30 项检查通过；完整 CLS 属性补充复验 3 项通过，属于前述测试的子集，不重复计数。已有宽宏邻居打包另有 3 项检查通过，覆盖真实校准容量、已满槽位和不移动 FF。

四份已结束的失败候选导出经无损压缩和 HTML 归档，释放约 1.8 GB。40 个原 replay 来源哈希重新核验通过，主结果和冻结 ZIP 不变。清单为 `cleanup/terminal_structural_exports_20261106_001.json`；40 GB 仍为唯一容量上限。

```powershell
runtime_bundle/python/python.exe scripts/run_ownpr_region_coopt.py --report results/vexriscv_state_frontier_002/qualified_replay_checkpoint.json --transform multilevel --timing-feedback --protect-slack 0.5 --target-aware-protection --share-wide-luts --target-mhz 90 --cut-count 8 --cut-width 7 --min-cut-cells 2 --cut-window 4 --cut-growth 48 --gain-target-ns 4 --legalize-macros --seconds 2400 --out results/<新的共享 cut 目录>

runtime_bundle/python/python.exe scripts/run_ownpr_region_coopt.py --report results/vexriscv_state_frontier_002/qualified_replay_checkpoint.json --transform wide_neighbors --timing-feedback --wide-neighbor-level 6 --wide-neighbor-count 1 --target-mhz 90 --seconds 2400 --out results/<新的 LUT6 邻接打包目录>
```

仍未完成：动态加载时序驱动的共享槽位选择、共享 FF/ALU 模式、更多宏槽位/器件类型、冷 RTL 全局共享打包和更广的硬资源/多时钟支持。90/150/200 MHz 与通用原厂优势仍需继续验证。
