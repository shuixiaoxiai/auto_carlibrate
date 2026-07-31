#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#define AppName "BLE Calibration"
#define AppExeName "BLECalibration.exe"

[Setup]
AppId={{F2A79F41-777D-4D94-8D79-5F43047BEA70}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=BLE Calibration Team
DefaultDirName={localappdata}\Programs\BLECalibration
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\..\dist
OutputBaseFilename=BLECalibration-{#AppVersion}-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
SetupIconFile=..\..\build\windows-assets\BLECalibration.ico
UninstallDisplayIcon={app}\{#AppExeName}
VersionInfoVersion={#AppVersion}

[Files]
Source: "..\..\dist\BLECalibration\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加快捷方式："; Flags: unchecked

[Run]
Filename: "{app}\{#AppExeName}"; Description: "启动 {#AppName}"; Flags: nowait postinstall skipifsilent
