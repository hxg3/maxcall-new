@echo off
setlocal EnableExtensions
title MaxCall - repair Windows 7 setup

rem Cleans stale WebView2 processes, corrupted WebView2 user-data folders and
rem interrupted-update leftovers, then reinstalls the current Win7 build.
rem It does NOT remove the maxcall.local hosts entry.

set "MIRROR=http://192.168.1.32:3001/updates"
set "KEY=HKLM\SOFTWARE\Policies\Microsoft\Edge\WebView2\BrowserExecutableFolder"
set "KEY32=HKLM\SOFTWARE\Wow6432Node\Policies\Microsoft\Edge\WebView2\BrowserExecutableFolder"
set "HOSTS=%WINDIR%\System32\drivers\etc\hosts"
set "LOG=%USERPROFILE%\Desktop\maxcall-repair-log.txt"
set "TARGET="
set "APPDIR="

net session >nul 2>&1
if errorlevel 1 (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

call :run > "%LOG%" 2>&1
type "%LOG%"
echo.
echo A copy was saved to: %LOG%
echo Send that file if MaxCall still fails.
echo.
pause
exit /b 0

:run
echo === MaxCall Win7 repair ===
ver
echo.

echo [1/7] Locating MaxCall.exe...
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
echo [2/7] WebView2 processes and their executable paths:
tasklist /FI "IMAGENAME eq msedgewebview2.exe" 2>nul | find /I "msedgewebview2.exe"
wmic process where "name='msedgewebview2.exe'" get ExecutablePath 2>nul

echo.
echo [3/7] Closing MaxCall and WebView2 processes...
taskkill /F /IM MaxCall.exe >nul 2>&1
taskkill /F /IM msedgewebview2.exe >nul 2>&1
echo OK.

echo.
echo [4/7] Removing interrupted-update leftovers...
if exist "%APPDIR%\update" rd /S /Q "%APPDIR%\update" >nul 2>&1
del /F /Q "%APPDIR%\MaxCall.updating.exe" >nul 2>&1
del /F /Q "%TARGET%.old" >nul 2>&1
echo OK.

echo.
echo [5/7] Removing WebView2 user-data folders (profile/cache only)...
for %%D in (
  "%APPDIR%\MaxCall.exe.WebView2"
  "%APPDIR%\MaxCall-win7.exe.WebView2"
  "%LOCALAPPDATA%\MaxCall\WebView2"
  "%LOCALAPPDATA%\MaxCall\EBWebView"
  "%LOCALAPPDATA%\MaxCall\User Data"
) do (
  if exist %%D (
    echo   deleting %%D
    rd /S /Q %%D >nul 2>&1
  )
)
echo OK.

echo.
echo [6/7] Reinstalling the current Win7 build...
powershell -NoProfile -Command "(New-Object Net.WebClient).DownloadFile('%MIRROR%/MaxCall-win7-beta.exe', '%TEMP%\MaxCall-win7.exe')"
powershell -NoProfile -Command "(New-Object Net.WebClient).DownloadFile('%MIRROR%/WebView2Loader.dll', '%TEMP%\WebView2Loader.dll')"
if not exist "%TEMP%\MaxCall-win7.exe" goto :downloadfail
if not exist "%TEMP%\WebView2Loader.dll" goto :downloadfail
copy /Y "%TEMP%\MaxCall-win7.exe" "%TARGET%" >nul
copy /Y "%TEMP%\WebView2Loader.dll" "%APPDIR%\WebView2Loader.dll" >nul
certutil -hashfile "%TARGET%" SHA256 2>nul | find /V "hash"
echo OK.

echo.
echo [6b/7] Checking the WebView2 109 runtime files...
set "RT="
for /f "delims=" %%F in ('dir /s /b "C:\WV2Clean\msedgewebview2.exe" 2^>nul') do set "RT=%%~dpF"
if not defined RT (
  echo Runtime missing - downloading from the mirror...
  powershell -NoProfile -Command "(New-Object Net.WebClient).DownloadFile('%MIRROR%/Microsoft.WebView2.FixedVersionRuntime.109.0.1518.78.x86.cab', '%TEMP%\wv2.cab')"
  if exist "%TEMP%\wv2.cab" (
    if not exist "C:\WV2Clean" mkdir "C:\WV2Clean" >nul
    expand "%TEMP%\wv2.cab" -F:* "C:\WV2Clean" >nul
    echo Extracted.
  )
) else (
  echo OK: %RT%
  dir "%RT%" 2>nul | find /I "msedgewebview2.exe"
)

echo.
echo [7/7] Verification:
echo   hosts entry (kept on purpose):
findstr /C:"maxcall.local" "%HOSTS%"
echo   WebView2 policy (64-bit):
reg query "%KEY%" /v *
echo   WebView2 policy (32-bit view):
reg query "%KEY32%" /v *
echo   last log lines:
if exist "%APPDIR%\maxcall_debug.log" (
  powershell -NoProfile -Command "Get-Content '%APPDIR%\maxcall_debug.log' -Tail 8"
)
echo.
echo DONE. Restart WINDOWS now, then start MaxCall again.
exit /b 0

:noexe
echo ERROR: MaxCall.exe not found. Drag it onto this script.
exit /b 1

:downloadfail
echo ERROR: cannot download from %MIRROR%. Check the network.
exit /b 1
