param(
    [switch]$IncludeZlgcan,
    [switch]$SkipTests,
    [switch]$SkipInstaller,
    [string]$PythonExecutable = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $ProjectRoot

function Resolve-BuildPython {
    param([string]$RequestedExecutable)

    if (-not [string]::IsNullOrWhiteSpace($RequestedExecutable)) {
        if (-not (Test-Path -LiteralPath $RequestedExecutable -PathType Leaf)) {
            throw "The requested Python interpreter does not exist: $RequestedExecutable"
        }
        return (Resolve-Path -LiteralPath $RequestedExecutable).Path
    }

    $Candidates = @()
    if (-not [string]::IsNullOrWhiteSpace($env:VIRTUAL_ENV)) {
        $Candidates += (Join-Path $env:VIRTUAL_ENV "Scripts\python.exe")
    }
    if (-not [string]::IsNullOrWhiteSpace($env:CONDA_PREFIX)) {
        $Candidates += (Join-Path $env:CONDA_PREFIX "python.exe")
    }

    $PipCommand = Get-Command pip.exe -ErrorAction SilentlyContinue
    if ($null -ne $PipCommand) {
        $PipEnvironment = Split-Path (Split-Path $PipCommand.Source -Parent) -Parent
        $Candidates += (Join-Path $PipEnvironment "python.exe")
    }

    $PythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($null -eq $PythonCommand) {
        $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
    }
    if ($null -ne $PythonCommand) {
        $Candidates += $PythonCommand.Source
    }

    $ResolvedCandidate = $Candidates |
        Select-Object -Unique |
        Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
        Select-Object -First 1
    if ($null -eq $ResolvedCandidate) {
        throw "No Python interpreter was found. Activate the build environment or pass -PythonExecutable."
    }
    return (Resolve-Path -LiteralPath $ResolvedCandidate).Path
}

function Invoke-BuildPython {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    & $script:PythonExecutable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed with exit code $LASTEXITCODE: $script:PythonExecutable $($Arguments -join ' ')"
    }
}

$PythonExecutable = Resolve-BuildPython -RequestedExecutable $PythonExecutable

if ([Environment]::Is64BitProcess -ne $true) {
    throw "The build must run in 64-bit Python/PowerShell."
}
$PythonBits = (Invoke-BuildPython -Arguments @(
    "-c", "import struct; print(struct.calcsize('P') * 8)"
) | Out-String).Trim()
if ($PythonBits -ne "64") {
    throw "The build must use 64-bit Python; current interpreter is $PythonBits-bit."
}
$PythonVersion = (Invoke-BuildPython -Arguments @(
    "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
) | Out-String).Trim()
Write-Host "Build interpreter: $PythonExecutable (CPython $PythonVersion x64)"

Invoke-BuildPython -Arguments @("-m", "pip", "install", "--upgrade", "pip")
Invoke-BuildPython -Arguments @("-m", "pip", "install", "-e", ".")
Invoke-BuildPython -Arguments @(
    "-m", "pip", "install", "-r", "packaging\windows\build-requirements.txt"
)
Invoke-BuildPython -Arguments @("-m", "pip", "install", "python-can==4.6.1")

if ($IncludeZlgcan) {
    Invoke-BuildPython -Arguments @(
        "-c",
        "from importlib import metadata; from pathlib import Path; import zlgcan; from zlgcan.zlgcan import ZCANDeviceType; assert metadata.version('zlgcan') == '0.3.0'; files=list(Path(zlgcan.__file__).parent.rglob('*clgcan_driver*.pyd')); assert files, 'clgcan_driver.pyd not found'; print(files)"
    )
}

if (-not $SkipTests) {
    Invoke-BuildPython -Arguments @("-m", "unittest", "discover", "-s", "tests", "-v")
    $env:QT_QPA_PLATFORM = "offscreen"
    Invoke-BuildPython -Arguments @(
        "tools\ui_smoke.py",
        "--max-refresh-ms", "200",
        "--report", "dist\acceptance\source-ui.json"
    )
    Invoke-BuildPython -Arguments @(
        "tools\manual_ui_smoke.py",
        "--width", "1100",
        "--height", "720",
        "--report", "dist\acceptance\manual-workflow.json"
    )
    Invoke-BuildPython -Arguments @(
        "tools\live_ui_smoke.py",
        "--width", "1100",
        "--height", "720",
        "--report", "dist\acceptance\live-workflow.json"
    )
}

Invoke-BuildPython -Arguments @("packaging\windows\generate_assets.py")
$env:BLE_CALIBRATION_INCLUDE_ZLGCAN = "0"
if ($IncludeZlgcan) {
    $env:BLE_CALIBRATION_INCLUDE_ZLGCAN = "1"
}
Invoke-BuildPython -Arguments @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--distpath", "dist",
    "--workpath", "build\pyinstaller-windows",
    "packaging\windows\ble_calibration.spec"
)

$VerifyArguments = @(
    "packaging\windows\verify_bundle.py",
    "--exe", "dist\BLECalibration\BLECalibration.exe",
    "--output-dir", "dist\acceptance"
)
if ($IncludeZlgcan) {
    $VerifyArguments += "--expect-zlgcan"
}
Invoke-BuildPython -Arguments $VerifyArguments

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

$Version = (Invoke-BuildPython -Arguments @(
    "-c", "from ble_calibration.version import __version__; print(__version__)"
) | Out-String).Trim()
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
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup failed with exit code $LASTEXITCODE."
    }
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
Invoke-BuildPython -Arguments $ManifestArguments

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
Invoke-BuildPython -Arguments $AuditArguments

Write-Host "Windows build complete."
Write-Host "Onedir:  dist\BLECalibration\BLECalibration.exe"
Write-Host "Archive: $Archive"
Write-Host "Manifest: dist\acceptance\build-manifest.json"
Write-Host "Audit:    dist\acceptance\release-audit.json"
