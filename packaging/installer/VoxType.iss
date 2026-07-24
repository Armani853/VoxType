#ifndef RepoRoot
  #error RepoRoot must be passed by build_installer.ps1
#endif
#ifndef InstallerOutput
  #error InstallerOutput must be passed by build_installer.ps1
#endif

[Setup]
AppId={{5A4A7C70-50ED-4F41-90A6-48C69682B1A7}
AppName=VoxType
AppVersion=0.1.0
AppPublisher=VoxType
DefaultDirName={localappdata}\Programs\VoxType
DefaultGroupName=VoxType
DisableProgramGroupPage=yes
OutputDir={#InstallerOutput}
OutputBaseFilename=VoxType-0.1.0-win64-setup
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\VoxType.exe
WizardStyle=modern

[Files]
Source: "{#RepoRoot}\dist\VoxType\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\VoxType"; Filename: "{app}\VoxType.exe"
Name: "{autodesktop}\VoxType"; Filename: "{app}\VoxType.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Ярлыки:"; Flags: unchecked

[Run]
Filename: "{app}\VoxType.exe"; Description: "Запустить VoxType"; Flags: nowait postinstall skipifsilent
