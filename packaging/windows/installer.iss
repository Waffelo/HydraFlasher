; Inno Setup script for the HydraFlasher Windows installer.
; Built by packaging/build.py, which passes AppVersion, SourceDir (the PyInstaller
; folder build), OutputDir, IconFile and LicenseFile with /D.

[Setup]
; Keep this AppId: it lets a new installer find and upgrade an existing install
AppId={{A32496EC-416C-4915-8508-3012BA83473A}
AppName=HydraFlasher
AppVersion={#AppVersion}
AppVerName=HydraFlasher {#AppVersion}
AppPublisher=Waffelo
AppPublisherURL=https://github.com/Waffelo/HydraFlasher
AppSupportURL=https://github.com/Waffelo/HydraFlasher/wiki
AppUpdatesURL=https://github.com/Waffelo/HydraFlasher/releases
DefaultDirName={autopf}\HydraFlasher
DefaultGroupName=HydraFlasher
DisableProgramGroupPage=yes
; Install for everyone (admin) or just this user, the user picks
PrivilegesRequiredOverridesAllowed=dialog
UninstallDisplayIcon={app}\HydraFlasher.exe
SetupIconFile={#IconFile}
LicenseFile={#LicenseFile}
OutputDir={#OutputDir}
OutputBaseFilename=HydraFlasher-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\HydraFlasher"; Filename: "{app}\HydraFlasher.exe"
Name: "{group}\{cm:UninstallProgram,HydraFlasher}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\HydraFlasher"; Filename: "{app}\HydraFlasher.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\HydraFlasher.exe"; Description: "{cm:LaunchProgram,HydraFlasher}"; Flags: nowait postinstall skipifsilent
