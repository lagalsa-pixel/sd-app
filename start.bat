@echo off
REM ============================================================
REM START.BAT - all-in-one: install + run
REM ============================================================
REM This script does EVERYTHING:
REM   1. Deletes old .venv (if broken)
REM   2. Creates new .venv
REM   3. Installs all packages (Pillow, numpy, scipy, requests)
REM   4. Starts the application
REM
REM Just double-click this file. That's it.
REM ============================================================

cd /d "%~dp0"

echo.
echo ============================================================
echo   SD App - One-Click Start
echo ============================================================
echo.

echo Current directory:
cd
echo.

REM Check Python
echo Step 1: Check Python...
python --version
if errorlevel 1 (
    echo.
    echo ERROR: Python not found!
    echo Install Python 3.12 from:
    echo   https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe
    echo Check "Add Python to PATH" during install!
    echo.
    pause
    exit /b 1
)
echo OK
echo.

REM Delete old .venv if it exists but is broken
if exist ".venv\Scripts\activate.bat" (
    echo Step 2: Check if .venv has Pillow...
    ".venv\Scripts\python.exe" -c "import PIL" 2>nul
    if errorlevel 1 (
        echo   .venv exists but Pillow is missing. Rebuilding...
        rmdir /s /q ".venv" 2>nul
    ) else (
        echo   .venv OK, Pillow found.
        goto RUN_APP
    )
) else (
    echo Step 2: No .venv found, will create new one.
)

REM Create venv
echo Step 3: Create virtual environment...
python -m venv .venv
if errorlevel 1 (
    echo ERROR: Failed to create .venv
    pause
    exit /b 1
)
echo OK
echo.

REM Install packages
echo Step 4: Install packages (Pillow, numpy, scipy, requests)...
echo This takes 3-10 minutes...
echo.
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\pip.exe" install numpy scipy Pillow requests
if errorlevel 1 (
    echo ERROR: Failed to install packages.
    pause
    exit /b 1
)
echo OK
echo.

REM Check tkinter
echo Step 5: Check tkinter...
".venv\Scripts\python.exe" -c "import tkinter; print('tkinter OK')"
if errorlevel 1 (
    echo WARNING: tkinter not found! GUI won't work.
    echo Reinstall Python 3.12 with tcl/tk option.
    pause
    exit /b 1
)
echo OK
echo.

:RUN_APP
echo Step 6: Starting application...
echo.
".venv\Scripts\python.exe" main.py

if errorlevel 1 (
    echo.
    echo ============================================================
    echo   App exited with error. Check app.log
    echo ============================================================
    echo.
    pause
)
