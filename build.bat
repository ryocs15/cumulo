@echo off
rem HavaDurumu'yu klasor kipinde derler: dist\HavaDurumu.exe + dist\_internal
rem (tek dosyaya gore yaklasik iki kat hizli acilir, tek surec calisir).
rem dist icindeki ayar / onbellek / gunluk dosyalarina dokunulmaz.
rem Calisan widget varsa once kapatin (dosyalar kilitli olur).
cd /d "%~dp0"
python make_icon.py || goto :err
python -m PyInstaller --noconfirm --clean --onedir --windowed ^
  --name HavaDurumu --icon icon.ico ^
  --exclude-module numpy --exclude-module matplotlib ^
  --collect-submodules winrt --collect-binaries winrt ^
  --distpath build\onedir --workpath build\work_onedir ^
  hava_widget.py || goto :err
if not exist dist mkdir dist
if exist dist\_internal rmdir /s /q dist\_internal
robocopy build\onedir\HavaDurumu dist /E /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 goto :err
echo.
echo Hazir: %~dp0dist\HavaDurumu.exe
goto :eof
:err
echo Derleme basarisiz.
exit /b 1
