#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

#ifndef VersionInfoVersion
  #define VersionInfoVersion "0.1.0.0"
#endif

[Setup]
AppId={{D97DB0C6-6B1A-49E7-B472-16F21F7FA4C8}
AppName=FLEX
AppVersion={#AppVersion}
AppVerName=FLEX {#AppVersion}
AppPublisher=Ayman Khan
AppPublisherURL=https://github.com/AYMAN-KHAN-000
AppSupportURL=https://github.com/AYMAN-KHAN-000/Flex1
AppUpdatesURL=https://github.com/AYMAN-KHAN-000/Flex1/releases
DefaultDirName={autopf}\FLEX
DefaultGroupName=FLEX
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
WizardStyle=modern
SetupIconFile=..\flex\ui\assets\flex_icon.ico
UninstallDisplayIcon={app}\FLEX.exe
OutputDir=..\dist\installer
OutputBaseFilename=FLEX-Setup-{#AppVersion}
VersionInfoVersion={#VersionInfoVersion}
VersionInfoCompany=Ayman Khan
VersionInfoDescription=FLEX Windows desktop assistant setup
VersionInfoProductName=FLEX
VersionInfoProductVersion={#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
CloseApplications=yes
RestartApplications=no
UninstallRestartComputer=no

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "..\dist\FLEX\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\FLEX"; Filename: "{app}\FLEX.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\FLEX"; Filename: "{app}\FLEX.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\FLEX.exe"; Description: "Launch FLEX"; Flags: postinstall nowait skipifsilent

; FLEX user data lives outside {app}; do not add uninstall deletion rules for it.
