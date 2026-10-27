# 上下文加长 + 机娘味回补 + 群昵称字段

- 日期：2026-10-27
- 涉及：`bot.py`、`group_context.py`、`persona/依蜜尔爱因.md`、
  `_test_group_context.py`、`.env.example`、`README.md`、`docs/ENV-AND-PITFALLS.md`

## 1. 上下文加长

线上 `.env`：

```ini
QQ_BOT_CHAT_MEMORY=200
QQ_BOT_CHAT_MEMORY_MINUTES=60
QQ_BOT_GROUP_CONTEXT_MAX=60
QQ_BOT_GROUP_CONTEXT_MINUTES=120
QQ_BOT_GROUP_CONTEXT_CHARS=4000
```

- 人格记忆：100 条 / 20 分钟 → **200 条 / 60 分钟**
- 群聊背景：30 条 / 30 分钟 / 2000 字 → **60 条 / 120 分钟 / 4000 字**
- 代码默认值没改（仍是 100/20、30/30/2000），避免影响其他部署；
  线上靠 `.env` 覆盖。

## 2. 机娘味回补

角色卡新增 §3.5「机娘味配方」：

- 活人感是「机娘学着像人说话」，不是变成普通群友；
- 保留本机、机械术语、毒舌、胜负欲、恋爱脑（仅主人）、汉字报时、偶尔【标签】；
- 群聊每 2~3 条回复至少保留一处机娘味（一个标签/一个机械词/一句本机式吐槽）；
- 禁止的是「【报告】…【建议】…【结论】…」报告体连发，不是禁止标签本身。

## 3. 群昵称 / QQ 昵称

### 官方 API 现状（2026-10-27 实测）

- 官方有 `GET /v2/groups/{group_openid}/members`，返回
  `member_openid` / `username` / `member_role` / `bot` / `joined_at` / `union_openid`；
- 但该接口**仅白名单机器人可用**，本机实测返回
  `11253 应用无接口访问权限`，两个群都一样。

### 当前实现

- `author_display_name(message)` 从原始 payload 的 `author` 里读：
  - 优先 `card`（群昵称，若平台给）；
  - 其次 `member_name` / `username` / `nickname`（QQ 昵称）；
  - **不读备注名**；
  - 都拿不到就回退 openid 后 6 位。
- `group_messages` 新增 `display_name` 列；旧库自动 `ALTER TABLE` 补列。
- 群聊上下文行变成 `[HH:MM] 昵称: 内容`；人格输入前会加
  `（发言人：昵称）`，模型能分清谁在说话。
- 旧数据（补列前写入的）`display_name` 为空，仍显示 `用户xxxxxx`；
  新消息开始带昵称。

### SnowLuma（待小号）

SnowLuma 的 `get_group_member_list` 能拿到 `nickname`（QQ 昵称）和
`card`（群昵称），正好符合“不要备注名”的要求。但有一个硬问题：

> 官方 Bot 收到的是 `member_openid`，SnowLuma 给的是 QQ 号；
> 两者没有官方映射。

可行方案（按优先级）：

1. **先用官方 payload 的 `author.username` + `/叫我` 别名**（不依赖第三方）；
2. SnowLuma 只作为“群昵称来源”，用同群消息内容+时间做关联映射
   （同一群、同一内容、时间差 < 5 秒），置信度低的回退 openid 尾号；
3. 不做“备注名”抓取；SnowLuma 用小号，注意第三方协议封号风险与 EULA。

## 4. 测试

`_test_group_context.py` 新增：

- `display_name` 优先显示（小明: 带昵称）；
- 旧库没有 `display_name` 列时自动迁移，仍能写入/读取。

## 5. 备注

- 群昵称/QQ 昵称能否直接拿到，取决于平台 payload 是否带 `author.username`；
  如果实际为空，下一步就上 `/叫我` 别名或 SnowLuma 关联。
- 上下文加长会增加 token；DS V4.1 Flash 1M 上下文 + 缓存，成本可控，
  但群很活跃时可把 `GROUP_CONTEXT_MAX` 调回 30。
