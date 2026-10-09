@echo off
REM ============================================================
REM DIAGNOSTIC - shows system info for troubleshooting
REM ============================================================
echo.
echo ============================================================
echo   SYSTEM DIAGNOSTIC
echo ============================================================
echo.

echo --- Windows version ---
ver
echo.

echo --- Python check ---
python --version 2>&1
echo.

echo --- Python location ---
where python 2>&1
echo.

echo --- pip check ---
pip --version 2>&1
echo.

echo --- Current directory ---
cd
echo.

echo --- Files in current directory ---
dir /b 2>&1
echo.

echo --- .venv check ---
if exist ".venv\Scripts\activate.bat" (
    echo .venv EXISTS
    dir ".venv\Scripts" 2>&1
) else (
    echo .venv NOT FOUND
)
echo.

echo --- Python 3.12 registry check ---
reg query "HKCU\Software\Python\PythonCore\3.12" 2>nul
reg query "HKLM\Software\Python\PythonCore\3.12" 2>nul
echo.

echo ============================================================
echo   Copy ALL text above and send it for analysis!
echo ============================================================
echo.
pause
