# 向管理员汇报（主动私聊）+ R6 key 过期提醒

日期：2026-10-12

## 需求

需要**人来处理**的事（第一个：R6 战绩查询的 API key 快过期）要能自动**私聊通知管理员**；
以后别的要汇报的事也复用这套。

## 设计（`notices.py`）

- **定时**（默认 720 分钟 = 12 小时）+ **启动时**各跑一次；
- 检查项清单 `CHECKS`：每个是无参函数，返回 `(唯一key, 通知正文)` 或 `None`；
  当前只有 `check_r6_key`。**加新检查往 `CHECKS` 里塞一个函数即可**。
- 每个通知有**唯一 key**，发过就不再发——持久化在 `data/push.db` 的 `seen_notices`
  （`PushStore.unseen_notices` / `mark_notices`）。无管理员时**不标记**，等登记了再发。
- 发送：`api.post_c2c_message(openid=…, content=…)` **主动私聊** `store.list_admins()` 里的每个管理员；
  失败只记日志（不影响主流程）。

配置（`.env`）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `QQ_BOT_NOTICE_MINUTES` | 720 | 检查间隔分钟，0 = 关闭 |
| `QQ_BOT_NOTICE_WARN_DAYS` | 7 | 提前几天开始提醒（R6 key） |
| `QQ_BOT_R6_KEY_EXPIRE` | 空 | R6 key 到期日 `YYYY-MM-DD`（API 不返回到期日，须手配） |

## 改动文件

| 文件 | 说明 |
|---|---|
| `notices.py` | 新增：检查调度 + 去重 + 私聊管理员 |
| `push_store.py` | 新增 `seen_notices` 表与 `unseen_notices` / `mark_notices` |
| `bot.py` | `import notices`；`on_ready` 启动 `notices.poll_loop(self.api, store, push_store)` |
| `_test_notices.py` | 新增：14 项（离线） |
| `.env.example` | 配置说明 |

## 测试

- `_test_notices.py`：**14 项全过**（离线：key 判定/去重/无管理员/调度）。
- **真机**：临时把 `QQ_BOT_R6_KEY_EXPIRE` 设成 2 天后重启 → 日志
  `[通知] 已通知 2 名管理员：r6key-soon:2026-10-14`，**两名管理员都收到了主动私聊**；
  随后改回 `2026-11-11` 并清掉测试去重标记。

## 备注

- **主动私聊（c2c）实测可发**；「每群每自然月 4 条」的限制只针对**群**主动消息，私聊不受影响（至少目前）。
- R6 key 将于 2026-11-11 到期：届时（提前 7 天）会自动私聊提醒，重新生成后更新 `.env` 重启即可。
