@echo off
REM ============================================================
REM SD APP - clear cache
REM ============================================================

cd /d "%~dp0"

if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"

echo Clearing pip cache...
pip cache purge 2>nul

echo Clearing app data...
if exist "gpon_work\osm_raw" rmdir /s /q "gpon_work\osm_raw" 2>nul
if exist "gpon_work\tiles" rmdir /s /q "gpon_work\tiles" 2>nul
if exist "gpon_work\maps" rmdir /s /q "gpon_work\maps" 2>nul
if exist "app.log" del /q "app.log" 2>nul
if exist "config.json" del /q "config.json" 2>nul

echo Done.
pause
