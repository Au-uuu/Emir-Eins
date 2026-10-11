# 视觉链路可切换厂商：支持智谱 GLM-4.6V-Flash（免费）

日期：2026-10-09

## 需求

用户希望把「看图」用的视觉模型从通义千问换成智谱 **GLM-4.6V-Flash**（据称更好且免费）。

## 调研

- **GLM-4.6V-Flash 确实存在**：智谱（Z.ai / BigModel）2025-12-08 发布的 9B 视觉语言模型，
  API **免费**（输入/输出均 ¥0，有速率限制、无 SLA）。
- **不在同一条链路**：它是智谱的模型，通义千问（DashScope）不提供。换它 = 换端点 + 换 Key。
- 端点 `https://open.bigmodel.cn/api/paas/v4/chat/completions`，`Authorization: Bearer <智谱KEY>`，
  模型名 `glm-4.6v-flash`，请求体与我们现在的 OpenAI 兼容格式一致。
- **图片格式差异**：智谱要**裸 base64**，不带 `data:image/png;base64,` 前缀；DashScope 要 data URI。
- 支持 `"thinking": {"type": "enabled"/"disabled"}`；`max_tokens` 上限 32768；`temperature` 支持。
- **「效果更好」存疑**：GLM-4.6V-Flash 是 9B 免费档，官方对标的是 `Qwen3-VL-8B`；而现在用的
  `qwen3-vl-plus` 是更大更贵的付费档。**「免费」确定，「更强」不确定。**

## 设计

`persona.py` 把「文本链路」与「视觉链路」的 **Key / URL / 模型名拆开配置**，默认行为不变：

- 文本：`QQ_BOT_QWEN_KEY` / `QQ_BOT_QWEN_URL` / `QQ_BOT_QWEN_MODEL`（DashScope，人设模型）。
- 视觉：`QQ_BOT_VL_MODEL` / `QQ_BOT_VL_KEY` / `QQ_BOT_VL_URL`；留空则回退到文本链路
  （兼容旧的 `QQ_BOT_QWEN_VL_MODEL`）。
- `QQ_BOT_VL_IMAGE_FORMAT`：`data_uri`（默认）或 `base64`（智谱裸 base64）。
- `QQ_BOT_VL_THINKING`：`enabled`/`disabled`；留空不带该字段。
- **成对校验**：`QQ_BOT_VL_URL` 与 `QQ_BOT_VL_KEY` 只配一个视为配置错误 → 自动禁用看图，
  避免拿 A 家的 Key 去调 B 家的端点。

## 改动文件

| 文件 | 说明 |
|---|---|
| `persona.py` | 文本/视觉分离配置；`vl_key`/`vl_url`/`vl_model`/`vl_image_format`/`vl_thinking`；`_vision_provider_ok` 成对校验；`_user_content` 支持裸 base64；`_request_sync` 走视觉端点并可带 thinking |
| `_test_persona.py` | [5] 增加「成对配置」与「裸 base64」断言；并把「默认值」断言先清空所有 `VL_*` 环境变量（避免服务器配 GLM 后本地过、服务器挂） |
| `.env.example` | 新增视觉独立厂商配置说明（含 GLM 示例） |
| `docs/CHANGELOG.md` | 索引 |

## 启用方式（需要智谱 Key）

在服务器 `.env` 增加（**Key 由用户提供**）：

```
QQ_BOT_VL_MODEL=glm-4.6v-flash
QQ_BOT_VL_URL=https://open.bigmodel.cn/api/paas/v4/chat/completions
QQ_BOT_VL_KEY=<智谱 API Key>
QQ_BOT_VL_IMAGE_FORMAT=base64
QQ_BOT_VL_THINKING=disabled
```

然后 `sudo systemctl restart qqbot`。不配这些时，视觉仍走原来的 `qwen3-vl-plus`，行为不变。

## 测试

- `_test_persona.py`：**0 失败**（含新增的成对配置、裸 base64 断言）。
- `_test_tagger.py`：**0 失败**。
- 未做真实调用（缺智谱 Key）；拿到 Key 后需发图实测，重点看：能否收到正常中文回复、
  延迟、是否出现思考内容（若出现则把 `QQ_BOT_VL_THINKING=disabled`）。

## 备注

- 免费模型有速率限制，小群够用；若被限流会静默不回复（沿用现有「模型失败即静默」策略）。
- 视觉与文本换厂商后，两种回复的**文风可能略有差异**（文本 `qwen-flash-character` vs 视觉 GLM），
  但角色卡是同一张，人设不变。

## 追加：两阶段看图 + 视频（同日）

用户提出：图片消息改成「GLM 客观描述 → qwen 转成人设语言」，并顺带让 GLM 识别视频。

### 设计

- 新开关 `QQ_BOT_VL_DESCRIBE=1`（默认关，行为不变）。
- 打开后，`bot.persona_reply` 遇到图片/视频时：
  1. 调**视觉链路**（GLM）用 `persona.DESCRIBE_PROMPT` 生成**客观描述**（不带人设）；
  2. 把描述拼成 `（视觉模型对这条内容的客观描述：…）` 注入输入，交给**文本链路**
     （`qwen-flash-character`）生成符合人设的回复；
  3. 描述失败 → 退回原来的「单次视觉调用」（仍带打标线索）。
- **视频**：`bot.collect_video_url()` 认视频附件，`_user_content` 生成
  `{"type":"video_url","video_url":{"url":…}}`（GLM 支持）。QQ 是否把群内视频作为附件推给
  机器人取决于平台，取不到就当没有、不影响其它链路。
- `persona._request_sync` / `_user_content` 增加 `video_url` 参数；`has_media = 图片或视频`，
  据此选视觉端点/模型与 `thinking`。

### 改动文件（追加）

| 文件 | 说明 |
|---|---|
| `persona.py` | `describe_mode()` / `describe()` / `DESCRIBE_PROMPT`；`_user_content`、`_request_sync` 支持 `video_url` |
| `bot.py` | `collect_video_url()`；`persona_reply` 两阶段分支；三处「空正文+媒体」入口纳入视频 |
| `_test_persona.py` | 新增：视频 content、两阶段开关、视频附件识别、两阶段链路（含失败回退） |
| `.env.example` | `QQ_BOT_VL_DESCRIBE` 说明 |

### 测试（追加）

- `_test_persona.py`：**0 失败**（新增「两阶段：describe 收到图片 / 描述拼进文本链路 /
  未走单次视觉链路 / describe 失败回退」「视频附件识别」「429 自动重试」等）。
- **真实 API 实测（2026-10-09，智谱 GLM-4.6V-Flash）**：
  - 图片描述：✅ 返回客观描述；
  - 视频描述：✅ 官方示例视频可识别（约 30~90s 完成）；
  - 免费档会突发 `429 / code 1305「访问量过大」`，**重试即可恢复** → 已加
    `_request_with_retry`（429/5xx 重试 3 次，退避 1.2s×n）。

### 超时（实测踩到的坑）

视频理解很慢：**示例 11MB 视频用 30s 超时会超时**。故把视觉/描述超时与文本拆开：

- `QQ_BOT_VL_TIMEOUT`（图片，默认 **60s**）
- `QQ_BOT_VL_VIDEO_TIMEOUT`（视频，默认 **180s**）
- 文本仍用 `QQ_BOT_CHAT_TIMEOUT`（默认 20s）。

> 因此**视频回复会比图片慢很多**（几十秒）；QQ 被动回复窗口 5 分钟，能回，但体验一般。
> 是否要保留视频，取决于群里是否真有视频需求。

## 回退记录（2026-10-09 当日）

- **实测 GLM-4.6V-Flash 免费档限流严重**：私聊发图实测（15:06/15:07 两次 `[单聊] [+图片]`）
  → 描述被 `429 / code 1305「访问量过大」`连续拒绝（重试 3 次全失败）→ 回退的 `generate_vision`
  仍走 GLM → 再 429 → `[人格] 本轮无回复`。**即：免费档不可用于生产。**
- **已回退到 qwen**：把服务器 `.env` 的 `QQ_BOT_VL_*` 全部注释（`vl_*` 自动回退到
  `QQ_BOT_QWEN_VL_MODEL`），`QQ_BOT_VL_DESCRIBE` 随之关闭 → 恢复「单次 qwen 视觉调用」。
  启动日志 `看图已启用：视觉模型=qwen3-vl-plus`；实测 `generate_vision` 正常出人设回复。
- **代码模块全部保留**（文本/视觉分离、`describe`、`video_url`、重试、超时拆分），
  将来要再启用只需取消 `.env` 里 `QQ_BOT_VL_*` 的注释。
- 若日后再上 GLM，建议：① 用付费 **GLM-4.6V-FlashX**（便宜且稳）；或 ② 把「描述失败的回退」
  改成走 **qwen 视觉**兜底（当前回退仍复用同一个 `vl_*`，同厂商挂了会一起挂）。
