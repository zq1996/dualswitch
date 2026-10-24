@echo off
rem Screen Switch - Logitech Easy-Switch (HID++ host switch) wrapper.
rem Use this instead of calling python directly: python on PATH may be C:/Python27.
rem
rem Usage:
rem   logi-mouse.bat list
rem   logi-mouse.bat probe
rem   logi-mouse.bat switch 2

setlocal
set "ROOT=%~dp0"

set "PY=%ROOT%.venv\Scripts\python.exe"
if exist "%PY%" goto run

set "PY=C:/Users/Administrator/.workbuddy-ai/binaries/python/envs/default/Scripts/python.exe"
if exist "%PY%" goto run

echo Python environment not found.
exit /b 1

:run
if "%~1"=="" goto usage
"%PY%" "%ROOT%tools\logi_host_switch.py" %*
exit /b %errorlevel%

:usage
echo Usage: logi-mouse.bat list ^| probe ^| switch 1-3
exit /b 2
