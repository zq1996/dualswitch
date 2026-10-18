@echo off
rem Screen Switch - clipboard CLI wrapper (Windows).
rem
rem Why this exists: the "python" on PATH may be C:\Python27 (Python 2.7), which has
rem no dualswitch package -> "No module named dualswitch". The project's interpreter
rem lives in a venv. Always go through this wrapper instead of calling python directly.
rem
rem Usage:
rem   clip.bat status
rem   clip.bat probe
rem   clip.bat get
rem   clip.bat send "hello"
rem   clip.bat pair --pin 123456
rem   clip.bat selftest
rem
rem Same as: <venv>\Scripts\python.exe -m dualswitch clip <args>

setlocal
set "ROOT=%~dp0"

set "PY=%ROOT%.venv\Scripts\python.exe"
if exist "%PY%" goto run

set "PY=C:\Users\Administrator\.workbuddy-ai\binaries\python\envs\default\Scripts\python.exe"
if exist "%PY%" goto run

echo Python environment not found.
echo Run: py -3 -m venv .venv ^&^& .venv\Scripts\pip install -e .
pause
exit /b 1

:run
if "%~1"=="" goto usage
"%PY%" -m dualswitch clip %*
exit /b %errorlevel%

:usage
echo Usage: clip.bat status ^| probe ^| get ^| send "text" ^| pair --pin 123456 ^| selftest
exit /b 2
