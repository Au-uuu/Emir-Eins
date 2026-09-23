# 部署到云服务器（腾讯云 / 阿里云 Lighthouse）

把本地的 QQ 机器人迁到云服务器上常驻运行。全程约 15 分钟。

---

## 0. 前置条件

- 一台 **国内节点** 的 Ubuntu 22.04 服务器（轻量应用服务器即可，最低配足够）
- 服务器 **公网 IP**
- 本地项目目录（本仓库），含 `.env` 和 `data/`（图片库）

> ⚠️ 服务器地域必须是**国内**（上海/广州/北京等）。QQ 的 API 对海外 IP 延迟高且可能被限流。

---

## 1. 从本地打包

在 **Windows 本地** `D:\DSH` 目录下执行：

```powershell
cd D:\DSH
tar -czf qqbot-deploy.tar.gz `
  --exclude="qqbot/.venv" `
  --exclude="qqbot/__pycache__" `
  --exclude="qqbot/logs" `
  --exclude="qqbot/botpy.log" `
  qqbot
```

产物：`D:\DSH\qqbot-deploy.tar.gz`（约几百 KB，含图片库）

> 用系统自带的 `tar` 即可（Win10 1803+ 都带）。如果报错，也可以直接用 WinSCP 拖整个 `qqbot` 目录。

---

## 2. 上传到服务器

### 方式 A：命令行（推荐）

```powershell
# 把 <SERVER_IP> 换成你的公网 IP
scp D:\DSH\qqbot-deploy.tar.gz root@<SERVER_IP>:/root/
```

回车后输入服务器 root 密码。

### 方式 B：腾讯云控制台自带上传

轻量应用服务器控制台 → 实例详情 → **登录** → 用网页终端
→ 终端右上角有「**上传文件**」按钮，直接选 `qqbot-deploy.tar.gz`

（阿里云 ECS 控制台也有类似的「发送文件」功能）

---

## 3. 解压

SSH 登录服务器后：

```bash
ssh root@<SERVER_IP>

cd /root
tar -xzf qqbot-deploy.tar.gz
ls qqbot/deploy/          # 应该看到 deploy.sh 和 qqbot.service
```

---

## 4. 一键部署

```bash
cd /root/qqbot
sudo bash deploy/deploy.sh
```

脚本会自动完成：

1. 装 `python3` / `venv` / `pip`
2. 把项目复制到 `/opt/qqbot`
3. 建虚拟环境并安装 `qq-botpy`、`python-dotenv`、`Pillow`
4. 注册 `qqbot.service` 并**开机自启 + 崩溃自动重启**
5. 跑一次自检并打印服务状态

看到 `部署完成` 和自检全 `[OK]` 就成功了。

---

## 5. 验证

```bash
# 看日志（应出现"机器人「xxx」启动成功！"）
journalctl -u qqbot -n 30

# 实时跟踪
journalctl -u qqbot -f
```

然后**去群里 @ 机器人发 `/help`**，能回复就通了。

---

## 6. 必须做的收尾（容易漏）

### 6.1 开放平台加 IP 白名单

如果开放平台开了 IP 白名单，**必须把服务器公网 IP 加进去**，否则服务器上的机器人连不上：

> QQ 开放平台 → 机器人管理端 → 开发设置 → 服务器 IP 白名单

把服务器的公网 IP 填进去（白名单最多 50 个，支持单个 IPv4，不支持网段）。

### 6.2 停掉本地机器人

**同一个机器人不能被两个程序同时连接**——两个实例会互相抢事件，导致消息随机丢失（这个坑我们踩过）。

确认服务器上跑通之后，在本地执行：

```powershell
Get-Process python | Where-Object { $_.Path -like "*qqbot*" } | Stop-Process -Force
```

### 6.3 更新隐私协议

隐私协议里「存储位置」那段要如实改成服务器所在环境，例如：

> 上述信息存储于腾讯云（/阿里云）位于中国境内的服务器中。

协议内容必须和实际行为一致，这是审核红线。

---

## 7. 日常运维

| 操作 | 命令 |
|---|---|
| 看实时日志 | `journalctl -u qqbot -f` |
| 看最近 50 行 | `journalctl -u qqbot -n 50` |
| 重启机器人 | `systemctl restart qqbot` |
| 停止机器人 | `systemctl stop qqbot` |
| 查看状态 | `systemctl status qqbot` |
| 关闭开机自启 | `systemctl disable qqbot` |

### 备份图片库

```bash
tar -czf ~/qqbot-data-$(date +%F).tar.gz -C /opt/qqbot data
```

建议挂个 crontab 每天自动备份：

```bash
crontab -e
# 加入：每天凌晨 3 点备份
0 3 * * * tar -czf /root/qqbot-data-$(date +\%F).tar.gz -C /opt/qqbot data
```

### 更新代码

本地改完代码后重新上传：

```powershell
scp qqbot\bot.py root@<SERVER_IP>:/opt/qqbot/
```

然后在服务器上：

```bash
systemctl restart qqbot
```

`data/` 图片库不会被覆盖。

---

## 8. 常见问题

### 机器人启动成功但群里不回复

1. `journalctl -u qqbot -n 50` 看有没有 `[群@]` 记录
   - **没有** → 消息没送达：确认 @ 了机器人、IP 白名单加了服务器 IP
   - **有** → 看后面的 `[ERROR]`
2. 确认本地机器人**已经停掉**（双实例抢连接）

### 报 `主动消息失败, 无权限`

正常现象。未认证的机器人**不能主动推送**，所有回复必须由用户消息触发（被动回复）。我们的代码就是这样设计的。

### 连不上 / 一直重连

多半是 **IP 白名单**没加服务器 IP。先加白名单再 `systemctl restart qqbot`。

### 部署脚本报 apt 相关错误

Lighthouse 的 Hermes/OpenClaw 应用镜像可能精简过 apt 源，试着：

```bash
apt-get update
apt-get install -y python3 python3-venv python3-pip
```

装不上就换 **纯 Ubuntu 22.04 系统镜像**重装（本仓库不依赖任何应用镜像）。

### 想看机器人内部日志（不只 journald）

```bash
tail -f /opt/qqbot/logs/bot.log
```

---

## 9. 关于看门狗

`watchdog.py` 会监控 WebSocket 长连接。botpy 的长连接可能**静默失效**（进程活着但收不到事件，日志无报错）。

在 Linux 上，看门狗发现失效后会以退出码 2 退出进程，**systemd 的 `Restart=always` 会自动拉起**——比 Windows 上的 `run_forever.ps1` 更可靠。

默认 15 分钟无任何网关消息就判定失效。安静的小群可以调大：

```bash
# /opt/qqbot/.env
QQ_BOT_IDLE_TIMEOUT=3600
```
