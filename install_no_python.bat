@echo off
REM ============================================================
REM SD APP INSTALL - for users without Python
REM ============================================================
REM This script:
REM   1. Checks if Python is installed
REM   2. If not - downloads and installs Python 3.12
REM      (no admin rights needed, for current user)
REM   3. Installs all app dependencies
REM   4. Optionally installs SD packages (torch, diffusers)
REM
REM How to run: double-click this file
REM Requirements: Windows 10 64-bit, internet
REM ============================================================

REM Change to script directory (critical!)
cd /d "%~dp0"

echo.
echo ============================================================
echo   SD App Installation (auto Python install)
echo ============================================================
echo.
echo Current directory:
cd
echo.

REM Step 1: Check Python
echo [1/5] Checking Python...
python --version >nul 2>&1
if errorlevel 1 goto INSTALL_PYTHON
echo   Python found:
python --version
goto CREATE_VENV

:INSTALL_PYTHON
echo   Python NOT found. Will install Python 3.12.
echo.
echo   NOTE: Do NOT use Python 3.13 or 3.14 - they are too new
echo   and some packages (numpy, scipy) may not be available yet.
echo   This script will install Python 3.12 (stable, all packages work).
echo.
echo [2/5] Downloading Python 3.12...

set PYTHON_INSTALLER=python-3.12.7-amd64.exe
set PYTHON_URL=https://www.python.org/ftp/python/3.12.7/%PYTHON_INSTALLER%

if not exist "%PYTHON_INSTALLER%" (
    echo   Downloading %PYTHON_INSTALLER% ...
    powershell -Command "Invoke-WebRequest -Uri '%PYTHON_URL%' -OutFile '%PYTHON_INSTALLER%'"
    if not exist "%PYTHON_INSTALLER%" (
        echo   [ERROR] Failed to download Python.
        echo.
        echo Please download manually from:
        echo   https://www.python.org/downloads/release/python-3127/
        echo Scroll down to "Windows installer (64-bit)"
        echo Install with "Add Python to PATH" checkbox!
        pause
        exit /b 1
    )
)
echo   OK

echo.
echo   Installing Python (for current user, no admin needed)...
echo   This takes 1-2 minutes...

"%PYTHON_INSTALLER%" /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_tcltk=1

echo   Waiting for installation to complete...
:WAIT_PYTHON
timeout /t 3 /nobreak >nul
python --version >nul 2>&1
if errorlevel 1 goto WAIT_PYTHON

echo   Python installed:
python --version

del "%PYTHON_INSTALLER%" 2>nul
goto CREATE_VENV

:CREATE_VENV
echo.
echo [3/5] Creating virtual environment...
if exist ".venv\Scripts\activate.bat" goto ACTIVATE_VENV
python -m venv .venv
if errorlevel 1 (
    echo   [ERROR] Failed to create venv.
    pause
    exit /b 1
)

:ACTIVATE_VENV
call ".venv\Scripts\activate.bat"
echo   OK

REM Step 4: Install dependencies
echo.
echo [4/5] Installing dependencies...
echo   This takes 5-15 minutes (depends on internet)...
echo.

python -m pip install --upgrade pip

echo   Installing base packages (numpy, scipy, Pillow, requests)...
pip install "numpy>=1.24" "scipy>=1.10" "Pillow>=10.0" "requests>=2.28"
if errorlevel 1 (
    echo   [ERROR] Failed to install base packages.
    echo.
    echo If you have Python 3.13 or 3.14, packages may not be available.
    echo Please install Python 3.12 from:
    echo   https://www.python.org/downloads/release/python-3127/
    echo Then run this script again.
    pause
    exit /b 1
)

echo.
echo   Base packages installed. GPON FTTH Planner is ready!
echo.
echo   Do you want to install SD packages (torch, diffusers)
echo   for Stable Diffusion image generation? Additional ~2 GB.
echo.
set /p INSTALL_SD="Install SD packages? (y/n) [n]: "
if /i "%INSTALL_SD%"=="y" goto INSTALL_SD
if /i "%INSTALL_SD%"=="Y" goto INSTALL_SD
echo   SD packages skipped. You can install later via install.bat
goto DONE

:INSTALL_SD
echo.
echo   Installing PyTorch (CPU version)...
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
echo.
echo   Installing diffusers, transformers, accelerate...
pip install "diffusers>=0.27" "transformers>=4.38" "accelerate>=0.25"
if errorlevel 1 (
    echo   [WARNING] Failed to install SD packages.
    echo   SD Generator won't work, but GPON Planner will.
) else (
    echo   SD packages installed!
)

:DONE
echo.
echo [5/5] Done!
echo.
echo ============================================================
echo   INSTALLATION COMPLETE!
echo ============================================================
echo.
echo   To start the app:
echo   1. Double-click run.bat
echo   2. Or run: python main.py
echo.
echo   The app has two tabs:
echo   - SD Image Generator (if SD packages installed)
echo   - GPON FTTH Planner (works immediately)
echo.
pause
