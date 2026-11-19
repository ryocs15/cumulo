@echo off
rem Cumulo (WPF) derlemesi: build\publish icine kendi .NET calisma ortamiyla (self-contained)
rem kullanicida .NET kurulu olmasi gerekmez; sessiz guncellemeden sonra widget calismaz hale gelmez.
rem Kurulum dosyasi: iscc /DAppVer=1.7.0 installer.iss  -> build\release\Cumulo-Setup.exe
cd /d "%~dp0"
rem .NET 10 SDK (LTS): kullanici klasorundeki kurulum varsa o, yoksa PATH'teki dotnet
set DOTNET=dotnet
if exist "%LOCALAPPDATA%\dotnet10\dotnet.exe" set DOTNET="%LOCALAPPDATA%\dotnet10\dotnet.exe"
set PY=python
if exist "%LOCALAPPDATA%\Programs\Python\Python315\python.exe" set PY="%LOCALAPPDATA%\Programs\Python\Python315\python.exe"
rem Ingilizce sozluk Python surumunun lang_en.py dosyasindan uretilir; o klasor yoksa depodaki lang_en.json kullanilir
if exist "..\hava_durumu_widget\lang_en.py" %PY% tools\export_lang.py || goto :err
%PY% tools\test_lang.py || goto :err
if exist build\publish rmdir /s /q build\publish
%DOTNET% publish HavaDurumu.csproj -c Release -r win-x64 --self-contained true -o build\publish || goto :err
build\publish\HavaDurumu.exe --selftest || goto :err
echo.
echo Hazir: %~dp0build\publish\HavaDurumu.exe
goto :eof
:err
echo Derleme basarisiz.
exit /b 1
