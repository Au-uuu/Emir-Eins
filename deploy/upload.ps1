<#
.SYNOPSIS
    把本地代码上传到服务器并重启机器人（日常更新用）。

.DESCRIPTION
    DEPLOY.md 讲的是「第一次把机器人部署到服务器」；本脚本解决之后每次改完代码
    怎么更新上去。它做四件事：

      1. 把仓库里所有 *.py（含 _test_*.py）经 scp 送到服务器 /tmp/
      2. 服务器上先把旧文件备份到 /root/<名>.bak-<时间戳>，再覆盖安装
      3. **逐个比对 SHA256**，确认线上就是本地这一份（不一致就报错退出）
      4. 重启服务、打印启动日志；并在服务器上跑全部测试套件

    为什么要经 /tmp 中转而不是直接覆盖：安装需要 root，而登录用的是 ubuntu 用户。
    先 scp 到 ubuntu 可写的 /tmp，再用 sudo 拷进安装目录。

    为什么用 base64 传远程脚本：PowerShell 会吃掉内嵌引号，直接拼 bash 命令经常在
    中文、引号、$ 上翻车；base64 编码后远程 `base64 -d | bash` 最稳。

.PARAMETER Server
    登录目标，默认 ubuntu@124.223.62.16。

.PARAMETER KeyFile
    SSH 私钥路径，默认 C:\DSH\.keys\qqbot-tencent.pem。

.PARAMETER KnownHosts
    用于校验服务器主机指纹的文件，默认 C:\DSH\.keys\known_hosts。

.PARAMETER AppDir
    服务器上的安装目录，默认 /opt/qqbot。

.PARAMETER SkipTests
    不在服务器上跑测试套件（约十几秒，建议保留）。

.PARAMETER NoRestart
    只上传不重启（改文档、加测试文件时可用）。

.PARAMETER DryRun
    只打印将上传的文件清单和远程步骤，不连服务器。

.EXAMPLE
    .\deploy\upload.ps1
    最常用：上传 + 服务器跑测试 + 重启 + 打印启动日志。

.EXAMPLE
    .\deploy\upload.ps1 -NoRestart -SkipTests
    只更新文件，不跑测试不重启（改动不会生效）。

.EXAMPLE
    .\deploy\upload.ps1 -DryRun
    先看看会上传哪些文件。

.NOTES
    回滚：服务器 /root/<文件名>.bak-<时间戳> 是每次上传前的旧版本，例如
      sudo cp /root/bot.py.bak-2026-10-05-1208 /opt/qqbot/bot.py
      sudo systemctl restart qqbot
#>
[CmdletBinding()]
param(
    [string]$Server = 'ubuntu@124.223.62.16',
    [string]$KeyFile = 'C:\DSH\.keys\qqbot-tencent.pem',
    [string]$KnownHosts = 'C:\DSH\.keys\known_hosts',
    [string]$AppDir = '/opt/qqbot',
    [switch]$SkipTests,
    [switch]$NoRestart,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

# 中文输出：不设的话 Windows 控制台按 GBK 解释 SSH 回来的 UTF-8，日志全是乱码
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ssh = Join-Path $env:SystemRoot 'System32\OpenSSH\ssh.exe'
$scp = Join-Path $env:SystemRoot 'System32\OpenSSH\scp.exe'
foreach ($exe in @($ssh, $scp)) {
    if (-not (Test-Path $exe)) { throw "找不到 $exe（Windows 10 1809+ 自带 OpenSSH 客户端）" }
}
if (-not (Test-Path $KeyFile)) { throw "找不到私钥：$KeyFile" }

# 仓库根 = 本脚本所在目录（deploy/）的上一级
$root = Split-Path -Parent $PSScriptRoot
$files = Get-ChildItem -Path $root -Filter '*.py' -File |
    Sort-Object Name | Select-Object -ExpandProperty Name
if (-not $files) { throw "在 $root 下没找到任何 .py" }

# 角色卡等人设素材：不带上的话改了人设也不会生效（教训）
$personaDir = Join-Path $root 'persona'
$personaFiles = if (Test-Path $personaDir) {
    Get-ChildItem -Path $personaDir -File | Sort-Object Name | Select-Object -ExpandProperty Name
}
else { @() }

$opts = @('-i', $KeyFile, '-o', 'BatchMode=yes', '-o', "UserKnownHostsFile=$KnownHosts")

Write-Host "仓库根目录 : $root"
Write-Host "目标服务器 : $Server ($AppDir)"
Write-Host "待上传文件 : $($files.Count) 个 .py + $($personaFiles.Count) 个 persona 素材"
$files | ForEach-Object { Write-Host "             $_" }
$personaFiles | ForEach-Object { Write-Host "             persona/$_" }

if ($DryRun) {
    Write-Host ''
    Write-Host '[DryRun] 远程将执行：备份 → 覆盖安装 → 校验 SHA256 → 跑测试 → 重启 → 打印日志'
    return
}

# ---------- 1) 本地哈希（供远程安装后比对） ----------
$localHashes = @{}
foreach ($f in $files) {
    $localHashes[$f] = (Get-FileHash (Join-Path $root $f) -Algorithm SHA256).Hash.ToLower()
}
foreach ($f in $personaFiles) {
    $localHashes["persona/$f"] = (Get-FileHash (Join-Path $personaDir $f) -Algorithm SHA256).Hash.ToLower()
}

# ---------- 2) 上传到 /tmp ----------
Write-Host ''
Write-Host '[1/4] 上传到服务器 /tmp ...' -ForegroundColor Cyan
$paths = @($files | ForEach-Object { Join-Path $root $_ })
if ($personaFiles.Count) {
    $paths += @($personaFiles | ForEach-Object { Join-Path $personaDir $_ })
}
& $scp @opts @paths "${Server}:/tmp/"
if ($LASTEXITCODE -ne 0) { throw "scp 失败（exit $LASTEXITCODE）" }

# ---------- 3) 生成并执行远程脚本 ----------
Write-Host '[2/4] 服务器端备份并安装 ...' -ForegroundColor Cyan

$testCmd = if ($SkipTests) { 'echo "（-SkipTests：跳过测试）"' } else {
    'for t in _test_*.py; do r=$(sudo -n $APP/.venv/bin/python $t 2>/dev/null | tail -1); echo "  $t: $r"; done'
}
$restartCmd = if ($NoRestart) {
    'echo "（-NoRestart：未重启，改动尚未生效）"'
} else {
    'sudo -n systemctl restart qqbot; sleep 12; ' +
    'echo "服务状态: $(systemctl is-active qqbot)"; ' +
    'sudo -n journalctl -u qqbot --since "1 minute ago" --no-pager | grep -E "已上线|启用|轮询|ERROR" | tail -8'
}

# 每个文件打一行 "  <标签> <哈希前12位>"，供本地解析比对。
# ⚠️ $APP / $_ 的转义：这里是**双引号**字符串，$APP 会被 PowerShell 当自己的变量
#    展开成空串（踩过），远程要的是字面量，所以写成 `$APP。
$hashTargets = @()
foreach ($f in $files) { $hashTargets += , @($f, "`$APP/$f") }
foreach ($f in $personaFiles) { $hashTargets += , @("persona/$f", "`$APP/persona/$f") }

$hashCheck = ($hashTargets | ForEach-Object {
        "echo -n `"  $($_[0]) `"; sudo -n sha256sum $($_[1]) | cut -c1-12"
    }) -join "`n"

$remote = @'
set -e
APP="__APPDIR__"
TS=$(date +%F-%H%M)

echo "--- 备份旧版本到 /root/（后缀 $TS）---"
for f in __FILES__; do
  [ -f "$APP/$f" ] && sudo -n cp "$APP/$f" "/root/$f.bak-$TS"
done
for f in __PERSONA__; do
  [ -f "$APP/persona/$f" ] && sudo -n cp "$APP/persona/$f" "/root/persona-$f.bak-$TS"
done
echo "备份完成"

echo "--- 覆盖安装 ---"
for f in __FILES__; do
  sudo -n cp "/tmp/$f" "$APP/$f"
done
sudo -n chmod 644 $APP/*.py

if [ -n "__PERSONA__" ]; then
  echo "--- 安装 persona 素材 ---"
  sudo -n mkdir -p "$APP/persona"
  for f in __PERSONA__; do
    sudo -n cp "/tmp/$f" "$APP/persona/$f"
  done
  sudo -n chmod 644 "$APP/persona"/*
fi

echo "--- 线上文件哈希 ---"
__HASHCHECK__

echo "--- 服务器上跑测试套件 ---"
cd $APP
__TESTS__

__RESTART__
'@

$remote = $remote.Replace('__APPDIR__', $AppDir).
    Replace('__FILES__', ($files -join ' ')).
    Replace('__PERSONA__', ($personaFiles -join ' ')).
    Replace('__HASHCHECK__', $hashCheck).
    Replace('__TESTS__', $testCmd).
    Replace('__RESTART__', $restartCmd)

$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($remote))
$output = & $ssh @opts $Server "echo $b64 | base64 -d | bash" 2>&1 | Out-String
Write-Host $output
if ($LASTEXITCODE -ne 0) { throw "远程安装失败（exit $LASTEXITCODE）" }

# ---------- 4) 逐文件比对哈希（真比对，不一致就失败） ----------
Write-Host '[3/4] 校验线上文件与本地一致 ...' -ForegroundColor Cyan
$remoteHashes = @{}
foreach ($line in ($output -split "\r?\n")) {
    # 行形如 "  bot.py a2519c01b3b6" 或 "  persona/依蜜尔爱因.md 401b869518bf"
    if ($line -match '^\s+(\S+)\s+([0-9a-f]{12})\s*$') {
        $remoteHashes[$Matches[1]] = $Matches[2]
    }
}

$bad = @()
foreach ($label in $localHashes.Keys) {
    $local = $localHashes[$label].Substring(0, 12)
    $remoteHash = $remoteHashes[$label]
    if ($remoteHash -eq $local) {
        Write-Host ("  [OK]   {0} {1}" -f $label.PadRight(30), $local)
    }
    else {
        $shown = if ($remoteHash) { $remoteHash } else { '未取到' }
        Write-Host ("  [FAIL] {0} 本地 {1} / 线上 {2}" -f $label.PadRight(30), $local, $shown) -ForegroundColor Red
        $bad += $label
    }
}
if ($bad.Count) { throw "以下文件线上与本地不一致：$($bad -join ', ')" }

Write-Host '[4/4] 完成' -ForegroundColor Green
if ($NoRestart) {
    Write-Host '提醒：未重启，改动尚未生效。需要时执行：' -ForegroundColor Yellow
    Write-Host "  ssh -i $KeyFile $Server 'sudo systemctl restart qqbot'"
}
