# Скрипт публикации проекта на GitHub (PowerShell для Windows)
# Использование: .\publish_to_github.ps1 -Username <USERNAME> -RepoName <REPO_NAME> -Token <TOKEN>

param(
    [Parameter(Mandatory=$true)] [string]$Username,
    [Parameter(Mandatory=$true)] [string]$RepoName,
    [Parameter(Mandatory=$true)] [string]$Token
)

$ErrorActionPreference = "Stop"

Write-Host "[1/5] Проверка git..." -ForegroundColor Cyan
if (-not (Test-Path ".git")) {
    git init
    git config user.name $Username
    git config user.email "$Username@users.noreply.github.com"
    git branch -M main
}

$hasCommit = git log --oneline 2>$null | Select-Object -First 1
if (-not $hasCommit) {
    git add .
    git commit -m "Initial commit: SD Image Generator + GPON FTTH Planner v1.2"
}

Write-Host "[2/5] Создание репозитория..." -ForegroundColor Cyan
$body = @{ name = $RepoName; private = $false; description = "Local desktop app: SD Image Generator + GPON FTTH Planner (CPU-optimized)" } | ConvertTo-Json
$headers = @{ "Authorization" = "token $Token"; "Accept" = "application/vnd.github.v3+json" }

try {
    $response = Invoke-RestMethod -Uri "https://api.github.com/user/repos" -Method Post -Headers $headers -Body $body
    $htmlUrl = $response.html_url
    Write-Host "  ✓ Репозиторий создан: $htmlUrl" -ForegroundColor Green
} catch {
    Write-Host "ОШИБКА: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

Write-Host "[3/5] Добавление remote..." -ForegroundColor Cyan
git remote remove origin 2>$null
git remote add origin "https://$Username`:$Token@github.com/$Username/$RepoName.git"

Write-Host "[4/5] Push..." -ForegroundColor Cyan
git push -u origin main

Write-Host "[5/5] Очистка токена..." -ForegroundColor Cyan
git remote set-url origin "https://github.com/$Username/$RepoName.git"

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "✅ ГОТОВО!" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host "Репозиторий: $htmlUrl"
