; Installateur Ruche — Inno Setup 6
;
; Compilation :
;   "C:\Users\<vous>\AppData\Local\Programs\Inno Setup 6\ISCC.exe" installer\ruche.iss
;
; Définitions surchargeables :
;   /DAppVersion=0.1.0  /DPublisher=Boyz

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#ifndef Publisher
  #define Publisher "Boyz"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\Ruche"
#endif

#define AppName "Ruche"
#define AppExe "Ruche.exe"
#define AppId "{{8F6C2A54-3D71-4B9E-9A2F-5C1E7B4D0A33}"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#Publisher}
AppPublisherURL=https://github.com/VOTRE_COMPTE/ruche
AppSupportURL=https://github.com/VOTRE_COMPTE/ruche/issues
AppUpdatesURL=https://github.com/VOTRE_COMPTE/ruche/releases
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
OutputDir=Output
OutputBaseFilename=Ruche-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SetupLogging=yes

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
