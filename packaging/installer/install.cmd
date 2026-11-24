@echo off
rem Double-click to install snap2midi, or run with options, e.g.:
rem   install.cmd -Dir D:\Tools\snap2midi -Gpu no -Force
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
set "RC=%ERRORLEVEL%"
if "%~1"=="" pause
exit /b %RC%
