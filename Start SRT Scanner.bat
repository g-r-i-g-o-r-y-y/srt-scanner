@echo off
rem Double-click to start SRT Scanner. Needs Python 3 from python.org (or the Microsoft Store).
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (py -3 srt_scanner.py %*) else (python srt_scanner.py %*)
if errorlevel 1 pause
