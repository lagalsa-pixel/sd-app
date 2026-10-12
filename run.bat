@echo off
REM ============================================================
REM SD APP - run the application
REM ============================================================
REM Double-click this file to start the app
REM Requires: .venv (created by start.bat or install.bat)
REM ============================================================

cd /d "%~dp0"

REM Check .venv
if not exist ".venv\Scripts\activate.bat" (
    echo [ERROR] Virtual environment not found.
    echo Run start.bat first to create it.
    echo.
    pause
    exit /b 1
)

REM Activate venv
call ".venv\Scripts\activate.bat"

REM Start app
echo Starting SD App...
".venv\Scripts\python.exe" main.py

if errorlevel 1 (
    echo.
    echo [APP EXITED WITH ERROR]
    echo Check app.log for details.
    echo.
    pause
)
