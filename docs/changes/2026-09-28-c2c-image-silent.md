# 单聊只发图片时不再回复 /help

日期：2026-09-28

## 需求

用户在**私聊**里发了一张图片，机器人回了一屏 `/help`，很烦。

## 原因

`on_c2c_message_create` 里的分支是：

```python
content = normalize_incoming(message.content)
...
if not content:                    # 图片消息没有正文
    await send_help(...)           # 于是被当成「空内容」，回了帮助
```

图片消息的正文天然为空，所以被「空内容 → /help」这条规则误伤。

## 改动

空内容时先判断这条消息**是不是图片消息**，是则静默返回：

```python
if not content:
    if collect_image_attachments(message):
        log.info("[单聊] 只发了图片，已静默忽略")
        return
    await send_help(message, full=await store.is_admin(author_openid(message)))
    return
```

用 `collect_image_attachments()` 而不是 `pick_image_attachment()`，因为它同时覆盖：

1. 本条消息自己带的图（top-level `attachments`）
2. **引用**消息里的图（藏在 `msg_elements` 里）

所以「只引用一张图片、不打字」也不会再触发帮助。

### 保持不变的行为

| 场景 | 行为 |
|---|---|
| 单聊空内容（例如只发了个表情） | 仍给 `/help`（不变） |
| 单聊非指令文本 | 仍无视（不变） |
| **群聊 @ 但只有图片** | 仍给 `/help`（@ 是主动行为，保持现状） |

## 改动文件

| 文件 | 说明 |
|---|---|
| `bot.py` | `on_c2c_message_create` 空内容分支 |
| `_test_reply_policy.py` | 新增 3 个回归用例 |
| `README.md` | 事件表里单聊的行为描述 |

## 测试方式

新增用例：

- `[8]` 单聊只发一张图片（top-level attachments）→ 无回复
- `[9]` 单聊只引用一张图片（注入 `raw_events` 的 msg_elements）→ 无回复
- `[10]` 单聊空内容无图 → 仍给帮助（防止改过头）

结果：本地 13 个测试套件全部通过；部署到服务器后**在服务器上重跑同一测试，退出码 0**。

## 备注

- 已 `systemctl restart qqbot` 生效，旧版 `bot.py` 备份在服务器 `/root/bot.py.bak-*`。
- 群聊 @ 空内容（含只有图片）目前仍回 `/help`。如果也想去掉，改
  `on_group_at_message_create` 的同一个分支即可。
