@echo off
rem Launcher for the MP3 -> Jianpu transcriber GUI (ASCII only, to avoid codepage garbling).
setlocal
cd /d "%~dp0"

set "PYW=D:\PYTHON\pythonw.exe"
if not exist "%PYW%" set "PYW=D:\PYTHON\python.exe"
if not exist "%PYW%" set "PYW=pythonw.exe"

if not exist "%~dp0app.py" (
    echo [ERROR] app.py was not found next to this script:
    echo   %~dp0app.py
    pause
    exit /b 1
)

rem %~dp0 always ends with a backslash, so quotes stay intact for non-ASCII paths.
start "MP3 Jianpu Transcriber" "%PYW%" "%~dp0app.py"
exit /b 0
