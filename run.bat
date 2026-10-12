@echo off
REM ============================================================
REM SD Image Generator + GPON FTTH Planner — Запуск
REM ============================================================
REM Двойной клик по этому файлу запускает приложение.
REM
REM Приложение автоматически:
REM   1. Проверяет зависимости (Pillow, numpy, scipy, requests)
REM   2. Если нужно — устанавливает их через pip
REM   3. Проверяет SD-пакеты (torch, diffusers) и спрашивает,
REM      устанавливать ли их
REM   4. Запускает главное окно
REM ============================================================

chcp 65001 >nul

cd /d "%~dp0"

REM Проверка Python
where python >nul 2>nul
if errorlevel 1 (
    echo [ОШИБКА] Python не найден в PATH.
    echo Установите Python 3.10+ с https://www.python.org/downloads/windows/
    echo При установке ОБЯЗАТЕЛЬНО отметьте "Add Python to PATH".
    pause
    exit /b 1
)

REM Если есть .venv — используем его, иначе — системный Python
if exist ".venv\Scripts\activate.bat" (
    call ".venv\Scripts\activate.bat"
    echo Используется виртуальное окружение .venv
) else (
    echo Виртуальное окружение не найдено — использую системный Python.
    echo Рекомендуется запустить install.bat для создания .venv
    echo.
)

echo Запускаю приложение...
echo.
python main.py

if errorlevel 1 (
    echo.
    echo [ПРИЛОЖЕНИЕ ЗАВЕРШИЛОСЬ С ОШИБКОЙ]
    echo Смотрите подробности в файле app.log
    echo.
    pause
)
