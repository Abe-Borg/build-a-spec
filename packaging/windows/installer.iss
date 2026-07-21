; Inno Setup script for Build-a-Spec (Windows desktop app).
; Cloned from Claude-Spec-Critic packaging/windows/installer.iss.
;
; Compile with:
;   ISCC /DMyAppVersion=0.5.0 packaging\windows\installer.iss
; and expects the PyInstaller one-folder output at dist\BuildASpec\.
;
; Produces dist\installer\BuildASpecSetup.exe — a normal double-click
; installer with a Start-menu shortcut, optional desktop icon, and a clean
; uninstaller. The app is NOT code-signed, so Windows SmartScreen shows a
; "Windows protected your PC" notice on first run (More info -> Run anyway);
; expected and documented in docs/RELEASE_WINDOWS.md.

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

#define MyAppName "Build-a-Spec"
#define MyAppPublisher "Abraham Borg"
#define MyAppExeName "BuildASpec.exe"
#define MyAppURL "https://github.com/Abe-Borg/build-a-spec"

[Setup]
; A stable AppId ties every version together so an install upgrades in
; place instead of stacking side-by-side. Unique to Build-a-Spec — NOT
; shared with Spec Critic or any sibling app. Do NOT change across releases.
AppId={{89E58C42-A4F6-49F8-8FCB-1147CB0186DB}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}/releases/latest
DefaultDirName={autopf}\Build-a-Spec
DefaultGroupName=Build-a-Spec
DisableProgramGroupPage=yes
; Per-user install: no admin/UAC prompt and no install-mode dialog, keeping
; the unsigned experience as smooth as possible. Power users can still pass
; /ALLUSERS on the command line for a machine-wide install.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline
OutputDir=..\..\dist\installer
OutputBaseFilename=BuildASpecSetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
; Let an in-place update replace the running app: Inno detects a running
; instance and offers to close it. Pairs with the in-app updater, which
; exits the app before launching this installer.
CloseApplications=yes
RestartApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; The entire PyInstaller one-folder output.
Source: "..\..\dist\BuildASpec\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
