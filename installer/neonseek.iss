; inno setup 6 script of the NeonSeek installer (PLAN.md phase 4, D-218).
; scripts\build.ps1 compiles it with the version, the folder of the built program and the
; address of the source code, so none of them is written here:
;   ISCC /DAppVersion=0.1.0 /DSourceDir=<dist\NeonSeek> /DSourceUrl=<url> /O<out> neonseek.iss
; per user, without administrator rights, into %LOCALAPPDATA%\Programs\NeonSeek.
; uninstalling removes the program only: %APPDATA%\NeonSeek and the music library stay.

#ifndef AppVersion
  #error Pass the version: ISCC /DAppVersion=x.y.z (scripts\build.ps1 does it)
#endif
#ifndef SourceDir
  #error Pass the built program folder: ISCC /DSourceDir=<dist\NeonSeek>
#endif
#ifndef SourceUrl
  #error Pass the address of the source code: ISCC /DSourceUrl=<url>
#endif

[Setup]
; the same id in every version, so a new version installs over the old one
AppId={{6F3C1B52-8E0D-4C7A-9B1E-2D4F5A6C7E81}
AppName=NeonSeek
AppVersion={#AppVersion}
AppVerName=NeonSeek {#AppVersion}
AppPublisher=NeonSeek
#if Pos("http", SourceUrl) == 1
AppPublisherURL={#SourceUrl}
AppSupportURL={#SourceUrl}
#endif
VersionInfoVersion={#AppVersion}
VersionInfoProductName=NeonSeek
VersionInfoDescription=NeonSeek setup
PrivilegesRequired=lowest
DefaultDirName={autopf}\NeonSeek
DefaultGroupName=NeonSeek
DisableProgramGroupPage=yes
; the gpl needs no acceptance (section 9), so the license is an information page
InfoBeforeFile={#SourceDir}\LICENSE.txt
OutputBaseFilename=NeonSeek-Setup-{#AppVersion}
SetupIconFile=neonseek.ico
UninstallDisplayIcon={app}\NeonSeek.exe
UninstallDisplayName=NeonSeek
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
CloseApplications=yes
ShowLanguageDialog=auto
LanguageDetectionMethod=uilanguage

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "pl"; MessagesFile: "compiler:Languages\Polish.isl"

[Messages]
; the header has room for two lines, the label above the license text grows with its text
en.WizardInfoBefore=License
en.InfoBeforeLabel=NeonSeek is free software under the GNU General Public License, version 3 or later. You do not have to accept it to use NeonSeek.
en.InfoBeforeClickLabel=The license gives you the right to share and change NeonSeek. Source code of this version: {#SourceUrl}%n%nWhen you are ready to continue, click Next.
pl.WizardInfoBefore=Licencja
pl.InfoBeforeLabel=NeonSeek jest wolnym oprogramowaniem na licencji GNU General Public License, wersja 3 lub późniejsza. Nie musisz jej akceptować, żeby używać NeonSeek.
pl.InfoBeforeClickLabel=Licencja daje Ci prawo do udostępniania i zmieniania NeonSeek. Kod źródłowy tej wersji: {#SourceUrl}%n%nGdy zechcesz kontynuować, kliknij Dalej.

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; program files of the previous version, so no old library stays behind. never user data
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\manual"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\NeonSeek"; Filename: "{app}\NeonSeek.exe"
Name: "{autodesktop}\NeonSeek"; Filename: "{app}\NeonSeek.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\NeonSeek.exe"; Description: "{cm:LaunchProgram,NeonSeek}"; Flags: nowait postinstall skipifsilent
