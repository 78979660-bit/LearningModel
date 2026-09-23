; LearningModel per-user Windows installer (Inno Setup 6).
; Compile from the project root through tools\build_release.ps1.

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

#define AppName "学习模型"
#define AppInternalName "LearningModel"
#define AppPublisher "LearningModel Project"
#define AppId "{{E06D2BBE-5FA4-4F72-B03E-130652DE5A52}"
#define ProjectRoot AddBackslash(SourcePath) + ".."
#define AppSource ProjectRoot + "\dist\LearningModel"
#define AppExeName "LearningModel.exe"
#define AppIcon ProjectRoot + "\study_app\ui\assets\app.ico"

#if !FileExists(AppSource + "\" + AppExeName)
  #error "dist\LearningModel\LearningModel.exe is missing; build the onedir application first."
#endif

#if !FileExists(AppIcon)
  #error "The required release icon study_app\ui\assets\app.ico is missing."
#endif

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
VersionInfoVersion={#AppVersion}.0
VersionInfoCompany={#AppPublisher}
VersionInfoDescription={#AppName} 安装程序
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\{#AppInternalName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir={#ProjectRoot}\release\artifacts
OutputBaseFilename={#AppInternalName}-Setup-{#AppVersion}-x64
SetupIconFile={#AppIcon}
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
ChangesAssociations=no
ChangesEnvironment=no
UsePreviousAppDir=yes
UsePreviousGroup=yes
UsePreviousTasks=yes
AppContact=
AppSupportURL=
AppUpdatesURL=

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加快捷方式："; Flags: unchecked

[Files]
; The source is the already verified PyInstaller directory. No project source,
; test tree, app_data, legacy root JSON, or user profile path is copied here.
Source: "{#AppSource}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "启动 {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Intentionally empty. User data lives under %LOCALAPPDATA%\LearningModel and
; must survive uninstall and reinstall. Never add that directory here.
