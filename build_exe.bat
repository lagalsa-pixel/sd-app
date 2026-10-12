@echo off
REM ============================================================
REM SD Image Generator — Сборка standalone .exe
REM ============================================================
REM Создаёт один .exe файл, который можно запускать без Python.
REM Внимание: модели НЕ включаются в exe — они качаются
REM автоматически при первом запуске.
REM ============================================================

chcp 65001 >nul

cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
    echo [ОШИБКА] Виртуальное окружение не найдено.
    echo Сначала запустите install.bat
    pause
    exit /b 1
)

call ".venv\Scripts\activate.bat"

echo Устанавливаю PyInstaller...
pip install pyinstaller

echo.
echo Собираю exe...
pyinstaller --noconfirm --onefile --windowed --name "SDImageGenerator" ^
    --collect-all diffusers ^
    --collect-all transformers ^
    --collect-all tokenizers ^
    --collect-all accelerate ^
    --collect-all PIL ^
    main.py

if errorlevel 1 (
    echo.
    echo [ОШИБКА СБОРКИ]
    pause
    exit /b 1
)

echo.
echo === Сборка завершена ===
echo exe-файл: dist\SDImageGenerator.exe
echo.
echo Примечание: модели (~4 ГБ для SD 1.5) НЕ включены в exe.
echo При первом запуске приложение скачает их автоматически
echo в кэш пользователя %USERPROFILE%\.cache\huggingface\.
echo.
pause
