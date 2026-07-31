# BLE Calibration

汽车数字钥匙 BLE 标定 Windows 应用。当前已完成需求冻结，并开始建立不依赖 GUI 和
实车的核心工程。

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

## Windows CAN 依赖

现有实车脚本验证过以下版本：

- `python-can==4.6.1`
- `zlgcan==0.3.0`
- `zlgcan` 包自带的 `clgcan_driver.pyd`

精确依赖记录在 `requirements/windows-can.txt`。Windows EXE 打包前需要在目标电脑
记录 Python ABI，并验证它与原生 `.pyd` 一致。

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
  session/      方向选择、记录、距离和完成状态机
  strategy/     基础规则和 5 套附加策略仿真
tools/          兼容工具入口和实车参考脚本
tests/          自动化测试
plans/          分步实施与验收文档
```
