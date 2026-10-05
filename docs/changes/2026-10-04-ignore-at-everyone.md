# @全体成员的消息一律不响应

日期：2026-10-04

## 需求

群里有人（群主/管理员）@全体成员 时，机器人不该有任何反应。
尤其不能把「@全体成员、正文为空」的消息误判成「只有 @ 没有正文 → /help」，
把帮助图刷进群里。

## 原因

`on_group_at_message_create` / `on_group_message_create` 都有
「正文剥掉 @ 前缀后为空 → 视为 /help」的分支。@全体成员 的消息
正文同样会被剥空，一旦平台把这类消息推给机器人（@全体在语义上
@ 了包括机器人在内的所有人），就会命中该分支回复帮助。

另一个风险点是 `mentioned_bot()`：如果 @全体成员 在 `mentions[]` 里
产生 `is_you` 条目，后面跟的非指令文本还会触发人格聊天。

## 设计

新增 `mentions_everyone(message)`，放在 `mentioned_bot()` 旁边。
@全体成员 的推送形态没有官方统一标准（改动时没抓到真实 payload，
服务器日志无法远程查看），四种可能的样子**全部检查**，宁可误杀不可漏放：

1. payload 里 `mention_everyone` 为真值（频道消息有此字段）
2. `mentions[]` 里带 everyone 条目（`id` / `member_openid` 为 `everyone`，
   或 `username` 含「全体成员」）
3. 原始 `content` 里有 `<@!everyone>` 之类的角括号标记
   ——`normalize_incoming` 会把标记整个剥掉，必须在剥之前查
4. 原始 `content` 里有「@全体成员」字面文本

两个群消息处理器（群@、群全量）在 dedup 之后**立即**调用该判定，
命中即整条忽略并记一条日志，指令、人格聊天、关键词全部跳过。
单聊不存在 @全体成员，不处理。

误杀面：正文里**字面出现**「@全体成员」四个字加 @ 符号的普通消息
也会被忽略（第 4 条）。这是刻意的——@全体成员 本来就是公告性质，
机器人在这种消息下面插话不合适；正文只提「全体成员」不带 @ 不受影响。

## 改动文件

| 文件 | 说明 |
|---|---|
| `bot.py` | 新增 `mentions_everyone()` + `_EVERYONE_MARKUP_RE`；两个群处理器入口加守卫 |
| `_test_reply_policy.py` | 新增用例 [12]–[17] |
| `README.md` | 回复策略段补充「@全体成员不响应」 |

## 测试报告

### 自动化（本地全通过）

`_test_reply_policy.py` 17 项 / 0 失败，新增：

- `[12]` 群@：mentions 带 everyone 条目、无正文 → 不回帮助（旧代码会回）
- `[13]` 群@：正文「@全体成员 /ping」字面文本 → 整条忽略，不回 pong
- `[14]` 群全量：`mention_everyone=true` + `/ping` → 忽略
- `[15]` 群全量：content 带 `<@!everyone>` 标记（剥掉后剩 `/ping`）→ 忽略
- `[16]` 群全量：普通 `/ping` 无全体成员 → 正常回 pong（防误杀回归）
- `[17]` 群@：正文只提「全体成员」不带 @ → 行为不变；被 @bot 的 `/ping` 仍正常

连带回归：`_test_commands.py`、`_test_ark_news.py`、`smoke_test.py` 全部通过。

### 待人工验证（部署后）

1. 服务器更新 `bot.py` 并 `systemctl restart qqbot`
2. 群里 @全体成员（不带正文）→ 机器人**沉默**，日志出现
   `[群@] @全体成员消息，已忽略` 或 `[群全量] @全体成员消息，已忽略`
3. 群里 @全体成员 + 文字（如「@全体成员 明日方舟更新了」）→ 仍沉默
4. 群里正常 @机器人 `/ping` → 仍回 pong

## 备注

- 改动时**没有**真实 @全体成员 的 payload 样本（服务器只支持密码登录，
  本地无法查看线上日志），四形态判定是按平台惯例推断的。如果部署后
  机器人仍会响应 @全体成员，在服务器 `.env` 加 `QQ_BOT_DUMP_EVENTS=1`
  重启，让用户再发一条 @全体成员，从 `logs/raw_events.jsonl` 里把
  真实形态补进 `mentions_everyone()`。
- 用户 2026-10-04 已在群里发过一条 @全体成员（当时线上还是旧代码），
  下次对话先确认当时机器人是否响应了、响应了什么。

## 同日修正：真实 payload 形态

当天下午的架构调研（读了 nonebot/adapter-qq 对新版群消息 payload 的建模，
[models/qq.py](https://github.com/nonebot/adapter-qq/blob/master/nonebot/adapters/qq/models/qq.py)）
拿到了 @全体成员 的实锤格式，与上文的推断形态有出入，已补进判定：

| 形态 | 上文推断 | 实锤（adapter-qq） | 处理 |
|---|---|---|---|
| mentions 条目 | `id == "everyone"` | `{scope: "all", is_you: true, username}`，**没有** id 字段 | 新增 `scope == "all"` 判定（保留旧判定兜底） |
| content 内联标记 | `<@!everyone>` | `<qqbot-at-everyone />` | 正则改为两者都匹配（外加频道风格 `<@all>`） |

补充要点：实锤条目同样带 `is_you: true`，所以 `mentions_everyone()` 必须先于
`mentioned_bot()` 判定执行（现在两个入口都在 `group_preamble()` 里最先做守卫）。

新增用例 `[18]` scope:"all"、`[19]` `<qqbot-at-everyone />`、`[20]` `<@all>`，全套 20 项通过。
