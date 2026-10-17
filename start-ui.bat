@echo off
rem Screen Switch - Windows launcher (no console window)
rem Double-click to run. Closing the window sends it to the tray;
rem right-click the tray icon and choose the last item to quit.

setlocal
set "ROOT=%~dp0"

set "PYW=%ROOT%.venv\Scripts\pythonw.exe"
if exist "%PYW%" goto run

set "PYW=C:\Users\Administrator\.workbuddy-ai\binaries\python\envs\default\Scripts\pythonw.exe"
if exist "%PYW%" goto run

echo Python environment not found.
echo Run: py -3 -m venv .venv ^&^& .venv\Scripts\pip install -e ".[gui]"
pause
exit /b 1

:run
start "" "%PYW%" -m dualswitch ui
exit /b 0
