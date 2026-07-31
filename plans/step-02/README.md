# 第二步：建立工程和核心模型

状态：**已完成**

完成日期：2026-07-30

## 交付物

### 标准 Python 工程

- `pyproject.toml`：包元数据、`src` 布局和 `ble-calibration` 命令入口。
- `requirements/windows-can.txt`：锁定已在实车脚本验证的
  `python-can==4.6.1`、`zlgcan==0.3.0`。
- `run_app.py`：无需先安装包即可运行的仓库入口。
- `src/ble_calibration/app/main.py`：可运行的无 GUI 应用空壳。

UI 依赖在进入桌面界面步骤时锁定；当前核心层只使用 Python 标准库，测试不依赖网络、
CAN 盒或实车。

### 统一核心模型

| 模块 | 内容 |
| --- | --- |
| `domain/enums.py` | 5 节点、8 方向、事件类型、状态、策略和距离评级 |
| `domain/models.py` | CAN 帧、RSSI 采样、实车事件、实线/虚线、方向记录、项目模型 |
| `domain/schema.py` | 项目、CAN JSONL、Mock manifest 和分析版本 |
| `config/settings.py` | CAN/运行参数验证、默认值和原子 JSON 保存 |
| `diagnostics/logging.py` | 控制台与滚动文件日志 |

关键不变量：

- 节点顺序固定为主、前、后、左、右。
- 方向顺序固定为正前、右前、正右、右后、正后、左后、正左、左前。
- `0x55A` 的 `1/2` 分别映射为解锁/闭锁实车事件。
- 实线模型保存条件开始满足时刻、触发节点、策略和 5 节点 RSSI。
- 虚线模型保存动作时刻及其来源。
- 同一项目不允许出现重复方向。

### 单一 CAN/Mock 实现

- 正式协议：`src/ble_calibration/can/protocol.py`。
- 正式 Mock：`src/ble_calibration/mock/generator.py`。
- `tools/can_protocol.py` 和 `tools/mock_can_generate.py` 是兼容包装，不复制业务逻辑。
- Mock 帧直接使用正式 `CanFrame` 模型。

## 运行方式

```bash
python3 run_app.py info --json
python3 run_app.py generate-mock \
  --output mock_data/eight_directions.jsonl \
  --manifest mock_data/eight_directions.manifest.json
python3 -m unittest discover -s tests -v
```

安装为标准包后也支持：

```bash
ble-calibration info
ble-calibration generate-mock --output mock_data/eight_directions.jsonl
```

## 验收证据

- [x] 应用空壳可直接运行并输出版本、5 节点和 8 方向。
- [x] 相同 Mock 种子生成完全一致的帧和 manifest。
- [x] 标准场景生成 7712 帧，包含 8 个方向。
- [x] 8 个方向均包含先闭锁、后解锁请求。
- [x] Mock 和旧工具入口使用正式 CAN 协议函数。
- [x] CAN 帧、方向记录和项目模型可序列化往返。
- [x] 配置可以验证并原子保存。
- [x] 18 个自动化测试通过。

## 后续边界

下列内容属于后续步骤：

- 第 3 步：`CanSource`、Mock 回放、ZLG 适配器、独立采集线程和 BLF 分卷。
- 第 4 步：五节点时间对齐、stale、边沿检测和方向会话状态机。
- 第 5～6 步：基础/附加策略、距离与 200 ms 八方向重算。
- 第 7～9 步：Qt UI、项目存储和 Windows 打包。
- 第 10 步：CAN 盒和实车验证。
