@echo off
REM ============================================================
REM УСТАНОВКА SD APP БЕЗ ПРЕДУСТАНОВЛЕННОГО PYTHON
REM ============================================================
REM Этот скрипт:
REM   1. Проверяет, есть ли Python
REM   2. Если нет — скачивает и устанавливает Python 3.12
REM      (без прав администратора, для текущего пользователя)
REM   3. Устанавливает все зависимости приложения
REM   4. Запускает приложение
REM
REM Запуск: дважды кликните по этому файлу
REM Требования: Windows 10 64-bit, интернет
REM ============================================================

chcp 65001 >nul
setlocal EnableDelayedExpansion

echo.
echo ============================================================
echo   Установка SD App (с автоматической установкой Python)
echo ============================================================
echo.

REM Проверка архитектуры
if not "%PROCESSOR_ARCHITECTURE%"=="AMD64" (
    echo [ОШИБКА] Требуется Windows 10 64-bit.
    pause
    exit /b 1
)

REM Шаг 1: Проверка Python
echo [1/5] Проверка Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo   Python не найден. Будет установлен.
    set NEED_PYTHON=1
) else (
    echo   Python найден:
    python --version
    set NEED_PYTHON=0
)

REM Шаг 2: Установка Python если нужно
if "!NEED_PYTHON!"=="1" (
    echo.
    echo [2/5] Скачивание Python 3.12...
    set PYTHON_INSTALLER=python-3.12.7-amd64.exe
    set PYTHON_URL=https://www.python.org/ftp/python/3.12.7/!PYTHON_INSTALLER!

    if not exist "!PYTHON_INSTALLER!" (
        echo   Загрузка !PYTHON_INSTALLER! ...
        powershell -Command "& { $wc = New-Object System.Net.WebClient; $wc.DownloadFile('!PYTHON_URL!', '!PYTHON_INSTALLER!') }"
        if errorlevel 1 (
            echo   [ОШИБКА] Не удалось скачать Python.
            echo.
            echo Скачайте вручную с https://www.python.org/downloads/
            echo и установите с галочкой "Add Python to PATH"
            pause
            exit /b 1
        )
    )
    echo   OK

    echo.
    echo   Установка Python (для текущего пользователя, без прав администратора)...
    echo   Это займёт 1-2 минуты...
    "!PYTHON_INSTALLER!" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0 Include_doc=0 Include_dev=0 Include_launcher=0 Include_tcltk=1 Include_pip=1

    REM Ждём завершения установки
    echo   Ожидание завершения установки...
    :WAIT_PYTHON
    timeout /t 2 /nobreak >nul
    python --version >nul 2>&1
    if errorlevel 1 goto WAIT_PYTHON

    echo   Python установлен:
    python --version

    REM Удаляем installer
    del "!PYTHON_INSTALLER!" 2>nul
) else (
    echo   Python уже установлен, пропускаем.
)

REM Шаг 3: Создание venv
echo.
echo [3/5] Создание виртуального окружения...
if not exist ".venv\Scripts\activate.bat" (
    python -m venv .venv
    if errorlevel 1 (
        echo   [ОШИБКА] Не удалось создать venv.
        pause
        exit /b 1
    )
)
call ".venv\Scripts\activate.bat"
echo   OK

REM Шаг 4: Установка зависимостей
echo.
echo [4/5] Установка зависимостей...
echo   Это займёт 5-15 минут (зависит от интернета)...
echo.

python -m pip install --upgrade pip

echo   Устанавливаю базовые пакеты (numpy, scipy, Pillow, requests)...
pip install "numpy>=1.24" "scipy>=1.10" "Pillow>=10.0" "requests>=2.28"
if errorlevel 1 (
    echo   [ОШИБКА] Не удалось установить базовые пакеты.
    pause
    exit /b 1
)

echo.
echo   Базовые пакеты установлены. GPON FTTH Planner готов к работе!
echo.
echo   Хотите установить SD-пакеты (torch, diffusers) для генерации
echo   изображений через Stable Diffusion? Это дополнительно ~2 ГБ.
echo.
set /p INSTALL_SD="Установить SD-пакеты? (y/n) [n]: "
if /i "!INSTALL_SD!"=="y" (
    echo.
    echo   Устанавливаю PyTorch (CPU version)...
    pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
    echo.
    echo   Устанавливаю diffusers, transformers, accelerate...
    pip install "diffusers>=0.27" "transformers>=4.38" "accelerate>=0.25"
    if errorlevel 1 (
        echo   [ПРЕДУПРЕЖДЕНИЕ] Не удалось установить SD-пакеты.
        echo   SD Generator не будет работать, но GPON Planner будет.
    ) else (
        echo   SD-пакеты установлены!
    )
) else (
    echo   SD-пакеты пропущены. Их можно установить позже через install.bat
)

echo.
echo [5/5] Готово!
echo.
echo ============================================================
echo   УСТАНОВКА ЗАВЕРШЕНА!
echo ============================================================
echo.
echo   Для запуска приложения:
echo   1. Дважды кликните по run.bat
echo   2. Или выполните в командной строке: python main.py
echo.
echo   Приложение откроется с двумя вкладками:
echo   - 🎨 SD Image Generator (если установлены SD-пакеты)
echo   - 📡 GPON FTTH Planner (работает сразу)
echo.
pause
