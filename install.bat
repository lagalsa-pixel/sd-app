@echo off
REM ============================================================
REM SD APP - install dependencies (if Python already installed)
REM ============================================================
REM Requires: Python 3.10, 3.11, or 3.12 (NOT 3.13/3.14 - too new)
REM ============================================================

REM Change to script directory (critical!)
cd /d "%~dp0"

echo.
echo ============================================================
echo   SD App - Installing dependencies
echo ============================================================
echo.
echo Current directory:
cd
echo.

REM Step 1: Check Python
echo Step 1: Check Python...
python --version
if errorlevel 1 goto NO_PYTHON
echo OK
echo.

REM Step 2: Check version - warn if 3.13+
python -c "import sys; exit(0 if sys.version_info < (3,13) else 1)" 2>nul
if errorlevel 1 (
    echo [WARNING] Python 3.13+ detected.
    echo Some packages (numpy, scipy) may not have wheels for 3.13/3.14.
    echo If installation fails, install Python 3.12 from:
    echo   https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe
    echo.
)

REM Step 3: Ask about GPU
set /p GPU="Do you have NVIDIA GPU? (y/n) [n]: "
if /i "%GPU%"=="y" set TORCH_IDX=cu121
if /i "%GPU%"=="Y" set TORCH_IDX=cu121
if not defined TORCH_IDX set TORCH_IDX=cpu

echo.
echo Selected install: %TORCH_IDX%
echo.

REM Step 4: Create venv if not exists
echo Step 4: Create virtual environment...
if exist ".venv\Scripts\activate.bat" goto ACTIVATE
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

:ACTIVATE
echo Step 5: Activate virtual environment...
call ".venv\Scripts\activate.bat"
if errorlevel 1 (
    echo ERROR: Failed to activate .venv
    pause
    exit /b 1
)
echo OK
echo.

REM Step 6: Upgrade pip
echo Step 6: Upgrade pip...
python -m pip install --upgrade pip
echo.

REM Step 7: Install PyTorch
echo Step 7: Install PyTorch (%TORCH_IDX%)...
echo This takes 5-15 minutes...
echo.
if "%TORCH_IDX%"=="cu121" (
    pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
) else (
    pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
)
if errorlevel 1 (
    echo WARNING: PyTorch installation failed.
    echo SD Image Generator won't work, but GPON FTTH Planner will.
    echo Continuing...
)
echo.

REM Step 8: Install other dependencies
echo Step 8: Install other dependencies...
pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: Failed to install dependencies.
    pause
    exit /b 1
)
echo OK
echo.

REM Step 9: Optional xformers for CUDA
if "%TORCH_IDX%"=="cu121" (
    echo Step 9: Install xformers (optional)...
    pip install xformers 2>nul
)
echo.

REM Step 10: Check tkinter
echo Step 10: Check tkinter...
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
exit /b 0

:NO_PYTHON
echo.
echo ERROR: Python not found!
echo.
echo Please install Python 3.12 from:
echo   https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe
echo.
echo IMPORTANT: Check "Add Python to PATH" during installation!
echo.
echo Or run install_no_python.bat for automatic installation.
echo.
pause
exit /b 1
