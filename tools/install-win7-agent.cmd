@echo off
setlocal EnableExtensions
title Install MaxCall diagnostic agent

net session >nul 2>&1
if errorlevel 1 (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

echo Installing diagnostic agent...
copy /Y "%~dp0win7-agent.ps1" "C:\maxcall\win7-agent.ps1" >nul
if errorlevel 1 (
  echo ERROR: C:\maxcall not found.
  pause
  exit /b 1
)

schtasks /Create /TN MaxCallDiag /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\maxcall\win7-agent.ps1" /SC ONLOGON /RL HIGHEST /F
schtasks /Run /TN MaxCallDiag

echo.
echo Agent installed and running. It only polls the internal dashboard.
echo To remove it later: schtasks /Delete /TN MaxCallDiag /F
echo.
pause
