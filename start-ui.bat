@echo off
rem Screen Switch - Windows launcher for the NEW (v2) UI: python -m dualswitch ui2
rem
rem Double-click to run. Closing the window sends it to the tray;
rem right-click the tray icon and pick the last item to quit.
rem
rem v2 UI = frameless title bar + tray + three pages (switch / clipboard / settings).
rem The entry point dispatches by platform: Windows -> ui/win2, macOS -> ui/mac2.
rem
rem Extra arguments are forwarded, e.g.:
rem   start-ui.bat --dry-run          log only, never send a real DDC command
rem   start-ui.bat --console          keep a console window so logs stay visible
rem   start-ui.bat --config my.json
rem
rem The old (v1) UI is still there if you need it: python -m dualswitch ui

setlocal
set "ROOT=%~dp0"

rem ---- locate an interpreter (pythonw = no console window) ------------------
set "PYW=%ROOT%.venv\Scripts\pythonw.exe"
if exist "%PYW%" goto found

set "PYW=%ROOT%venv\Scripts\pythonw.exe"
if exist "%PYW%" goto found

set "PYW=C:\Users\Administrator\.workbuddy-ai\binaries\python\envs\default\Scripts\pythonw.exe"
if exist "%PYW%" goto found

for %%P in (pythonw.exe) do if not "%%~$PATH:P"=="" set "PYW=%%~$PATH:P"
if exist "%PYW%" goto found

echo Python environment not found.
echo Run: py -3 -m venv .venv ^&^& .venv\Scripts\pip install -e ".[gui]"
pause
exit /b 1

:found
set "PY=%PYW:pythonw.exe=python.exe%"

rem ---- the GUI needs PySide6; fail loudly instead of a silent flash ---------
"%PY%" -c "import PySide6" >nul 2>&1
if errorlevel 1 (
    echo PySide6 is missing in: %PY%
    echo Install: "%PY%" -m pip install -e ".[gui]"
    pause
    exit /b 1
)

:run
rem --console: use python.exe so the log window stays open
if /I "%~1"=="--console" goto run_console
start "" "%PYW%" -m dualswitch ui2 %*
exit /b 0

:run_console
start "" "%PY%" -m dualswitch ui2 %*
exit /b 0
