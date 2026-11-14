# 文本/视觉切到 DeepSeek 官方 API

- 日期：2026-11-14
- 配置：`QQ_BOT_TEXT_*`、`QQ_BOT_VL_*`
- 影响：人格聊天、识图

## 原因

OpenCode Go 的 `deepseek-v4.1-flash` 可用，但从腾讯云服务器实测比 DeepSeek 官方 API 慢：

同一道题（“先有鸡还是先有蛋”），`max_tokens=512`：

| 模式 | DeepSeek 官方 | OpenCode Go |
|---|---|---|
| low | 2.39 / 1.92 / 2.88 秒 | 7.98 / 6.87 / 4.99 秒 |
| high | 2.19 / 2.71 / 2.39 秒 | 5.62 / 4.43 / 5.41 秒 |

网络层：

| 端点 | TLS 完成 | 首响应 |
|---|---:|---:|
| `api.deepseek.com` | 0.042s | 0.103s |
| `opencode.ai` | 0.309s | 0.547s |

OpenCode Go 多一层 Cloudflare/网关，且实际推理排队也更慢。  
识图同样切到官方 `deepseek-flash`，同一张 KFC 图描述从 12~16 秒降到 **6.3 秒**，画面/文字/梗也更准。

## 线上配置

```ini
QQ_BOT_TEXT_URL=https://api.deepseek.com/chat/completions
QQ_BOT_TEXT_KEY=<DeepSeek 平台 key>
QQ_BOT_TEXT_MODEL=deepseek-flash

QQ_BOT_VL_URL=https://api.deepseek.com/chat/completions
QQ_BOT_VL_KEY=<DeepSeek 平台 key>
QQ_BOT_VL_MODEL=deepseek-flash
QQ_BOT_VL_IMAGE_FORMAT=data_uri
```

- `deepseek-flash` 支持文本 + 图片输入；
- `deepseek-v4-pro` 不支持视觉；
- `thinking` / `reasoning_effort` 官方接口都接受；
- 旧 `.env` 备份在 `/root/qqbot-env.bak-*`。

## 用量费用

DeepSeek 官方响应没有 OpenCode Go 的 `list_cost`，`usage_store` 增加估算：

- 默认按峰时 `deepseek-flash`：输入 0.30、输出 1.20、缓存 0.006 美元/1M token；
- 可用 `QQ_BOT_TEXT_COST_INPUT/OUTPUT/CACHE` 覆盖；
- 日报里的费用是估算值，不是账单。

## 回滚

```ini
QQ_BOT_TEXT_URL=https://opencode.ai/inference/go/openai/v1/chat/completions
QQ_BOT_TEXT_MODEL=deepseek-v4.1-flash
QQ_BOT_VL_URL=https://opencode.ai/inference/go/openai/v1/chat/completions
QQ_BOT_VL_MODEL=deepseek-v4.1-flash
# 并填回旧 OpenCode Go key
```

## 验证

- `/models` + 最小 chat 请求 200；
- 直接 `persona.generate(..., reasoning="high")` 1.8 秒；
- 直接 `persona.describe` KFC 图 6.3 秒，能识别疯狂星期四/vivo50/阿米娅；
- 服务已重启，`qqbot` active。
