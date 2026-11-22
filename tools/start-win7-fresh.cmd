@echo off
setlocal EnableExtensions
title Start MaxCall with a fresh WebView2 profile

set "WEBVIEW2_USER_DATA_FOLDER=C:\maxcall\WebView2Fresh"
if not exist "%WEBVIEW2_USER_DATA_FOLDER%" mkdir "%WEBVIEW2_USER_DATA_FOLDER%"

echo Starting MaxCall with:
echo WEBVIEW2_USER_DATA_FOLDER=%WEBVIEW2_USER_DATA_FOLDER%
start "" "C:\maxcall\MaxCall.exe"
exit /b 0
