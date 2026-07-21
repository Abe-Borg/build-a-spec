; Inno Setup script for Build-a-Spec (Windows desktop app).
; Cloned from Claude-Spec-Critic packaging/windows/installer.iss.
;
; Compile with:
;   ISCC /DMyAppVersion=0.9.0 packaging\windows\installer.iss
; and expects the PyInstaller one-folder output at dist\BuildASpec\.
;
; Produces dist\installer\BuildASpecSetup.exe — a normal double-click
; installer with a Start-menu shortcut, optional desktop icon, and a clean
; uninstaller. The app is NOT code-signed, so Windows SmartScreen shows a
; "Windows protected your PC" notice on first run (More info -> Run anyway);
; expected and documented in docs/RELEASE_WINDOWS.md.
;
; If packaging\windows\MicrosoftEdgeWebview2Setup.exe is present at compile
; time (the release workflow downloads it), the installer bundles the Edge
; WebView2 Evergreen bootstrapper and runs it silently on machines that
; don't already have the runtime — so the app's native window works on a
; clean Windows install with no other tooling. The check is a no-op on
; current Windows 10/11, which ship the runtime.

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

#define MyAppName "Build-a-Spec"
#define MyAppPublisher "Abraham Borg"
#define MyAppExeName "BuildASpec.exe"
#define MyAppURL "https://github.com/Abe-Borg/build-a-spec"

; Bundle the WebView2 bootstrapper only if it was fetched next to this
; script (release workflow does this). Manual builds without it still
; compile — the app degrades to a browser window if WebView2 is missing.
#define WebView2Bootstrapper "MicrosoftEdgeWebview2Setup.exe"
#if FileExists(SourcePath + WebView2Bootstrapper)
  #define HaveWebView2
#endif

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
SetupIconFile=assets\BuildASpec.ico
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
#ifdef HaveWebView2
; Edge WebView2 Evergreen bootstrapper — extracted to {tmp} only when the
; runtime is missing, then removed after the install.
Source: "{#WebView2Bootstrapper}"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: WebView2Needed
#endif

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
#ifdef HaveWebView2
; Install the WebView2 runtime silently, per-user (no admin), before the
; app launches. Runs only when it isn't already present. Non-fatal: if it
; fails (e.g. no network), the install still completes and the app opens a
; browser window instead of the native WebView2 window.
Filename: "{tmp}\{#WebView2Bootstrapper}"; Parameters: "/silent /install"; StatusMsg: "Installing Microsoft Edge WebView2 runtime..."; Check: WebView2Needed; Flags: waituntilterminated
#endif
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
{ Detect the Edge WebView2 Evergreen Runtime via the EdgeUpdate client key.
  Per-machine installs register under HKLM\...\WOW6432Node (64-bit Windows);
  per-user installs register under HKCU. A non-empty 'pv' that isn't
  0.0.0.0 means the runtime is present. }
function IsWebView2RuntimeInstalled: Boolean;
var
  pv: String;
begin
  Result :=
    (RegQueryStringValue(HKEY_LOCAL_MACHINE,
       'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}',
       'pv', pv) and (pv <> '') and (pv <> '0.0.0.0'))
    or
    (RegQueryStringValue(HKEY_LOCAL_MACHINE,
       'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}',
       'pv', pv) and (pv <> '') and (pv <> '0.0.0.0'))
    or
    (RegQueryStringValue(HKEY_CURRENT_USER,
       'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}',
       'pv', pv) and (pv <> '') and (pv <> '0.0.0.0'));
end;

{ Check used by the WebView2 [Files]/[Run] entries. }
function WebView2Needed: Boolean;
begin
  Result := not IsWebView2RuntimeInstalled;
end;
