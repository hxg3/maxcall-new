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

reg delete "HKLM\SOFTWARE\Policies\Microsoft\Edge\WebView2\BrowserExecutableFolder" /v * /f >nul 2>&1
reg delete "HKLM\SOFTWARE\Wow6432Node\Policies\Microsoft\Edge\WebView2\BrowserExecutableFolder" /v * /f >nul 2>&1
echo.
echo WebView2 policy override removed.
echo You may now delete C:\WebView2-109 to free disk space.
echo.
pause
