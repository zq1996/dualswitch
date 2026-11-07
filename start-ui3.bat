@echo off
rem Screen Switch - Windows launcher for the V3 UI: python -m dualswitch ui3
rem
rem V3 is isolated from V2 on purpose:
rem   config dir  %APPDATA%\dualswitch-v3   (V2 uses %APPDATA%\dualswitch)
rem   TCP ports   47833 / 47834             (V2 uses 47823 / 47824)
rem so both versions can run side by side without fighting over the port.
rem
rem Double-click to run. Closing the window sends it to the tray;
rem right-click the tray icon and pick the last item to quit.
rem
rem Extra arguments are forwarded, e.g.:
rem   start-ui3.bat --dry-run          log only, never send a real DDC command
rem   start-ui3.bat --console          keep a console window so logs stay visible

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
pause
exit /b 1

:found
set "PY=%PYW:pythonw.exe=python.exe%"
set "PYTHONPATH=%ROOT%src"

rem ---- the GUI needs PySide6; fail loudly instead of a silent flash ---------
"%PY%" -c "import PySide6" >nul 2>&1
if errorlevel 1 (
    echo PySide6 is missing in: %PY%
    echo Install: "%PY%" -m pip install -e ".[gui]"
    pause
    exit /b 1
)

if /I "%~1"=="--console" goto run_console
start "" "%PYW%" -m dualswitch ui3 %*
exit /b 0

:run_console
start "" "%PY%" -m dualswitch ui3 %*
exit /b 0
