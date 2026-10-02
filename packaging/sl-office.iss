; Inno-Setup-Skript für das Windows-Setup von SL-Office.
;
;     iscc /DAppVersion=1.0.0 packaging\sl-office.iss
;
; Erwartet den fertigen Ordner dist\SL-Office aus packaging\sl-office.spec.
; Installiert für die angemeldete Person (keine Administratorrechte nötig);
; die Daten liegen getrennt davon in %APPDATA%\SL-Office und bleiben bei
; einer Deinstallation oder einem Update erhalten.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6C1C5B8E-4A1F-4E5B-9D0B-5F1E7A2C9B31}
AppName=SL-Office
AppVersion={#AppVersion}
AppPublisher=SL-Office
DefaultDirName={localappdata}\Programs\SL-Office
DefaultGroupName=SL-Office
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=SL-Office-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\SL-Office.exe

[Languages]
Name: "german"; MessagesFile: "compiler:Languages\German.isl"

[Tasks]
Name: "desktopicon"; Description: "Verknüpfung auf dem Desktop anlegen"; GroupDescription: "Zusätzlich:"

[Files]
Source: "..\dist\SL-Office\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\SL-Office"; Filename: "{app}\SL-Office.exe"
Name: "{group}\Datenordner von SL-Office"; Filename: "{userappdata}\SL-Office"
Name: "{userdesktop}\SL-Office"; Filename: "{app}\SL-Office.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\SL-Office.exe"; Description: "SL-Office jetzt starten"; Flags: nowait postinstall skipifsilent
