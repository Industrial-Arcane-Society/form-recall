; Compile through scripts/build-native-installer.ps1 after package verification.
#ifndef PackageDirectory
  #error PackageDirectory is required
#endif
#ifndef ReleaseVersion
  #error ReleaseVersion is required
#endif
#ifndef WindowsVersion
  #error WindowsVersion is required
#endif
#ifndef InstallerOutputDirectory
  #error InstallerOutputDirectory is required
#endif

[Setup]
AppId={{A7B8F0C2-7D53-4E39-964A-16281F85C922}
AppName=FormRecall
AppVersion={#ReleaseVersion}
AppVerName=FormRecall {#ReleaseVersion}
AppPublisher=FormRecall
DefaultDirName={localappdata}\Programs\FormRecall
DefaultGroupName=FormRecall
DisableDirPage=no
DisableProgramGroupPage=yes
UsePreviousAppDir=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#InstallerOutputDirectory}
OutputBaseFilename=FormRecall-{#ReleaseVersion}-windows-x64-setup
VersionInfoVersion={#WindowsVersion}
VersionInfoProductVersion={#WindowsVersion}
VersionInfoTextVersion={#WindowsVersion}
VersionInfoProductTextVersion={#ReleaseVersion}
LicenseFile={#PackageDirectory}\LICENSE
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
UninstallDisplayIcon={app}\FormRecall.exe
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
LanguageDetectionMethod=uilanguage
ShowLanguageDialog=yes

[Languages]
Name: "chinesesimplified"; MessagesFile: "languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#PackageDirectory}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\FormRecall"; Filename: "{app}\FormRecall.exe"; WorkingDir: "{app}"; AppUserModelID: "FormRecall.Desktop"
Name: "{autodesktop}\FormRecall"; Filename: "{app}\FormRecall.exe"; WorkingDir: "{app}"; AppUserModelID: "FormRecall.Desktop"; Tasks: desktopicon

[Run]
Filename: "{app}\FormRecall.exe"; WorkingDir: "{app}"; Description: "{cm:LaunchProgram,FormRecall}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\.formrecall-updates"
