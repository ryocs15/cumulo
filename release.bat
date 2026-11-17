@echo off
rem Yeni surum yayinlar. Ornek:  release.bat 1.1.0 "Neler degisti"
rem Once denemek icin:          release.bat 1.1.0 "..." --dry-run
cd /d "%~dp0"
python release.py %*
