# 2026-09-21 回复策略：不再回显、错误指令无视、空 @ 视为帮助

## 需求

- 收到 @ 消息后**不再回复**「收到：xxx」。
- **错误的指令直接无视**（静默，不回复）。
- 只有 @ 而**没有任何正文**时，视为 `/help`。

## 设计

### 群 @（`on_group_at_message_create`）

- 正文为空 → 发送帮助（`send_help`），即「只 @ 不说话 = 帮助」。
- 有正文 → 交给 `handle_command`：
  - 命中指令 → 正常回复；
  - 认不出 → **不回复**，只记一条 `[群@] 非指令消息，已忽略` 日志。
- 删除原来的「收到：{content}」与「你好，我是机器人…」文案。

### 单聊（`on_c2c_message_create`）

- 空内容（例如只发图/表情）→ 视为 `/help`。
- 命中指令 → 正常回复；认不出 → 静默忽略（不再回「收到：…」）。

### 群全量（`on_group_message_create`）

- 行为不变：非指令且未命中 `QQ_BOT_KEYWORD` 时本就不回复。

## 效果

机器人**只对「有效指令」和「空 @（=帮助）」做出回应**，其余一律静默，避免群里刷屏。

## 改动文件

- `bot.py`：`on_group_at_message_create`、`on_c2c_message_create` 逻辑调整。
- `_test_reply_policy.py`：新增，覆盖空 @ 帮助、错误指令静默、有效指令正常、
  单聊空内容/非指令、以及「代码里不再出现『收到：』」。
- `README.md`：响应表与回复策略说明。
- `deploy/deploy.sh`、`.gitignore`：纳入新测试。

## 测试

```
.\.venv\Scripts\python.exe _test_reply_policy.py  # exit 0
.\.venv\Scripts\python.exe _test_admin.py         # exit 0
.\.venv\Scripts\python.exe _test_gallery.py       # exit 0
.\.venv\Scripts\python.exe _test_delete.py        # exit 0
.\.venv\Scripts\python.exe _test_links.py         # exit 0
.\.venv\Scripts\python.exe _test_store.py         # exit 0
.\.venv\Scripts\python.exe _test_commands.py      # exit 0
```

## 备注

- 「错误指令」判定为「不是任何已知指令」；因此像 `/来芝` 这类错别字会被静默忽略，
  用户不会得到任何反馈。若希望错别字也给个提示，可另加一个「像指令但不是」的判断。
