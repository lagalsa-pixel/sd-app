@echo off
REM ============================================================
REM SD Image Generator — Установка зависимостей
REM ============================================================
REM Запускать двойным кликом.
REM Требуется Python 3.10+ (должен быть в PATH).
REM ============================================================

chcp 65001 >nul
setlocal EnableDelayedExpansion

echo.
echo === SD Image Generator — Установка ===
echo.

REM Проверка Python
where python >nul 2>nul
if errorlevel 1 (
    echo [ОШИБКА] Python не найден в PATH.
    echo Установите Python 3.10+ с https://www.python.org/downloads/windows/
    echo При установке ОБЯЗАТЕЛЬНО отметьте "Add Python to PATH".
    pause
    exit /b 1
)

echo Найден Python:
python --version
echo.

REM Спрашиваем GPU
set /p gpu="Есть ли у вас GPU NVIDIA? (y/n) [n]: "
if /i "%gpu%"=="y" (
    set "TORCH_IDX=cu121"
) else (
    set "TORCH_IDX=cpu"
)

echo.
echo Выбран вариант установки: %TORCH_IDX%
echo.

REM Создаём виртуальное окружение
if not exist ".venv" (
    echo Создаю виртуальное окружение .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo [ОШИБКА] Не удалось создать виртуальное окружение.
        pause
        exit /b 1
    )
)

REM Активируем
call ".venv\Scripts\activate.bat"

REM Обновляем pip
echo.
echo Обновляю pip...
python -m pip install --upgrade pip

REM Устанавливаем PyTorch
echo.
echo Устанавливаю PyTorch (%TORCH_IDX%)...
if "%TORCH_IDX%"=="cu121" (
    pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
) else (
    pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
)

REM Устанавливаем остальные зависимости
echo.
echo Устанавливаю остальные зависимости...
pip install -r requirements.txt

REM xformers (только для CUDA)
if "%TORCH_IDX%"=="cu121" (
    echo.
    echo Устанавливаю xformers (опционально, ускоряет на NVIDIA)...
    pip install xformers || echo [ПРЕДУПРЕЖДЕНИЕ] xformers не установился — это не критично, продолжаем.
)

echo.
echo === Установка завершена! ===
echo Теперь запустите run.bat для старта приложения.
echo.
pause
