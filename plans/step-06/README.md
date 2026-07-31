# 第六步：项目存储和离线回放

状态：**已完成**

完成日期：2026-07-30

## SQLite 项目存储

`ble_calibration.storage` 已实现：

- 保存、新建、列出和打开项目。
- 保存车辆名称、VIN、当前/原始云推参数和计算版本。
- 保存八方向记录、实测解闭锁距离、事件时间及原始附件引用。
- 保存参数修改历史和每次八方向分析结果。
- JSONL/BLF 只记录附件路径和格式，不作为数据库大字段写入。
- 以 SQLite WAL 模式运行，并启用外键约束。

## 自动保存和异常恢复

`AutosaveWorker` 在后台按可配置间隔保存当前内存项目的恢复快照，不阻塞 UI
线程。应用正常保存后可清除快照；异常退出后可按项目 ID 读回方向进度、距离、参数和
附件引用。

## 离线回放

`ReplayService` 支持：

- 读取 Mock JSONL。
- 通过 `python-can==4.6.1` 的 `BLFReader` 延迟读取 BLF。
- 根据每个方向的手动开始/结束时间裁剪原始帧。
- 复用正式 `CanFrameProcessor` 重建 5 节点对齐 RSSI 序列。
- 在不连接 CAN 设备的情况下重新运行策略和 What-if 分析。

数据库仅保存项目元数据和原始附件引用；RSSI 序列由原始文件重建，避免同一数据产生
两份不一致的来源。

## 汇总联动

每次 What-if 调用返回同一个不可分割的 `RecomputeResult`，其中同时包含：

- 8 个方向的新条件时刻、动作时刻、触发节点、瞬时 RSSI 和动作距离。
- 闭锁优秀/良/差数量、优秀率、良/差方向和未触发方向。
- 解锁优秀/良/差数量、优秀率、良/差方向和未触发方向。

因此阈值或策略变化后，图表与优良差汇总不会使用旧缓存。原始参数使用实测虚线距离
评级；What-if 参数使用相对实测动作时刻和步速投影后的动作距离重新评级。

## 闭环验证命令

以下命令会执行“采集会话构建 → 保存 → 关闭数据库 → 重开 → 离线回放 → 八方向
重算 → 保存分析结果”：

```bash
python3 run_app.py generate-mock \
  --output mock_data/eight_directions.jsonl \
  --manifest mock_data/eight_directions.manifest.json

python3 run_app.py project-demo \
  --input mock_data/eight_directions.jsonl \
  --manifest mock_data/eight_directions.manifest.json \
  --database data/projects.sqlite3
```

## 验收证据

- [x] 项目关闭数据库后可完整重开，8 个方向顺序及字段不变。
- [x] JSONL 回放得到的每个 `RssiSample` 与采集完成时逐项相同。
- [x] 回放后的八方向事件和闭锁/解锁汇总与采集时一致。
- [x] BLF 读取器按需导入，并能转换为统一 `CanFrame`。
- [x] 参数历史、计算结果和计算版本可追溯。
- [x] 自动保存线程可写入并恢复项目快照。
- [x] What-if 阈值变化会同步重算优良差汇总和未触发方向。

## 下一步

第 7 步开发 Windows 原生 Qt 界面，把存储、回放、云推参数和同一次
`RecomputeResult` 接到可隐藏参数区、八方向图表及汇总卡片。
