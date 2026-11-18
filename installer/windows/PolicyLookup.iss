; Windows installer for Policy Lookup (Inno Setup 6). Built by GitHub Actions:
;   iscc /DAppVersion=1.0.0 installer\windows\PolicyLookup.iss
; Installs for the current user only (no admin rights needed), adds a
; Start menu + desktop shortcut, and starts the app when you click Finish.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{6F3C2B1E-6C55-4E0A-9C7B-5B6E9B2F7A11}
AppName=Policy Lookup
AppVersion={#AppVersion}
AppPublisher=Policy Lookup
DefaultDirName={localappdata}\Programs\Policy Lookup
DefaultGroupName=Policy Lookup
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\..\build\installers
OutputBaseFilename=PolicyLookup-Setup-Windows
SetupIconFile=..\..\build\icons\icon.ico
UninstallDisplayIcon={app}\app\icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "..\..\build\PolicyLookup\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "..\..\build\icons\icon.ico"; DestDir: "{app}\app"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Policy Lookup"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\app\desktop_launcher.py"""; WorkingDir: "{app}\app"; IconFilename: "{app}\app\icon.ico"
Name: "{autodesktop}\Policy Lookup"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\app\desktop_launcher.py"""; WorkingDir: "{app}\app"; IconFilename: "{app}\app\icon.ico"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\app\desktop_launcher.py"""; WorkingDir: "{app}\app"; Description: "Open Policy Lookup now"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
; Program files only. Your documents and index in %LOCALAPPDATA%\PolicyLookup are kept.
Type: filesandordirs; Name: "{app}\python\Lib\site-packages\__pycache__"
