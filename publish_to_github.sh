#!/bin/bash
# ============================================================
# Скрипт публикации проекта на GitHub
# ============================================================
# Использование:
#   1. Создайте Personal Access Token на GitHub:
#      https://github.com/settings/tokens/new
#      scope: repo (Full control of private repositories)
#      expiration: 30 days (или больше)
#
#   2. Запустите этот скрипт с параметрами:
#      ./publish_to_github.sh <GitHub_USERNAME> <REPO_NAME> <TOKEN>
#
#   Пример:
#      ./publish_to_github.sh johndoe sd-app ghp_xxxxxxxxxxxx
#
#   3. Скрипт:
#      - создаст репозиторий на GitHub через API
#      - добавит remote 'origin'
#      - запушит код
#      - выведет URL готового репозитория
# ============================================================

set -e

if [ "$#" -ne 3 ]; then
    echo "Использование: $0 <GitHub_USERNAME> <REPO_NAME> <TOKEN>"
    echo ""
    echo "Пример:"
    echo "  $0 johndoe sd-app ghp_xxxxxxxxxxxx"
    exit 1
fi

USERNAME="$1"
REPO_NAME="$2"
TOKEN="$3"

# Проверка, что мы в git-репозитории
if [ ! -d ".git" ]; then
    echo "[1/5] Инициализация git..."
    git init
    git config user.name "$USERNAME"
    git config user.email "$USERNAME@users.noreply.github.com"
    git branch -M main
fi

# Проверка, что есть хотя бы один коммит
if ! git log --oneline | head -1 > /dev/null 2>&1; then
    echo "[1/5] Создание первого коммита..."
    git add .
    git commit -m "Initial commit: SD Image Generator + GPON FTTH Planner v1.2

Features:
- SD Image Generator (Stable Diffusion, offline, CPU/GPU)
- GPON FTTH Planner (algorithm from lagalsa-pixel/gpon-ftth-planner v1.1,
  optimized for CPU with vectorized numpy/scipy operations)
- FTTH network map rendering on Google Satellite z18 mosaic
- Local RU-EN prompt translator (Helsinki-NLP/opus-mt-ru-en)
- Auto-update dependencies on startup (updater.py)
- Two-tab Tkinter GUI"
fi

echo "[2/5] Создание репозитория на GitHub: $USERNAME/$REPO_NAME ..."
RESPONSE=$(curl -s -X POST \
    -H "Authorization: token $TOKEN" \
    -H "Accept: application/vnd.github.v3+json" \
    https://api.github.com/user/repos \
    -d "{\"name\": \"$REPO_NAME\", \"private\": false, \"description\": \"Local desktop app: SD Image Generator + GPON FTTH Planner (CPU-optimized)\"}")

# Проверка успеха
HTML_URL=$(echo "$RESPONSE" | grep -o '"html_url": *"[^"]*"' | head -1 | cut -d'"' -f4)
if [ -z "$HTML_URL" ]; then
    echo "ОШИБКА: Не удалось создать репозиторий."
    echo "Ответ GitHub:"
    echo "$RESPONSE" | head -20
    exit 1
fi
echo "  ✓ Репозиторий создан: $HTML_URL"

echo "[3/5] Добавление remote 'origin'..."
# Удаляем старый origin если есть
git remote remove origin 2>/dev/null || true
git remote add origin "https://$USERNAME:$TOKEN@github.com/$USERNAME/$REPO_NAME.git"

echo "[4/5] Push в GitHub..."
git push -u origin main

echo "[5/5] Очистка токена из remote URL (безопасность)..."
git remote set-url origin "https://github.com/$USERNAME/$REPO_NAME.git"

echo ""
echo "============================================================"
echo "✅ ГОТОВО!"
echo "============================================================"
echo "Репозиторий: $HTML_URL"
echo "Клонирование: git clone $HTML_URL.git"
echo ""
echo "Для будущих обновлений:"
echo "  git add . && git commit -m 'Update' && git push"
echo ""
echo "⚠ Не забудьте заменить 'your-username' в README.md и pyproject.toml"
echo "  на '$USERNAME' (поиском по файлу)."
