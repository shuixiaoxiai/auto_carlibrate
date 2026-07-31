# 第三步：CAN 数据源抽象与录制能力

状态：**已完成**

完成日期：2026-07-30

## 交付物

### 统一数据源

- `CanSource`：连接、接收、停止、状态订阅和上下文管理协议。
- `MockCanSource`：JSONL 原速、倍速、最快和循环回放。
- `ZlgCanSource`：沿用 `can_read_save.py` 参数，连接时才加载
  `python-can`/`zlgcan`，因此 Mac 和无硬件测试环境可以运行其他模块。
- 循环 Mock 的输出时间戳保持单调递增，不会在下一轮回退。

### 采集与录制

- `CaptureWorker` 在独立非守护线程中执行连接、接收、录制和回调。
- `JsonlFrameRecorder` 保存统一 CAN JSONL。
- `RotatingBlfRecorder` 延迟加载 `python-can`，按帧数分卷，且不会生成空尾卷。
- 任一异常都会保存在 `CaptureWorker.last_error`，退出时始终尝试关闭录制器和数据源。

### 无界面采集命令

```bash
python3 run_app.py capture-mock \
  --input mock_data/eight_directions.jsonl \
  --speed 10 \
  --output mock_data/captured.jsonl
```

参数：

- `--speed 1`：原速。
- `--speed 10`：十倍速。
- `--speed 0`：不等待，最快回放。
- `--loop --max-frames N`：循环回放到指定帧数后停止。

## 验收证据

- [x] Mock 数据源可以完整回放并发出连接、运行、停止状态。
- [x] Mock 循环后的时间戳连续递增。
- [x] 自动化加速回放覆盖超过 30 分钟的源时间轴。
- [x] ZLG 适配器使用假总线验证连接参数、通道过滤、帧转换和 shutdown。
- [x] BLF 假写入器验证 `2/2/1` 分卷和全部文件关闭。
- [x] CaptureWorker 达到帧数后自动结束，数据源和录制器均关闭。
- [x] CLI 可以无界面回放并采集指定帧数。
- [x] 四类目标 CAN ID 的共享协议测试继续通过。
- [x] 共 27 个自动化测试通过。

真实墙钟 30 分钟 Mock 长稳将在第 9 步发布测试中再次执行；本步骤的自动化测试使用
最快模式覆盖同等源时间跨度，避免日常测试固定等待 30 分钟。Windows ZLG 实车连接仍按
原计划在打包后执行。
