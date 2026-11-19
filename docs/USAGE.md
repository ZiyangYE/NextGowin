# 使用指南

## 配置与运行

在 `bin/gowin-path.txt` 中填写 Gowin 安装根目录或其 `IDE` 子目录。文件使用 UTF-8，除空行和以 `#` 开头的注释外，只能有一行路径。支持引号、空格和中文，相对路径以 `bin` 为起点。发行包针对 Gowin V1.9.12.04 x64。检测到版本或 SDK 文件不匹配时，会弹出版本提示。选择 Cancel 将以退出码 8 停止，选择 Continue 将继续执行检查，并把提示记入日志和清单。

在解压目录运行以下命令。也可以将 `.gprj` 拖到启动程序上，观察六个阶段的进度，结束时按回车关闭窗口。

```powershell
.\bin\nextgowin.exe --cpu-info
.\bin\nextgowin.exe --self-test --no-pause
.\bin\nextgowin.exe D:\FPGA\blinky_gw5ast138\blinky.gprj --no-sync-ide --no-pause
.\bin\nextgowin.exe .\project.gprj --out .\build_out --gowin-dir "C:\Gowin\Gowin_V1.9.12.04_x64" --no-pause
.\bin\nextgowin.exe --config config.json --out .\flow_out --no-pause
```

工程中的相对路径以 `.gprj` 所在目录为起点。启动程序读取已启用的源文件、物理和时序约束、顶层模块、语言标准、包含目录，以及可选的 `impl/<工程名>_process_config.json`。

## 六个阶段

| 目录 | 工作内容 |
| --- | --- |
| `01_frontend` | 本机 Gowin 前端执行 `dev` 和 `extract`，展开 HDL、参数、生成块和受保护 IP。 |
| `02_bridge` | NextGowin 转换网表、恢复行为级存储器、保留约束引用的层次名称，并读取原语参数。 |
| `03_resources` | NextGowin 配合 Yosys 推断 DSP、块存储器、分布式存储器和 ALU 进位链。 |
| `04_mapping` | NextGowin 配合 Yosys 与 ABC 重写逻辑，映射 LUT 与宽多路选择器，调整 LUT 输入顺序，并执行组合等价检查。 |
| `05_netlist` | 插入顶层 I/O，保留时钟网络别名，复制高扇出逻辑或触发器，生成结构网表 `mapped.vg`。 |
| `06_pnr` | NextGowin 完成布局、布线与关键路径优化。本机 Gowin 库提供器件及约束数据、布线后静态时序分析、报告和位流编码。NextGowin 检查布线树，并按实际压缩和 CRC 模式校验位流。 |

## 查找结果

默认结果目录是工程旁的 `<工程名>_nextgowin`。其中的 `.fs` 是位流，`.tr` 与时序 HTML 可查看建立、保持等检查，`.rpt.txt` 和 `.rpt.html` 给出资源及布线统计，`.pin.html` 给出引脚分配，`.vo` 是布线后网表。`nextgowin.log` 记录诊断，`manifest.json` 记录各阶段指标、校验结果和同步路径。

默认同步会覆盖 `impl/gwsynthesis` 与 `impl/pnr` 下的同名生成文件，命名遵循 `OUTPUT_BASE_NAME`，已有的 `impl/pnr/device.cfg` 设置会保留。希望单独保存本次结果时使用 `--no-sync-ide`。批处理使用 `--no-pause` 关闭结束等待。

[完整英文备用版](USAGE.en.md)

## 独立检查位流

在解压目录执行 `bin\nextgowin.exe --verify-fs 工程.fs` 可检查帧序列和结束标记。工程显式指定的压缩和 CRC 选项优先于已有 device.cfg 中的同名设置。
