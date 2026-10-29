# Bot 管理员群

> 最后确认：2026-10-29
> 用途：接收 bot 的运营/成本类主动消息（目前是每日用量日报）。

## 1. 当前管理员群

| 项目 | 值 |
|---|---|
| 群名 | 白、_樛木、Auuuu |
| 群 openid | `D604F254A4EBBCDBE37BB0A6BB53D0C1` |
| 成员数 | 4 |
| 主动发言 | 已开启并实测成功（2026-10-29） |

对应配置：

```ini
QQ_BOT_ADMIN_GROUP=D604F254A4EBBCDBE37BB0A6BB53D0C1
QQ_BOT_USAGE_REPORT=1
QQ_BOT_USAGE_REPORT_HOUR=9
```

这个群同时也是 MaaEnd webhook 的默认转发群，所以群里会同时看到：

- MaaEnd 任务日志（按任务分段推送）；
- 每天 09:00 后的模型用量日报；
- 以后如果增加其他管理员通知，也默认发到这里。

## 2. 不是什么群

`94C43E8EB49E9ABA7BA066BE771579C5`（群名「邪恶煮汁」，45 人）**不是**
管理员群。2026-10-29 曾误把它当成管理员群配置，主动发送返回：

```text
40034105 主动消息失败, 无权限
```

不要再用它接收日报。

## 3. 换群 / 查 openid

### 换群

改 `/opt/qqbot/.env`：

```ini
QQ_BOT_ADMIN_GROUP=<新的群 openid>
```

然后重启：

```bash
sudo systemctl restart qqbot
```

### 在群里查 openid

在目标群里发：

```text
/查询标识
```

bot 会回复当前群的 `group_openid`（以及你的用户 openid）。

也可以用官方接口查群名，确认没配错：

```bash
curl -H "Authorization: QQBot <access_token>" \
  "https://api.sgroup.qq.com/v2/groups/<group_openid>/info"
```

## 4. 主动消息权限

管理员群必须由群主在群设置里开启 **「机器人主动发言」**，否则
`post_group_message` 会报 `40034105`，日报发不出去。

如果暂时没开，`usage_report.py` 会：

1. 把当天日报存成 `pending`；
2. 等管理员群下次有人发**普通聊天消息**时，用被动回复补发；
3. 指令和 @ 消息会跳过，避免和它们的回复抢 `msg_seq`。

所以没开权限也不会完全丢日报，只是不能保证每天 9 点准时到。

## 5. 日报内容

```text
【用量日报】2026-10-28
调用 86 次 · 输入 34,096 · 输出 38,515 · 推理 35,997 · 缓存 6,400
花费 $0.0373（约 ¥0.27）
类型：学习 84 / 看图 1 / 对话 1
Top：memory-learn $0.0349 / 单聊 68BC60AB1E $0.0024
```

- 统计前一天 `00:00–24:00`（服务器本地时区）；
- 花费来自 OpenCode Go 返回的 `list_cost`（美元），人民币按 `×7.2` 估算；
- 状态文件：`data/usage_report.json`；
- 想手动看最近 N 天：管理员私聊 `/用量 [天数]`。

## 6. 相关文件

- `usage_report.py`：日报生成、定时检查、pending 补发；
- `usage_store.py`：用量落库与区间汇总；
- `bot.py`：启动日报任务、管理员群普通聊天时触发补发；
- `.env.example`：`QQ_BOT_ADMIN_GROUP` / `QQ_BOT_USAGE_REPORT*` 示例；
- `docs/changes/2026-10-29-recent-features.md`：本次功能总结。
