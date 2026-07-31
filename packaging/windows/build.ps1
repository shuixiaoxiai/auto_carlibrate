param(
    [switch]$IncludeZlgcan,
    [switch]$SkipTests,
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $ProjectRoot

if ([Environment]::Is64BitProcess -ne $true) {
    throw "The build must run in 64-bit Python/PowerShell."
}
$PythonBits = (python -c "import struct; print(struct.calcsize('P') * 8)").Trim()
if ($PythonBits -ne "64") {
    throw "The build must use 64-bit Python; current interpreter is $PythonBits-bit."
}
$PythonVersion = (python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')").Trim()
Write-Host "Build interpreter: CPython $PythonVersion x64"

python -m pip install --upgrade pip
python -m pip install -e .
python -m pip install -r packaging\windows\build-requirements.txt
python -m pip install python-can==4.6.1

if ($IncludeZlgcan) {
    python -c "from importlib import metadata; from pathlib import Path; import zlgcan; from zlgcan.zlgcan import ZCANDeviceType; assert metadata.version('zlgcan') == '0.3.0'; files=list(Path(zlgcan.__file__).parent.rglob('*clgcan_driver*.pyd')); assert files, 'clgcan_driver.pyd not found'; print(files)"
}

if (-not $SkipTests) {
    python -m unittest discover -s tests -v
    $env:QT_QPA_PLATFORM = "offscreen"
    python tools\ui_smoke.py `
        --max-refresh-ms 200 `
        --report dist\acceptance\source-ui.json
    python tools\manual_ui_smoke.py `
        --width 1100 `
        --height 720 `
        --report dist\acceptance\manual-workflow.json
    python tools\live_ui_smoke.py `
        --width 1100 `
        --height 720 `
        --report dist\acceptance\live-workflow.json
}

python packaging\windows\generate_assets.py
$env:BLE_CALIBRATION_INCLUDE_ZLGCAN = "0"
if ($IncludeZlgcan) {
    $env:BLE_CALIBRATION_INCLUDE_ZLGCAN = "1"
}
python -m PyInstaller `
    --noconfirm `
    --clean `
    --distpath dist `
    --workpath build\pyinstaller-windows `
    packaging\windows\ble_calibration.spec

$VerifyArguments = @(
    "packaging\windows\verify_bundle.py",
    "--exe", "dist\BLECalibration\BLECalibration.exe",
    "--output-dir", "dist\acceptance"
)
if ($IncludeZlgcan) {
    $VerifyArguments += "--expect-zlgcan"
}
python @VerifyArguments

if ($IncludeZlgcan) {
    $BundledDriver = Get-ChildItem `
        -Path dist\BLECalibration `
        -Filter "*clgcan_driver*.pyd" `
        -Recurse |
        Select-Object -First 1
    if ($null -eq $BundledDriver) {
        throw "The built bundle does not contain clgcan_driver.pyd."
    }
    Write-Host "Bundled ZLG native driver: $($BundledDriver.FullName)"
}

$Version = python -c "from ble_calibration.version import __version__; print(__version__)"
$Archive = "dist\BLECalibration-$Version-win64.zip"
Compress-Archive -Path "dist\BLECalibration\*" -DestinationPath $Archive -Force

if (-not $SkipInstaller) {
    $IsccCommand = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    $IsccPath = $null
    if ($null -ne $IsccCommand) {
        $IsccPath = $IsccCommand.Source
    }
    if ($null -eq $IsccPath) {
        $Candidate = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
        if (Test-Path $Candidate) {
            $IsccPath = $Candidate
        }
    }
    if ($null -eq $IsccPath) {
        throw "Inno Setup 6 was not found. Install it or pass -SkipInstaller."
    }
    & $IsccPath "/DAppVersion=$Version" packaging\windows\installer.iss
}

$ManifestArguments = @(
    "packaging\windows\write_build_manifest.py",
    "--onedir-exe", "dist\BLECalibration\BLECalibration.exe",
    "--archive", $Archive,
    "--acceptance-dir", "dist\acceptance",
    "--output", "dist\acceptance\build-manifest.json"
)
if (-not $SkipInstaller) {
    $ManifestArguments += @(
        "--installer",
        "dist\BLECalibration-$Version-Setup.exe"
    )
}
if ($IncludeZlgcan) {
    $ManifestArguments += "--include-zlgcan"
}
if (-not $SkipTests) {
    $ManifestArguments += "--source-tests-run"
}
python @ManifestArguments

$AuditArguments = @(
    "packaging\windows\audit_build_manifest.py",
    "--manifest", "dist\acceptance\build-manifest.json",
    "--output", "dist\acceptance\release-audit.json",
    "--require-windows"
)
if ($IncludeZlgcan) {
    $AuditArguments += "--require-zlgcan"
}
if (-not $SkipTests) {
    $AuditArguments += "--require-source-tests"
}
python @AuditArguments

Write-Host "Windows build complete."
Write-Host "Onedir:  dist\BLECalibration\BLECalibration.exe"
Write-Host "Archive: $Archive"
Write-Host "Manifest: dist\acceptance\build-manifest.json"
Write-Host "Audit:    dist\acceptance\release-audit.json"
