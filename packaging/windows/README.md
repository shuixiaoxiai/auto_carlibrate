# Windows 10/11 64 位构建

## 构建环境

- Windows 10/11 64 位。
- CPython 3.9 64 位。
- `python-can==4.6.1`。
- PyInstaller 和 Pillow 版本见 `build-requirements.txt`。
- 需要安装 Inno Setup 6；只生成 onedir 和 ZIP 时可使用 `-SkipInstaller`。

ZLG 实车候选包还要求当前 Python 环境已经安装 `zlgcan==0.3.0`，并且包内存在与
CPython 3.9 64 位匹配的 `clgcan_driver.pyd`。公开 PyPI 当前无法取得该版本，因此脚本
不会尝试从公开源安装它；请在已经能运行 `tools/can_read_save.py` 的 Windows 环境中
使用 `-IncludeZlgcan`。

## 生成 Mock 候选包

在仓库根目录的 PowerShell 中执行：

```powershell
packaging\windows\build.ps1
```

脚本依次完成：

1. 校验 PowerShell 和 Python 都是 64 位。
2. 安装应用、固定构建依赖和 `python-can==4.6.1`。
3. 运行 75 个单元测试、Qt 联动测试和八方向手动工作流测试。
4. 生成 ICO 和 Windows 版本资源。
5. 生成 PyInstaller onedir 应用。
6. 启动冻结后的 EXE，验证 8 个方向和优良差汇总随 What-if 同步重算。
7. 生成 ZIP 和 Inno Setup 安装包。

产物：

```text
dist\BLECalibration\BLECalibration.exe
dist\BLECalibration-<version>-win64.zip
dist\BLECalibration-<version>-Setup.exe
dist\acceptance\analysis.json
dist\acceptance\analysis.png
dist\acceptance\manual.png
```

`analysis.json` 必须记录：

- `direction_count = 8`；
- What-if 全界面刷新时间小于 200 ms；
- 闭锁和解锁均为 `优 0 / 良 0 / 差 8 / 未触发 8`；
- 汇总控件显示与重算结果一致。

## 生成包含 ZLG 原生驱动的候选包

先在当前 64 位 Python 3.9 环境确认参考脚本能运行，再执行：

```powershell
packaging\windows\build.ps1 -IncludeZlgcan
```

脚本会在打包前校验 `zlgcan==0.3.0` 和 `clgcan_driver.pyd`，并在打包后再次检查
onedir 中确实包含该 `.pyd`。任一检查失败都不会产出通过状态。

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
