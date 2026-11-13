# Mode-2 进位链整链平移准入、漏洞防范与高收益协同优化评审

## 一、`0x14444f0` 反汇编机制与在线临时变异准入边界

- **【观察】顶层分派仅依赖器件库族索引、`slot` 奇偶与 `SEL0` 关联，未直接读取 `(r, c)`**：`binary_audit/carry_cell_translation_001/alu_dispatch.asm` 显示，`0x14444f0` 从 `[rcx+0x10]+2` 读取时序库族索引定位 ALU 表 `rbp`，校验 `[cell+0x94]==0x6a` 后按引脚 ID 分流：
  1. `SUM(0x4d8)`：`CIN(0x4da)` 仅检测 `phy=[cell+0x48]` 的 `byte [phy+0x16] & 1`（`slot` 奇偶位，未读 `+0x12/+0x14` 行列），选第 `13/14` 行入 `0x1444780` 纯查表；`I0/I1/I3(2/3/5)` 尾调 `0x146e030`。
  2. `COUT(0x4d9)`：`CIN(0x4da)` 调 `0x46f040` 取 `"SEL0"` wire ID，经 `0x2c6e50(in_pin)` 收集关联节点并扫描 `[node+4]==r15w`，命中取第 `10` 行、否则取第 `11` 行入 `0x1444780`；`I0/I1/I3` 调 `0x146d4c0`。
- **【观察】绑定态存在双向指针与子对象缓存**：`binary_audit/ownpr_native/alu/308610.asm` 与 `bind_tail.asm`（`Alu::setPin2Node`）不仅写 `[pin+0x18]=node`，还写 `[Alu+0xf8]=phy_sub`、`[node+8]=pin`，并调 `0x2ffd80(Alu, phy_sub)` 注册辅助节点；`native_extended_timing.py:57` 直接向 `0x14444f0` 传入已绑定 `Pin` 指针。
- **【推断】在线临时改 `phy` 坐标与 `Pin.node` 存在双向失真风险**：由于 `0x146e030`、`0x146d4c0`、`0x2c6e50` 内部未包含在当前反汇编切片中，若其读取未同步更新的 `[Alu+0xf8]`（仍指源 CFU）或 `0x2ffd80` 缓存，会把非法目标误判为等价（假阳性）；若 `0x2c6e50` 依赖目标 tile 的 `[node+8]` 反向指针或 CFU 模式态，而目标同 slot 在已导入实例中已被占用或未初始化，则会误拒或污染后续查询。
- **可靠准入协议（Fail-Closed）**：
  1. **【推断】**先补齐 `0x146e030/0x146d4c0/0x2c6e50` 只读反汇编，确认数据弧是否仅依赖 `(in_id, slot, profile, rbp)` 以及 `SEL0` 发现路径。
  2. 若做在线快速筛选，仅限在**空闲** `tile_type==17` 目标位点执行，快照并在 `finally` 字节级恢复 `phy` 头与全部 6 个 `Pin.node`，且要求 slow(`speed=5`)/fast(`speed=0`) 下全部 7 条 mode-2 单元弧（`I0/I1/I3->SUM`、`I0/I1/I3->COUT`、`CIN->SUM/COUT`）四元浮点向量与 `ownpr_extended_timing_model` 严格一致。
  3. **【观察】**`0x14444f0` 不覆盖专用进位线与配置位，只能作必要非充分过滤；准入终审必须经 `native_import_ownpr.py` 完整导入，逐条核验 `within_cfu`/`east_cfu_bridge` 的 `timing_edge_present==True` 且延迟为 `0/0 ns`，并复核全设计 2798 个端点 setup/hold 与帧位投影。

## 二、必须防范的所有权、静态链与时序图漏洞

1. **所有权与配置编码盲区**：
   - **【观察】零负载 `SUM` 与跳线输入漏拆**：比较器链中间位 `SUM(F0..F5)` 常无扇出，`ownpr_route_design.py:132-133` 跳过空 `sinks` 网，使 `ownpr_incremental_routes.py:48-55` 的 `endpoints` 不含目标 CFU 的空载 `F`、`CIN/COUT`、`SEL0` 及 `arithmetic_skip`（`I2` 等常值折叠）`ABCD` 节点；但 `setPin2Node` 会无条件覆盖这些节点的 `[node+8]`。平移前必须把目标 CFU 全部原语占用节点（`F/ABCD/CIN/COUT/SEL`）并入拆线与禁入集合。
   - **【观察】残尾 CFU 混排 `LUT[6/7]` 与差分表越界**：`ownpr_alu_architecture.py` 与 `ownpr_alu_configuration.py` 仅标定了 `mode=2` 既有 `(slot, input_profile)` 差分表及 `CIN0` 起始模数（`i%6==0`），平移严禁改写端口常值形态；同时 `ownpr_carry_lut_sharing.py:18,27` 仅在满 6-ALU CFU 标定了 `LUT[6/7]` 共享，若链尾部分占用（`0..k, k<5`）的 CFU 混入 `LUT[6/7]`，`ownpr_configuration.py:78-84` 会置 `shared=True` 并对全部 4 个 CLS 组写 `independent_ff_groups([])`，在空 ALU 对上生成未标定位。应禁止非满 ALU CFU 混入 `LUT[6/7]`，并补齐 `ownpr_carry_macros.validate:52-58` 对 `mux_clusters` 与 `hard_clusters` 的碰撞检查。
   - **【观察】`P2R` 零所有权契约**：`direct.asm`(`0x2f0050`) 查 `p2r+0x10` 红黑树，跳过厂商 P&R 时恒为 0 条目，跨列桥亦不进 `P2R`，但原生 STA 仍生成 `0 ns` 进位边；严禁伪造 `P2R` 所有权。
2. **静态链连续性漏洞**：
   - **【观察】**`ownpr_lut_graph_feedback.py:34,64` 当前锁死 `arithmetic_clusters` 与 `static`。放开平移时，除保持 `slot 0` 起步、同 `(slot, input_profile)` 及每列 `grid[r'][c'+q]==17` 外，必须实证每个跨列 `COUT5(r',c'+q-1)->CIN0(r',c'+q)` 在目标列对间真实存在原生 RRG/STA 零线（**【推断】**跨 BRAM/DSP/时钟脊柱列或特定列界可能断桥），且维持 `COUT` 零普通织物扇出。
3. **时序图增量重建漏洞**：
   - **【观察】断桥被旧零延迟掩盖**：`ownpr_lut_graph_feedback.py:101-114` 的 `locations` 不含 `CIN`，`wire()` 落入 `else` 直接沿用旧 `0 ns` 边，且单元弧仅重算 `LUT1..4` 而原样拷贝 ALU 弧。放开平移后必须显式校验静态进位边存在性。
   - **【观察】伴随 LUT 槽组跳变与非单调双角守卫**：普通 LUT 在 `slot 0..5` 与 `6..7` 间移动会改变 `slot_group`（`LUT4 I3` 慢角由 `0.260 ns` 升至 `0.2875 ns`），必须留在 `clusters` 中重算单元弧与常值引脚裁剪（`L76-77`）；同时 mode-2 数据弧具 4 极性非单调传播（`ownpr_extended_timing_model.py:92-97`），缩短进位链入出线会压低最小到达时间，威胁 VexRiscv `+0.133 ns`、UART `+0.037 ns`、int2float `+0.002 ns` 的紧 hold 余量，评估器必须同步守卫全端点 setup 与 fast-corner hold。

## 三、面向 VexRiscv 110 MHz 的高收益单次实测路线

- **【观察】瓶颈诊断**：`docs/STRUCTURAL_GUARDS_AND_REFERENCE_MODELS_20261111.md` 与 `final_guard_audit.json` 表明，VexRiscv 在 110 MHz 下差 `-2.195 ns`（TNS `-350.689 ns`，370 个违例端点），且 `ownpr_joint_lut_search.py` 单点局部枚举的 `+0.251 ns` 单元收益被 `+0.194 ns` 布线回弹抵消；关键锥横跨“源 FF -> 前置操作数 LUT -> mode-2 进位链 -> 比较/分支或 `MUX2_LUT5/6` 汇合 -> 10 块 BRAM 地址/PC FF”（单段 BRAM 地址线高达 `8.835 ns`）。仅靠在线有限探测加几何距离平移，极易再次被扇出回弹吞噬。
- **推荐路线：全芯片离线进位走廊图谱 + 到达时间驱动的“进位链-馈入/汇合锥-宽 MUX”走廊联迁 + 裕量预算子树重布**：
  1. **离线一次性构建 `carry_corridor_atlas`（替代运行期指针变异）**：**【推断】**在独立只读探针中一次性扫遍 GW5AST-138 所有 `tile_type==17` 位点与相邻列对，固化 `(r,c,slot,profile)` 的 `0x14444f0` 慢/快角等价类、`east_cfu_bridge` 连通矩阵及帧位模板（SHA 锁定）。P&R 搜索期纯查表零内存篡改，彻底消除残态风险并严守 1 小时预算。
  2. **RRG 离散延迟表与全图到达时间驱动的跨原语走廊联迁**：超越加权几何与单 LUT 贪心，基于 `native_rrg` 与 `NativeWireTiming` 预计算 `CLS 输出 -> ABCD / BRAM 地址 / FF D` 最短路径延迟表；针对 PC/分支加法器、`slt/sltu` 比较链与访存地址加法器，以 `FixedBoundaryLutFeedback` 最小化**整锥到达时间与 370 端点 TNS** 为目标，沿合法 `tile_type==17` 走廊批量联合重定位不冲突宏锥：`mode-2 整链 + 满链 CFU 内置 LUT[6/7] + 邻近 slot 0..5 快引脚(I3 0.260ns)馈入/汇合 LUT + 已标定 MUX2_LUT5/6(0ns 级联)`，直接贴近目标 BRAM 地址口与关键汇聚 FF。
  3. **端点裕量预算 Steiner 重布与高扇出操作数选择性复制**：**【推断】**进位链 32 位输入常共享控制/移位/操作数高扇出树，整链搬迁若拉长旁路分支会触发回弹。联迁时应对跨链共享高扇出源 LUT 做选择性复制解耦，并在 `mutable_fallback` 协商重布中按汇点 slack 分配 Steiner 拓扑预算（兼顾 fast-corner hold 下界），最终以完整原生导入的全 I/O STA 复核增益并同步守住 UART/int2float/TMDS 95% 回归红线，实现单次实测大幅削减 `-2.195 ns` 关键路径赤字。
