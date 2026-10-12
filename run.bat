@echo off
REM ============================================================
REM SD APP - run the application
REM ============================================================
REM Double-click this file to start the app
REM ============================================================

cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
    echo [ERROR] Virtual environment not found.
    echo Run install.bat first.
    pause
    exit /b 1
)

call ".venv\Scripts\activate.bat"

echo Starting SD App...
python main.py

if errorlevel 1 (
    echo.
    echo [APP EXITED WITH ERROR]
    echo See app.log for details.
    echo.
    pause
)
