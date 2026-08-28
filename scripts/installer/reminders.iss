; Inno Setup script for Reminders for Windows.
;
; The app is built unpackaged and self-contained (WindowsPackageType=None,
; WindowsAppSDKSelfContained=true), so installing is genuinely just laying down
; the publish output plus shortcuts and an uninstaller. There is no runtime to
; chain in and no Windows App SDK dependency to install first.
;
; Driven by scripts/build-installer.ps1, which supplies AppVersion, Arch,
; SourceDir, OutputDir and IconFile. Do not run ISCC against this file directly.

#ifndef AppVersion
  #error AppVersion must be supplied by build-installer.ps1
#endif
#ifndef Arch
  #error Arch must be supplied by build-installer.ps1
#endif
#ifndef SourceDir
  #error SourceDir must be supplied by build-installer.ps1
#endif
#ifndef OutputDir
  #error OutputDir must be supplied by build-installer.ps1
#endif
#ifndef IconFile
  #error IconFile must be supplied by build-installer.ps1
#endif

[Setup]
; A stable GUID keeps upgrades in place instead of stacking side-by-side
; entries in Apps & Features. Never change it for this product.
AppId={{7C4E1E2A-9F3B-4D5C-8A61-2B0E7F9C4A13}
AppName=Reminders for Windows
AppVersion={#AppVersion}
AppVerName=Reminders for Windows {#AppVersion}
AppPublisher=paulsavvas.com
AppPublisherURL=https://paulsavvas.com
AppSupportURL=https://github.com/Psavvas/iCloud-Reminders-for-Windows/issues
AppUpdatesURL=https://github.com/Psavvas/iCloud-Reminders-for-Windows/releases
VersionInfoVersion={#AppVersion}

; Per-user install: no UAC prompt, and it matches where the app already keeps
; its data (%LOCALAPPDATA%\RemindersSync). A machine-wide install would need
; admin for no benefit, since nothing here is shared between users.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={autopf}\Reminders for Windows
DefaultGroupName=Reminders for Windows
DisableProgramGroupPage=yes
AllowNoIcons=yes

OutputDir={#OutputDir}
OutputBaseFilename=Reminders-for-Windows-{#Arch}-Setup
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\Reminders.exe
UninstallDisplayName=Reminders for Windows

; The sidecar holds the SQLite cache open, so an upgrade over a running install
; would otherwise fail on locked binaries. Restart Manager asks the running
; instance to close instead, which needs no cooperation from the app itself.
CloseApplications=yes
CloseApplicationsFilter=Reminders.exe,reminders-sidecar.exe
RestartApplications=no

Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; The WinUI runtime ships inside the publish output, so the payload is large;
; solid LZMA2 keeps the download reasonable.

#if Arch == "arm64"
ArchitecturesAllowed=arm64
ArchitecturesInstallIn64BitMode=arm64
#else
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
#endif

; Windows 10 1809, matching TargetPlatformMinVersion in the csproj.
MinVersion=10.0.17763

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startup"; Description: "Start Reminders when I sign in"; GroupDescription: "Startup"; Flags: unchecked

[Files]
; Everything the publish step produced, including reminders-sidecar.exe. The
; app locates the sidecar beside its own executable, so the two must stay
; together in one directory.
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Reminders for Windows"; Filename: "{app}\Reminders.exe"
Name: "{group}\{cm:UninstallProgram,Reminders for Windows}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Reminders for Windows"; Filename: "{app}\Reminders.exe"; Tasks: desktopicon
Name: "{userstartup}\Reminders for Windows"; Filename: "{app}\Reminders.exe"; Tasks: startup

[Run]
Filename: "{app}\Reminders.exe"; Description: "{cm:LaunchProgram,Reminders for Windows}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Log files the app writes next to its data. Reminder content, settings and the
; sync cache are deliberately left in place so reinstalling does not lose them;
; credentials live in Windows Credential Manager and are never touched here.
Type: filesandordirs; Name: "{localappdata}\RemindersSync\logs"
