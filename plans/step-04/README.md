# 第四步：时间对齐和方向测试会话

状态：**已完成**

完成日期：2026-07-30

## 五节点时间对齐

`RssiTimeAligner` 提供：

- 主、前、后、左、右 5 节点最新值缓存。
- 默认 10 Hz 统一采样时间轴。
- 每个节点的 `age_ms` 和 `stale` 状态。
- 未收到的节点保持 `None + stale`，不会伪造 RSSI。
- 基于源时间戳而不是样本数量推进时间。
- 时间戳回退的帧计入 `out_of_order_count` 并忽略，不污染后续序列和事件。

## 解闭锁边沿

`RequestEdgeDetector` 对 `0x55A` 高四位去重：

- 非 `2` 变为 `2`：产生一次实际闭锁事件。
- 非 `1` 变为 `1`：产生一次实际解锁事件。
- 连续周期 `1` 或 `2` 不重复产生事件。
- `1/2` 变为 `0` 只清除状态，不产生事件。

## 测试人员工作流

`DirectionSessionController` 已实现：

```text
空闲
  → 手动选择方向
  → 手动开始
  → 等待闭锁
  → 等待解锁
  → 输入/确认实际闭锁和解锁距离
  → 完成
```

具体行为：

- 方向和开始记录必须由测试人员操作。
- 步速可以在每个方向开始时指定。
- 闭锁和解锁事件齐全后继续采样，保留实际解锁后的 What-if 序列。
- 距离可以在行走过程中预先输入，也可以在两个事件后输入。
- 测试人员手动结束；事件和距离齐全时保存为完整，否则保存为不完整。
- 支持清除旧方向记录并重录。
- 每个方向保存 RSSI 样本数、实际事件、实际距离、步速和原始数据引用。

## 无界面八方向验收

```bash
python3 run_app.py generate-mock \
  --output mock_data/eight_directions.jsonl \
  --manifest mock_data/eight_directions.manifest.json

python3 run_app.py session-demo \
  --input mock_data/eight_directions.jsonl \
  --manifest mock_data/eight_directions.manifest.json \
  --json
```

`session-demo` 不读取 manifest 中的预期事件作为结果；CAN 帧仍经过正式协议、时间对齐、
请求边沿和方向状态机。manifest 只提供方向区间及测试人员应输入的两项实际距离。

## 验收证据

- [x] 5 节点错开到达后可以形成统一 RSSI 样本。
- [x] 节点超过 stale 时间后正确标记。
- [x] 周期性 `0x55A` 请求只产生一次边沿。
- [x] 时间戳回退不会生成错误实车事件。
- [x] 单方向包含 RSSI 序列、闭锁事件、解锁事件和两项实际距离。
- [x] 8 个标准 Mock 方向全部由手动结束动作保存为完整方向。
- [x] 缺少解锁时可以手动结束为不完整。
- [x] 已完成方向可以清除并重录。
- [x] 共 37 个自动化测试通过。

## 后续边界

- 第 5 步实现云推参数双向编解码和基础/附加策略。
- 第 6 步实现距离换算、优良差汇总及 200 ms 八方向联动基准。
- 第 7 步把本状态机连接到 Windows Qt 界面。
