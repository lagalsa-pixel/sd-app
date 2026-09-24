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

echo Запускаю SD Image Generator...
python main.py

if errorlevel 1 (
    echo.
    echo [ПРИЛОЖЕНИЕ ЗАВЕРШИЛОСЬ С ОШИБКОЙ]
    echo Смотрите подробности в файле app.log
    echo.
    pause
)
