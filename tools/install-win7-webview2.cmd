@echo off
setlocal EnableExtensions
title MaxCall - WebView2 109 setup for Windows 7

rem Usage: double-click this file (the .cab must be next to it),
rem        or drag the .cab file onto this script.

set "ARG=%~1"
set "CAB=%~1"
if not defined CAB set "CAB=%~dp0Microsoft.WebView2.FixedVersionRuntime.109.0.1518.78.x86.cab"
set "DEST=C:\WebView2-109"
set "KEY=HKLM\SOFTWARE\Policies\Microsoft\Edge\WebView2\BrowserExecutableFolder"
set "KEY32=HKLM\SOFTWARE\Wow6432Node\Policies\Microsoft\Edge\WebView2\BrowserExecutableFolder"
set "HOSTS=%WINDIR%\System32\drivers\etc\hosts"
set "RUNTIME_DIR="

net session >nul 2>&1
if errorlevel 1 (
  echo.
  echo Administrator rights are required. Restarting elevated...
  if defined ARG (
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '\"%ARG%\"' -Verb RunAs"
  ) else (
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  )
  exit /b
)

echo.
echo [1/5] Adding maxcall.local to the Windows hosts file...
attrib -r "%HOSTS%" >nul 2>&1
findstr /C:"maxcall.local" "%HOSTS%" >nul 2>&1
if errorlevel 1 (
  echo.>> "%HOSTS%"
  echo 192.168.1.32    maxcall.local>> "%HOSTS%"
  if errorlevel 1 (
    echo ERROR: cannot write "%HOSTS%". Check antivirus / file permissions.
    echo.
    pause
    exit /b 1
  )
  echo OK: entry added.
) else (
  echo OK: entry already present.
)
ipconfig /flushdns >nul 2>&1

echo.
echo [2/5] Checking cab file...
if not exist "%CAB%" (
  echo ERROR: not found: "%CAB%"
  echo Download the x86 cab and place it next to this script,
  echo or drag the cab file onto this script.
  echo.
  pause
  exit /b 1
)
echo OK: "%CAB%"

echo.
echo [3/5] Extracting / locating the runtime...
for /f "delims=" %%F in ('dir /s /b "%DEST%\msedgewebview2.exe" 2^>nul') do set "RUNTIME_DIR=%%~dpF"
if not defined RUNTIME_DIR (
  if not exist "%DEST%" mkdir "%DEST%" >nul
  expand "%CAB%" -F:* "%DEST%" >nul
  for /f "delims=" %%F in ('dir /s /b "%DEST%\msedgewebview2.exe" 2^>nul') do set "RUNTIME_DIR=%%~dpF"
)
if not defined RUNTIME_DIR (
  echo ERROR: msedgewebview2.exe was not found after extraction.
  echo Try manually:
  echo   expand "%CAB%" -F:* "%DEST%"
  echo.
  pause
  exit /b 1
)
set "RUNTIME_DIR=%RUNTIME_DIR:~0,-1%"
echo OK: %RUNTIME_DIR%

echo.
echo [4/5] Setting the WebView2 policy...
reg add "%KEY%" /v * /t REG_SZ /d "%RUNTIME_DIR%" /f >nul
reg add "%KEY32%" /v * /t REG_SZ /d "%RUNTIME_DIR%" /f >nul
echo OK.

echo.
echo [5/5] Verification:
echo   hosts file:
findstr /C:"maxcall.local" "%HOSTS%"
echo   registry:
reg query "%KEY%" /v *
echo.
echo DONE. Start MaxCall, sign in, open the Tasks tab.
echo If login fails, send %%APPDATA%%\MaxCall\maxcall_debug.log to support.
echo.
pause
