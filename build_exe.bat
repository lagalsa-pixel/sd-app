@echo off
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
pause
