@echo off
setlocal EnableExtensions
title Restart MaxCall diagnostic agent

net session >nul 2>&1
if errorlevel 1 (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

echo Restarting diagnostic agent...
schtasks /End /TN MaxCallDiag >nul 2>&1
taskkill /F /IM powershell.exe >nul 2>&1
copy /Y "%~dp0win7-agent.ps1" "C:\maxcall\win7-agent.ps1" >nul
schtasks /Run /TN MaxCallDiag >nul 2>&1
echo OK.
echo.
pause
