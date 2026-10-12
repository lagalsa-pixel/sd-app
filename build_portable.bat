@echo off
REM ============================================================
REM СБОРКА PORTABLE-ВЕРСИИ для Windows 10 64-bit
REM ============================================================
REM Создаёт папку sd-app-portable с:
REM   - Python 3.12 embedded (без установки)
REM   - Все зависимости (numpy, scipy, Pillow, requests)
REM   - Приложение sd-app
REM   - Запуск без прав администратора
REM
REM SD-пакеты (torch, diffusers) НЕ включены (~2 ГБ) —
REM они устанавливаются через updater.py при первом запуске
REM если пользователь хочет использовать SD Image Generator.
REM GPON FTTH Planner работает сразу.
REM
REM Запуск: дважды кликните по этому файлу на Windows 10 64-bit
REM ============================================================

cd /d "%~dp0"
chcp 65001 >nul
setlocal EnableDelayedExpansion

echo.
echo ============================================================
echo   Сборка PORTABLE-версии SD App для Windows 10 64-bit
echo ============================================================
echo.

REM Проверка архитектуры
if not "%PROCESSOR_ARCHITECTURE%"=="AMD64" (
    echo [ОШИБКА] Требуется Windows 10 64-bit.
    pause
    exit /b 1
)

REM Настройки
set PYTHON_VERSION=3.12.7
set PYTHON_EMBED=python-3.12.7-embed-amd64.zip
set PYTHON_URL=https://www.python.org/ftp/python/%PYTHON_VERSION%/%PYTHON_EMBED%
set GET_PIP_URL=https://bootstrap.pypa.io/get-pip.py
set PORTABLE_DIR=sd-app-portable
set PYTHON_DIR=%PORTABLE_DIR%\python

REM Проверка интернет-соединения
echo [1/8] Проверка интернет-соединения...
ping -n 1 python.org >nul 2>&1
if errorlevel 1 (
    echo [ОШИБКА] Нет интернет-соединения.
    pause
    exit /b 1
)
echo   OK

REM Очистка старой сборки
if exist "%PORTABLE_DIR%" (
    echo [2/8] Очистка старой сборки...
    rmdir /s /q "%PORTABLE_DIR%" 2>nul
)
mkdir "%PORTABLE_DIR%"
mkdir "%PYTHON_DIR%"
echo   OK

REM Скачивание Python embedded
echo [3/8] Скачивание Python %PYTHON_VERSION% embedded...
if not exist "%PYTHON_EMBED%" (
    echo   Загрузка %PYTHON_EMBED% ...
    powershell -Command "& { $wc = New-Object System.Net.WebClient; $wc.DownloadFile('%PYTHON_URL%', '%PYTHON_EMBED%') }" 2>nul
    if errorlevel 1 (
        echo   [ОШИБКА] Не удалось скачать Python.
        pause
        exit /b 1
    )
)
echo   Распаковка...
powershell -Command "& { Add-Type -AssemblyName System.IO.Compression.FileSystem; [System.IO.Compression.ZipFile]::ExtractToDirectory('%PYTHON_EMBED%', '%PYTHON_DIR%') }" 2>nul
if errorlevel 1 (
    echo   [ОШИБКА] Не удалось распаковать Python.
    pause
    exit /b 1
)
echo   OK

REM Активация pip (раскомментировать pth файл)
echo [4/8] Настройка pip...
cd "%PYTHON_DIR%"
REM Раскомментируем python312._pth для возможности установки пакетов
powershell -Command "(Get-Content 'python312._pth') -replace '^#import site', 'import site' | Set-Content 'python312._pth'"
cd ..

REM Скачивание get-pip.py
echo   Скачивание get-pip.py...
powershell -Command "& { $wc = New-Object System.Net.WebClient; $wc.DownloadFile('%GET_PIP_URL%', 'get-pip.py') }" 2>nul

REM Установка pip
echo   Установка pip...
"%PYTHON_DIR%\python.exe" get-pip.py --no-warn-script-location 2>nul
if errorlevel 1 (
    echo   [ОШИБКА] Не удалось установить pip.
    pause
    exit /b 1
)
del get-pip.py 2>nul
echo   OK

REM Установка зависимостей (без SD-пакетов — они ставятся через updater)
echo [5/8] Установка зависимостей (numpy, scipy, Pillow, requests)...
"%PYTHON_DIR%\python.exe" -m pip install --no-warn-script-location ^
    "numpy>=1.24" ^
    "scipy>=1.10" ^
    "Pillow>=10.0" ^
    "requests>=2.28" 2>nul
if errorlevel 1 (
    echo   [ОШИБКА] Не удалось установить зависимости.
    pause
    exit /b 1
)
echo   OK

REM Очистка кэша pip
echo [6/8] Очистка кэша pip...
"%PYTHON_DIR%\python.exe" -m pip cache purge 2>nul
echo   OK

REM Копирование файлов приложения
echo [7/8] Копирование приложения...
cd ..
set APP_SRC=%CD%
set APP_DST=%CD%\%PORTABLE_DIR%\app

REM Создаём папку app
mkdir "%APP_DST%" 2>nul

REM Копируем все .py файлы
for %%f in (*.py) do (
    copy "%%f" "%APP_DST%\" >nul 2>&1
)

REM Копируем requirements.txt, pyproject.toml, README.md, LICENSE
copy requirements.txt "%APP_DST%\" >nul 2>&1
copy pyproject.toml "%APP_DST%\" >nul 2>&1
copy README.md "%APP_DST%\" >nul 2>&1
copy LICENSE "%APP_DST%\" >nul 2>&1

REM Создаём пустые папки для данных
mkdir "%APP_DST%\output" 2>nul
mkdir "%APP_DST%\models" 2>nul
mkdir "%APP_DST%\gpon_work\osm_raw" 2>nul
mkdir "%APP_DST%\gpon_work\tiles" 2>nul
mkdir "%APP_DST%\gpon_work\maps" 2>nul

echo   OK

REM Создание запускатора
echo [8/8] Создание запускатора...

REM Создаём portable_launcher.bat в папке portable
set LAUNCHER=%PORTABLE_DIR%\sd-app-portable.bat
(
    echo @echo off
    echo chcp 65001 ^>nul
    echo cd /d "%%~dp0"
    echo echo Запуск SD App Portable...
    echo "python\python.exe" "app\main.py" %%*
    echo if errorlevel 1 ^
    echo    echo. ^& echo [ОШИБКА] Смотрите app\app.log
    echo pause
) > "%LAUNCHER%"

echo   OK

REM Финальная статистика
echo.
echo ============================================================
echo   СБОРКА ЗАВЕРШЕНА!
echo ============================================================
echo.
echo   Папка: %PORTABLE_DIR%\
echo   Запуск: %LAUNCHER%
echo.
echo   Размер:
dir /s "%PORTABLE_DIR%" 2>nul | findstr /C:"файлов" /C:"File(s)"
echo.
echo   Для распространения:
echo   1. Запакуйте папку %PORTABLE_DIR% в ZIP
echo   2. Скопируйте на любой ПК с Windows 10 64-bit
echo   3. Распакуйте и запустите sd-app-portable.bat
echo.
echo   SD-пакеты (torch, diffusers) НЕ включены.
echo   Они установятся автоматически через updater.py при первом
echo   запуске, если пользователь захочет использовать SD Generator.
echo   GPON FTTH Planner работает сразу.
echo.
pause
