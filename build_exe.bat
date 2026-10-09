@echo off
REM ============================================================
REM СБОРКА .EXE ЧЕРЕЗ PYINSTALLER (альтернативный способ)
REM ============================================================
REM Создаёт один .exe файл SDImageGenerator.exe
REM
REM Преимущества:
REM   - Один файл, удобно распространять
REM   - Не нужна папка Python
REM
REM Недостатки:
REM   - Большой размер (~500 МБ с torch)
REM   - Долгий первый запуск (распаковка во временную папку)
REM
REM SD-пакеты (torch, diffusers) включаются в exe, если установлены.
REM Если не установлены — приложение запустится, но SD-вкладка
REM предложит их установить.
REM
REM Запуск: дважды кликните по этому файлу на Windows 10 64-bit
REM Требуется: Python 3.10+ установленный (с PATH)
REM ============================================================

cd /d "%~dp0"
chcp 65001 >nul
setlocal

echo.
echo ============================================================
echo   Сборка .exe через PyInstaller
echo ============================================================
echo.

REM Проверка Python
where python >nul 2>nul
if errorlevel 1 (
    echo [ОШИБКА] Python не найден в PATH.
    echo Установите Python 3.10+ с https://www.python.org/downloads/
    pause
    exit /b 1
)

echo Найден Python:
python --version
echo.

REM Создание venv если нет
if not exist ".venv\Scripts\activate.bat" (
    echo [1/4] Создание виртуального окружения...
    python -m venv .venv
    if errorlevel 1 (
        echo [ОШИБКА] Не удалось создать venv.
        pause
        exit /b 1
    )
)

call ".venv\Scripts\activate.bat"

REM Установка PyInstaller
echo [2/4] Установка PyInstaller...
python -m pip install --upgrade pip
pip install pyinstaller 2>nul
if errorlevel 1 (
    echo [ОШИБКА] Не удалось установить PyInstaller.
    pause
    exit /b 1
)

REM Установка базовых зависимостей
echo [3/4] Установка базовых зависимостей...
pip install -r requirements.txt 2>nul

REM Проверка, установлен ли torch (для решения о включении в сборку)
python -c "import torch" 2>nul
set HAS_TORCH=%errorlevel%

REM Сборка
echo [4/4] Сборка .exe...
echo.

if "%HAS_TORCH%"=="0" (
    echo Режим: ПОЛНАЯ сборка (с torch/diffusers)
    echo Размер .exe будет ~500-700 МБ
    echo.
    pyinstaller --noconfirm --onefile --windowed ^
        --name "SDImageGenerator" ^
        --collect-all diffusers ^
        --collect-all transformers ^
        --collect-all tokenizers ^
        --collect-all accelerate ^
        --collect-all PIL ^
        --collect-all scipy ^
        --collect-all numpy ^
        --collect-all tkinter ^
        --hidden-import "tkinter.ttk" ^
        --hidden-import "tkinter.filedialog" ^
        --hidden-import "tkinter.messagebox" ^
        --hidden-import "PIL.ImageTk" ^
        --hidden-import "PIL.ImageDraw" ^
        --hidden-import "PIL.ImageFont" ^
        --add-data "requirements.txt;." ^
        --add-data "README.md;." ^
        --add-data "LICENSE;." ^
        main.py
) else (
    echo Режим: ЛЁГКАЯ сборка (без torch/diffusers)
    echo SD-пакеты будут установлены через updater.py при первом запуске
    echo Размер .exe будет ~50-80 МБ
    echo.
    pyinstaller --noconfirm --onefile --windowed ^
        --name "SDImageGenerator" ^
        --collect-all PIL ^
        --collect-all scipy ^
        --collect-all numpy ^
        --collect-all tkinter ^
        --excludes torch ^
        --excludes torchvision ^
        --excludes diffusers ^
        --excludes transformers ^
        --excludes accelerate ^
        --hidden-import "tkinter.ttk" ^
        --hidden-import "tkinter.filedialog" ^
        --hidden-import "tkinter.messagebox" ^
        --hidden-import "PIL.ImageTk" ^
        --hidden-import "PIL.ImageDraw" ^
        --hidden-import "PIL.ImageFont" ^
        --add-data "requirements.txt;." ^
        --add-data "README.md;." ^
        --add-data "LICENSE;." ^
        main.py
)

if errorlevel 1 (
    echo.
    echo [ОШИБКА СБОРКИ]
    echo Смотрите лог выше.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   СБОРКА ЗАВЕРШЕНА!
echo ============================================================
echo.
echo   .exe файл: dist\SDImageGenerator.exe
echo.

REM Размер файла
for %%A in ("dist\SDImageGenerator.exe") do (
    set FILE_SIZE=%%~zA
    set /A FILE_SIZE_MB=!FILE_SIZE! / 1048576
    echo   Размер: !FILE_SIZE_MB! МБ
)

echo.
echo   Для распространения:
echo   1. Скопируйте dist\SDImageGenerator.exe на любой ПК
echo   2. Запустите двойным кликом
echo.
echo   Примечания:
echo   - При первом запуске .exe распаковывается во временную папку
echo     (занимает 5-15 секунд)
echo   - Модели SD (~4 ГБ) скачиваются при первом использовании
echo   - Кэш OSM и тайлов создаётся в папке пользователя
echo.
pause
