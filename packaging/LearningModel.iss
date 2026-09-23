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

[Code]
var
  RemoveUserData: Boolean;

function InitializeUninstall: Boolean;
var
  Dialog: TSetupForm;
  KeepOption, RemoveOption: TNewRadioButton;
  ContinueButton, CancelButton: TNewButton;
  Hint: TNewStaticText;
  DataPath: String;
begin
  Result := True;
  RemoveUserData := False;
  if UninstallSilent then Exit;
  DataPath := ExpandConstant('{localappdata}\LearningModel');
  Dialog := CreateCustomForm(ScaleX(500), ScaleY(230), True, True);
  try
    Dialog.Caption := '卸载学习模型';


    KeepOption := TNewRadioButton.Create(Dialog);
    KeepOption.Parent := Dialog;
    KeepOption.SetBounds(ScaleX(24), ScaleY(24), ScaleX(450), ScaleY(24));
    KeepOption.Caption := '保留学习数据（重装后可继续使用）';
    KeepOption.Checked := True;
    RemoveOption := TNewRadioButton.Create(Dialog);
    RemoveOption.Parent := Dialog;
    RemoveOption.SetBounds(ScaleX(24), ScaleY(62), ScaleX(450), ScaleY(24));
    RemoveOption.Caption := '同时清除全部应用数据';
    Hint := TNewStaticText.Create(Dialog);
    Hint.Parent := Dialog;
    Hint.AutoSize := False;
    Hint.WordWrap := True;
    Hint.SetBounds(ScaleX(24), ScaleY(102), ScaleX(450), ScaleY(65));
    Hint.Caption := '包括学习记录、设置、备份、日志和默认导出文件。' + #13#10 + '请先退出学习模型。';
    ContinueButton := TNewButton.Create(Dialog);
    ContinueButton.Parent := Dialog;
    ContinueButton.SetBounds(ScaleX(280), ScaleY(184), ScaleX(90), ScaleY(28));
    ContinueButton.Caption := '继续卸载';
    ContinueButton.ModalResult := mrOk;
    ContinueButton.Default := True;
    CancelButton := TNewButton.Create(Dialog);
    CancelButton.Parent := Dialog;
    CancelButton.SetBounds(ScaleX(384), ScaleY(184), ScaleX(90), ScaleY(28));
    CancelButton.Caption := '取消';
    CancelButton.ModalResult := mrCancel;
    CancelButton.Cancel := True;
    Result := Dialog.ShowModal = mrOk;
    if Result and RemoveOption.Checked then begin
      Result := MsgBox('永久删除以下目录中的全部应用数据？' + #13#10#13#10 +
        DataPath + #13#10#13#10 + '学习记录、备份及该目录内的导出文件均无法恢复。其他位置的资料不会删除。',
        mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
      RemoveUserData := Result;
    end;
  finally
    Dialog.Free;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataPath, ParentPath: String;
begin
  if (CurUninstallStep <> usPostUninstall) or not RemoveUserData then Exit;
  ParentPath := RemoveBackslashUnlessRoot(ExpandConstant('{localappdata}'));
  DataPath := ParentPath + '\LearningModel';
  { Fixed per-user child only. Never accept a command-line deletion path.
    Inno DelTree does not follow directory reparse points. }
  if (Length(ParentPath) < 4) or
     (CompareText(ExtractFileDir(DataPath), ParentPath) <> 0) then
    RaiseException('应用数据路径无效，已停止清理。');
  if DirExists(DataPath) then begin
    if not DelTree(DataPath, True, True, True) then begin
      Log('User data cleanup incomplete: ' + DataPath);
      MsgBox('程序已卸载，但部分应用数据未能删除（可能仍被占用）。' + #13#10 +
        '请关闭学习模型后检查：' + #13#10 + DataPath, mbError, MB_OK);
    end else Log('User data cleanup completed: ' + DataPath);
  end;
end;
