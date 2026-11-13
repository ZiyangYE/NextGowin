# NextGowin 独立架构与基线时钟咨询（#19）

**前置纠偏声明**：（1）保持方程为 `Slack_hold = Arrival_data - (tCLK_capture + tUnc + tHld)`，捕获时钟更晚只会抬高 `Required`、恶化保持，绝不会消除保持缓冲；（2）UART 单个邻核 FF 样本不构成全芯片 Fmax 上限证明；（3）当前自研 VexRiscv `90.380 MHz` 绝不作为官方基线。

---

## 1. 四设计官方 Hold 违例机理核查与异步 Removal 合规收敛

- **【观察】推翻“未传禁用参数但 138 默认未启用 IOB 寄存器”的假设**：虽然手册称 IDE 在 GW5AST-138 下默认关闭 IOB 寄存器，但裸调 `gowin.dll`（`cmd.do` 仅含 `-correct_hold 1 -route_option 0`，未传 `-ireg_not_in_iob/-oreg_not_in_iob`）时，底层 `IregNotIobFlag/OregNotIobFlag` 实为 `false`，**实际打开了输入/输出 IOB 寄存器打包**。同时，全部四设计的核内计算路径（FF→FF）保持裕量均为正，负保持 `100%` 集中在输入端口捕获与异步复位路径，但并非单一机理，而是两类物理机制叠加：
  1. **硬连线输入 IOB 寄存器零布线路径（解释 `-1.389 ns` 与 `-1.365 ns`）**：数据经 `IBUF→IOREG/D` 专用硬连线（`tNET=0.000 ns`），无法插入任何通用布线绕行，`-correct_hold 1` 与 `-co-place_io_registers 1` 对其物理无解。
  2. **过约束 Setup 下未修复的核内输入布线路径（解释 TMDS `-0.728 ns` 及 UART/Vex 其余违例）**：输入经通用布线进核内 FF（`tNET>0`），本可由路由器绕线修复（历史 `100 MHz` 官方 TMDS `136.437 MHz` 即为 `0` 违例），但在 `200 MHz` 或 `1.224*B_i` 过约束下（四份报告均呈 `Routing Phase 3: CPU time = 0s`），路由器放弃或因输入锥 Setup 已为负而拒绝增加绕线延迟。
- **【源码与报告依据】**（`target_relative_probe/*/pnr/{mapped.rpt.txt,mapped.tr}`）：
  - **UART（`175.202 MHz`）与 int2float（`169.005 MHz`）同为 `-1.389 ns` 的精确成因**：两设计 `I/O Register` 分别为 `19/900`（8 入 + 11 出）和 `17/900`（11 入 + 6 出）。两设计 `clk` 均绑 `L22=IOR85[A]`，走 `PRIMARY(PBR2/PBR3)` 到 `M20=IOR92[A]` 的快角延迟同为 `tCLK = 0.608 + 1.319 = 1.927 ns`，`Required = 1.927 + 0.050 + 0.022 = 1.999 ns`；而 UART `tx_data[6]` 与 int2float `a[7]` 恰好均绑 `M20=IOR92[A]` 并打入输入 IOB FF（`tx_data_latch_6_s0` / `r_7_s0`），`Arrival = 0.610 + 0.000 = 0.610 ns`，故 `0.610 - 1.999 = -1.389 ns` 逐皮秒一致。int2float 全部 `11` 个违例均为此类零线网输入 IOB FF（核内 FF→FF 起步 `+1.026 ns`）；UART 除 8 个输入 IOB FF 外，`rx_ibuf→rx_bits_0_s0`（`tNET=0.296 ns, -1.057 ns`）等属核内输入未绕线，核内 FF→FF 起步 `+0.130 ns`。
  - **VexRiscv（`97.656 MHz`）**：`I/O Register = 157/900`（98 入 + 59 出）。Path 1–25（`-1.365 ns`）同样是零线网输入 IOB FF（`externalInterruptArray[15]` 于 `IOR1[A]`，`Arrival=0.610 ns`；`clk` 自 `IOB114[A]` 经 `PRIMARY` 达 `1.903 ns`，`Required=1.975 ns`）；其余至 `141` 端点为核内输入路径，核内 FF→FF 起步 `+0.130 ns`。
  - **TMDS（`170.426 MHz`）**：输入全经组合逻辑或同步复位，输入 IOB FF 为 `0`（仅 10 个输出 IOB FF）。全部 `28` 个违例均为核内布线输入路径（如 `IOB140[A] resetn_ibuf → R97C158 q_out_1_s0/RESET`，`tNET=0.542 ns, -0.728 ns`），而此时 `din/de` 输入锥 Setup 已达 `-1.314 ns`。
  - **异步 Removal（UART `-1.128 ns`）合规处理**：`rst_n` 受 `0 ns` 输入延迟约束，严禁 `set_false_path` 或改 RTL 加同步器。`mapped.tr` 显示 `rst_n_ibuf(IOB179[A])` 异步 Recovery 裕量高达 **`+3.619 ns`**（`Arrival 5.844 ns` vs `Required 9.463 ns`），而最差 Removal 位于输出 IOB FF `IOR109[A] tx_reg_s0/PRESET`（`tNET=0.175 ns, -1.128 ns`）及核内 FF `state_0_s0/CLEAR`（`tNET=0.771 ns, -0.540 ns`）。合法解法唯有**物理布线增延**（快角补约 `1.2 ns` 线延，慢角仅消耗约 `2.2 ns` Recovery 裕量，Recovery 仍 `> +1.3 ns`）；若官方路由器无法对 `IOR109[A]/PRESET` 绕线，则在官方命令行加 `-oreg_not_in_iob` 让 `tx_reg_s0` 留在核内 CLS 由路由器合法绕线。
- **【推测与不确定性】**：`Phase 3 CPU=0s` 高度指向官方路由器在全局负 Setup 下跳过保持绕线，可通过单组 `125 MHz` 约束对照直接证伪或证实。

---

## 2. 官方 P&R 内存态保留与仅改周期的重解析 Routed STA

- **【观察】导出接口现状**：`executePnrTask` 返回后，`globalMem`、`Design`、`chip` 及 `node+0x18` 布线树仍在内存。`gowin.dll` 导出了 SDC/STA 清理与重跑符号，但**未导出 `globalMem::setSdcFile`**（仅有 `getSdcFile`），且 `SdcHandler::addTcstClock/addTcstIODly` 呈追加语义；若不清理直接二次调 `readSdcFile`，极易产生重复 `TC_*` 时钟或旧约束残留，而仅调 `restartTiming` 不会重读磁盘 SDC。
- **【源码与报告依据】**（`binary_audit/gowin_exports.txt`、`experiments/native_import_ownpr.py`、`scripts/project_timing.py`、`scripts/ownpr_effective_io_constraints.py`）：
  - 已确认导出符号：`globalMem::freeTiming`(`0x24e970`)、`globalMem::freeSdcHandler`(`0x24e920`)、`SdcReader::freeSdcReader`(`0x12978a0`)、`SdcHandler::resetTcst`(`0x1295ce0`)、`TmMonitor::clearTiming`(`0x12f21c0`)、`readSdcFile`(`0x1297930`)、`setTimingData`(`0x13ac740`)、`TmMonitor::readTimingData`(`0x139dbb0`)、`TmMonitor::restartTiming`(`0x13a7070`)、`generateTimingRptoutFile`(`0x6a97d0`)，以及 `globalMem::set Flow=7 / PlaceUsed=0 / RouteUsed=0`。
- **【推测与不确定性】及上线前必做三项证明**：由于 `SdcHandlerV2`(`0x2597a0`) 内部状态未完全导出，同进程“先备份原 SDC 哈希→原路径覆写新周期 SDC→清理 Timing/SDC→`readSdcFile`→`flow=7` 出报告”在未验证前不能直接采信，必须先通过三道门：（1）**同周期零误差门**：重解析原周期 `T_1`，Setup/Hold/Recovery/Removal WNS/TNS 与 `reported_fmax_mhz` 须与原 `.tr` 差 `0.000 ns`，且 `project_timing.py` 确认单一时钟无 `TC_2`；（2）**变周期不变量门**：改周期 `T_2` 后，Hold/Removal WNS/TNS 及 Setup 路径 `Data Arrival` 变化须为 `0.000 ns`，`Data Required` 精确平移 `T_2 - T_1`，且 `ownpr_effective_io_constraints.py` 校验 `tIn=tOut=0`；（3）**物理态零变更门**：调用前后全实例 `(row,col,cls,bel)`、`PIO_ATTR`/Bank `VCCIO` 与全部 `node+0x18` 父指针哈希 `0` 差异。
- **更稳妥的零脏状态替代机制**：在 `executePnrTask` 返回瞬间用已验证 getter（同 `native_primitive_binding_oracle.py`）导出官方布局与 `node+0x18` 路由树，在**全新干净进程**中由 `native_import_ownpr.py` 绑定新周期 SDC 执行纯 `flow=7` STA（显式标记 `vendor_routed_sta_audit`，先在 `T_1` 过 `0.000 ns` 一致门，再跑派生周期），彻底规避同进程解析器残留风险，且无需臆造 `.pr` 读取 ABI。

---

## 3. 建立真正公平的纯官方基线（零自研修网表、零放宽 I/O）

- **【观察】**：用 `1.224*B_i`（自研超 20% 后的目标频率）去约束原厂 P&R，等于人为把原厂置于 `+22%` 深度 Setup 违例区，诱发其放弃 Hold 修复；而未传 `-ireg_not_in_iob` 又偏离了 138 器件 IDE 默认值并制造了 `tNET=0` 死结。历史 `coopt_comparison_20261104` 中 `100 MHz` 官方 UART（`136.878 MHz`）与 TMDS（`136.437 MHz`）已证明：在合理周期与核内捕获下，纯官方流程完全能在零 I/O 放宽下实现 Hold/Removal 归零（历史 `100 MHz` VexRiscv 仍有 71 个保持违例，正是因为 `100 MHz` 对其 `75.5 MHz` 仍是过约束）。
- **【源码与报告依据】**：`scripts/gowin_probe.py` 默认即带 `-ireg_not_in_iob -oreg_not_in_iob`；`results/coopt_comparison_20261104/summary.json` 记录了 `I/O Register` 关闭输入打包时的纯官方零违例证据。
- **公平纯官方基线协议（建议）**：严禁混入任何历史自研修补网表或放宽 `0 ns` I/O。对四设计统一使用纯 `gw_sh` 综合 + 官方 P&R，显式对齐 138 默认选项 `-ireg_not_in_iob -correct_hold 1 -route_option 0`（并对称测试 `-oreg_not_in_iob` 开/关两档以兼顾输出 `tCO` 与异步复位绕线），在预声明的**Setup 可行周期网格**（如前三设计 `115/130/140 MHz`、VexRiscv `68/75/80 MHz`）下运行；按与自研完全对称的 `2%` STA 裕量与全门禁（Hold/Removal/Recovery 违例数均为 `0`）选取各设计合法官方最高 Fmax 定为 `B_i`，再据此锁定 `E_i = 1.224 * B_i` 并在固定物理态下完成审计。

---

## 4. 跳出基线争议：最优先的一项联合架构实验与失败判据

- **【观察】独立质疑当前优化重心**：即使官方基线落在 `UART ~137 / int2float ~136 / TMDS ~136.4 / VexRiscv ~76–80 MHz`，当前统一自研前沿（`197.0 / 167.5 / 112.37 / 89.27–90.38 MHz`）中真正的木桶最短板并非 VexRiscv（`~1.13–1.18x`），而是 **TMDS（`112.37 / 136.44 = 0.824x`）**！继续微调软 LUT DAG 切分无法救活 `min_i`。
- **【源码与报告依据】**：对比 `tmds/pnr/mapped.rpt.txt` 与自研统计，官方 `gw_sh` 将 TMDS 的计 1 与视差加减树映射为 **`69 LUT + 23 ALU` 硬进位链**，而自研 Yosys 流程将其全部拆成 `104` 个软 LUT（`0 ALU`），每比特进位由 `~0.035 ns` 退化为 `~0.55 ns` 软路由；同时自研在非配对 FF 的 `D` 端仍插入直通 `LUT1`（VexRiscv 多达 `427` 个），徒增一级延迟与拥塞。
- **【推测与不确定性】首要实验及硬性证伪判据**：
  - **实验内容**：在统一无分支流程中实施**“Yosys `$alu`（位宽 `>=4`）直降 Gowin `ALU` 硬进位链列宏（贯通打包+解析布局+合法化）+ CLS `DI` 直连去直通 `LUT1`”**联合改造。
  - **失败（证伪）判据**：单次全流程 `<600 s` 且四设计全过等价性、`Hold >= 0`、`Removal >= 0` 前提下，若（1）TMDS 综合/打包未生成 `>=16` 个 `ALU` 或其原生 Fmax 未从 `112.37 MHz` 跃升至 **`>=150 MHz`**，则“算术原语失配主导 TMDS 退化”假设被证伪（说明主因在 I/O 边界布局）；或（2）VexRiscv 直通 `LUT1` 未削减 `>=80%` 且原生 Fmax 未突破 **`94.0 MHz`**，则立即中止该分支并转向边界感知含 I/O 时序权重解析全局布局。
