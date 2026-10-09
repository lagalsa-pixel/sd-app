@echo off
REM ============================================================
REM SD APP PORTABLE — запуск без установки
REM ============================================================
REM Запускает приложение с использованием встроенного Python
REM из папки python\ (создаётся build_portable.bat)
REM
REM Не требует:
REM   - Установки Python на ПК
REM   - Прав администратора
REM   - Установки пакетов (всё уже в папке python\)
REM
REM Работает с USB-флешки!
REM ============================================================

chcp 65001 >nul
cd /d "%~dp0"

echo.
echo ============================================================
echo   SD App Portable
echo   SD Image Generator + GPON FTTH Planner
echo ============================================================
echo.

REM Проверка Python embedded
if not exist "python\python.exe" (
    echo [ОШИБКА] Python не найден в папке python\
    echo.
    echo Если вы скачали только исходный код без Python embedded,
    echo запустите build_portable.bat для создания полной portable-версии.
    echo.
    pause
    exit /b 1
)

REM Проверка приложения
if not exist "app\main.py" (
    echo [ОШИБКА] Файл app\main.py не найден.
    echo Проверьте целостность portable-сборки.
    pause
    exit /b 1
)

echo Запуск приложения...
echo.

REM Устанавливаем переменные окружения
set PYTHONPATH=%CD%\app;%PYTHONPATH%
set PYTHONHOME=%CD%\python
set TCL_LIBRARY=%CD%\python\tcl\tcl8.6
set TK_LIBRARY=%CD%\python\tcl\tk8.6

REM Запуск приложения
"python\python.exe" "app\main.py" %*

if errorlevel 1 (
    echo.
    echo ============================================================
    echo   Приложение завершилось с ошибкой
    echo ============================================================
    echo.
    echo Лог: app\app.log
    echo.
    echo Возможные причины:
    echo   - Нет интернет-соединения (для OSM/тайлов)
    echo   - Повреждена папка python\ (пересоберите build_portable.bat)
    echo   - Антивирус блокирует выполнение
    echo.
    pause
)

REM Если запускали без паузы (с параметрами), не закрываем окно
if "%1"=="" pause
