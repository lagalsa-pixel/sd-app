@echo off
REM ============================================================
REM SIMPLE INSTALL - shows all errors, never closes
REM ============================================================

cd /d "%~dp0"

echo.
echo ============================================================
echo   SD App - Simple Install
echo ============================================================
echo.
echo Current directory:
cd
echo.

echo Step 1: Check Python...
python --version
if errorlevel 1 (
    echo.
    echo ERROR: Python not found!
    echo Please install Python 3.12 from:
    echo   https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe
    echo.
    echo IMPORTANT: Check "Add Python to PATH" during installation!
    echo.
    pause
    exit /b 1
)
echo OK
echo.

echo Step 2: Create virtual environment...
echo Running: python -m venv .venv
python -m venv .venv
if errorlevel 1 (
    echo.
    echo ERROR: Failed to create .venv
    echo Possible reasons:
    echo   - Python was just installed, need to restart CMD
    echo   - Antivirus blocking
    echo   - No write permission in this folder
    echo.
    echo Try: close this window, open new CMD, run again.
    echo.
    pause
    exit /b 1
)
echo OK
echo.

echo Step 3: Activate virtual environment...
call ".venv\Scripts\activate.bat"
if errorlevel 1 (
    echo ERROR: Failed to activate .venv
    pause
    exit /b 1
)
echo OK
echo.

echo Step 4: Upgrade pip...
python -m pip install --upgrade pip
if errorlevel 1 (
    echo WARNING: pip upgrade failed, continuing...
)
echo.

echo Step 5: Install packages...
echo Installing numpy, scipy, Pillow, requests...
echo This takes 5-15 minutes...
echo.
pip install numpy scipy Pillow requests
if errorlevel 1 (
    echo.
    echo ERROR: Failed to install packages.
    echo.
    pause
    exit /b 1
)
echo OK
echo.

echo Step 6: Install tkinter (should be included with Python)...
python -c "import tkinter; print('tkinter OK, version:', tkinter.TkVersion)"
if errorlevel 1 (
    echo WARNING: tkinter not found! GUI won't work.
    echo Reinstall Python 3.12 with "tcl/tk and IDLE" option.
)
echo.

echo ============================================================
echo   INSTALL COMPLETE!
echo ============================================================
echo.
echo Now run run.bat to start the application.
echo.
pause
