@echo off
rem Double-click to install snap2midi, or run with options, e.g.:
rem   install.cmd -Dir D:\Tools\snap2midi -Gpu no -Force
setlocal
rem When started from PowerShell 7, PSModulePath points at PowerShell 7's modules and
rem Windows PowerShell 5.1 then cannot load its own (Get-FileHash, Expand-Archive...).
rem Cleared, 5.1 rebuilds its default module path.
set "PSModulePath="
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
set "RC=%ERRORLEVEL%"
if "%~1"=="" pause
exit /b %RC%
