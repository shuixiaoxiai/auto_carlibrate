# 第九步：Windows 打包和无硬件验收

状态：**进行中**

开始日期：2026-07-30

## 已完成

- PyInstaller onedir 入口、固定依赖和资源生成。
- Windows ICO、版本资源和 Inno Setup 安装脚本。
- Windows 10/11、CPython 3.9、x64 构建前置校验。
- Mock 包与包含 `zlgcan==0.3.0` 包两种构建模式。
- 打包前和打包后 `clgcan_driver.pyd` 双重检查。
- 冻结应用自动启动验收：
  - 8 个方向和 40 条 RSSI 曲线可加载；
  - 修改 What-if 后闭锁/解锁优良差汇总同步重算；
  - 汇总控件和计算结果一致；
  - Mock 手动方向记录工作区可启动；
  - ZLG 设备配置、连接和方向记录工作区可启动。
- 模拟实时源自动完成 8 方向工作流，验证一次连接跨方向复用、配置持久化、方向原始文件
  和闭锁/解锁汇总。
- 两小时 Mock CAN 循环采集、解码、What-if 重算和内存门禁脚本。
- Windows 2022、Python 3.9 x64 构建和产物上传工作流。

## 当前本机等价验收

开发机为 macOS Apple Silicon，不能在本机交叉生成 Windows PE/EXE。使用完全相同的
PyInstaller spec 生成本机 onedir，并启动冻结后的可执行文件验证：

```text
8 个方向
40 条 RSSI 曲线
闭锁汇总：优 0 / 良 0 / 差 8 / 未触发 8
解锁汇总：优 0 / 良 0 / 差 8 / 未触发 8
源代码 Qt：防抖到绘制完成 79.395 ms
冻结应用：What-if 核心及控件刷新约 18 ms
冻结应用 onedir：约 116 MB
```

当前完整自动化回归为 78 个测试。冻结应用验收额外生成 `analysis.png`、`manual.png`
和 `live-zlg.png` 三类界面证据。

3 秒快速长稳预检处理 10,720 帧、完成 30 次八方向重算，最大核心重算 82.270 ms，
Python 跟踪内存峰值 3.078 MB。

## 尚待 Windows 环境完成

- 在 Windows 10/11 64 位运行 `packaging\windows\build.ps1`。
- 在无 Python 的干净 Windows 电脑安装和启动 `Setup.exe`。
- 执行完整 7,200 秒 Mock 长稳并归档 `mock-stability.json`。
- 使用能运行 `tools/can_read_save.py` 的环境执行 `-IncludeZlgcan` 构建，验证 Windows
  onedir 中的 `clgcan_driver.pyd`。

仓库当前没有 Git remote，无法从本机触发已配置的 Windows GitHub Actions runner。
因此本步骤暂不标记完成，也不把 macOS 等价产物冒充为 Windows EXE。

## 完成标准

- [x] 打包入口、图标、版本资源、onedir、ZIP 和安装包脚本已落盘。
- [x] 打包产物验收覆盖 What-if 后 8 图及闭/解优良差汇总重算。
- [x] 两小时 Mock 长稳门禁已落盘。
- [ ] Windows 64 位构建实际通过。
- [ ] 7,200 秒 Mock 长稳实际通过。
- [ ] 干净 Windows 10/11 无 Python 环境安装、启动、回放、重算和保存通过。
- [ ] 生成可下载的 `Setup.exe` 和 `win64.zip`。
