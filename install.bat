@echo off
REM ============================================================
REM SD APP - install dependencies (if Python already installed)
REM ============================================================
REM Requires: Python 3.10, 3.11, or 3.12 (NOT 3.13/3.14 - too new)
REM ============================================================

echo.
echo ============================================================
echo   SD App - Installing dependencies
echo ============================================================
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found.
    echo Please install Python 3.12 from:
    echo   https://www.python.org/downloads/release/python-3127/
    echo Install with "Add Python to PATH" checkbox!
    echo.
    echo Or run install_no_python.bat for automatic installation.
    pause
    exit /b 1
)

echo Python found:
python --version
echo.

REM Check version - warn if 3.13+
python -c "import sys; exit(0 if sys.version_info < (3,13) else 1)" 2>nul
if errorlevel 1 (
    echo [WARNING] Python 3.13+ detected.
    echo Some packages (numpy, scipy) may not have wheels for 3.13/3.14 yet.
    echo If installation fails, please install Python 3.12 from:
    echo   https://www.python.org/downloads/release/python-3127/
    echo.
)

REM Ask about GPU
set /p GPU="Do you have NVIDIA GPU? (y/n) [n]: "
if /i "%GPU%"=="y" set TORCH_IDX=cu121
if /i "%GPU%"=="Y" set TORCH_IDX=cu121
if not defined TORCH_IDX set TORCH_IDX=cpu

echo.
echo Selected install: %TORCH_IDX%
echo.

REM Create venv if not exists
if not exist ".venv\Scripts\activate.bat" (
    echo Creating virtual environment .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create venv.
        pause
        exit /b 1
    )
)

call ".venv\Scripts\activate.bat"

echo.
echo Updating pip...
python -m pip install --upgrade pip

echo.
echo Installing PyTorch (%TORCH_IDX%)...
if "%TORCH_IDX%"=="cu121" (
    pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
) else (
    pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
)

echo.
echo Installing other dependencies...
pip install -r requirements.txt

if "%TORCH_IDX%"=="cu121" (
    echo.
    echo Installing xformers (optional)...
    pip install xformers 2>nul
)

echo.
echo ============================================================
echo   Installation complete!
echo ============================================================
echo.
echo Now run run.bat to start the app.
echo.
pause
