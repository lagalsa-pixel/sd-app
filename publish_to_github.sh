#!/bin/bash
# Скрипт публикации проекта на GitHub
# Использование: ./publish_to_github.sh <GitHub_USERNAME> <REPO_NAME> <TOKEN>

set -e

if [ "$#" -ne 3 ]; then
    echo "Использование: $0 <GitHub_USERNAME> <REPO_NAME> <TOKEN>"
    echo "Пример: $0 lagalsa-pixel sd-app ghp_xxxxxxxxxxxx"
    exit 1
fi

USERNAME="$1"
REPO_NAME="$2"
TOKEN="$3"

if [ ! -d ".git" ]; then
    git init
    git config user.name "$USERNAME"
    git config user.email "$USERNAME@users.noreply.github.com"
    git branch -M main
fi

if ! git log --oneline | head -1 > /dev/null 2>&1; then
    git add .
    git commit -m "Initial commit: SD Image Generator + GPON FTTH Planner v1.2"
fi

echo "[2/5] Создание репозитория на GitHub: $USERNAME/$REPO_NAME ..."
RESPONSE=$(curl -s -X POST \
    -H "Authorization: token $TOKEN" \
    -H "Accept: application/vnd.github.v3+json" \
    https://api.github.com/user/repos \
    -d "{\"name\": \"$REPO_NAME\", \"private\": false, \"description\": \"Local desktop app: SD Image Generator + GPON FTTH Planner (CPU-optimized)\"}")

HTML_URL=$(echo "$RESPONSE" | grep -o '"html_url": *"[^"]*"' | head -1 | cut -d'"' -f4)
if [ -z "$HTML_URL" ]; then
    echo "ОШИБКА: Не удалось создать репозиторий."
    echo "$RESPONSE" | head -20
    exit 1
fi
echo "  ✓ Репозиторий создан: $HTML_URL"

echo "[3/5] Добавление remote 'origin'..."
git remote remove origin 2>/dev/null || true
git remote add origin "https://$USERNAME:$TOKEN@github.com/$USERNAME/$REPO_NAME.git"

echo "[4/5] Push в GitHub..."
git push -u origin main

echo "[5/5] Очистка токена из remote URL..."
git remote set-url origin "https://github.com/$USERNAME/$REPO_NAME.git"

echo ""
echo "============================================================"
echo "✅ ГОТОВО!"
echo "============================================================"
echo "Репозиторий: $HTML_URL"
echo "Клонирование: git clone $HTML_URL.git"
