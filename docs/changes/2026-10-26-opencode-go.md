# 接入 OpenCode Go / DeepSeek V4.1 Flash（文本 + 识图）

- 日期：2026-10-26
- 涉及：`persona.py`、`bot.py`、`_test_persona.py`、`.env.example`、线上 `.env`

## 背景

原来的文本链路是 `qwen-flash`，视觉是 `qwen3-vl-plus`。这次按调研把
**文本和视觉都切到 OpenCode Go 的 `deepseek-v4.1-flash`**，目标是：
一个 Key、一个模型、1M 上下文，聊天和识图共用；顺便把 token/费用打进日志。

## 改了什么

### 1. `persona.py`：两个必需请求头

OpenCode Go 前面有两道门，实测都踩到了：

| 现象 | 根因 | 修法 |
|---|---|---|
| `403 error code: 1010` | Cloudflare 拦默认 `Python-urllib/3.12` UA | 所有请求带 `User-Agent: Emir-Eins-QQBot/1.0`（`QQ_BOT_TEXT_UA` 可覆盖） |
| `400 MissingSessionID` | OpenCode Go 强制要 `x-opencode-session` | 只在 `opencode.ai` 端点带 `sha256(scope)[:32]`，同一群/单聊稳定命中路由与缓存 |

`scope` 由 `bot.persona_reply` 传给 `generate` / `generate_vision` / `describe`；
`scope` 不直接发给第三方，发的是哈希。

### 2. `persona.py`：用量日志

每次模型返回记一行：

```
[模型] deepseek-v4.1-flash in=4354 out=110 cache=0 reasoning=58 cost=7.191e-05
```

- `cost` 是 OpenCode 返回的 `usage.opencode.list_cost`（美元）；
- 回复为空但 `reasoning > 0` 时额外 warning，提示 `max_tokens` 可能太小。

### 3. 配置（线上 `.env`）

```ini
QQ_BOT_TEXT_KEY=<OpenCode Go key>
QQ_BOT_TEXT_URL=https://opencode.ai/inference/go/openai/v1/chat/completions
QQ_BOT_TEXT_MODEL=deepseek-v4.1-flash

QQ_BOT_VL_KEY=<同一个 key>
QQ_BOT_VL_URL=https://opencode.ai/inference/go/openai/v1/chat/completions
QQ_BOT_VL_MODEL=deepseek-v4.1-flash
QQ_BOT_VL_IMAGE_FORMAT=data_uri
QQ_BOT_VL_DESCRIBE=1

QQ_BOT_CHAT_MAX_TOKENS=2048
QQ_BOT_CHAT_TIMEOUT=30
```

改前备份：`/root/qqbot.env.bak-20261026-211608`。

### 4. 测试

`_test_persona.py` 新增 4 条断言：

- 自定义 UA 已带上；
- OpenCode 端点带 `x-opencode-session`，值等于 `session_id(scope)`；
- 不同群会话 ID 不同；
- 非 OpenCode 端点不带会话头。

同时在测试开头清空模型凭据，避免在线上跑测试时意外真联网。

## 关键坑：长角色卡 + 单阶段看图会误判

同一张纯色图：

| 调用方式 | 结果 |
|---|---|
| 不带人设直接问 DS | 红/绿/蓝/黄全对；`MAAEND 42`、`终末地 42` 读对 |
| 带 6.6k 字角色卡单阶段 `generate_vision` | 红→白、绿→黑、蓝→白、黄→暗红 |
| 两阶段 `describe`（无人设）→ `generate`（人设） | 红/蓝/绿/黄全对 |

结论：**长人设会稀释视觉注意力**，不是图片没传到。生产因此开
`QQ_BOT_VL_DESCRIBE=1`，每张图多一次调用（约 +3~5 秒），换事实准确。

另：推理 token 计入 `max_tokens`。实测 `max_tokens=256` 时纯色图只输出
reasoning、`content` 为空；512 能回，2048 更稳。生产设 2048。

## 回滚

```bash
sudo cp /root/qqbot.env.bak-20261026-211608 /opt/qqbot/.env
sudo chmod 600 /opt/qqbot/.env
sudo systemctl restart qqbot
```

代码回滚：`/root/persona.py.bak-*`、`/root/bot.py.bak-*`（部署脚本每次都会备份）。

## 注意

- OpenCode Go 官方定位是 coding agent，QQ 闲聊属于非编码流量；长期高频有被
  限流/封禁风险。若出现 403/429 或额度异常，先切回 Qwen/DeepSeek 官方 API。
- Key 已写入 `/opt/qqbot/.env`（600 root）。如果这个 Key 在别处泄露过，去
  OpenCode Console 重新生成，再更新 `.env` 即可。
- 视频目前仍不会走两阶段描述（`video_url` 发给 DS 不支持）；等
  `QQ_BOT_VL_VIDEO_MODE=frames` 的抽帧兜底落地后再补。
