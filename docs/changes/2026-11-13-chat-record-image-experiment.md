# 聊天记录解析 + 识图干扰实验（关打标器/KB）

- 日期：2026-11-13
- 模块：`bot.py`、`kb.py`
- 线上实验开关：`QQ_BOT_TAGGER=0`、`QQ_BOT_KB=0`

## 背景

管理群反馈两件事：

1. 引用/转发一份聊天记录问问题，机器人看不到里面的消息；
2. 识图不如「大肥鱼」准，怀疑角色打标器/KB 干扰。

## 1. 聊天记录解析

原来 `quoted_content()` 只收顶层 `msg_elements[].content`，且必须带
`[消息类型] 引用消息` 标记。合并转发/聊天记录是 `message_type=102` 的**嵌套结构**，
真正内容在 `msg_elements[].msg_elements[]` 里，所以之前只看到
`[xxx和yyy的聊天记录]` 这个标题。

现在递归收集嵌套 `msg_elements` 的文本：

- 普通引用仍走原逻辑；
- `message_type=102/103` 时把嵌套文本全部纳入；
- 跳过 `[xxx聊天记录]` 这种只有标题的占位；
- 上限从 600 放宽到 1200 字，URL 仍抹掉。

## 2. 识图干扰实验

线上临时关闭：

```ini
QQ_BOT_TAGGER=0   # 不注入 Camie 角色/作品标签
QQ_BOT_KB=0       # 不注入 data/kb 的动漫/游戏设定
```

同时代码层加了一条：**带图/视频的消息不再查 KB**（即使 `QQ_BOT_KB=1`），
避免「这是谁」被错误的角色条目带偏；KB 只服务纯文本问题。

- 人格自带帮助（`HELP_TEXT` / self-manual）不受影响；
- 明日方舟数值/语音/敌人仍走 `arkdata` / `arklore` / `arkenemy`，不受 KB 开关影响；
- KB 关闭后，`search_should` 的 `not kb_ctx` 条件更容易成立，联网搜索会相对多一些；
- 视觉模型仍是 `deepseek-v4.1-flash`；如果关掉打标器/KB 后仍不如大肥鱼，
  下一步应换更强的视觉模型（qwen3-vl-plus / GLM 系列）做 A/B，而不是继续加提示词。

## 回滚

```ini
QQ_BOT_TAGGER=1
QQ_BOT_KB=1
```

重启 `qqbot` 即可恢复。

## 测试

- `_test_persona.py` 新增嵌套聊天记录解析：能读到“第一句/第二句”，标题占位被跳过；
- 原有引用文本、引用图片、人格链路测试通过；
- `_test_group_context.py`、`_test_chat_style.py`、`_test_profiles.py` 通过。
