@echo off
setlocal EnableExtensions
title Install Windows 7 SP1 (required by WebView2)

rem Windows 7 build 7600 (RTM) is below the minimum platform required by
rem WebView2 109. This installs KB976932 once and reboots once.

set "MIRROR=http://192.168.1.32:3001/updates"
set "LOG=%USERPROFILE%\Desktop\win7-sp1-log.txt"

net session >nul 2>&1
if errorlevel 1 (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

call :run > "%LOG%" 2>&1
type "%LOG%"
echo.
echo Saved to: %LOG%
pause
exit /b 0

:run
echo === Windows 7 SP1 installer ===
ver
echo.

reg query "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion" /v CurrentBuild 2>nul
reg query "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion" /v CSDVersion 2>nul

reg query "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion" /v CSDVersion 2>nul | find /I "Service Pack 1" >nul
if not errorlevel 1 (
  echo.
  echo SP1 is already installed. Nothing to do.
  exit /b 0
)

echo.
if exist "%TEMP%\Windows6.1-KB976932-X86.exe" (
  echo Reusing the file downloaded on the previous attempt.
) else (
  echo Downloading SP1 from the local mirror (538 MB)...
  powershell -NoProfile -Command "(New-Object Net.WebClient).DownloadFile('%MIRROR%/Windows6.1-KB976932-X86.exe', '%TEMP%\Windows6.1-KB976932-X86.exe')"
)
if not exist "%TEMP%\Windows6.1-KB976932-X86.exe" (
  echo ERROR: download failed.
  exit /b 1
)

echo Verifying checksum...
set "HASH="
for /f "tokens=*" %%H in ('certutil -hashfile "%TEMP%\Windows6.1-KB976932-X86.exe" SHA256 2^>nul ^| find /V "hash" ^| find /V "SHA256"') do set "HASH=%%H"
set "HASH=%HASH: =%"
echo   %HASH%
if /I not "%HASH%"=="e5449839955a22fc4dd596291a" (
  echo ERROR: checksum mismatch - aborting.
  exit /b 1
)
echo OK.

echo.
echo Installing SP1 quietly. This takes several minutes - do not power off.
"%TEMP%\Windows6.1-KB976932-X86.exe" /quiet /norestart
echo Installer exit code: %ERRORLEVEL%

echo.
echo Done. RESTART WINDOWS once now.
echo After the reboot, start MaxCall again - no other steps are needed.
exit /b 0
