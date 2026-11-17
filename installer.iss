; Kurulum dosyası: iscc /DAppVer=1.2.3 installer.iss  (release.py çağırır)
; Kullanıcı klasörüne kurar: config.json exe'nin yanında durduğu ve uygulama içi güncelleme
; dosyaları değiştirdiği için Program Files (yönetici izni) kullanılamaz.
#ifndef AppVer
  #define AppVer "0.0.0"
#endif

[Setup]
AppId={{6F3B2C1E-8D4A-4E5B-9C7F-2A1D0E9B8C34}
AppName=Cumulo
AppVersion={#AppVer}
AppPublisher=ryocs15
AppPublisherURL=https://github.com/ryocs15/hava-durumu-widget
DefaultDirName={localappdata}\Programs\HavaDurumu
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=build\release
OutputBaseFilename=HavaDurumu-Kurulum
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\HavaDurumu.exe
UninstallDisplayName=Cumulo
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
ShowLanguageDialog=auto

[Languages]
; Windows diline göre seçilir; eşleşmezse ilk dil (İngilizce)
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "tr"; MessagesFile: "compiler:Languages\Turkish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; eski sürümden kalan kitaplıklar karışmasın
Type: filesandordirs; Name: "{app}\_internal"
; uygulamanın eski adı (1.4.1 ve öncesi)
Type: files; Name: "{userprograms}\Hava Durumu.lnk"
Type: files; Name: "{userdesktop}\Hava Durumu.lnk"

[Files]
Source: "build\onedir\HavaDurumu\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; ad ve kimlik uygulamanın kendi oluşturduğu kısayolla aynı (bildirim başlığı için gerekli)
Name: "{userprograms}\Cumulo"; Filename: "{app}\HavaDurumu.exe"; AppUserModelID: "HavaDurumu.Widget"
Name: "{userdesktop}\Cumulo"; Filename: "{app}\HavaDurumu.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\HavaDurumu.exe"; Description: "{cm:LaunchProgram,Cumulo}"; Flags: nowait postinstall skipifsilent
; uygulama içi güncelleme sessiz kurar: widget'ı yeniden başlat
Filename: "{app}\HavaDurumu.exe"; Flags: nowait; Check: WizardSilent

[UninstallDelete]
Type: files; Name: "{app}\config*.json"
Type: files; Name: "{app}\cache*.json"
Type: files; Name: "{app}\events*.json"
Type: files; Name: "{app}\*.log"
Type: files; Name: "{app}\*.old"
Type: filesandordirs; Name: "{app}\_internal.old"
Type: filesandordirs; Name: "{app}\_update"
Type: files; Name: "{app}\_update.zip"
Type: files; Name: "{userstartup}\HavaDurumuWidget.vbs"
Type: dirifempty; Name: "{app}"
