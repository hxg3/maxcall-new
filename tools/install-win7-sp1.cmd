@echo off
setlocal EnableExtensions
title Install Windows 7 SP1

rem Windows 7 build 7600 (RTM) is below the minimum platform required by
rem WebView2 109. This installs KB976932 once and reboots once.

set "MIRROR=http://192.168.1.32:3001/updates"
set "SP1=%TEMP%\Windows6.1-KB976932-X86.exe"
set "LOG=%USERPROFILE%\Desktop\win7-sp1-log.txt"

net session >nul 2>&1
if errorlevel 1 goto :elevate
goto :start

:elevate
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
exit /b

:start
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
if errorlevel 1 goto :needsp1
echo.
echo SP1 is already installed. Nothing to do.
exit /b 0

:needsp1
echo.
if exist "%SP1%" goto :havefile
echo Downloading SP1 from the local mirror (538 MB)...
powershell -NoProfile -Command "(New-Object Net.WebClient).DownloadFile('http://192.168.1.32:3001/updates/Windows6.1-KB976932-X86.exe','%TEMP%\Windows6.1-KB976932-X86.exe')"
if exist "%SP1%" goto :havefile
echo ERROR: download failed.
exit /b 1

:havefile
echo Using: %SP1%
certutil -hashfile "%SP1%" SHA256 > "%TEMP%\sp1hash.txt" 2>nul
set "HASH="
for /f "skip=1 tokens=*" %%H in ('type "%TEMP%\sp1hash.txt"') do if not defined HASH set "HASH=%%H"
set "HASH=%HASH: =%"
echo   %HASH%
if /I not "%HASH%"=="e5449839955a22fc4dd596291aff1433b998f9797e1c784232226aba1f8abd97" goto :badhash
echo Checksum OK.

echo.
echo Installing SP1 quietly. This takes several minutes - do not power off.
"%SP1%" /quiet /norestart
echo Installer exit code: %ERRORLEVEL%
echo.
echo Done. RESTART WINDOWS once now.
echo After the reboot, start MaxCall directly - no other steps are needed.
exit /b 0

:badhash
echo ERROR: checksum mismatch - aborting.
exit /b 1
