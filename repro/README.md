# WIP：小设计完整复现

Windows x64。示例只有 6 行 RTL，4 个输入、2 个寄存器输出。目标为 GW5AST-138，时钟约束 20 MHz；覆盖全部输入、输出的 setup/hold。固定引脚只用于评测，未验证实际板卡接线。不会烧录硬件。

在本分支的仓库根目录，用 PowerShell 执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\repro\Get-Dependencies.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\repro\Run-Small.ps1
```

下载器按 `dependencies.json` 下载 24 个分块，每块不超过 **8,000,000 字节**，总压缩大小 **185,160,011 字节**。每块及合并 ZIP 均核验 SHA-256，再解压和逐文件检查。无需预装 Python、Yosys 或 ABC。解压后的依赖约 667 MB；安装时需为分块、合并 ZIP 和解压内容预留约 1.05 GB。下载成功的分块可以复用，安装成功后默认删除下载缓存。保留缓存可加 `-KeepDownloads`。

运行包括 RTL 资源推断、NextGowin 技术映射、等价检查、自研打包/布局/布线、必要的 hold 修复、高云最终 STA 与位流编码。总运行预算 900 秒，超时或任何完整 I/O 时序违例均视为失败。输出位于 `repro_runs/small_<时间>/`；顶层 `manifest.json` 记录最终指标和采用的检查点。

`results/native_small_repro_io_002` 是明确标注的**原厂 I/O 校准参考**。运行时不会调用原厂 P&R；高云 DLL 用于最终 STA 和编码。启动脚本设置独立运行时和证据路径重定位，并阻止 Python 读取原开发工作区。依赖使用固定版本的原生接口，不能随意替换 SDK DLL。

源码在 Git 中，运行时和校准数据在对应 WIP Release 中。原项目 Apache-2.0 许可不改变随包第三方工具、Python 包或高云组件的原有许可；保留的第三方许可文本位于各运行时目录。包中不含许可证密钥或 `gwlicense.ini`。此包为 Windows x64 实验快照，不承诺其他系统或 SDK 版本兼容。

该示例验证小设计的冷 RTL 闭环，**不是 74 MHz VexRiscv 数据集**，也不证明任意设计已可自动收敛或普遍优于原厂。没有加入大型 CPU 实验网表、完整历史报告或旧交接 ZIP。高频和其他资源模式请以各自完整时序检查为准，不能仅用报告中的 Fmax 推断。
