# 文本链路可切厂商：接入 DeepSeek

日期：2026-10-09

## 需求

在调用 API 的模块里增加 DeepSeek，作为可选文本模型（用户提供 API Key）。

## 设计

沿用「视觉链路可切厂商」的同款思路，把**文本链路**也做成可覆盖：

- `QQ_BOT_TEXT_KEY` / `QQ_BOT_TEXT_URL` / `QQ_BOT_TEXT_MODEL`
  优先于 DashScope 的 `QQ_BOT_QWEN_KEY` / `QQ_BOT_QWEN_URL` / `QQ_BOT_QWEN_MODEL`；
  留空即回退到原来的通义千问（`qwen-flash-character`）。
- `available()` 改用 `text_key()`（文本 Key），保证「只配 DeepSeek、不配 DashScope」也能启用。
- `generate()` 与 `_request_sync()` 的**非媒体分支**改用 `text_*`；视觉分支仍用 `vl_*`。

DeepSeek 是 OpenAI 兼容端点，无需新依赖（仍是 `urllib`）：
`https://api.deepseek.com/chat/completions`，模型 `deepseek-v4-pro` / `deepseek-flash`。
**注意：DeepSeek 无视觉 API**，只能用于文本链路；看图仍走 `vl_*`（qwen / GLM）。

## 实测（服务器直连）

- `GET /models` → `['deepseek-flash', 'deepseek-v4-pro']`；
- `POST /chat/completions`（`deepseek-chat`）→ 200，正常返回。

## 改动文件

| 文件 | 说明 |
|---|---|
| `persona.py` | 文本链路 `text_key/text_url/text_model`；`available()`/`generate()`/`_request_sync` 改用 text_* |
| `.env.example` | `QQ_BOT_TEXT_*` + DeepSeek 示例 |
| `docs/CHANGELOG.md` | 索引 |

## 启用方式

默认仍是 `qwen-flash-character`。要用 DeepSeek，在服务器 `.env` 取消这三行注释并重启：

```
QQ_BOT_TEXT_KEY=sk-...（伊密尔爱因的 Key）
QQ_BOT_TEXT_URL=https://api.deepseek.com/chat/completions
QQ_BOT_TEXT_MODEL=deepseek-v4-pro        # 或 deepseek-flash
```

> 已把这三行（注释状态）写入服务器 `/opt/qqbot/.env` 备用；取消注释 + `systemctl restart qqbot` 即切换。

## 测试

- `_test_persona.py` / `_test_tagger.py` / `_test_names.py`：**0 失败**。
- 本地自测 `text_*` 覆盖：不配 → 回退 qwen；配 DeepSeek → 走 deepseek URL/模型。

## 备注

- DeepSeek 是通用模型（非角色专用），人设仍由同一张角色卡驱动；风格可能与
  `qwen-flash-character` 不同，切换后建议实测观感。
- 文本/视觉可分别指向不同厂商，逐步过渡更方便。
