# 黑话 / 人物印象自动学习

- 日期：2026-10-27
- 涉及：`memory_store.py`（新）、`learning.py`（新）、`bot.py`、`persona.py`、
  `group_context.py`、`_test_memory.py`（新）、`_test_learning.py`（新）、
  `.env.example`、`README.md`、`docs/ENV-AND-PITFALLS.md`

## 需求

大肥鱼的黑话学习、人物印象、称呼，按群隔离，保留依蜜尔爱因风味；
默认低频自动学习，手动可查/可删。

## 数据

`data/memory.db`（SQLite，按群隔离）：

- `aliases(scope, openid, alias)`：`/叫我 小明`；
- `impressions(scope, openid, note, source)`：bot 对人的印象；
- `slang(scope, term, meaning, source, confidence)`：黑话/梗/缩写；
- `learn_state(scope, last_ts)`：上次自动学习时间，避免重复喂同一批消息。

`scope` 为 `group:<群openid>` / `c2c:<openid>`；黑话和印象不跨群共享。

## 命令

```text
/叫我 小明                 任何人，设置群内称呼
/黑话                      列出本群黑话
/黑话 yyds                 查词
/黑话 添加 yyds 永远的神    管理员
/黑话 删除 yyds            管理员
/黑话 学                    管理员，从最近 40 条群聊提取 0~3 个黑话
/印象                      看 bot 对你的称呼/印象
/印象 记 说话直，常聊终末地  管理员，手写
/印象 学                    管理员，从最近群聊总结 3 个人
/印象 清                    管理员，清空本群印象
```

## 注入

只拼当前发言人的称呼/印象 + 当前消息/群背景里出现的黑话：

```text
（本机对小明 的印象：说话直，常聊终末地）
（本群黑话：yyds=永远的神；草=语气词）
（发言人：小明）
...
```

不把整本词典塞进提示词；单条黑话解释 ≤120 字，印象 ≤400 字。

## 自动学习

- 后台任务 `learning.poll_loop`：启动后跑一轮，之后每
  `QQ_BOT_MEMORY_LEARN_MINUTES` 分钟（线上 120）；
- 只处理最近 4 小时有消息、且自上次学习后新增 ≥6 条的群，最多 5 个群/轮；
- 黑话：最近 40 条 → DS 提取 0~3 个，`confidence < 0.5` 丢弃；
- 印象：按发言人分组，发言 ≥3 条的人，最多 3 人，每人 1~2 条；
- 自动条目 `source=auto`；**不覆盖管理员手写条目**；
- 敏感词命中的黑话直接丢弃；
- 学习只读本群消息，不联网、不跨群；不抓备注名。

## 配置

```ini
QQ_BOT_MEMORY=1
QQ_BOT_MEMORY_LEARN_MINUTES=120
QQ_BOT_MEMORY_LEARN_MAX_GROUPS=5
```

## 测试

- `_test_memory.py`：称呼/印象/黑话读写、渲染、手写优先、低置信不注入、命令；
- `_test_learning.py`：黑话提取、低置信过滤、手写保护、印象提取、模型乱返回不炸；
- 其他会走 `persona_reply` 的测试显式 `QQ_BOT_MEMORY=0`，避免线上记忆干扰断言。

## 线上首次效果（2026-10-27 20:41）

- 黑话 2 条（`涩图`、`拉了`）；
- 人物印象 3 人；
- 启动日志：`记忆：开（黑话/印象；自动学习每 120 分钟，最多 5 群）`。

## 备注

- 自动学习会消耗 DS token，但每轮最多 1+3 次调用，成本很低；
- 如果觉得学得太多/太杂：调大间隔、调小 `MAX_GROUPS`，或 `/印象 清`、`/黑话 删除`；
- 删除 `data/memory.db` 只丢记忆，不影响图库/人格记忆。
