@echo off
cd /d "%~dp0"
where pythonw.exe >nul 2>nul
if errorlevel 1 goto console
start "" pythonw.exe "%~dp0launch.pyw"
exit /b
:console
py -3 "%~dp0app.py"
pause
