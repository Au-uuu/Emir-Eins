# 近期功能总结（2026-10-29）

- 日期：2026-10-29
- 涉及：`bot.py`、`persona.py`、`persona/依蜜尔爱因.md`、`group_context.py`、
  `memory_store.py`、`learning.py`、`random_reply.py`、`ark_news.py`、
  `usage_store.py`、`usage_report.py`、`README.md`
- 同批：`maaend-log` 的任务分段与 `X-Push-Source` 推送源方案

## 1. GIF / 视频抽帧：明确告诉模型「这是一个动图」

问题：GIF 只抽了 3 帧，但没告诉模型这些帧来自同一段动图，模型容易当成
「用户连发三张图」逐张点评，回复很不自然。

现在：

- `collect_vision_images()` 回填 `meta`：
  - `gif=True`、`gif_frames=N`：单发动图抽了几帧；
  - `video_frames=N`：视频抽帧数量。
- 输入模型前加【媒体说明】：
  - GIF：前面 N 张是同一段动图按时间顺序抽的帧，不是 N 张独立图片；
  - 视频：前面 N 张是按时间顺序从视频抽的画面帧；
  - 单帧 GIF：说明这是动图的一帧，不是静态图。
- 两阶段看图（`QQ_BOT_VL_DESCRIBE=1`）时，媒体说明同时交给描述模型，
  避免它写成「第一张图/第二张图」。
- 角色卡 §8 的动图规则同步改成「先连起来看成一个动作，再自然接话，
  不要逐帧报告」。

边界：单发 GIF 抽 3 帧；多图混发时 GIF 仍只看首帧；视频最多 30 帧。

## 2. 公告实时性

### 现状

- 轮询间隔从 10 分钟改为 **5 分钟**；
- 请求带 `Cache-Control: no-cache`、`Pragma: no-cache`；
- URL 加 `_t=<时间戳>`，绕过 CDN/预渲染旧缓存；
- 每条新公告记录「发布 → 实际看到」的延迟，超过 90 分钟打 warning；
- 每 12 轮（5 分钟间隔 = 1 小时）打一条心跳：

```text
[公告] 轮询心跳：已跑 12 轮，间隔 5 分钟，服务正常
```

### 关于「昨天的公告今天才发」

- `qqbot` 从 2026-10-27 23:19 起一直没重启，`NRestarts=0`，不是服务挂了；
- 争议公告 `cid=3893`：
  - `displayTime` 是 `2026-10-28 18:00`；
  - 我们第一次看到并推送是 `2026-10-29 13:01`；
  - 但封面图路径是 `20261029/...`，正文写 `10/29 12:00 开放`。
- 结论：更像官网列表/预渲染或 CDN 当时没给出这条，而不是机器人没轮询。
  现在加了 no-cache + 时间戳 + 延迟日志，下次能直接判断是官网慢还是我们慢。

## 3. 依蜜尔爱因味与上下文

- 保留「先理解再开口、准确优先」的主线，只补风味：
  - 除非是「？」「草」「行吧」这类极短反应，否则至少一个【标签】或机械词；
  - 日常陈述用「本机认为 / 演算上没问题 / 不赖」这类本机口径；
  - 群聊不要每句挂标签，但不要整段洗成普通网友；
  - 标签通常一个，情绪强两个，慌乱可连用 2~3 个短标签。
- 群聊上下文从 `60 条 / 120 分钟 / 4000 字` 提到
  **`80 条 / 180 分钟 / 5000 字`**。

## 4. 群聊随机接话 + 群友画像

### 随机接话

- 线上开启：`QQ_BOT_RANDOM_REPLY=0.03`（每条群消息 3%）；
- 每群冷却 `QQ_BOT_RANDOM_REPLY_COOLDOWN=300` 秒；
- 只对非指令、没 @ 机器人、没命中关键词的文本生效；
- 如果消息在 @ 别人，跳过，避免打断点名对话；
- 回复用当前消息 `msg_id` 走被动回复，不占每月 4 条主动消息额度。

### `/接话`：任何人可调本群概率

```text
/接话           查看本群当前概率
/接话 5%        设成本群 5%
/接话 0.03      设成本群 3%
/接话 关        本群关闭
/接话 默认      删除本群设置，恢复全局默认
```

- 不检查管理员，任何人都能用；
- 只影响当前群；
- 上限 50%，防止直接拉满刷屏；
- 设置存 `memory.db` 的 `group_settings` 表，重启后保留；
- 启动时 `random_reply.load()` 读回覆盖。

### 群友画像

- 自动学习从每 120 分钟改为 **每 60 分钟**；
- 每个群最多研究 **5 个最近活跃群友**（原来 3 个）；
- 每人至少 2 条发言即可形成初步印象；
- 印象包括说话风格、常聊话题、语气、爱用的梗；
- **只在随机接话时**注入最近活跃成员的画像，避免正常回复白烧 token；
- 主动插话提示会明确说「本机是自己决定插一句，不是被点名」，
  要求先读群聊上下文、顺着话题接、不答非所问、不重复别人。

数据：`data/memory.db`（`impressions` / `aliases` / `group_settings`）；
人工修正用 `/印象`，手动学习用 `/印象 学`。

## 5. MaaEnd 任务推送与 header 方案

`maaend-log` 侧：

- 去掉统一 `【MaaEnd】` 前缀；
- MaaEnd 日志按「开始任务 → 完成任务」切块，一条任务一条 QQ；
- `基建换班` 的设施/干员明细压缩成一行设施摘要；
- 没完成/被截断的任务不发；
- 多条之间隔 0.9 秒，`QQ_TASK_MAX` 默认 8 条。

推送源 header：

```http
POST /hook/<TOKEN>
X-Push-Source: maaend
```

| Header | 解析 |
|---|---|
| `maaend` | 按任务切块 |
| `maa` | 先尝试任务切块 |
| `plain` | 整段一条 |
| 缺省/未知 | auto：有「开始任务」就切块，否则整段一条 |

源名会清洗、入库，查看页标题前显示 `[maaend]`；加新推送端只需在
`server.py` 的 `PARSERS` 里登记一行。

## 6. B 站视频：本地插件与线上限制

找到并跑通 `Sacredbluedragon/dsh-bili-watch`：

- 支持 BV/av/b23 短链；
- `frames <视频> --step 15` = 每 15 秒抽 1 帧；
- `sheet` 拼九宫格；`view` 看标题/UP/热评；`text` 走字幕/ASR/OCR 降级链；
- 本地安装位置：`C:\DSH\tools\bili-cli\node_modules\dsh-bili-watch`；
- 包装脚本：`C:\DSH\tools\bili-watch.cmd`；
- 复用 DSH Desktop 自带 Node，已装 ffmpeg/yt-dlp。

示例：

```powershell
.\tools\bili-watch.cmd view BV1chxT6xEyo
.\tools\bili-watch.cmd frames BV1chxT6xEyo --step 15
.\tools\bili-watch.cmd sheet BV1chxT6xEyo --grid 3x3
```

**线上 QQ bot 直接抓 B 站暂不可行**：腾讯云服务器访问 B 站网页/API 返回
`HTTP 412`，`yt-dlp` 同样被风控。要接进 bot，需要服务器代理、登录 cookie
（不保证绕过 IP 风控），或本地小服务中转。

## 7. 每日用量日报

新增 `usage_report.py`：每天固定时间把前一天的模型用量发到管理员群。

配置：

```ini
QQ_BOT_USAGE_REPORT=1
QQ_BOT_USAGE_REPORT_HOUR=9
QQ_BOT_ADMIN_GROUP=<管理员群 openid>
# 线上：D604F254A4EBBCDBE37BB0A6BB53D0C1（白、_樛木、Auuuu）
# 或 QQ_BOT_USAGE_REPORT_GROUP=<群 openid>
```

内容：

```text
【用量日报】2026-10-28
调用 123 次 · 输入 456,789 · 输出 12,345 · 推理 6,789 · 缓存 23,456
花费 $0.1234（约 ¥0.89）
类型：对话 80 / 看图 20 / 学习 15 / 摘要 8
Top：群 7AF61E9EA3 $0.0500 / 单聊 ... $0.0100
```

- 统计前一天 `00:00–24:00`（服务器本地时区）；
- 每 5 分钟检查一次，过 `REPORT_HOUR` 且当天没发过就发；
- 状态文件 `data/usage_report.json`，重启不会同一天重复；
- 发送失败（含 `22009` 主动消息配额）只记日志，当天不反复重试；
- 没配置管理员群则自动跳过，不影响启动。

- 主动发送被拒（`40034105` 未开「机器人主动发言」或 `22009` 配额）时，
  日报会存成 `pending`；管理员群下次有人说话时，
  用被动回复补发。想要每天准时主动发，需让群主开启
  「机器人主动发言」。

## 8. 验证

- `_test_persona.py`：GIF/视频媒体说明、两阶段看图；
- `_test_ark_news.py`：首次初始化、去重、关键词、配额错误；
- `_test_random_reply.py` / `_test_random_reply_cmd.py`：概率、冷却、
  `/接话` 按群设置、持久化、上限；
- `_test_profiles.py`：最近活跃成员、群友画像、主动插话提示；
- `_test_usage_report.py`：日报内容、到点发送、当天去重、无群跳过、22009；
- `maaend-log/test_tasks.py`：任务切块、header 分发、source 入库。
