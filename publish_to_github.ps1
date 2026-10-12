# ============================================================
# Скрипт публикации проекта на GitHub (PowerShell для Windows)
# ============================================================
# Использование:
#   .\publish_to_github.ps1 -Username <GitHub_USERNAME> -RepoName <REPO_NAME> -Token <TOKEN>
#
# Пример:
#   .\publish_to_github.ps1 -Username johndoe -RepoName sd-app -Token ghp_xxxxxxxxxxxx
#
# Перед запуском:
#   1. Создайте Personal Access Token: https://github.com/settings/tokens/new
#      scope: repo (Full control of private repositories)
#   2. Разрешите выполнение скриптов:
#      Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
# ============================================================

param(
    [Parameter(Mandatory=$true)]
    [string]$Username,

    [Parameter(Mandatory=$true)]
    [string]$RepoName,

    [Parameter(Mandatory=$true)]
    [string]$Token
)

$ErrorActionPreference = "Stop"

Write-Host "[1/5] Проверка git..." -ForegroundColor Cyan
if (-not (Test-Path ".git")) {
    Write-Host "  Инициализация git..."
    git init
    git config user.name $Username
    git config user.email "$Username@users.noreply.github.com"
    git branch -M main
}

# Проверка коммита
$hasCommit = git log --oneline 2>$null | Select-Object -First 1
if (-not $hasCommit) {
    Write-Host "  Создание первого коммита..."
    git add .
    git commit -m "Initial commit: SD Image Generator + GPON FTTH Planner v1.2"
}

Write-Host "[2/5] Создание репозитория на GitHub: $Username/$RepoName ..." -ForegroundColor Cyan
$body = @{
    name = $RepoName
    private = $false
    description = "Local desktop app: SD Image Generator + GPON FTTH Planner (CPU-optimized)"
} | ConvertTo-Json

$headers = @{
    "Authorization" = "token $Token"
    "Accept" = "application/vnd.github.v3+json"
}

try {
    $response = Invoke-RestMethod -Uri "https://api.github.com/user/repos" -Method Post -Headers $headers -Body $body
    $htmlUrl = $response.html_url
    Write-Host "  ✓ Репозиторий создан: $htmlUrl" -ForegroundColor Green
} catch {
    Write-Host "ОШИБКА: Не удалось создать репозиторий." -ForegroundColor Red
    Write-Host $_.Exception.Message
    exit 1
}

Write-Host "[3/5] Добавление remote 'origin'..." -ForegroundColor Cyan
git remote remove origin 2>$null
$remoteUrl = "https://$Username`:$Token@github.com/$Username/$RepoName.git"
git remote add origin $remoteUrl

Write-Host "[4/5] Push в GitHub..." -ForegroundColor Cyan
git push -u origin main

Write-Host "[5/5] Очистка токена из remote URL..." -ForegroundColor Cyan
$cleanUrl = "https://github.com/$Username/$RepoName.git"
git remote set-url origin $cleanUrl

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "✅ ГОТОВО!" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host "Репозиторий: $htmlUrl"
Write-Host "Клонирование: git clone $htmlUrl.git"
Write-Host ""
Write-Host "Для будущих обновлений:"
Write-Host "  git add . && git commit -m 'Update' && git push"
Write-Host ""
Write-Host "⚠ Не забудьте заменить 'your-username' в README.md и pyproject.toml" -ForegroundColor Yellow
Write-Host "  на '$Username' (поиском по файлу)."
