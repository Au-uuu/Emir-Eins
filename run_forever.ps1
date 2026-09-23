# 带自动重启的机器人启动脚本（Windows）
#
# 配合 watchdog.py 使用：
#   - 看门狗发现长连接静默失效（默认 15 分钟无任何网关消息）会以 exit code 2 退出
#   - 本脚本检测到退出码 2 会自动拉起新进程
#   - 其他退出（如 Ctrl+C 手动停止）不会重启
#
# 用法：.\run_forever.ps1

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$bot = Join-Path $root "bot.py"
$logDir = Join-Path $root "logs"

if (-not (Test-Path $python)) {
    Write-Host "未找到虚拟环境：$python" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path (Join-Path $root ".env"))) {
    Write-Host "未找到 .env，请先复制 .env.example 并填写凭据" -ForegroundColor Red
    exit 1
}

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$round = 0
while ($true) {
    $round++
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] 第 $round 次启动机器人..." -ForegroundColor Green

    & $python $bot
    $code = $LASTEXITCODE

    if ($code -eq 2) {
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] 看门狗判定连接失效，5 秒后自动重启" -ForegroundColor Yellow
        Start-Sleep -Seconds 5
        continue
    }

    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] 机器人已停止（exit=$code），不再重启" -ForegroundColor Yellow
    break
}
