; Jarvis Guard: a Windows Setup.exe around the agent and its setup script.
;
; The agent's own install.ps1 does the real work (registers the PC with the hub
; as a child's machine, mints the device key, copies the guard to Program Files,
; registers the SYSTEM startup task and the tray shield). This installer only
; puts the files in place and then runs that script, elevated, at the end - so a
; parent downloads one file instead of unpacking a zip and running PowerShell by
; hand.
;
; Built by .github/workflows/windows-build.yml:
;   iscc /DAppVersion=1.8.0 /DSourceDir=D:\a\...\dist jarvis-guard.iss

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#ifndef SourceDir
  #define SourceDir "..\dist"
#endif

[Setup]
AppId={{7F1C2B44-3D8E-4E1B-9A6C-5B2D4E8F1A73}
AppName=Jarvis Guard
AppVersion={#AppVersion}
AppVerName=Jarvis Guard {#AppVersion}
AppPublisher=Jarvis
DefaultDirName={autopf}\Jarvis Guard
DefaultGroupName=Jarvis Guard
DisableProgramGroupPage=yes
PrivilegesRequired=admin
OutputDir=output
OutputBaseFilename=JarvisGuard-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\jarvis-guard.exe
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "{#SourceDir}\jarvis-guard.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\install.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\uninstall.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Jarvis Guard"; Filename: "{app}\jarvis-guard.exe"; Parameters: "--tray"
Name: "{group}\Uninstall Jarvis Guard"; Filename: "{uninstallexe}"

[Run]
; install.ps1 is interactive on purpose: it asks for a parent's hub sign-in so the
; admin key never has to be written anywhere, then hands the device its own key.
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\install.ps1"""; Description: "Set this PC up for the family now (asks for a parent's Jarvis sign-in)"; Flags: postinstall waituntilterminated
Filename: "{app}\README.md"; Description: "Read what this installs and how to undo it"; Flags: postinstall shellexec unchecked

[UninstallRun]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\uninstall.ps1"""; Flags: runhidden waituntilterminated
