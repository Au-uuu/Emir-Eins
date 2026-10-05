# 日常更新：把本地代码上传到服务器

`DEPLOY.md` 讲的是**第一次**把机器人部署到服务器（装 Python、建 venv、注册 systemd）。
这篇讲之后**每次改完代码怎么更新上去**——也就是「上传服务器」。

- 服务器：`ubuntu@124.223.62.16`（腾讯云轻量，国内节点）
- 安装目录：`/opt/qqbot`，systemd 服务名 `qqbot`
- 登录方式：SSH 密钥 `C:\DSH\.keys\qqbot-tencent.pem`（`ubuntu` 用户 + 免密 sudo）
- 主机指纹：`C:\DSH\.keys\known_hosts`

---

## 一、一键上传（推荐）

```powershell
cd C:\DSH\qqbot
powershell -NoProfile -ExecutionPolicy Bypass -File .\deploy\upload.ps1
```

> **为什么要写 `powershell -ExecutionPolicy Bypass -File`**：本机默认执行策略是
> `Restricted`，直接 `.\deploy\upload.ps1` 会报「禁止运行脚本」。
> 想一劳永逸，用管理员或当前用户执行一次：
> ```powershell
> Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
> ```
> 之后就能直接 `.\deploy\upload.ps1` 了。（仓库里的 `start.ps1` / `run_forever.ps1`
> 同理。）

脚本会依次做四件事：

| 步骤 | 内容 |
|---|---|
| 1 | 把仓库里**所有 `*.py`**（含 `_test_*.py`、`smoke_test.py`）+ **`persona/*`** 经 `scp` 传到服务器 `/tmp/` |
| 2 | 服务器上先把旧文件备份到 `/root/<名>.bak-<时间戳>`，再覆盖安装到 `/opt/qqbot`，并补 `chmod 644` |
| 3 | **逐个比对 SHA256**（本地 vs 线上），不一致就报错退出 |
| 4 | 在服务器上跑全部测试套件，然后 `systemctl restart qqbot`，打印状态与启动日志 |

为什么经 `/tmp` 中转：安装需要 root，而登录用的是 `ubuntu`；先传到 ubuntu 可写的
`/tmp`，再用 `sudo` 拷进安装目录。

---

## 二、常用参数

| 参数 | 说明 |
|---|---|
| `-DryRun` | 只列文件清单和将要执行的步骤，不连服务器 |
| `-SkipTests` | 不在服务器上跑测试套件（省十几秒） |
| `-NoRestart` | 只更新文件不重启（**改动不会生效**，适合只加测试/文档时） |
| `-Server` | 默认 `ubuntu@124.223.62.16` |
| `-AppDir` | 默认 `/opt/qqbot` |
| `-KeyFile` / `-KnownHosts` | 默认取 `C:\DSH\.keys\` 下那两个文件 |

示例：

```powershell
# 先看看会上传什么
powershell -NoProfile -ExecutionPolicy Bypass -File .\deploy\upload.ps1 -DryRun

# 快速更新，不跑测试
powershell -NoProfile -ExecutionPolicy Bypass -File .\deploy\upload.ps1 -SkipTests
```

---

## 三、上传后要确认什么

脚本最后会打印启动日志，重点看这几行：

```
人格聊天已启用：模型=qwen-flash-character，记忆 100 条/会话
看图已启用：视觉模型=qwen3-vl-flash（带图消息优先走视觉链路）
[公告] 轮询已启动：间隔 10 分钟，关键词=更新|维护|停机|版本|活动预告
机器人「依蜜尔爱因-测试中」已上线
```

再看一眼服务状态与实时日志：

```powershell
# 状态（脚本已打印，也可手动）
ssh -i C:\DSH\.keys\qqbot-tencent.pem -o UserKnownHostsFile=C:\DSH\.keys\known_hosts ubuntu@124.223.62.16 "systemctl is-active qqbot; sudo journalctl -u qqbot -n 40"
```

自动化测不到的**真机行为**要人工验（按改动类型挑）：

- 改 `/help`：群里发 `/help`，看是不是图片、体感速度
- 改人格/看图：私聊发一张图 → 应以角色口吻点评；「图 + 这句话什么意思」→ 回复与图相关
- 改公告：群里 `/公告 开 明日方舟`，等一条新公告，或 `/公告 查 明日方舟`
- 改群消息处理：群里 @机器人 `/ping` 应回 pong；**@全体成员应完全沉默**；回复别人的消息不应触发

---

## 四、回滚

每次上传前，旧文件都备份在服务器 `/root/`，文件名带时间戳：

```bash
# 在服务器上（或经 ssh）
sudo cp /root/bot.py.bak-2026-10-05-1210 /opt/qqbot/bot.py
sudo systemctl restart qqbot
```

`/root` 下会越积越多，偶尔清一清：

```bash
sudo ls -1t /root/*.bak-* | tail -n +21 | sudo xargs -r rm -f   # 只留最近 20 份
```

---

## 五、手动上传（脚本不可用时）

```powershell
$key = "C:\DSH\.keys\qqbot-tencent.pem"
$opts = @("-i", $key, "-o", "BatchMode=yes", "-o", "UserKnownHostsFile=C:\DSH\.keys\known_hosts")

# 1) 传到 /tmp
scp @opts .\bot.py .\persona.py ubuntu@124.223.62.16:/tmp/

# 2) 备份 + 安装 + 重启（用 base64 传远程脚本，避免引号/中文被 PowerShell 吃掉）
```

远程那一步建议照抄脚本里的做法：把 bash 脚本 base64 编码后
`ssh ... "echo <base64> | base64 -d | bash"`。直接拼多行 bash 命令很容易在
中文、引号、`$` 上翻车（本项目踩过多次）。

---

## 六、几个必须知道的坑

1. **`.ps1` 必须以 UTF-8 **带 BOM** 保存**
   Windows PowerShell 5.1 对无 BOM 的文件按 GBK 解码，中文注释/字符串会全乱，
   严重时引号被破坏、整个脚本解析失败（`upload.ps1` 和仓库里的 `start.ps1`、
   `run_forever.ps1` 都曾因此坏过）。用记事本「另存为 → UTF-8（带 BOM）」，
   或：

   ```powershell
   $p = ".\deploy\upload.ps1"
   $t = [IO.File]::ReadAllText($p, [Text.Encoding]::UTF8)
   [IO.File]::WriteAllText($p, $t, (New-Object Text.UTF8Encoding($true)))
   ```

2. **`.env` 不会被覆盖**
   服务器上的 `.env` 有自己的运维参数（`IDLE_TIMEOUT`、公告关键词等），可能和本地
   不一样。脚本**只装 `.py` 与 `persona/`**，绝不碰 `/opt/qqbot/.env`。
   要改配置就 ssh 上去 `sudo vi /opt/qqbot/.env` 然后重启。

3. **`data/` 不会被覆盖**
   图库 `images.db` + `images/`、`chat.db`、`push.db`、`help_bg.jpg` 都在服务器上，
   上传只动代码，数据不受影响。

4. **`requirements.txt` 变了要手动装**
   脚本不跑 `pip install`。新增依赖时：

   ```bash
   sudo /opt/qqbot/.venv/bin/pip install -r /opt/qqbot/requirements.txt
   sudo systemctl restart qqbot
   ```

5. **确认本地没在跑机器人**
   同一个机器人**不能被两个实例同时连接**，否则消息随机丢。上传后只在服务器上跑。

6. **`$` 在双引号里会被 PowerShell 展开**
   远程命令里的 `$APP`、`$(...)` 如果写在 PowerShell 的**双引号**字符串里会被本地
   展开成空串（本项目踩过）。远程脚本用单引号 here-string（`@' ... '@`），或写成
   `` `$APP ``。
