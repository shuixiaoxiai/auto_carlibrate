# Windows 10/11 64 位构建

## 构建环境

- Windows 10/11 64 位。
- 64 位 CPython 3.9+；无硬件 CI 固定使用 3.9。
- `python-can==4.6.1`。
- PyInstaller 和 Pillow 版本见 `build-requirements.txt`。
- 需要安装 Inno Setup 6；只生成 onedir 和 ZIP 时可使用 `-SkipInstaller`。

ZLG 实车候选包还要求当前 Python 环境已经安装 `zlgcan==0.3.0`，并且包内存在与
当前 64 位 Python 匹配的 `clgcan_driver.pyd`。构建脚本不会替换这套已经过实车脚本
验证的依赖；请直接在能运行 `tools/can_read_save.py` 的同一个 Python 环境中使用
`-IncludeZlgcan`。

`python-can` 会在两种模式下都明确收集，以支持 BLF 保存和回放；`zlgcan` 只在传入
`-IncludeZlgcan` 时收集，避免“机器上碰巧安装了包”导致无硬件构建结果不确定。

## 生成无硬件候选包

在仓库根目录的 PowerShell 中执行：

```powershell
packaging\windows\build.ps1
```

脚本依次完成：

1. 校验 PowerShell 和 Python 都是 64 位。
2. 记录当前 Python 版本；实车模式以能运行参考脚本的解释器为准。
3. 安装应用、固定构建依赖和 `python-can==4.6.1`。
4. 运行 86 个单元测试、Qt What-if、Mock 手动采集和模拟 ZLG 持久连接工作流测试。
5. 生成 ICO 和 Windows 版本资源。
6. 生成 PyInstaller onedir 应用。
7. 启动冻结后的 EXE，先生成 BLF 验证 `python-can` 已正确收集，再验证 8 个方向和
   优良差汇总随 What-if 同步重算，并验证 Mock 手动工作区和 ZLG 设备工作区均可打开。
8. 生成 ZIP 和 Inno Setup 安装包。
9. 为 EXE、ZIP、安装包、原生 `.pyd` 和验收证据生成 SHA-256 构建清单。

产物：

```text
dist\BLECalibration\BLECalibration.exe
dist\BLECalibration-<version>-win64.zip
dist\BLECalibration-<version>-Setup.exe
dist\acceptance\analysis.json
dist\acceptance\analysis.png
dist\acceptance\manual.png
dist\acceptance\live-zlg.png
dist\acceptance\bundle-can.blf
dist\acceptance\bundle-can.manifest.json
dist\acceptance\source-ui.json
dist\acceptance\manual-workflow.json
dist\acceptance\live-workflow.json
dist\acceptance\build-manifest.json
dist\acceptance\release-audit.json
```

`analysis.json` 必须记录：

- `direction_count = 8`；
- What-if 全界面刷新时间小于 200 ms；
- 闭锁和解锁均为 `优 0 / 良 0 / 差 8 / 未触发 8`；
- 汇总控件显示与重算结果一致。

## 生成包含 ZLG 原生驱动的候选包

先在能够运行参考脚本的同一个 64 位 Python 环境中执行：

```powershell
packaging\windows\build.ps1 -IncludeZlgcan
```

脚本会在打包前校验 `zlgcan==0.3.0` 和 `clgcan_driver.pyd`，并在打包后再次检查
onedir 中确实包含该 `.pyd`。冻结后的 EXE 还会执行一次不打开硬件的后端自检，确认
`python-can` 能发现 `zlgcan` 入口、`ZCAN_USBCANFD_200U` 枚举可导入，并生成
`dist\acceptance\zlg-bundle.json`。任一检查失败都不会产出通过状态。

`build-manifest.json` 中的 `include_zlgcan` 必须为 `true`，`native_drivers` 必须列出
`clgcan_driver.pyd`，并记录 EXE/ZIP/Setup 的绝对路径、大小和 SHA-256。该文件用于把
Windows 构建结果回传后核对产物，而不是只凭控制台的“构建成功”判断。
正式构建时 `source_tests_run` 还必须为 `true`，并包含三份桌面流程报告；
`source_revision` 用来核对 EXE 对应的源码提交。
`release-audit.json` 只有在 Windows/x64、ZLG 依赖、方向操作、距离输入、云推编解码、
阈值与策略的 200 ms 门禁全部通过时才会写入 `ok: true`。

安装后的 `BLECalibration.exe` 无参数启动时默认进入 ZLG 实车工作区。连接一次设备后，
8 个方向共享同一个实时接收线程；每个方向开始时单独创建 BLF，手动结束后关闭该方向
BLF，但保持设备连接。缺少任一解闭锁事件时仍允许手动结束并保存为不完整方向。

## 两小时 Mock 长稳

```powershell
python tools\stability_smoke.py `
  --duration-seconds 7200 `
  --report dist\acceptance\mock-stability.json
```

长稳过程中持续循环读取和解码 CAN 帧，并每 200 ms 在原始参数与 What-if 参数之间
切换。任一采集异常、方向丢失、重算超过 200 ms 或跟踪内存峰值超过 256 MB都会失败。

GitHub Actions 的 `windows-build` 工作流也执行相同的两小时门禁。仓库尚未配置远程仓库
或 Windows runner 时，可直接在目标 Windows 电脑运行上述 PowerShell 脚本。
