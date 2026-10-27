# 长期记忆摘要 + 视频抽帧 + /用量

- 日期：2026-10-27
- 涉及：`chat_store.py`、`bot.py`、`persona.py`、`video_frames.py`（新）、
  `usage_store.py`（新）、`_test_summary.py` / `_test_video_frames.py` /
  `_test_usage.py`（新）、`persona/依蜜尔爱因.md`、`.env.example`、`README.md`

## 1. 长期记忆摘要

- 每累计 **50 条**对话（`QQ_BOT_CHAT_SUMMARY_EVERY`），把**最近 20 条以前**的
  早期对话交给 DS，压成 **3~5 条事实**；
- 最近 **20 条**始终保留原文（`QQ_BOT_CHAT_RECENT`）；
- 摘要存 `chat.db` 的 `summaries(scope, summary, covered_id)`；
- 已有摘要 + 新早期对话一起合并，始终保持 3~5 条；
- 摘要后台跑，不阻塞回复；下次以 `【长期记忆摘要】` system 消息注入；
- 删 `data/chat.db` 会同时丢摘要和最近对话。

## 2. 视频抽帧

- `QQ_BOT_VIDEO_MODE=frames`（默认）：下载视频 → ffmpeg 抽帧 → 当图片交给视觉模型；
- 每 **15 秒**一帧；短视频**至少 9 帧**；最多 30 帧（防一次喂太多图）；
- `QQ_BOT_VIDEO_FRAME_INTERVAL=15`、`MIN_FRAMES=9`、`MAX_FRAMES=30`、
  `MAX_MB=40`、`FFMPEG=ffmpeg`、`TIMEOUT=180`；
- 抽帧按 `duration / n` 均匀分布：10 秒视频抽 9 帧（约 1.1 秒一帧），
  150 秒抽 10 帧（15 秒一帧）；
- 抽帧失败按“无视频”处理，不把 `video_url` 塞给 DS（DS 不支持）；
- 视频帧不做 Camie 打标（几十帧逐个打太慢）；
- 看图默认常开：只有 `QQ_BOT_VL_MODEL=off` 才会关。

## 3. `/用量`

- 新 `data/usage.db`：每次模型调用记 scope / model / kind / input / output /
  reasoning / cached / cost（OpenCode 的 `list_cost`）；
- `kind`：`text` / `vision` / `learn` / `summary`；
- `/用量 [天数]` 管理员：调用次数、token、费用（USD + 约 CNY）、分项、Top 群；
- 默认今天，最多 30 天；账本写入失败只 warning，不影响聊天。

## 4. 自然语言指令提示

- 不做自动映射；
- 角色卡新增：用户用自然语言说“来张猫图/怎么删图库”时，用依蜜尔爱因口吻提示
  正确指令（如“要图就 /来只 猫”），**不假装执行**。

## 5. 测试

- `_test_summary.py`：触发条件、最近 20 条排除、摘要合并、3~5 条事实、去编号；
- `_test_video_frames.py`：10s→9 帧、150s→约 10 帧、超长封顶 30 帧；
  `persona_reply` 收到帧、`video_url` 清空、正常回复；
- `_test_usage.py`：记录/汇总/分项/按群；
- 既有测试显式 `QQ_BOT_USAGE=0`，避免线上账本干扰。

## 6. 线上配置

```ini
QQ_BOT_CHAT_RECENT=20
QQ_BOT_CHAT_SUMMARY_EVERY=50
QQ_BOT_VIDEO_MODE=frames
QQ_BOT_VIDEO_FRAME_INTERVAL=15
QQ_BOT_VIDEO_MIN_FRAMES=9
QQ_BOT_VIDEO_MAX_FRAMES=30
QQ_BOT_VIDEO_MAX_MB=40
QQ_BOT_USAGE=1
QQ_BOT_VL_TIMEOUT=120
```

## 备注

- 摘要/视频/用量都走 DS，成本会体现在 `/用量`；
- 30 帧是默认上限，10 分钟视频会抽 30 帧（不是每 15 秒一帧到 40 帧）；
  要严格 15 秒一帧可调大 `MAX_FRAMES`，但 token 会涨；
- 本地 Windows 没 ffmpeg，抽帧只在服务器生效；测试用 `frame_plan` 和 mock。
