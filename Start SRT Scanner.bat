@echo off
rem Double-click to start SRT Scanner. Needs Python 3 from python.org (or the Microsoft Store).
cd /d "%~dp0"
if not exist "srt_scanner.py" (
  echo This launcher has to stay in the SRT Scanner folder, next to srt_scanner.py
  echo and the app, engine and vendor folders. Download the whole repository as a ZIP,
  echo right-click it, choose Extract All, and run this file from the extracted folder.
  pause
  exit /b 1
)
where py >nul 2>nul
if %errorlevel%==0 (py -3 srt_scanner.py %*) else (python srt_scanner.py %*)
if errorlevel 1 pause
