# 架构调研落地：守卫前置抽取、公告推送配额认知修正

日期：2026-10-04

## 背景

对主流 QQ 机器人（NoneBot2 / zhenxun_bot / AstrBot / MaiBot / LangBot）和
QQ 官方平台现状做了架构调研（完整结论见对话记录），本记录只落地对本仓库
**低风险高收益**的三项。调研还否决了一个伪需求，一并记档。

## 改动

### 1. 群消息公共前置 `group_preamble()`（bot.py）

`on_group_at_message_create` / `on_group_message_create` 各自维护一份
「去重 → @全体成员守卫 → 正文归一化」前置逻辑，将来加新入口必漏。抽成
模块级函数（不用 self，测试的 FakeClient 直调 handler 的模式不受影响）：

```python
content = group_preamble(message, "群@", "at")   # 返回 None = 整条忽略
if content is None:
    return
```

去重键格式（`at:{id}` / `all:{id}`）与日志前缀不变。

### 2. @全体成员真实 payload 形态（bot.py，同日修正）

详见 [2026-10-04-ignore-at-everyone.md](2026-10-04-ignore-at-everyone.md) 的
「同日修正」一节：mentions 条目按 `scope == "all"` 判定（adapter-qq 实锤），
content 标记正则补 `<qqbot-at-everyone />` 与 `<@all>`。

### 3. 公告推送的平台配额认知修正（ark_news.py / README.md）

调研发现仓库里的配额认知是过时的（旧文档「认证后 60 条/分钟、每群每天
1000 条」）。现行官方规则（2026-06 起）：

- **主动消息（不带 msg_id）：每群每自然月 4 条**——公告推送会吃这个配额，
  低频更新公告（每月个位数）在限内，但没余量
- 全量开放的前提是**群主在群里开启「机器人主动发言」开关**；开启后频控为
  账号 30-60 条/分钟、单群 20 条/分钟
- 被动回复（带 msg_id）不受月配额限制，`/公告 查` 不受影响

代码侧：`push_to_groups()` 识别异常里的 `22009` / `msg limit`，日志明确提示
「主动消息超限 + 如何解决」，其余失败照旧记一行。模块 docstring 与 README
「关键限制」一节同步修正。

### 4. 否决项记档：人格上下文压缩（不做）

调研报告建议「上下文无限增长 → 加 LLM 摘要压缩」，但核对 `chat_store.py`
后发现是伪需求：**已有双重过期**（每会话最多 100 条 + 20 分钟 TTL，追加时
自动删旧行），上下文天然有界，qwen-flash-character 的窗口完全装得下。
加压缩只会引入每次摘要的 LLM 调用和新的失败路径。若将来把
`QQ_BOT_CHAT_MEMORY` 调大或把记忆做成长期的，再回头考虑 MaiBot 的
「中期记忆摘要」方案。

另记一个**暂不处理**的隐患：botpy 的 `reply()` 不带 `msg_seq`，同一条消息
回复第二次会因 `msg_id + msg_seq` 重复被平台拒。当前所有路径对一条消息
只回复一次，未触发；若将来加「一条消息多次回复」的功能，需自行维护
`msg_seq` 递增。

## 改动文件

| 文件 | 说明 |
|---|---|
| `bot.py` | 新增 `group_preamble()`；`mentions_everyone()` 补 scope/标签形态 |
| `ark_news.py` | docstring 配额修正；`push_to_groups()` 识别 22009 |
| `_test_reply_policy.py` | 用例 [18][19][20] |
| `_test_ark_news.py` | 新增 [4.5] 配额错误容错 |
| `README.md` | 「关键限制」配额修正；回复策略段补 @全体成员（上午已加） |

## 测试报告

### 自动化（本地，2026-10-04）

16 个 `_test_*.py` 套件 + `smoke_test.py` **全部通过，0 失败**：

- `_test_reply_policy.py`：20 项（新增 [18] scope:"all"、[19] 官方标签、[20] `<@all>`）
- `_test_ark_news.py`：新增 [4.5]——G1 抛 22009 不中断循环、G2 照常收到、
  失败群不计入每日保险丝、成功群计入
- 其余 14 个套件（add/admin/cards/commands/delete/gallery/help_image/
  layers/links/persona/privacy/store/thumb/watchdog）：回归通过

### 待人工验证（部署后）

1. 群里 @全体成员（不带正文 / 带文字各一次）→ 机器人沉默，日志出现
   `[群@] @全体成员消息，已忽略` 或 `[群全量] @全体成员消息，已忽略`
2. 正常 @机器人 `/ping` → 仍回 pong
3. 下次公告推送若失败，看日志是否出现 22009 明确提示（区分配额问题与网络问题）

## 备注

- 调研中评估但**未落地**的大项：命令注册表重构（治 bot.py 巨石化，约 2-3 天）、
  全量群聊历史入上下文（MaiBot 模式，涉及「记录未 @ 消息」的隐私取舍，
  需先对照 privacy-policy.md）、SDK 迁移（botpy 已停更，候选 ymbotpy /
  nonebot-adapter-qq / webhook）。等人工验证完本轮改动再逐项排期。
