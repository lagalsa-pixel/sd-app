# SD App Portable — версия без установки для Windows 10 64-bit

## 🚀 Быстрый старт

### Вариант 1: Получить готовую portable-версию

1. Скачайте `sd-app-portable.zip` со страницы [Releases](../../releases)
2. Распакуйте в любую папку (например, `C:\sd-app\` или на USB-флешку)
3. Запустите **`sd-app-portable.bat`** двойным кликом
4. Готово! Приложение откроется без установки

### Вариант 2: Собрать portable-версию самостоятельно

1. Клонируйте репозиторий:
   ```bash
   git clone https://github.com/lagalsa-pixel/sd-app.git
   cd sd-app
   ```

2. Запустите **`build_portable.bat`** двойным кликом
   - Скрипт скачает Python 3.12 embedded (~10 МБ)
   - Установит numpy, scipy, Pillow, requests (~30 МБ)
   - Скопирует файлы приложения
   - Создаст папку `sd-app-portable\`

3. Запустите **`sd-app-portable\sd-app-portable.bat`**

4. Для распространения запакуйте `sd-app-portable\` в ZIP

---

## 📋 Что входит в portable-версию

```
sd-app-portable/
├── sd-app-portable.bat    ← запуск приложения
├── python/                 ← Python 3.12 embedded (~40 МБ)
│   ├── python.exe
│   ├── python312.dll
│   ├── Lib/
│   ├── tcl/
│   └── site-packages/
│       ├── numpy/          ← ~15 МБ
│       ├── scipy/          ← ~30 МБ
│       ├── PIL/            ← ~5 МБ
│       └── requests/       ← ~1 МБ
└── app/                    ← приложение
    ├── main.py
    ├── gpon_planner.py
    ├── ftth_renderer.py
    ├── map_picker.py
    ├── generator.py
    ├── translator.py
    ├── updater.py
    ├── config.py
    ├── requirements.txt
    ├── README.md
    ├── LICENSE
    ├── output/             ← сюда сохраняются картинки
    ├── models/             ← сюда качаются SD-модели
    └── gpon_work/          ← рабочая папка GPON
        ├── osm_raw/        ← кэш OSM
        ├── tiles/          ← кэш тайлов
        └── maps/           ← отрисованные карты
```

**Итоговый размер:** ~80 МБ (без SD-пакетов)

---

## ✅ Преимущества portable-версии

| Параметр | Portable | Обычная установка |
|----------|----------|------------------|
| Python на ПК | НЕ нужен | нужен |
| Права администратора | НЕ нужны | нужны для pip |
| Установка пакетов | НЕ нужна | install.bat |
| Размер | ~80 МБ | ~6 ГБ (с SD) |
| Запуск с USB | ✅ | ❌ |
| GPON Planner | ✅ сразу | ✅ |
| SD Generator | ⚠ после установки SD-пакетов | ✅ |

---

## ⚠️ SD Image Generator в portable-версии

SD-пакеты (torch, diffusers, transformers) **не включены** в portable-версию, потому что:
- Они весят ~2 ГБ (с torch CPU)
- Не всем нужен SD Generator
- При первом запуске `updater.py` предложит их установить

### Установка SD-пакетов в portable-версию

1. Запустите `sd-app-portable.bat`
2. Приложение покажет диалог: "Установить SD-пакеты? (~2 ГБ)"
3. Нажмите **"Да"**
4. Дождитесь установки (5-15 минут)
5. Перезапустите приложение

SD-пакеты установятся в `python\Lib\site-packages\`.

---

## 🔧 Альтернатива: .exe через PyInstaller

Если нужен один .exe файл вместо папки:

1. Установите Python 3.10+ на любой ПК
2. Запустите **`build_exe.bat`**
3. Готовый `.exe` будет в `dist\SDImageGenerator.exe`

| Параметр | Portable (папка) | .exe (PyInstaller) |
|----------|------------------|-------------------|
| Размер | ~80 МБ | ~50 МБ (без SD) или ~700 МБ (с SD) |
| Первый запуск | мгновенно | 5-15 сек (распаковка) |
| Обновление | замена файлов | пересборка |
| Антивирус | дружелюбно | часто ложные срабатывания |

---

## 🛠 Решение проблем

### "python\python.exe не найден"
→ Запустите `build_portable.bat` для создания полной сборки

### "ModuleNotFoundError: No module named 'numpy'"
→ Переустановите зависимости:
```cmd
cd sd-app-portable
python\python.exe -m pip install numpy scipy Pillow requests
```

### Приложение не запускается
→ Проверьте лог: `app\app.log`
→ Запустите из командной строки для просмотра ошибок:
```cmd
cd sd-app-portable
python\python.exe app\main.py
```

### Антивирус блокирует .exe (PyInstaller)
→ Это ложное срабатывание. Добавьте в исключения или используйте portable-версию

### Нет интернет-соединения
→ GPON Planner требует интернет для:
  - Скачивания OSM-данных (при первом запуске для каждого села)
  - Скачивания спутниковых тайлов Google z16/z18
→ После первого запуска кэш сохраняется в `app\gpon_work\`
→ Повторные запуски того же села работают офлайн

---

## 📦 Создание ZIP для распространения

```cmd
cd sd-app-portable
:: Удалите кэш и временные файлы
rmdir /s /q app\gpon_work\osm_raw
rmdir /s /q app\gpon_work\tiles
rmdir /s /q app\gpon_work\maps
del /q app\app.log
del /q app\config.json

:: Запакуйте
powershell Compress-Archive -Path * -DestinationPath ..\sd-app-portable.zip -CompressionLevel Optimal
```

Готовый `sd-app-portable.zip` (~80 МБ) можно распространять.
