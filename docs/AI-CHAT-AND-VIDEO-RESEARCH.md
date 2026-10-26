# AI 聊天 / 识图 / 视频：外部项目调研与接入建议

> 调研日期：2026-10-26
> 对象：`Derpyu520/qq-bridge`（“大肥鱼”项目）+ SnowLuma + DeepSeek Harness（DSH）
> 目的：给本仓库（官方 QQ 机器人 `qqbot`）找可复用的 AI 聊天能力，并评估
> OpenCode Go 的 DeepSeek V4.1 Flash、视频识别插件。
> 结论先行：**我们的机器人不需要搬 qq-bridge 的整套架构**；值得抄的是
> “多模态模型选择、媒体链路细节、会话级提示词、成本观测”，以及一个
> **ffmpeg 抽帧做视频理解**的兜底方案。

---

## 0. 这次顺手做完的事：MaaEnd → QQ 群

需求：服务器收到 MaaEnd webhook 时，把消息发到群
`D604F254A4EBBCDBE37BB0A6BB53D0C1`。

实现位置在 **`C:\DSH\maaend-log`**（不是 qqbot）：

| 文件 | 改动 |
|---|---|
| `server.py` | 新增 QQ 群转发：入库后异步调用官方机器人 API；token 缓存/失效重试；格式化与截断；`?noforward=1` 可跳过；`/api/qqstatus` 查状态 |
| `deploy.py` | 新建 env 时写入 QQ 配置；老 env 幂等补齐；部署自检带 `noforward=1`，不白烧主动消息额度 |
| `env.example` / `README.md` | 补充 `QQ_FORWARD` / `QQ_GROUP` / `QQ_APPID` / `QQ_SECRET` / `QQ_BOT_ENV` 说明 |

要点：

- maaend-log 和 qqbot 在**同一台机器**（`124.223.62.16`），服务以 root 跑，
  所以凭据直接复用 `/opt/qqbot/.env`，**密钥只存一份**；
- 消息格式：`【MaaEnd】<title>`，正文与标题不同才换行附正文，最长 1500 字；
- 已实测发送成功（日志：`[QQ] 已推送到群 D604F254A4 ... id=...`）；
- QQ 主动消息有平台配额（每群每自然月 4 条，群主开「机器人主动发言」可解除）。
  超限报 `22009`，只记日志、不重试。MaaEnd 目前一天几条，够用。

---

## 1. “大肥鱼”项目到底是什么

| 组件 | 作用 | 链接 |
|---|---|---|
| **qq-bridge** | QQ ↔ DSH 的桥接层；把 QQ 消息投给 DSH agent，再把 agent 回复发回 QQ | https://github.com/Derpyu520/qq-bridge |
| **SnowLuma** | 第三方 QQ 协议端，把 QQ 原生会话转成 OneBot v11（HTTP / WS / WebUI / SDK / MCP） | https://github.com/SnowLuma/SnowLuma |
| **DSH** | DeepSeek 官方开源的 agent harness，“一切皆插件”，默认 Web UI `127.0.0.1:3080` | https://github.com/deepseek-ai/deepseek-harness |

数据流：

```
QQ ←→ SnowLuma(OneBot WS) ←→ qq-bridge ←→ DSH Web API ←→ agent(preset + MCP 工具)
```

截至 2026-10-26：qq-bridge 约 **2.6k star**，版本 `v0.2.1-r1`，适配
**DSH 0.2.1-rc.2**，实测 **SnowLuma 1.14.20**。注意它是**非官方协议端**
（有封号风险、EULA 限制），和我们的**官方机器人开放平台**完全两条路。

### 1.1 qq-bridge 的 AI 聊天能力清单

- **每个 QQ 会话 = 一个独立 DSH 会话**，映射持久化在 `state/sessions.json`；
  模型按 `config.json` 的 `dsh.model` 逐会话固定，默认
  `deepseek-flash` = **DeepSeek-V4.1-Flash（多模态）**。
- **人格 / 仿真两层提示词**：仿真层管行为协议，人格层（`roles/*.md`）管人设语气；
  控制台可改，人格保存即生效。
- **会话初始提示词**：给某个群/私聊预写圈子主题、氛围、忌讳，只在首次唤醒注入一次。
- **运行模式**：
  - `chat`：白名单群/私聊的安全聊天；
  - `closed-agent`：仅 owner 私聊，可用完整工具；
  - `reserved` / `reserved2`：一代/二代“仿真群友”，会自己决定何时看消息、发言、潜水、
    设置唤醒（`[SILENT]`、`qq_get_unread_messages`、`qq_send_message`、`qq_reply`）。
- **引用/回复解析**：把“被引用人 + 原文”注入，AI 能判断这句话在对谁说；
  引用机器人自己视为必回。
- **媒体**：图片/表情/GIF 可进多模态；表情包优先 QQ CDN 直链，`get_image`
  失败时用 `emoji_id` 推 CDN 兜底；下载有 SSRF 防护、IPv4 优先 + 逐地址故障切换
  （修掉了 IPv6 无出口时每次白等 20 秒的坑）；单条默认 8MB / 64MP / 5 张。
- **联网搜索**：优先复用 DSH 的 DeepSeek 搜索，Bing RSS 只做兜底；
  抓取自己做 SSRF 校验与正文大小限制。
- **黑话学习**：从群聊提取词条并自动检索/沉淀（可关）。
- **成本看板**：按群/好友/轮次统计 token 与金额，区分 DeepSeek 峰谷价，
  有 24h/3d/7d/30d 走势图。
- **语音**：发语音是支持的（独立 `voice-tool/`、TTS、AI 工具，默认关）；
  **收语音只显示占位文本，不做 ASR**。
- **视频**：**收到的视频也只显示占位文本**，没有视频理解。

---

## 2. 我们该抄什么、不该抄什么

我们的 `qqbot` 是官方开放平台 + `qq-botpy`，已有：人格聊天、看图、
两阶段描述（GLM 视觉 + qwen 人设）、视频 `video_url`、本地 KB、联网搜索、
离线打标（Camie）、引用文本注入、按会话记忆。**基础不比 qq-bridge 弱**，
差的是“工程细节 + 观测”。

| 能力 | 价值 | 成本 | 建议 |
|---|---|---|---|
| 一个多模态模型同时做文本+看图（DS V4.1 Flash） | 高 | 低 | **P0**，见 §3 |
| 调用后记录 `usage`（token/费用） | 高 | 低 | **P0**，现在响应里的 `usage` 被丢掉了 |
| 视频抽帧 → 视觉模型 | 高 | 中 | **P1**，见 §4 |
| 会话级“群设定”提示词 | 中 | 低 | P1（现在只有全局角色卡 + KB） |
| 引用“发言人昵称”注入 | 中 | 低 | P1（现在只注入被引用文本，不知道谁说的） |
| 表情包 CDN / `emoji_id` 兜底 | 中 | 中 | P2（官方机器人 attachments 是否带 emoji_id 需实测） |
| 仿真群友 / 主动插话 / 唤醒调度 | 低 | 高 | **不建议**：官方 bot 主动消息配额紧，且烧 token |
| MCP / agent 自主调工具 | 低 | 高 | **不建议**：等于把账号控制权交给模型，安全面太大 |
| 控制台 / token 看板 | 中 | 高 | 先做轻量日志，别做 Web 控制台 |
| 语音 TTS / ASR | 低 | 高 | 暂缓；收语音目前连 ASR 都没有，不是换模型能解决的 |

---

## 3. OpenCode Go + DeepSeek V4.1 Flash 接入

### 3.1 官方信息（2026-10-26 核对）

| 项 | 值 |
|---|---|
| 端点 | `https://opencode.ai/inference/go/openai/v1/chat/completions` |
| 模型 ID | `deepseek-v4.1-flash` |
| 协议 | OpenAI 兼容（`@ai-sdk/openai-compatible`） |
| 输入 | **文本 + 图片**（多模态；1M 上下文）※ |
| 最大输出 | 384K（模型能力，不代表套餐） |
| 价格 | 空闲 $0.15 / $0.60；高峰 $0.30 / $1.20（每 1M input/output） |
| 缓存读 | 空闲 $0.003 / 高峰 $0.006 |
| 套餐 | Go $10/月，含 **$80** 的 V4.1 Flash 额度；5 小时 20%、每周 50% |
| 高峰 | 周一至周五 UTC 01:00–04:00、06:00–10:00；其余（含周末）空闲 |

> 第三方文档里还会看到 `https://opencode.ai/zen/go/v1`，那是 Zen/旧版路径；
> **以官方 Go 文档的 `/inference/go/openai/v1` 为准**，不要在 `.env` 里混着填。
>
> ※ 模态信息来自 Pi 的模型目录与 qq-bridge 的实测记录（它把 `deepseek-flash`
> 直接用在多模态会话里）；OpenCode Go 官方文档没有逐模型列输入模态。
> 接之前**先用一张图实测**，别只看文档。

### 3.2 直接改 `.env` 就能接（文本+视觉同一套）

```ini
QQ_BOT_TEXT_KEY=sk-你的GoKey
QQ_BOT_TEXT_URL=https://opencode.ai/inference/go/openai/v1/chat/completions
QQ_BOT_TEXT_MODEL=deepseek-v4.1-flash

QQ_BOT_VL_KEY=sk-你的GoKey
QQ_BOT_VL_URL=https://opencode.ai/inference/go/openai/v1/chat/completions
QQ_BOT_VL_MODEL=deepseek-v4.1-flash
QQ_BOT_VL_IMAGE_FORMAT=data_uri
QQ_BOT_VL_DESCRIBE=1
QQ_BOT_CHAT_MAX_TOKENS=2048
```

- `persona.py` 走的是标准 OpenAI 兼容格式，**不需要改代码**；
  图片已经是 `data:image/...;base64,...`，符合 OpenAI 多模态格式。
- 想保留 `qwen-flash-character` 的人设语气：只把 `QQ_BOT_VL_*` 指到 DS/GLM
  做客观描述，文本仍用 qwen（`QQ_BOT_TEXT_*` 不填）；`QQ_BOT_VL_DESCRIBE=1`。
- 推理模型建议把 `QQ_BOT_CHAT_MAX_TOKENS` 从 512 提到 1024–2048，
  否则思考内容可能挤占回复预算（实测 256 会直接吐空 `content`）。
- 先用一条群 @ 消息 + 一张图实测；日志里应看到模型返回 200，且回复不为空。

### 3.3 必须知道的使用边界（重要）

1. **它不是给聊天机器人用的**：官方明确说 Go 面向 OpenCode 和“其他 coding
   agent”，会监控“破坏其他用户体验的滥用流量”。QQ 闲聊是典型的非编码流量，
   长期高频有被限流/封禁的风险。
2. **免费额度在 OpenCode 外部调用会 403**（`FreeTierError: can only be used
   from within OpenCode`）。必须是有 Go 订阅或 Console 余额的 Key。
3. **中国请求要开 Region**：Go 的隐私设置默认允许 Europe & US；用中国网络请求
   V4.1 Flash 需要在 `Settings › Privacy` 把 Regions 选 **Global**。
4. **建议带会话头**：官方要求稳定会话 ID `x-opencode-session`，
   并用自定义 User-Agent（别用 `python-urllib`）。`persona.py` 目前没带；
   如果真长期用，建议加两个可配置头，方便路由与缓存。
5. **更稳的备选**：DeepSeek 官方 API（`https://api.deepseek.com/chat/completions`，
   模型名以官方文档为准）或 OpenCode Zen 按量付费；两者都没有“仅限 coding
   agent”的边界问题。我们的 `persona.py` 换 URL/Key 即可。

### 3.4 已经补上的两个必需请求头（2026-10-26 实测）

只改 `.env` 还不够。实测发现 OpenCode Go 前面有两道门：

1. **默认 `Python-urllib/3.12` 的 UA 会被 Cloudflare 拦**：返回
   `403 error code: 1010`；
2. 换上自定义 UA 后，又会要求 **`x-opencode-session`**：
   `400 MissingSessionID`，提示 “cannot be routed efficiently”。

所以 `persona.py` 现在会：

- 给所有模型请求带 `User-Agent: Emir-Eins-QQBot/1.0`
  （可用 `QQ_BOT_TEXT_UA` 覆盖）；
- 只在 `opencode.ai` 端点带 `x-opencode-session`，值是
  `sha256(scope)[:32]`（`group:<openid>` / `c2c:<openid>`）。
  这样同一群/单聊稳定命中同一路由与缓存，又不会把 QQ openid 明文发出去；
  换 DashScope / 智谱时不会多带这个头。

`bot.py` 的 `persona_reply` 已把 `scope` 一路传给
`generate` / `generate_vision` / `describe`；`_test_persona.py` 有 4 条
断言守着（自定义 UA、scope 哈希、不同群 ID 不同、非 OpenCode 端点不带会话头）。

### 3.5 单阶段看图会误判 → 用两阶段（重要实测）

同一张纯色图，**不带人设**直接问 DS：红/绿/蓝/黄全对，文字图
`MAAEND 42`、`终末地 42` 也能读对；**带上 6.6k 字的角色卡后**，
单阶段 `generate_vision` 连续把红说成白、绿说成黑、蓝说成白、黄说成暗红。
把角色卡去掉又立刻恢复正常。

结论：**长角色卡 + 图片放在同一次调用里，DS 的视觉注意力会被人设文本带跑**，
不是图片没传到（抓包确认 `data:image/png;base64,...` 正常、模型也返回 200）。

所以生产配置用**两阶段**：

```ini
QQ_BOT_VL_DESCRIBE=1
```

流程变成：

1. `persona.describe()` 用 `DESCRIBE_PROMPT`（**不带人设**）客观描述图片；
   实测红/绿/蓝/黄、文字、形状都准；
2. 描述文本拼进输入，再由 `persona.generate()` 带角色卡生成回复。

代价是每张图多一次调用、多 3~5 秒；换来识图事实准确。若更在意速度且能接受
偶尔认错颜色/细节，再考虑 `QQ_BOT_VL_DESCRIBE=0` 或把视觉换回
`qwen3-vl-plus`。

> 另外：`max_tokens` 太小时，推理 token 会把回复预算吃光。实测
> `max_tokens=256` 时纯色图只输出 reasoning、`content` 为空；512 能回，
> 2048 更稳。生产已设 `QQ_BOT_CHAT_MAX_TOKENS=2048`。

---

## 4. 视频识别：现状、插件、落地方案

### 4.1 现状

- 我们**已经有视频链路**：`collect_video_url()` 取附件 URL，
  `persona._user_content()` 拼成 `{"type":"video_url","video_url":{"url":...}}`，
  两阶段模式下交给视觉模型（`GLM-4.6V` 支持 `video_url`）。
- 但：① 只有支持 `video_url` 的模型能吃；② QQ 官方机器人是否把群视频作为
  附件推给 bot 取决于平台，取不到就静默降级；③ 没有音频转写。
- **qq-bridge 没有视频理解**：README 明确“语音/视频仍使用占位文本”。
  所以不存在“大肥鱼官方视频识别插件”。

### 4.2 生态里真正的视频插件

| 插件 | 做法 | 依赖 | 链接 |
|---|---|---|---|
| **tmok11/dsh-vision** | `analyze_video`：ffmpeg 抽 **3×3 共 9 帧** → 视觉模型描述；可配 1–16 帧 | ffmpeg + 视觉模型 | https://github.com/tmok11/dsh-vision |
| **oil-oil/dsh-vision** | 视觉直通 / 视觉桥接，给纯文本模型补图像输入 | 视觉模型 | https://github.com/oil-oil/dsh-vision |
| `@kassing/pi-vision` | 给 DSH 文本模型加图片准入、视觉分析与文本注入 | 视觉模型 | 见 DSH 插件市场 |

注意：这些是 **DSH 插件**，不是 qq-bridge 插件。qq-bridge 即使装了
`dsh-vision`，QQ 视频仍以占位文本进 DSH，拿不到文件路径，**插件也发挥不了**。
要真用，得先改 qq-bridge 把视频下载/落盘并作为媒体传给 DSH。

### 4.3 给我们的落地方案（推荐，尚未实施）

服务器 `124.223.62.16` 已装 **`/usr/bin/ffmpeg`**（本机 Windows 没有），
所以生产端可以直接抽帧。建议加一个 `video_frames.py` + 开关：

```ini
# url   = 现状：直接把 video_url 交给模型（GLM-4.6V）
# frames= 下载视频 → ffmpeg 抽 9 帧 → 当图片交给任意视觉模型（DS V4.1 Flash / qwen-vl）
# off   = 不处理视频
QQ_BOT_VL_VIDEO_MODE=url
QQ_BOT_VIDEO_FRAMES=9          # 1~16
QQ_BOT_VIDEO_MAX_MB=40         # 视频下载上限
QQ_BOT_FFMPEG=ffmpeg           # 或绝对路径
QQ_BOT_VIDEO_TIMEOUT=60
```

实现要点：

1. `video_frames.extract(data, frames=9)`：
   - 临时文件 → `ffprobe` 取时长（取不到就按 2 秒一帧）；
   - `ffmpeg -vf "fps=<frames/duration>,scale=768:-2" -frames:v <frames> out_%02d.jpg`；
   - 读回 JPEG 列表，清临时目录；任何失败返回 `[]`，绝不抛到聊天链路。
2. `bot.persona_reply`：
   - `video_url` 非空且 `VIDEO_MODE=frames` → 下载、抽帧，`images += frames`，
     `video_url = ""`，后续完全走现有看图链路；
   - `VIDEO_MODE=url` → 保持现状；
   - 抽帧失败 → 如果有图片就继续，没有就按纯文本（与现在一致）。
3. 下载复用 `image_store` 的下载器，但视频上限单独放宽（默认 40MB）；
   仍要限制 Content-Length、超时、失败静默。
4. 测试：用服务器上的 ffmpeg 生成 3 秒测试视频（`-f lavfi -i testsrc`），
   断言抽出 3–9 张 JPEG；再 mock 视觉模型，断言 `images` 里出现帧、`video_url` 变空。
5. 局限：抽帧丢失音频与时间顺序；长视频只能均匀采样，不适合“找出第 37 秒发生了什么”。
   要音频得另接 ASR（Whisper / qwen-audio），qq-bridge 也还没做。

**在没接 DS V4.1 Flash 之前，不建议改**：现有 GLM `video_url` 链路是通的，
抽帧是给“不支持 video_url 的多模态模型”用的兜底。

---

## 5. 建议的推进顺序

1. **现在**：MaaEnd → QQ 群已上线（本文 §0）；观察几条真实任务通知。
2. **下一步（低风险）**：
   - 给 `persona.py` 加 `usage` 日志（每次调用记 input/output/cache token，
     按峰谷价折算），先看清聊天到底烧多少；
   - 用 OpenCode Go Key 做一次文本 + 一次看图实测，比较 `qwen-flash-character`
     与 DS V4.1 Flash 的人设稳定性，再决定是否切。
3. **确认模型后**：
   - 若切 DS V4.1 Flash：加 §4.3 的 `frames` 模式（视频才能继续用）；
   - 自定义 UA 与 `x-opencode-session` 已补（见 §3.4）。
4. **有余力再做**：会话级群设定、发言人昵称注入、按群 token 统计。
5. **不做**：仿真群友、主动插话、MCP 工具面、Web 控制台。

---

## 6. 参考链接

- qq-bridge：https://github.com/Derpyu520/qq-bridge
- qq-bridge 文档索引：https://github.com/Derpyu520/qq-bridge/blob/main/docs/README.md
- SnowLuma：https://github.com/SnowLuma/SnowLuma ｜ 文档：https://snowluma.github.io/
- DeepSeek Harness：https://github.com/deepseek-ai/deepseek-harness
- OpenCode Go 文档：https://opencode.ai/docs/go
- dsh-vision（视频抽帧）：https://github.com/tmok11/dsh-vision
- dsh-vision（视觉桥接）：https://github.com/oil-oil/dsh-vision
- MaaEnd 日志中心：`C:\DSH\maaend-log`（本机）+ `http://124.223.62.16/m/view/<TOKEN>`
