# BLE Calibration

汽车数字钥匙 BLE 标定 Windows 应用。当前已完成需求冻结，并开始建立不依赖 GUI 和
实车的核心工程。

## 本地运行

仓库内直接运行：

```bash
python run_app.py info
python run_app.py generate-mock --output mock_data/eight_directions.jsonl
```

以标准包方式运行：

```bash
python -m pip install -e .
ble-calibration info
ble-calibration generate-mock --output mock_data/eight_directions.jsonl
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
  app/          应用入口和后续 UI 组合
  can/          共用 CAN 协议及后续数据源
  config/       配置加载、验证和保存
  diagnostics/  日志和诊断
  domain/       时间、节点、方向、事件、项目等核心模型
  mock/         确定性八方向 Mock CAN
tools/          兼容工具入口和实车参考脚本
tests/          自动化测试
plans/          分步实施与验收文档
```
