@echo off
setlocal EnableExtensions
title MaxCall - repair Windows 7 setup

rem Cleans stale WebView2 processes and interrupted-update leftovers,
rem then reinstalls the current Win7 build from the mirror.
rem It does NOT remove the maxcall.local hosts entry.

set "MIRROR=http://maxcall.local:3001/updates"
set "KEY=HKLM\SOFTWARE\Policies\Microsoft\Edge\WebView2\BrowserExecutableFolder"
set "KEY32=HKLM\SOFTWARE\Wow6432Node\Policies\Microsoft\Edge\WebView2\BrowserExecutableFolder"
set "HOSTS=%WINDIR%\System32\drivers\etc\hosts"
set "TARGET="
set "APPDIR="

net session >nul 2>&1
if errorlevel 1 (
  echo.
  echo Administrator rights are required. Restarting elevated...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

echo.
echo [1/6] Locating MaxCall.exe...
for %%P in (
  "%LOCALAPPDATA%\MaxCall\MaxCall.exe"
  "%PROGRAMFILES%\MaxCall\MaxCall.exe"
  "C:\maxcall\MaxCall.exe"
  "%USERPROFILE%\Desktop\MaxCall.exe"
  "%USERPROFILE%\Downloads\MaxCall.exe"
) do if exist %%P set "TARGET=%%~P"
if not defined TARGET goto :noexe
for %%D in ("%TARGET%") do set "APPDIR=%%~dpD"
set "APPDIR=%APPDIR:~0,-1%"
echo OK: %TARGET%

echo.
echo [2/6] WebView2 processes currently running:
tasklist /FI "IMAGENAME eq msedgewebview2.exe" 2>nul | find /I "msedgewebview2.exe"
wmic process where "name='msedgewebview2.exe'" get ExecutablePath 2>nul

echo.
echo [3/6] Closing MaxCall and stale WebView2 processes...
taskkill /F /IM MaxCall.exe >nul 2>&1
taskkill /F /IM msedgewebview2.exe >nul 2>&1
echo OK.

echo.
echo [4/6] Removing interrupted-update leftovers...
if exist "%APPDIR%\update" rd /S /Q "%APPDIR%\update" >nul 2>&1
del /F /Q "%APPDIR%\MaxCall.updating.exe" >nul 2>&1
del /F /Q "%TARGET%.old" >nul 2>&1
echo OK.

echo.
echo [5/6] Reinstalling the current Win7 build...
powershell -NoProfile -Command "(New-Object Net.WebClient).DownloadFile('%MIRROR%/MaxCall-win7-beta.exe', '%TEMP%\MaxCall-win7.exe')"
powershell -NoProfile -Command "(New-Object Net.WebClient).DownloadFile('%MIRROR%/WebView2Loader.dll', '%TEMP%\WebView2Loader.dll')"
if not exist "%TEMP%\MaxCall-win7.exe" goto :downloadfail
if not exist "%TEMP%\WebView2Loader.dll" goto :downloadfail
copy /Y "%TEMP%\MaxCall-win7.exe" "%TARGET%" >nul
copy /Y "%TEMP%\WebView2Loader.dll" "%APPDIR%\WebView2Loader.dll" >nul
echo OK.

echo.
echo [5b/6] Checking the WebView2 109 runtime files...
set "RT="
for /f "delims=" %%F in ('dir /s /b "C:\WebView2-109\msedgewebview2.exe" 2^>nul') do set "RT=%%~dpF"
if not defined RT (
  echo Runtime missing - downloading it from the mirror (may take a while)...
  powershell -NoProfile -Command "(New-Object Net.WebClient).DownloadFile('%MIRROR%/Microsoft.WebView2.FixedVersionRuntime.109.0.1518.78.x86.cab', '%TEMP%\wv2.cab')"
  if exist "%TEMP%\wv2.cab" (
    if not exist "C:\WebView2-109" mkdir "C:\WebView2-109" >nul
    expand "%TEMP%\wv2.cab" -F:* "C:\WebView2-109" >nul
  )
) else (
  echo OK: %RT%
)

echo.
echo [6/6] Verification:
echo   hosts entry (kept on purpose):
findstr /C:"maxcall.local" "%HOSTS%"
echo   WebView2 policy:
reg query "%KEY%" /v *
if errorlevel 1 (
  echo   WARNING: policy missing - please run install-win7-app.cmd once.
)
echo.
echo DONE. Restart WINDOWS now (the previous update may still be staged),
echo then start MaxCall again.
echo.
pause
exit /b 0

:noexe
echo ERROR: MaxCall.exe not found. Drag it onto this script.
echo.
pause
exit /b 1

:downloadfail
echo ERROR: cannot download from %MIRROR%. Check the network.
echo.
pause
exit /b 1
