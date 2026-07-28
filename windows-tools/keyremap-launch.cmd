@echo off
rem keyremap launcher — put a copy (or shortcut) in shell:startup.
rem Prefers driver-based interception mode when the Interception driver is
rem installed; falls back to the driver-free heuristic script otherwise.
rem Assumes the generated .ahk files sit next to this script.

set "HERE=%~dp0"
set "AHK=%LOCALAPPDATA%\Programs\AutoHotkey\v2\AutoHotkey64.exe"
if not exist "%AHK%" set "AHK=%ProgramFiles%\AutoHotkey\v2\AutoHotkey64.exe"
if not exist "%AHK%" (
  echo AutoHotkey v2 not found - install it or edit AHK= in this script
  exit /b 1
)

reg query "HKLM\SYSTEM\CurrentControlSet\Services\keyboard" /v Start >nul 2>&1
if %errorlevel%==0 (
  start "" "%AHK%" "%HERE%keyremap_interception.ahk"
) else (
  start "" "%AHK%" "%HERE%keyremap_heuristic.ahk"
)
