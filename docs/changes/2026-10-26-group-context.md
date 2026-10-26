# 群聊上下文缓冲

- 日期：2026-10-26
- 涉及：`group_context.py`（新）、`bot.py`、`_test_group_context.py`（新）、
  `_test_persona.py` / `_test_commands.py` / `_test_reply_policy.py`（测试隔离）、
  `.env.example`、`README.md`

## 需求

`chat_store` 只记「用户 ↔ 机器人」的一问一答。群里其他人刚才聊了什么、在接什么梗，
模型完全不知道，被 @ 时只能回一句孤立的话。

群主开启「接收所有消息」后，机器人其实能看到全群消息；这些消息不该拿来主动回复，
但可以作为**背景**注入下一次被 @ 时的提示词。

## 设计

- 新模块 `group_context.py`：SQLite 环形缓冲，按群隔离。
- 表 `group_messages(id, group_openid, msg_id, openid, content, ts)`，
  `UNIQUE(group_openid, msg_id)` 去重（同一条消息可能由群@/群全量两个事件重复投递）。
- 默认保留最近 **30 条 / 30 分钟**，注入总字数上限 **2000**；
  单条渲染截到 200 字，行格式 `[HH:MM] 用户<openid后6位>: 内容`。
- 只存文字；图片/视频存 `[图片]` / `[视频]` 占位，不下载媒体。
- 斜杠命令不入缓冲；命令由 `handle_command` 拦截后不会走到 `remember`。
- 只用于群聊；单聊不注入。上下文只拼进**当前轮**输入，不写入 `chat.db`。
- 注入位置：`persona_reply` 里 KB/联网搜索之后、视觉/文本调用之前；
  当前这条消息按 `message.id` 排除，避免同一句话出现两次。
- 默认开启，`QQ_BOT_GROUP_CONTEXT=0` 关闭；DB 路径可用
  `QQ_BOT_GROUP_CONTEXT_DB` 覆盖（测试用临时路径）。

## 接入点

- `on_group_at_message_create`：非指令 @ 消息先 `remember` 再 `persona_reply`；
  只 @ + 图片时记 `[图片]`。
- `on_group_message_create`（全量模式）：
  - 非指令消息先 `remember`，再决定是否人格回复/关键词回复；
  - 没 @ 机器人的图片/视频也记占位；
  - 指令不记。
- `persona_reply`：`scope` 以 `group:` 开头时取最近上下文，拼在输入最前面。

## 测试

`_test_group_context.py` 覆盖：

- 只取最近 N 条、顺序旧→新、当前消息排除；
- 发言人尾号、头尾说明；
- 同 `msg_id` 去重、群隔离；
- 斜杠命令/空内容不存；
- 过期不返回、手动 clear；
- 注入人格链路（群聊有、单聊没有、当前消息只出现一次）。

`_test_persona.py` / `_test_commands.py` / `_test_reply_policy.py` 显式关闭
群聊上下文并指向临时 DB，避免线上 `data/group_context.db` 把真实群消息拼进断言。

## 限制

- 群没开「接收所有消息」时，机器人只能看到 @ 它的消息，缓冲效果有限。
- 只存文字，图片/视频只有占位；要理解群里的图仍需 @ 机器人发图。
- 发言人只有 openid 后 6 位，官方 Bot 拿不到群昵称。
- 30 条 / 30 分钟是经验值；群太吵可调小，冷清群可调大。

## 回滚

- 关闭：`.env` 设 `QQ_BOT_GROUP_CONTEXT=0`，重启。
- 删除：`rm data/group_context.db`（只丢缓冲）。
- 代码回滚：`/root/group_context.py.bak-*` / `/root/bot.py.bak-*`。
