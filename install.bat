@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

echo.
echo === SD Image Generator — Установка ===
echo.

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

set /p gpu="Есть ли у вас GPU NVIDIA? (y/n) [n]: "
if /i "%gpu%"=="y" (
    set "TORCH_IDX=cu121"
) else (
    set "TORCH_IDX=cpu"
)

echo.
echo Выбран вариант установки: %TORCH_IDX%
echo.

if not exist ".venv" (
    echo Создаю виртуальное окружение .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo [ОШИБКА] Не удалось создать виртуальное окружение.
        pause
        exit /b 1
    )
)

call ".venv\Scripts\activate.bat"

echo.
echo Обновляю pip...
python -m pip install --upgrade pip

echo.
echo Устанавливаю PyTorch (%TORCH_IDX%)...
if "%TORCH_IDX%"=="cu121" (
    pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
) else (
    pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
)

echo.
echo Устанавливаю остальные зависимости...
pip install -r requirements.txt

if "%TORCH_IDX%"=="cu121" (
    echo.
    echo Устанавливаю xformers (опционально)...
    pip install xformers || echo [ПРЕДУПРЕЖДЕНИЕ] xformers не установился — это не критично.
)

echo.
echo === Установка завершена! ===
echo Теперь запустите run.bat для старта приложения.
echo.
pause
