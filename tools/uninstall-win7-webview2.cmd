@echo off
setlocal EnableExtensions
title MaxCall - remove WebView2 109 override

net session >nul 2>&1
if errorlevel 1 (
  echo.
  echo Administrator rights are required. Restarting elevated...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

echo.
echo Removing WebView2 policy override...
reg delete "HKLM\SOFTWARE\Policies\Microsoft\Edge\WebView2\BrowserExecutableFolder" /v * /f >nul 2>&1
reg delete "HKLM\SOFTWARE\Wow6432Node\Policies\Microsoft\Edge\WebView2\BrowserExecutableFolder" /v * /f >nul 2>&1
echo OK.

echo Removing maxcall.local from the hosts file...
set "HOSTS=%WINDIR%\System32\drivers\etc\hosts"
set "TMP=%TEMP%\maxcall_hosts.tmp"
attrib -r "%HOSTS%" >nul 2>&1
findstr /V /C:"maxcall.local" "%HOSTS%" > "%TMP%" 2>nul
if not errorlevel 1 (
  copy /Y "%TMP%" "%HOSTS%" >nul 2>&1
  del "%TMP%" >nul 2>&1
)
ipconfig /flushdns >nul 2>&1
echo OK.

echo.
echo All MaxCall Windows 7 overrides removed.
echo You may delete C:\WebView2-109 to free disk space.
echo.
pause
