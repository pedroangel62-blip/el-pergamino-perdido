@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0detener-pergamino.ps1"
pause
endlocal
