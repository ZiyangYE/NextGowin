# 支持范围与限制

- **系统和处理器**　Windows 10/11 x64，处理器需支持 SSE2 和 SSE3。发行包采用统一指令集基线。
- **本机依赖**　需要单独安装 Gowin V1.9.12.04 x64，供前端展开、器件数据库、布线后时序分析和位流编码使用。其他版本的数据库或接口差异可能导致检查失败。
- **目标器件**　目标为 GW5AST-138C 和 GW5AST-138B。具体工程须使用器件数据库支持的封装与速度等级，例如 FCPBGA676A、FCPBGA484A。138C 可使用分布式 SSRAM；138B 的 SSRAM 容量为 0，使用 `RAM16*` 或 `ROM16` 将被拒绝。涉及的资源还包括 BSRAM、DSP、ALU、锁存器、PLL、DDRDLL、DQS、IOLOGIC、时钟资源、JTAG、SerDes 和 AE350_SOC。
- **工程输入**　支持 Verilog-2001、SystemVerilog-2017、VHDL、CST 物理约束、SDC 时序约束、GAO 的 `.rao` 和 SerDes 的 `.csr`。Synplify Pro 工程和拖放模式下的 `Disable_Insert_Pad=true` 不受支持。
- **结果比较**　Gowin 在 `.vo`、`.tr`、`.rpt.txt` 和 `.fs` 注释中写入生成时间。比较两次运行时，应将时间戳与位流配置帧、CRC 和时序值分别核对。
- **硬件下载**　程序离线生成并校验 `.fs`。上板需要另行使用 Gowin Programmer。

[英文备用版](LIMITATIONS.en.md)
