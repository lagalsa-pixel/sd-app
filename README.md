# Multi-tool Desktop App — SD Generator + GPON FTTH Planner

[![Tests](https://github.com/lagalsa-pixel/sd-app/actions/workflows/tests.yml/badge.svg)](https://github.com/lagalsa-pixel/sd-app/actions/workflows/tests.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform: Windows](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey.svg)](#)

**Локальное настольное приложение с двумя ИИ-инструментами:**

1. **🎨 SD Image Generator** — генерация изображений через Stable Diffusion
   (полностью офлайн после первой загрузки моделей).

2. **📡 GPON FTTH Planner** — планирование оптической сети FTTH для сельских
   населённых пунктов на основе алгоритма
   [`gpon-ftth-planner`](https://github.com/lagalsa-pixel/gpon-ftth-planner) v1.1,
   **оптимизированного для CPU**, с **отрисовкой карты сети** на спутниковой
   мозаике Google Satellite z18.

## ✨ Возможности

### 🎨 SD Image Generator

- **Текст → изображение** и **Изображение → изображение** режимы
- **Русский интерфейс + авто-перевод промптов** (локальная модель)
- **Полностью офлайн** после первой загрузки моделей
- **CPU/GPU**: работает на любом железе (медленнее на CPU)
- Управление памятью, сохранение в PNG/JPEG/WEBP

### 📡 GPON FTTH Planner

- **Векторизация** через NumPy/SciPy (вместо Python-циклов)
- **Sparse CSR-матрицы** для дорожного графа
- **KDTree** для O(log n) поиска ближайших узлов
- **Один вызов Dijkstra** (вместо K вызовов) — ускорение в K раз
- **Float32** для экономии памяти на больших графах
- **Кэширование OSM** на диске (повторный запуск быстрее)
- **🗺 Отрисовка карты сети** на спутниковой мозаике Google Satellite z18
  со слоями: зоны (выпуклые оболочки), дропы, ствол, фидеры (пунктир),
  муфты, ДХ, зонные ОРШ, ЦУ + легенда с авто-размещением + масштабная линейка

### 🔄 Автообновление зависимостей

Приложение **автоматически проверяет и обновляет зависимости** при каждом
запуске (через модуль `updater.py`):

1. **Критичные пакеты** (`Pillow`, `numpy`, `scipy`, `requests`) — устанавливаются автоматически
2. **SD-пакеты** (`torch`, `diffusers`, и т.д.) — спросит пользователя

## Быстрый старт

### Вариант A: install.bat + run.bat (рекомендуется)

1. Установите Python 3.10+ с https://www.python.org/downloads/windows/
   **ВАЖНО:** при установке отметьте галочку **"Add Python to PATH"**.
2. Клонируйте репозиторий:
   ```bash
   git clone https://github.com/lagalsa-pixel/sd-app.git
   cd sd-app
   ```
3. Запустите **`install.bat`** — установит зависимости в `.venv`
4. Запустите **`run.bat`** — откроется окно приложения

### Вариант B: Без install.bat (автообновление само всё установит)

1. Установите Python 3.10+ (с галочкой "Add Python to PATH")
2. Клонируйте репозиторий и просто запустите **`run.bat`**
3. Приложение само проверит и установит недостающие пакеты

### Вариант C: Из исходников (Linux/macOS)

```bash
git clone https://github.com/lagalsa-pixel/sd-app.git
cd sd-app

python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# .venv\Scripts\activate  # Windows

pip install -r requirements.txt
# Только для SD (CPU):
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu

python main.py
```

## Системные требования

| Компонент | Минимум | Рекомендуется |
|-----------|---------|---------------|
| ОС | Windows 10 64-bit | Windows 11 / Linux / macOS |
| Python | 3.10 | 3.11 / 3.12 |
| ОЗУ | 8 ГБ | 16 ГБ+ |
| VRAM (если GPU) | 4 ГБ | 6 ГБ+ |
| Место на диске | ~6 ГБ | ~10 ГБ |

## Использование GPON FTTH Planner

1. Перейдите на вкладку **"📡 GPON FTTH Planner"**
2. Заполните форму:
   - **Имя села** (латиницей)
   - **Широта, Долгота** (из OSM/Google Maps — правый клик)
   - **Ожидаемое число ДХ**
   - **Радиус поиска, м**
   - **Тип зоны:** bbox (авто) или radius (фиксированный)
   - **S_MIN** — порог окупаемости зонного ОРШ (по умолчанию 15 вол-км)
   - **MIN_ZONE** — мин. ДХ в зоне (по умолчанию 48 = заполнение 75%)
3. Включите чекбоксы:
   - **🗺 Отрисовать карту сети** — для визуализации
   - **📥 Скачать спутниковые тайлы Google z18** — для спутниковой подложки
4. Нажмите **"🚀 Запустить планирование"**
5. Дождитесь завершения — в правой панели появится отчёт + карта

### Что вы получите

- **Сеть:** ДХ, муфты, магистраль, дропы
- **BoQ схема A:** централизованная (сплиттеры в ЦУ)
- **BoQ схема D:** зонные ОРШ (рекомендуется) с экономией волокно-км
- **Оптический бюджет:** затухание худшей линии (Class B+/C+)
- **Карта:** JPG полное разрешение + PNG превью в UI

### Статусы оптического бюджета

- **ok** — зона годна, маржа ≥ 3 дБ
- **ok-hard** — годна, но запас < 3 дБ → рекомендация Class C+ или 1:32
- **EXCEEDED** — превышение 28 дБ → редизайн зоны

## Структура проекта

```
sd-app/
├── main.py            # GUI приложение (2 вкладки)
├── gpon_planner.py    # Оптимизированный для CPU FTTH-планировщик
├── ftth_renderer.py   # Отрисовка карты сети (Pillow + Google Satellite)
├── generator.py       # Обёртка над Stable Diffusion
├── translator.py      # Локальный переводчик RU→EN
├── updater.py         # Автообновление зависимостей при запуске
├── config.py          # Конфигурация SD-генератора
├── requirements.txt   # Python-зависимости
├── pyproject.toml     # Метаданные пакета (PEP 517/518)
├── install.bat        # Установка (Windows)
├── run.bat            # Запуск (Windows)
├── build_exe.bat      # Сборка .exe (Windows)
├── README.md          # Этот файл
├── LICENSE            # MIT лицензия
├── CONTRIBUTING.md    # Инструкции для контрибьюторов
├── .gitignore         # Git ignore patterns
├── .github/workflows/
│   └── tests.yml      # CI: проверка синтаксиса и тесты
│
├── config.json        # (создаётся при первом запуске)
├── app.log            # (журнал работы)
├── models/            # (папка для SD-моделей)
├── output/            # (сюда сохраняются картинки)
└── gpon_work/         # (рабочая папка GPON)
    ├── osm_raw/       # кэш OSM XML
    ├── tiles/g18/     # кэш спутниковых тайлов
    ├── maps/          # отрисованные карты (JPG + PNG)
    └── <имя_села>_result.json
```

## Оптимизации для CPU

1. **Один вызов Dijkstra для всех терминалов** — ускорение в K раз
2. **Векторизованная детекция границы села** через scipy.ndimage
3. **KDTree** для O(log n) поиска ближайших узлов
4. **CSR-матрицы** для дорожного графа
5. **Float32** для экономии памяти
6. **Кэш OSM на диске**

## Решение проблем

### GPON: "Не найдено ни одного домохозяйства"
→ В OSM нет разметки зданий в этом районе. Попробуйте другое село или
   увеличьте радиус поиска.

### GPON: карта не отрисовалась
→ Возможные причины:
   - выключен чекбокс "Отрисовать карту сети"
   - нет интернета для скачивания тайлов (выключите "Скачать спутниковые тайлы")
   - `Pillow` не установлен (`pip install Pillow`)

### SD: "ModuleNotFoundError: diffusers"
→ Запустите `install.bat` ещё раз или:
   ```
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```

### Автообновление не работает
→ Возможные причины:
   - Нет интернета
   - `pip` не установлен → `python -m ensurepip --upgrade`
   - Нет прав → запустите CMD от администратора

## Ссылки

- **Алгоритм FTTH:** https://github.com/lagalsa-pixel/gpon-ftth-planner
- **Документация алгоритма:** `ALGORITHM_FTTH.md` v1.1 в репозитории
- **Stable Diffusion:** https://huggingface.co/runwayml/stable-diffusion-v1-5
- **OSM API:** https://api.openstreetmap.org/

## Лицензии

Код приложения — MIT (см. [LICENSE](LICENSE)).
Модели Stable Diffusion имеют собственные лицензии (см. Hugging Face).
Данные OSM — ODbL 1.0.
