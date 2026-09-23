# 启动机器人（Windows）
# 用法：右键“使用 PowerShell 运行”，或在终端执行  .\start.ps1

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

if (-not (Test-Path "$root\.venv\Scripts\python.exe")) {
    Write-Host "未找到虚拟环境 .venv" -ForegroundColor Red
    Write-Host "请先执行：" -ForegroundColor Yellow
    Write-Host "  python -m venv .venv"
    Write-Host "  .\.venv\Scripts\python.exe -m pip install -r requirements.txt"
    exit 1
}

if (-not (Test-Path "$root\.env")) {
    Write-Host "未找到 .env，请复制 .env.example 为 .env 并填写凭据" -ForegroundColor Red
    exit 1
}

Write-Host "正在启动 QQ 机器人（Ctrl+C 停止）..." -ForegroundColor Green
& "$root\.venv\Scripts\python.exe" "$root\bot.py"
