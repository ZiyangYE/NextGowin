# GW5A 硬资源与负载分散优化

已将 DSP 映射、read-tags 小存储恢复、直通 LUT 消除、局部专用 mux 打包和寄存器负载分散接入默认 CPU 流程。原始 RTL、板级 SDC/CST 保持不变，不使用原厂综合器重新映射用户逻辑。

两轮完整 FPGAFly 均已生成新位流；**80 MHz 时序目标仍未达成**。比较链软化减少资源和耗时，但整板 Fmax 退化，因此 `soft_comparisons` 保持默认关闭，不作为时序提升推广。

| 原生实现结果 | 四项基本优化（001） | 再加比较链软化（002） |
| --- | ---: | ---: |
| P&R 外部计时 | 3461.30 s（57:41） | 2248.27 s（37:28） |
| 有效阶段合计 | 3841.72 s（64:02） | 2665.98 s（44:26） |
| ui_clk 报告 Fmax | 64.317 MHz | 60.691 MHz |
| 最差 setup slack | −3.048 ns | −3.977 ns |
| setup / hold 违例端点 | 5126 / 0 | 5086 / 0 |
| LUT / ALU | 37344 / 18948 | 37581 / 9281 |
| 物理 Logic / CLS | 56340 / 48248 | 46910 / 44888 |
| BSRAM / RAM16 | 268 / 8 | 268 / 8 |
| DSP 物理单位 / 原语实例 | 137 / 139 | 137 / 139 |
| LW | 5 / 8 | 5 / 8 |

数据来自各一轮实现，不能据此分离所有命名和布局差异的影响。[002 原生报告](../results/fpgafly_cpu_opt_002/06_pnr/mapped.rpt.txt)与[自动验收](../results/fpgafly_cpu_opt_002/acceptance.json)均已保存：文件、约束、五个时钟和资源预算核验通过，总耗时低于一小时，时序验收失败。002 的最差路径从 tile 5 的 `selected_groups[21].q` 到 `saved_u[1].q` 的同步 RESET，来自[实际 VG](../results/fpgafly_cpu_opt_002/05_netlist/mapped.vg)和[时序报告](../results/fpgafly_cpu_opt_002/06_pnr/mapped.tr)。比较链软化没有解决这一控制路径瓶颈。默认基本配置为 `configs/fpgafly_rtl_flow.json`；`configs/fpgafly_rtl_softcomp_flow.json` 作为面积/耗时对照保留。

完整 FPGAFly 的首轮优化已生成新位流，见 [fpgafly_cpu_opt_001](../results/fpgafly_cpu_opt_001/manifest.json) 和[验收清单](../results/fpgafly_cpu_opt_001/acceptance.json)。原生 P&R 外部计时 3461.30 秒（57 分 41 秒），完整阶段合计 3841.72 秒（64 分 2 秒）；P&R 单独低于一小时，但端到端仍超过一小时。ui_clk 仅达到 64.317 MHz，5126 个 setup 违例、0 个 hold 违例，**没有满足 80 MHz 目标**。每次 P&R 保持 3600 秒上限，超时判定失败。

实际物理资源为 LUT 37344、ALU 18948、寄存器 47862、BSRAM 268、分布式 RAM 8、DSP 137 个物理计量单位（139 个原语实例），LW 为 5/8。[原生报告](../results/fpgafly_cpu_opt_001/06_pnr/mapped.rpt.txt)记录主要布线阶段耗时 43 分 47 秒；此前仅依据进度百分比推测的阶段名称以此最终报告为准。5 个原始时钟及 SDC/CST 均已核对，P&R 前后输入网表哈希相同。LW 占用没有耗尽；不能据此倒推旧超时网表的原因。没有烧录开发板。

[自动验收](../scripts/assess_optimized_flow.py)会重新检查实际文件和原始约束哈希、全部规定时钟、物理资源、时序及阶段合计耗时。[首轮自动核验结果](../results/fpgafly_cpu_opt_001/acceptance_automated.json)的完整性检查全部通过，但时序和总耗时要求失败。总耗时是有效阶段时长之和；恢复运行时包含保留的上游阶段，不包含尝试间空闲或已放弃尝试。原生报告还保留了 board_clk 使用普通布线的 PR1014 提示；没有隐去警告。

验收脚本在任何要求未满足时保留 JSON 结果并返回退出码 1；主流程本身的 `complete` 只表示各阶段已经执行，不替代这个验收判断。

首轮最差路径不是 DSP 乘法：从 `control.score_page[28].q` 到 `control.response[263].q` 的回读控制/选择网络，原生报告为 14 级、slack −3.048 ns；数据延迟中逻辑为 4.074 ns，连线为 11.122 ns（71.570%）。另一组严重路径从 tile 5 的 `selected_groups[20].q` 到多个同步 RESET，slack −3.044 ns。这里的 RTL 名称由最终原生 VG 的实例 Q/D 连接核对，见[时序报告](../results/fpgafly_cpu_opt_001/06_pnr/mapped.tr)和[实际网表](../results/fpgafly_cpu_opt_001/05_netlist/mapped.vg)。因此 DSP 的模块级改善不能保证整板时序收敛；局部选择网络、控制路径和物理跨度仍是剩余问题。

## 已接入的变换

| 变换 | 入口 | 验证与限制 |
| --- | --- | --- |
| GW5A DSP | [gw5a_dsp.py](../scripts/gw5a_dsp.py) | 严格核对 Verific 乘法辅助模块的声明和完整算术表达；支持本工程 8 种有符号/无符号位宽。选择 MULT12X12、MULTALU27X18 或 MULT27X36，并正确扩展符号/零位、截取结果。未知表达拒绝改写。 |
| 小存储恢复 | [recover_small_memory.py](../scripts/recover_small_memory.py) | 恢复本工程被前端逐位展开的 read-tags 阵列。只有与实际导出模块的顺序等价全部证明后才采用恢复结果；新增 GAO 端口或接口变化会拒绝匹配。证明用内部状态别名在实现前删除，避免产生大量额外存储读端口。 |
| 直通 LUT | [optimize_native_json.py](../scripts/optimize_native_json.py) | 将 mapper 生成的 `LUT1 INIT=2` 转为电气连接别名；检查别名循环和初值冲突。不是删除所有用户插入的缓冲。 |
| 专用 mux | 同上 | 穷举 LUT3 真值表识别 2:1 mux；只有两个输入来自独占、非 keep、非模块端口的 LUT 叶节点时，才替换为 MUX2_LUT5。当前局部打包支持到这一层，不声称重建所有 LUT6/7/8 结构。 |
| 寄存器分支 | 同上 | 对超过两倍 64 负载的合格 FF 按消费单元分组。副本复制全部非 Q 连接、参数和有效初值，只改写消费端输入；不复制用作时钟/复位源、初值未知或直接连接模块端口的输出。64 是实验目标，不是全器件硬限制。 |

DSP 保留外部流水寄存器，不吸收 CE、reset 或额外流水级。所有 DSP 寄存器时钟参数显式设置为 `BYPASS`，占位 CLK 连接到已有的本域 `clk`，避免原生工具把接地占位端口当作未声明时钟。这个连接仅在所有 DSP 寄存器旁路时采用，不能套用到已注册的 DSP。

## 本工程的结构变化

完整网表的 [打包前资源检查](../results/fpgafly_cpu_opt_001/05_netlist/resource_preflight.json) 当前为：

- DSP：8 个 MULT12X12、129 个 MULTALU27X18、2 个 MULT27X36，共 139 个原语；
- BSRAM：260 个 SDPX9B、8 个 SDPB；
- 新恢复的分布式 RAM：8 个 RAM16SDP4；
- [后处理记录](../results/fpgafly_cpu_opt_001/05_netlist/optimizations.json)：消除 4330 个直通 LUT，打包 257 个专用 mux，增加 208 个 FF 副本；
- LUT＋ALU 估算为 55954，优化前 CPU 流程为 96002。这是结构计数，不能替代最终物理资源报告。

与原厂采用相同 DDR 黑盒边界的[比较结果](../results/optimization_comparison_001/comparison.json)：

| 指标 | 可布通原厂 | 优化前 CPU | 本轮 CPU |
| --- | ---: | ---: | ---: |
| LUT | 35964 | 65695 | 34987 |
| ALU | 5054 | 27157 | 17817 |
| DSP 原语 | 139 | 0 | 139 |
| 专用宽 mux | 3484 | 0 | 257 |
| DFF 原语 | 42638 | 42326 | 42396 |
| 扇出 ≥512 的非时钟网络 | 2 | 13 | 4 |
| 扇出 ≥1024 的非时钟网络 | 0 | 3 | 1 |

`core_selected` 的原 701 个负载被分到原输出和 10 个副本；6 个 `pixel_pulses` 输出各从 567 个负载拆为原输出和 8 个副本。原始 `core_selected_Z LOCAL_CLOCK` 约束保留，原输出分支的负载已降低。原有 reset 网络如 warm_clear 和 DDR reset 没有按这个规则复制，仍交给原生专用资源分配。

本轮仍有两个约千负载的组合 LUT 输出；ALU 数量也高于原厂，因此不据此宣称已消除所有拥塞或达到最优映射。

## 验证证据

- [DSP 原厂模型回归](../results/dsp_validation_001/manifest.json)：8 种几何，每种 4096 组输入，包含正负边界值；
- [LIF 常数乘法全取值检查](../results/lif_dsp_all_values_001/manifest.json)：对实际使用的 1307、2588、−5191 三个系数枚举全部输入位模式，共 1,048,576 组，逐项比较原 Verific 辅助模块与固定版本的原厂 DSP 功能模型；这项检查覆盖组合乘法，不包含流水状态；
- [read-tags 顺序证明](../results/fpgafly_cpu_opt_001/03_resources/memory_0/proof.log)：152 个等价点全部通过；
- [完整 LIF 原 RTL 对比](../results/lif_optimized_validation_002/manifest.json)：实际新综合网表在原厂模型上运行 4096 周期，覆盖 backpressure、reset、有符号边界和有效输出数据；
- [专用 mux 原生实现](../results/native_mux_validation_001/pnr/manifest.json)：读取、P&R 和报告生成成功；
- [显式 DSP 配置的 LIF 原生实现](../results/lif_dsp_clock_validation_001/pnr/manifest.json)：无未知选项、端口或额外接地时钟提示；
- [GAO 示例](../results/cpu_gao_optimized_001/manifest.json)：新流程生成位流并通过完整原生时序审计；[用户数据通路](../results/cpu_gao_optimized_sim_001/manifest.json)通过初值和 2000 周期回归，GAO 内核仍是已固定的原厂宏；
- [77 项自动测试](../results/optimization_validation_003.log)通过，包括 FF 副本的初值/CE/reset、mux 真值、未使用 DSP 输出补全和旁路时钟限制。

后续比较链与 JSON 读取回归加入后，[80 项测试](../results/optimization_validation_005.log)全部通过。

这些证据支持具体变换和已测模块；完整 RTL 顺序等价、开发板运行和 GAO 实机采样仍不在证明范围内。

另做了[ALU 模式特化实验](../results/lif_alu_mode_trial_001/pnr/manifest.json)：仅将固定控制、合法初始进位的完整加减链从模式 2 改为模式 0/1。177 个 ALU 改写后通过 4096 周期原 RTL 对比，但小模块 P&R 仍为 88.734 MHz，与未特化版本相同，因此没有把这项实验加入默认流程，也没有据此中断完整工程的布线。

比较链另有独立开关 `soft_comparisons`，默认关闭：仅将整条链的 SUM 均未被使用、导出或 keep 的模式 0/1/2 ALU 改为等价布尔表达，再送入 ACD。完整候选在导出前减少 8828 个 ALU；[LIF 对照](../results/lif_carry_only_trial_001/pnr/mapped.tr)减少 80 个 ALU，4096 周期[原 RTL 仿真](../results/lif_carry_only_trial_001/rtl_sim.json)通过，相同 20 ns 约束下 Fmax 从 88.734 提高到 112.925 MHz。完整 RTL 的 [fpgafly_cpu_opt_002](../results/fpgafly_cpu_opt_002/manifest.json)物理结果反而退化，见上表；不把模块收益推广为整板收益。重读 JSON 的匿名线自动命名冲突已通过给无别名位补充稳定别名修复，未修改原始信号的连接或名称。

通过正式流程配置开关的[再次 LIF 原 RTL 检查](../results/lif_optimized_validation_003/manifest.json)及检查点核验也已通过；对应[原生 P&R](../results/lif_optimized_validation_003/pnr/mapped.tr)为 112.868 MHz，setup/hold 均为 0。前述 112.925 MHz 来自独立试验，两个结果分别保留。

## 运行与恢复

```powershell
python scripts/prepare_fpgafly_cpu_project.py
python scripts/nextgowin.py --config configs/fpgafly_rtl_flow.json --out results/fpgafly_optimized_new
python scripts/verify_lif_optimized.py --out results/lif_validation_new
python scripts/nextgowin.py --config configs/fpgafly_rtl_softcomp_flow.json --out results/fpgafly_softcomp_new
python scripts/verify_lif_optimized.py --out results/lif_softcomp_new --soft-comparisons
python scripts/assess_optimized_flow.py results/fpgafly_softcomp_new
```

`resource_optimizations` 默认为 true，可以显式关闭用于消融对照。启用 DSP 会改变资源推断阶段，必须重新综合；不能将旧的零 DSP 映射检查点当作已升级结果。已完成的新映射可在哈希、实际 LUT 读回证明、DSP 模型接口和存储恢复证明核验后，用 `--resume` 重做导出/实现。

本轮修复过原生读取器要求的空输出端口、FF 副本的 INIT 表达和 DSP 占位时钟问题，失败或主动停止的尝试均保留在运行目录中，没有用旧位流补齐结果。
