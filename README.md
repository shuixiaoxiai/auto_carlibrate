# BLE Calibration

汽车数字钥匙 BLE 标定 Windows 应用。当前已完成核心数据闭环、项目存储、离线回放、
八方向桌面界面和 ZLG 实时采集入口。开发与自动化验收优先使用 Mock/模拟实时源，
Windows 实机通过同一界面连接 ZLG CAN 设备。

## 本地运行

仓库内直接运行：

```bash
python run_app.py info
python run_app.py generate-mock --output mock_data/eight_directions.jsonl
python run_app.py capture-mock \
  --input mock_data/eight_directions.jsonl \
  --speed 10 \
  --output mock_data/captured.jsonl
python run_app.py session-demo \
  --input mock_data/eight_directions.jsonl \
  --manifest mock_data/eight_directions.manifest.json \
  --json
python run_app.py cloud-decode "00C2C7..."
python run_app.py cloud-encode "00C2C7..." \
  --unlock -63 -58 -55 -67 -64 \
  --set quickLock.weakFront=2
python run_app.py project-demo \
  --input mock_data/eight_directions.jsonl \
  --manifest mock_data/eight_directions.manifest.json \
  --database data/projects.sqlite3
python run_app.py gui
python run_app.py gui \
  --manual-mock \
  --project-name "八方向手动标定"
python run_app.py gui \
  --live-zlg \
  --project-name "实车八方向标定"
```

以标准包方式运行：

```bash
python -m pip install -e .
ble-calibration info
ble-calibration generate-mock --output mock_data/eight_directions.jsonl
ble-calibration capture-mock --input mock_data/eight_directions.jsonl --speed 10
```

运行自动化测试：

```bash
python -m unittest discover -s tests -v
```

运行 Qt 端到端 UI 冒烟测试（支持离屏模式）：

```bash
python tools/ui_smoke.py --max-refresh-ms 200
python tools/manual_ui_smoke.py --width 1100 --height 720
python tools/live_ui_smoke.py --width 1100 --height 720
```

`--live-zlg` 工作区在顶部提供设备类型、索引、通道、仲裁域/数据域波特率、终端电阻和
library 路径配置。设备连接后保持在线，测试人员逐方向点击开始/结束；每个方向单独录制
BLF，方向切换不会重复打开 CAN 设备。设备配置默认保存在用户数据目录的
`settings.json`。

## Windows 打包

在 Windows 10/11 64 位、Python 3.9 64 位环境执行可复现的无硬件构建：

```powershell
packaging\windows\build.ps1
```

在已经验证 `tools/can_read_save.py` 可运行、且安装了 `zlgcan==0.3.0` 的环境生成包含
ZLG 原生驱动、可连接实车的候选包：

```powershell
packaging\windows\build.ps1 `
  -PythonExecutable "$env:CONDA_PREFIX\python.exe" `
  -IncludeZlgcan
```

完整依赖、产物和两小时 Mock 长稳命令见
[`packaging/windows/README.md`](packaging/windows/README.md)。

## Windows CAN 依赖

现有实车脚本验证过以下版本：

- `python-can==4.6.1`
- `zlgcan==0.3.0`
- `zlgcan` 包自带的 `clgcan_driver.pyd`

精确依赖记录在 `requirements/windows-can.txt`。实车 EXE 直接使用已经能够运行
`tools/can_read_save.py` 的同一个 64 位 Python 环境构建；脚本会自动检查依赖和原生
`.pyd`，不需要测试人员另行填写 ZLG 型号或驱动版本。

## 目录

```text
src/ble_calibration/
  analysis/     距离、优良差及 8 方向 What-if 重算
  app/          应用入口和后续 UI 组合
  can/          共用 CAN 协议及后续数据源
  capture/      独立采集线程和生命周期
  cloud/        云推 HEX 无损解码、修改和编码
  config/       配置加载、验证和保存
  diagnostics/  日志和诊断
  domain/       时间、节点、方向、事件、项目等核心模型
  mock/         确定性八方向 Mock CAN
  processing/   五节点时间对齐、stale 和请求边沿
  replay/       JSONL/BLF 离线回放与方向数据重建
  session/      方向选择、记录、距离和完成状态机
  storage/      SQLite 项目、历史、分析及异常恢复
  strategy/     基础规则和 5 套附加策略仿真
  ui/           PySide6 参数区、统计和八方向 pyqtgraph 图表
tools/          兼容工具入口和实车参考脚本
tests/          自动化测试
plans/          分步实施与验收文档
```
