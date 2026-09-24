# Содействие проекту

Спасибо за интерес к проекту! Ниже описано, как внести вклад.

## Как внести вклад

### Сообщение об ошибке

1. Проверьте, что баг ещё не описан в [Issues](../../issues)
2. Создайте новый issue с заголовком вида: `[BUG] Краткое описание`
3. В теле укажите:
   - Версия Python (`python --version`)
   - Версия ОС (Windows 10/11, macOS, Linux)
   - Шаги для воспроизведения
   - Ожидаемое и фактическое поведение
   - Содержимое `app.log` (если есть)

### Предложение новой функции

1. Создайте issue с заголовком `[FEATURE] Краткое описание`
2. Опишите:
   - Какую задачу решает функция
   - Как вы видите интерфейс
   - Альтернативы, которые вы рассматривали

### Pull Request

1. Форкните репозиторий
2. Создайте ветку: `git checkout -b feature/amazing-feature`
3. Закоммитьте изменения: `git commit -m 'Add amazing feature'`
4. Запушьте: `git push origin feature/amazing-feature`
5. Откройте Pull Request

## Стиль кода

- Python 3.10+
- Type hints где возможно
- Docstrings для всех функций
- Длина строки: 100 символов
- Имена: `snake_case` для функций и переменных, `PascalCase` для классов
- Файл должен заканчиваться пустой строкой

## Структура кода

```
main.py            # GUI приложение (вкладки SD + GPON)
gpon_planner.py    # FTTH-планировщик (CPU-оптимизированный)
ftth_renderer.py   # Отрисовка карты сети на спутниковой мозаике
generator.py       # Обёртка над Stable Diffusion
translator.py      # Локальный переводчик RU→EN
updater.py         # Автообновление зависимостей при запуске
config.py          # Конфигурация SD-генератора
```

## Тестирование

Перед PR запустите тесты:

```bash
# Синтаксис
python -m py_compile *.py

# Базовые тесты
python -c "
import sys; sys.path.insert(0, '.')
from gpon_planner import decompose, ceilr
assert decompose(8, [8, 12, 16]) == [8]
assert ceilr(770.0000001) == 770
print('OK')
"

# Updater
python updater.py --check-only --no-sd
```

## Лицензия

Внося вклад, вы соглашаетесь, что ваш код будет распространяться
под лицензией MIT (см. [LICENSE](LICENSE)).
