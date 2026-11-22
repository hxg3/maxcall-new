@echo off
setlocal EnableExtensions
title MaxCall - complete Windows 7 setup

rem Double-click this script, or drag your existing MaxCall.exe onto it.
rem It installs the Win7 build, its loader DLL, the WebView2 109 runtime
rem policy and the maxcall.local hosts entry in one pass.

set "MIRROR=http://maxcall.local:3001/updates"
set "CAB=%~dp0Microsoft.WebView2.FixedVersionRuntime.109.0.1518.78.x86.cab"
set "DEST=C:\WV2Clean"
set "KEY=HKLM\SOFTWARE\Policies\Microsoft\Edge\WebView2\BrowserExecutableFolder"
set "KEY32=HKLM\SOFTWARE\Wow6432Node\Policies\Microsoft\Edge\WebView2\BrowserExecutableFolder"
set "HOSTS=%WINDIR%\System32\drivers\etc\hosts"
set "TARGET="
set "RUNTIME_DIR="

net session >nul 2>&1
if errorlevel 1 (
  echo.
  echo Administrator rights are required. Restarting elevated...
  if "%~1"=="" (
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  ) else (
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '\"%~1\"' -Verb RunAs"
  )
  exit /b
)

echo.
echo [1/7] Locating MaxCall.exe...
if not "%~1"=="" set "TARGET=%~1"
if not defined TARGET (
  for %%P in (
    "%LOCALAPPDATA%\MaxCall\MaxCall.exe"
    "%PROGRAMFILES%\MaxCall\MaxCall.exe"
    "C:\maxcall\MaxCall.exe"
    "%USERPROFILE%\Desktop\MaxCall.exe"
    "%USERPROFILE%\Downloads\MaxCall.exe"
  ) do if exist %%P set "TARGET=%%~P"
)
if not defined TARGET goto :noexe
if not exist "%TARGET%" goto :noexe
for %%D in ("%TARGET%") do set "APPDIR=%%~dpD"
set "APPDIR=%APPDIR:~0,-1%"
echo OK: %TARGET%

echo.
echo [2/7] Checking that MaxCall is closed...
tasklist /FI "IMAGENAME eq MaxCall.exe" 2>nul | find /I "MaxCall.exe" >nul
if not errorlevel 1 (
  echo ERROR: MaxCall is running. Close it completely, then run this script again.
  echo.
  pause
  exit /b 1
)
echo OK.

echo.
echo [3/7] Downloading the Win7 build and its WebView2 loader from the mirror...
powershell -NoProfile -Command "(New-Object Net.WebClient).DownloadFile('%MIRROR%/MaxCall-win7.exe', '%TEMP%\MaxCall-win7.exe')"
powershell -NoProfile -Command "(New-Object Net.WebClient).DownloadFile('%MIRROR%/WebView2Loader.dll', '%TEMP%\WebView2Loader.dll')"
if not exist "%TEMP%\MaxCall-win7.exe" goto :downloadfail
if not exist "%TEMP%\WebView2Loader.dll" goto :downloadfail
echo OK.

echo.
echo [4/7] Installing the Win7 build (original saved as MaxCall-original.exe)...
copy /Y "%TARGET%" "%APPDIR%\MaxCall-original.exe" >nul
if errorlevel 1 goto :copyfail
copy /Y "%TEMP%\MaxCall-win7.exe" "%TARGET%" >nul
if errorlevel 1 goto :copyfail
copy /Y "%TEMP%\WebView2Loader.dll" "%APPDIR%\WebView2Loader.dll" >nul
if errorlevel 1 goto :copyfail
echo OK.

echo.
echo [5/7] Locating the WebView2 109 runtime...
for /f "delims=" %%F in ('dir /s /b "%DEST%\msedgewebview2.exe" 2^>nul') do set "RUNTIME_DIR=%%~dpF"
if not defined RUNTIME_DIR (
  if exist "%CAB%" (
    if not exist "%DEST%" mkdir "%DEST%" >nul
    expand "%CAB%" -F:* "%DEST%" >nul
    for /f "delims=" %%F in ('dir /s /b "%DEST%\msedgewebview2.exe" 2^>nul') do set "RUNTIME_DIR=%%~dpF"
  )
)
if not defined RUNTIME_DIR goto :noruntime
set "RUNTIME_DIR=%RUNTIME_DIR:~0,-1%"
echo OK: %RUNTIME_DIR%

echo.
echo [6/7] Setting maxcall.local and the WebView2 policy...
attrib -r "%HOSTS%" >nul 2>&1
rem Never bake a number: refresh the alias with the server's current address.
rem Optional second argument = current server IP when mDNS is unavailable.
powershell -NoProfile -Command "$h='%HOSTS%'; if (Test-Path $h) { $lines = @(Get-Content $h | Where-Object { $_ -notmatch 'maxcall\.local' }); Set-Content -Path $h -Value $lines -Encoding ASCII }"
set "MCIP="
if not "%~2"=="" set "MCIP=%~2"
if not defined MCIP for /f "delims=" %%I in ('powershell -NoProfile -Command "try { [Net.Dns]::GetHostAddresses('maxcall.local') ^| Where-Object { $_.AddressFamily -eq 'InterNetwork' } ^| Select-Object -First 1 -ExpandProperty IPAddressToString } catch { '' }"') do set "MCIP=%%I"
if defined MCIP (
  echo %MCIP%    maxcall.local>> "%HOSTS%"
  if errorlevel 1 goto :hostsfail
) else (
  echo WARNING: maxcall.local did not resolve. Re-run with the server's current IP as the second argument.
)
ipconfig /flushdns >nul 2>&1
reg add "%KEY%" /v * /t REG_SZ /d "%RUNTIME_DIR%" /f >nul
reg add "%KEY32%" /v * /t REG_SZ /d "%RUNTIME_DIR%" /f >nul
echo OK.

echo.
echo [7/7] Verification:
echo   exe:       %TARGET%
echo   loader:    %APPDIR%\WebView2Loader.dll
echo   runtime:   %RUNTIME_DIR%
echo   hosts:
findstr /C:"maxcall.local" "%HOSTS%"
echo   registry:
reg query "%KEY%" /v *
echo.
echo DONE. Start MaxCall, sign in, open the Tasks tab.
echo Updates are disabled in this Win7 build on purpose - re-run this
echo script to install a newer Win7 build when one is published.
echo.
pause
exit /b 0

:noexe
echo ERROR: MaxCall.exe not found. Drag your MaxCall.exe onto this script.
echo.
pause
exit /b 1

:downloadfail
echo ERROR: could not download from %MIRROR%. Check network / maxcall.local.
echo.
pause
exit /b 1

:copyfail
echo ERROR: could not replace MaxCall.exe. Close MaxCall and check permissions.
echo.
pause
exit /b 1

:noruntime
echo ERROR: WebView2 runtime not found. Keep the .cab next to this script.
echo.
pause
exit /b 1

:hostsfail
echo ERROR: cannot edit the hosts file. Check antivirus / permissions.
echo.
pause
exit /b 1
