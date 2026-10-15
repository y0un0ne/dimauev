#define MyAppName "Dimensions Auto Evaluate"
#define MyAppVersion "1.0.1"
#define MyAppPublisher "Dimensions Auto Evaluate"
#define MyAppExeName "DimensionsAutoEvaluate.exe"

[Setup]
AppId={{6E34A0F5-D5FB-4A63-97B2-348229E5924B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\DimensionsAutoEvaluate
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
OutputDir=..\release
OutputBaseFilename=DimensionsAutoEvaluate-Setup-{#MyAppVersion}-win-x64
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
LicenseFile=..\docs\TERMS.md
CloseApplications=yes
RestartApplications=no

[Files]
Source: "..\packaging\dist\DimensionsAutoEvaluate\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\docs\*"; DestDir: "{app}\docs"; Flags: ignoreversion recursesubdirs createallsubdirs

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Privacy and local data"; Filename: "{app}\docs\PRIVACY.md"
Name: "{group}\User guide"; Filename: "{app}\docs\USER_GUIDE.md"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Deliberately preserve %LOCALAPPDATA%\DimensionsAutoEvaluate user data,
; browser profile, logs, reports, and Windows Credential Manager entries.
