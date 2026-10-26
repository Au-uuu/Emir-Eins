# 环境与错题本

> **用途**：换人 / 换会话接手时先读这篇，再动手改。
> **配套**：`DEPLOY.md`（首次部署）、`DEPLOY-UPDATE.md`（日常更新）、
> `docs/CHANGELOG.md`（逐次改动索引）、`docs/changes/*.md`（单次改动详情）。
> **原则**：这篇只记**长期有效**的东西（环境、口径、坑）；一次性的改动细节放 `changes/`。

---

## 1. 线上环境

### 1.1 机器与服务

| 项 | 值 |
|---|---|
| 服务器 | `ubuntu@124.223.62.16`（腾讯云，大陆机房，约 3.7 GB 内存） |
| 登录 | `C:\DSH\.keys\qqbot-tencent.pem`，`ubuntu` 用户**免密 sudo** |
| 安装目录 | `/opt/qqbot` |
| 服务 | `systemd` 服务名 **`qqbot`**，以 **root** 运行（`/opt/qqbot/.venv/bin/python /opt/qqbot/bot.py`） |
| Python | 3.12，虚拟环境 `/opt/qqbot/.venv`（装依赖必须用它：`sudo -n /opt/qqbot/.venv/bin/pip install ...`） |
| 看日志 | `sudo -n journalctl -u qqbot --since "30 minutes ago" --no-pager` |
| 重启 | `sudo -n systemctl restart qqbot`（部署脚本会自动做） |
| 状态 | `systemctl is-active qqbot` |
| 回滚 | 每次部署会把旧文件备份到 `/root/<文件名>.bak-<时间戳>`，`sudo cp` 回去再重启即可 |

> ⚠️ `.env` 权限是 `600 root`，用 `ubuntu` 用户直接读会 Permission denied，记得加 `sudo -n`。

### 1.2 本地 / Git / 部署

| 项 | 值 |
|---|---|
| 本地工作区 | `C:\DSH\qqbot` |
| 远端 | `git@github.com:Au-uuu/Emir-Eins.git`，分支 `main` |
| 部署 | `powershell -NoProfile -ExecutionPolicy Bypass -File .\deploy\upload.ps1` |
| 部署做了什么 | ①scp 所有 `*.py` + `persona/` 到服务器 `/tmp` ②备份旧文件 ③覆盖安装 ④**逐文件 SHA256 比对** ⑤**在服务器跑全部 `_test_*.py`** ⑥重启并打印启动日志 |
| 只看会传什么 | 加 `-DryRun` |
| 只传不重启 | `-NoRestart`（改动不会生效） |

> **部署 = 重启进程** → 所有内存缓存清空（详见 §7.1）。
> 测试失败**不会**中断部署（脚本用 `set -e`，但测试命令的退出码被 `tail` 吞了），
> 所以**必须自己看**输出里的「失败 N 项」。

### 1.3 目录速查

| 路径 | 作用 |
|---|---|
| `bot.py` | 主程序：事件、指令分发、回复策略、msg_seq、help、人格入口 |
| `r6stats.py` | R6 查询/归一/缓存/额度；`r6_card.py` 画卡片；`r6_bind.py` 绑定存储 |
| `r6official.py` | 育碧官方 API 客户端（**已停用**，见 §6.6） |
| `kb.py` | 本地知识库 BM25 + 召回门槛；`tools/build_kb_server.py` 负责建库 |
| `fortune.py` | `/今日运势`（本地黄历 + 确定性抽签） |
| `ark_news.py` | 游戏公告抓取 + 主动推送；`notices.py` 管理员汇报 |
| `push_store.py` | 公告开关/已见去重（SQLite）；`chat_store.py` 人格记忆 |
| `image_store.py` / `uploader.py` / `thumb` 相关 | 图库、上传、缩略图 |
| `persona/依蜜尔爱因.md` | 人格卡（人设、看图规则等） |
| `deploy/upload.ps1` | 部署脚本 |
| `docs/changes/` + `docs/CHANGELOG.md` | 变更记录（改完要补） |

### 1.4 `data/` 里的状态文件

| 文件 | 内容 | 能否删 |
|---|---|---|
| `data/images.db` + `images/` + `thumbs/` | 图库（图片、关键词、层级、缩略图） | ❌ 删了图库就没了 |
| `data/chat.db` | 人格聊天记忆（**按场景**：`c2c:<openid>` / `group:<openid>`，默认 100 条） | 可删（丢记忆） |
| `data/push.db` | `push_groups`（群×游戏开关）+ `seen_news`（已见公告去重） | 可删（会重推/丢开关） |
| `data/r6bind.db` | R6 绑定（openid → 平台/昵称/profile_id） | 可删（丢绑定） |
| `data/r6_ranks.json` | Arenyze v7 段位表缓存（7 天 TTL） | 可删（会自动重拉） |
| `data/r6assets/` | 头像/段位图标磁盘缓存（7 天 TTL） | 可删 |
| `data/kb/` | 本地知识库语料（`*.md`，`_` 开头不索引） | ❌ 见 §5.3 |
| `data/help_bg.jpg` | 帮助图背景 | 可删（退回文本帮助） |

> 备份：`deploy/backup-nutstore.sh` 会把 `images/ thumbs/ images.db kb/` 同步到坚果云。

### 1.5 关键配置（`.env`）

完整清单看 `.env.example`。这里只列长期需要记住的：

| 变量 | 说明 |
|---|---|
| `QQ_BOT_TEXT_MODEL` | 人格文本模型（当前 `qwen-flash`）；**知识库构建不要占用它** |
| `QQ_BOT_R6_KEY` / `QQ_BOT_R6_KEY_EXPIRE` | Arenyze key 与到期日（到期前管理员会被私聊提醒） |
| `QQ_BOT_R6_TIMEOUT` | R6 接口超时秒数（默认 25） |
| `QQ_BOT_NEWS_POLL_MINUTES` | 公告轮询间隔（`0` = 关闭主动推送） |
| `QQ_BOT_NEWS_KEYWORDS` | 公告推送关键词（`\|` 分隔） |
| `QQ_BOT_NEWS_DAILY_LIMIT` | 每群每日推送上限（防刷屏保险丝） |
| `QQ_BOT_KB_DIR` / `QQ_BOT_KB_MIN_SCORE` / `QQ_BOT_KB_TOP` | 知识库目录 / 召回分数门槛 / 条数 |
| `QQ_BOT_WEBSEARCH` / `QQ_BOT_WEBSEARCH_MODEL` | 联网搜索开关与模型 |
| ~~`QQ_BOT_UBI_*`~~ | 育碧官方 API 的账号密码（**已删除**，见 §6.6） |

### 1.6 改完代码的固定动作

1. 本地改 → `python -c "import ast; ..."` 语法检查 → 跑相关 `_test_*.py`；
2. `deploy/upload.ps1` 部署（看「失败 N 项」和服务状态）；
3. 群/单聊里实测；
4. 补 `docs/changes/YYYY-MM-DD-简短英文名.md` + 在 `docs/CHANGELOG.md` 加一行；
5. 行为/约束有变化时**同步 `README.md`**（CHANGELOG 末尾的「约定」有写）。

---

## 2. 功能地图

| 指令 | 模块 | 说明 |
|---|---|---|
| 直接说话 / 群 @ | `bot.py` + `persona.py` + `kb.py` | 人格聊天，带本地知识库检索、看图（视觉模型）、联网搜索 |
| `/help`、`/help r6` | `bot.py` + `help_image.py` | 主帮助只列常用；`HELP_TOPICS` 放详细用法（r6/图库/公告/名片） |
| `/今日运势`、`/运势` | `fortune.py` | 本地黄历 + 确定性抽签（同人同天固定），**必须带斜杠** |
| `/r6`、`/彩六`、`/r6绑定`、`/r6网站` | `r6stats.py` / `r6_card.py` / `r6_bind.py` | 见 §6 |
| `/公告 开|关|查` | `bot.py` + `ark_news.py` + `push_store.py` | 按群开关；主动推送受 QQ 配额限制（§8.10） |
| `/复读`、`/echo` | `bot.py` | **必须带斜杠**（裸词走闲聊） |
| `/图库`、`/添加`、`/删除`、`/来只`… | `image_store.py` | 图库；只有这三个可省斜杠 |
| `/添加名片`、`/游戏名片` | 名片拼图 | — |

**数据流**

- R6：`Arenyze /v2/profile + /v2/fullstats` → `r6stats._build` 归一 → 缓存 5 分钟（SWR）→ `r6_card.render`（PIL）→ 上传发图。
- 知识库：`tools/build_kb_server.py` 抓 wiki → `data/kb/*.md` → `kb.py` BM25（**加载后不热更，改语料要重启**）→ 注入人格提示词。
- 公告：`ark_news.poll_loop`（10 分钟）→ 抓列表 → `push_store.filter_unseen` → 关键词过滤 → `api.post_group_message`（**主动消息**，无 msg_id）。

---

## 3. R6 专题（最容易踩，务必看完）

### 3.1 字段口径（**最重要**）

| 想要的 | 取哪 | 说明 |
|---|---|---|
| **生涯时长** | `/v2/profile → seasons.data.segments[type=="overview"].stats.timePlayed.value`（**秒**） | 与 R6Tracker 逐一对得上：mojinqiu 180h、MacieJay 9305h、XII-FuHua 1266h |
| 等级 | `account.level`（或 `seasons.data.metadata.clearanceLevel`） | — |
| 当前段位/RP | `stats.platform_families_full_profiles[0].board_ids_full_profiles[]` 里 `board_id=="ranked"` → `profile.rank_points / max_rank_points` | — |
| **逐赛季 RP/段位** | `fullstats.data.segments`（`gamemode=="pvp_ranked"`）→ **优先 `maxRankPoints`** | = R6Tracker 的 **MAX** 列（Arenyze 网站前端也是 `maxRankPoints ‖ rankPoints`，标题叫 Peak rank） |
| 胜率 | `matchesWon / (matchesWon + matchesLost)` | 不要用 `matchesPlayed` 当分母（里面有无结果场次，会差 1 个百分点） |
| 常用干员 | `fullstats.operators` | 这是 `fullstats` 目前**唯一**不可替代的用途（去掉它就能把每次查询降到 1 call） |
| ❌ 不要用 | `fullstats.totalsHoursPlayed.hours` | 是「所有 PvP 模式逐赛季相加的**会话**时长」，忽高忽低（388/5887/1360 vs 正确的 180/9305/1266） |

### 3.2 缓存与额度

- `CACHE_TTL = 300s`（5 分钟）+ **stale-while-revalidate**：有缓存立刻返回，过期那条丢后台线程刷新（按玩家去重，失败留旧数据）。
- **Arenyze 免费额度：20 次/分、120 次/时、300 次/天、2,000 次/月**（响应头 `X-RateLimit-*` 会带剩余量）。
  - 每次冷查 **2 个接口** → 2,000/月 ≈ 1,000 次冷查/月 ≈ 33 次/天。
  - `429` 时按 `Retry-After` 记「冷却到什么时候」，期间直接失败不再白打（`_blocked_until`）。
  - 当天剩余 ≤ 40 时，`effective_ttl()` 自动把缓存拉到 30 分钟「保命」。
  - 查自己用量：`GET https://public-api.arenyze.com/r6/api/me/usage`（带 `api-key`）。
- 冷查要 5~9 秒（Arenyze 现拉育碧数据）；热查 0 秒。
- 段位表 `/ranks` 落盘 `data/r6_ranks.json`（7 天），**429 时也不会让段位名全变空**。

### 3.3 外链格式（写错过）

```
R6Tracker：https://r6.tracker.network/r6siege/profile/{ubi|psn|xbl}/{昵称}/overview
stats.cc ：https://stats.cc/siege/{昵称}/{育碧 profile uuid}
```

- PC 的平台词是 **`ubi`**，不是 `pc`；主机是 `psn` / `xbl`。
- stats.cc 必须带育碧账号 UUID（`profile_id`），绑定时已一并存下，所以 `/r6网站` 秒回。
- 两个站都有 Cloudflare 反爬：**机器人只给链接，用户自己浏览器打开**。

### 3.4 绑定

- `/r6绑定 [平台] 昵称` → 真查一次（验证昵称 + 拿 `profile_id`）→ 存**规范昵称**；再绑即覆盖（**没有解绑指令**，`unbind()` 留给运维）。
- **按场景隔离**：单聊 `user_openid` 与各群 `member_openid` 不同 → 换场景要重绑（QQ 平台限制）。

### 3.5 已知限制

- 同一玩家在**本进程**里第一次查（含每次部署/重启后）必冷，5~9 秒。
- 上游对**老账号**历史数据有失真（实测 MacieJay 的场次/时长比例不合理），我们能拿到什么算什么。
- 并发多人同时冷查会一起等；别在重启后立刻群发。

### 3.6 官方 API（Plan B，已停用）

- `r6official.py` 保留：`playtime()` 解官方总/PvP/PvE 秒数与等级，`ranked()` 解当前 RP/最高。
- **必须配齐** `QQ_BOT_UBI_EMAIL` + `QQ_BOT_UBI_PASSWORD` + `QQ_BOT_UBI_PROXY` 才启用（缺一项完全不动）。
- 为什么停：本机是**大陆机房 IP**，育碧按 IP 限流（`errorCode 1101 Too many calls per IP address`，连登录都 429）；Clash 的新加坡机房节点同样 429；家宽直连吃 **DataDome** 挑战。匿名 appId 拿票已被关闭，必须邮箱密码。
- 另：官方 `player_skill_records`（逐赛季段位）**只覆盖旧 MMR 时代（赛季 6–27）**，RP 时代没有。
- 启用步骤：`pip install curl_cffi` → 配三项 env → 先用 `playtime()` 小样本验证，再叠加到 `r6stats`。

---

## 4. 知识库（KB）

### 4.1 语料格式（改语料必看）

- 一个作品一个 `data/kb/<作品名>.md`；
- 首段 `## <作品名>（作品/世界观）`，其后每个词条一个 `## <角色/词条名>`；
- `_` 开头文件不索引；**改完要重启进程**（`kb._load` 只加载一次）。

### 4.2 召回门槛（2026-10 新增）

- **必须提到作品名或角色名才检索**，否则返回空（挡"今日运势"把星穹铁道拽进来那种）。
- 作品名允许**部分提及**（"星穹铁道" → 《崩坏：星穹铁道》）；词条名必须**整段出现**。
- 命中后**只在该作品的词条内**做 BM25（不跨作品串味）。
- `_GENERIC_NAMES`：兼作日常词的词条名（满月/星星/天气…）+ 章节名（角色/设定/介绍…）不算触发词。
- **名字 < 2 字不参与**（实测《鸣潮》有单字词条「角」，会被"角色"包含）。
- 语料没有 `## 标题` 结构时，自动不启用门槛（兼容旧语料）。

### 4.3 调试

```bash
# 看某句话命中什么（门槛生效时应为空）
python3 -c "import kb; print(kb.search('今日运势'))"
```

---

## 5. 公告推送

- 按群开关：`/公告 开|关 明日方舟|终末地|全部`（**默认关**）；`/公告 查 [游戏]` 是**被动**查询，不受推送配额影响。
- 只推**标题命中关键词**的：线上是 `更新|维护|停机|版本|活动|联动|上线|开放|预告`（故意不含"限时"，会误命中"未成年人游戏限时通知"）。
- `seen_news` 是**按游戏全局**记的 → 新订阅的群**不会补推**历史公告。
- ⚠️ QQ 主动消息 **每群每自然月 4 条**（群主可在群设置开「机器人主动发言」解除）。超限报 `22009`，日志有提示，且**不会重试**（避免刷屏）。

---

## 6. 排查手册

### 6.1 日志里该找什么

| 标记 | 含义 |
|---|---|
| `on_ready ... 已上线` | 启动成功 |
| `[公告] 轮询已启动` | 公告轮询起来了（间隔/关键词会打印） |
| `[公告] 已推送到群 X（游戏）` | 推送成功（**唯一成功凭据**） |
| `[公告] 被平台拒绝（22009 ...）` | 主动消息配额用尽 |
| `[R6] 查询 platform=... name=...` | 收到查询 |
| `[R6] 查无此人` / `[R6] 接口不稳定` / `被限流，N 分钟后再试` | 三类失败，话术已分开 |
| `[人格] 触发限流，已跳过` | 人格回复被节流（正常保护） |
| `摘要: [QQ小程序]…` | **看图的卡片描述**，不是公告抓取（别误判） |
| `处理群全量指令失败: 消息被去重` | **msg_seq 没递增**（见 §7.2） |

### 6.2 常用命令

```bash
# 服务/启动日志
systemctl is-active qqbot
sudo -n journalctl -u qqbot --since "30 minutes ago" --no-pager | tail -50

# 看配置（.env 是 600 root）
sudo -n grep -E '^QQ_BOT_(R6|NEWS|KB|TEXT_MODEL)' /opt/qqbot/.env

# 看 R6 额度
K=$(sudo -n grep '^QQ_BOT_R6_KEY=' /opt/qqbot/.env | cut -d= -f2)
curl -s -H "api-key: $K" https://public-api.arenyze.com/r6/api/me/usage

# 看推送开关
sudo -n /opt/qqbot/.venv/bin/python -c "import sqlite3;c=sqlite3.connect('/opt/qqbot/data/push.db');print(c.execute('select * from push_groups').fetchall())"
```

### 6.3 在服务器上自测某段逻辑

```bash
# 把脚本 base64 传上去最稳（避免 PowerShell 吃引号）
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($py))
& $ssh ... "echo $b64 | base64 -d > /tmp/t.py && cd /opt/qqbot && sudo -n /opt/qqbot/.venv/bin/python /tmp/t.py"
```

> 注意：`data/` 与 `.env` 属 root，脚本里读文件要么用 `sudo -n` 跑，要么 `load_dotenv` 前确保权限。

---

## 7. 错题本

> 格式：**现象 → 根因 → 修法 → 防回归**。按踩坑时间倒序不重要，按「容易再犯」排序。

### 7.1 部署/重启会清空内存状态
- **现象**：每次更新后第一波查询变慢；段位名一度全空；改了 KB 语料但没生效。
- **根因**：所有缓存都在进程内存（R6 快照、段位表、字体、KB 索引）。
- **修法**：段位表 / 卡片素材已落盘；其余接受"重启后冷一次"。
- **防回归**：改 KB 语料后**必须重启**；部署后别立刻群发压测。

### 7.2 `msg_seq` 去重会把整条消息吞掉（**最容易复发**）
- **现象**：群里发了指令，机器人**完全没反应**；或第二条消息（图/预览）丢失。
  日志：`400 40054005 消息被去重，请检查请求msgseq`。
- **根因**：QQ 被动回复按 **`msg_id + msg_seq`** 去重；`message.reply()` 不自动递增，
  botpy 的 `msg_seq` 默认 1 → 同一条消息发第二条就判重丢弃。
- **修法**：需要多条回复时，每次用 `next_msg_seq(message.id)` 取号
  （`bot.py:191`，按 msg_id 计数、超 1024 清一半）。R6 曾加过「🔍 正在查…」前置提示，
  正好踩中这条把卡片吞了 → 现已去掉，**每条消息只回一次**。
- **防回归**：`_test_commands.py` 有「每条消息只回一次」断言；任何"多发一条提示"的想法
  先读这段。

### 7.3 R6 时长口径错（2 倍误差）
- **现象**：用户 180 小时显示成 387；MacieJay 9305 显示 5887。
- **根因**：用了 `totalsHoursPlayed.hours`（全模式会话时长）。
- **修法**：改取 `seasons.data.segments[type=="overview"].stats.timePlayed`（秒）。
- **防回归**：`_test_r6stats` 断言"取 overview、且不等于 totalsHoursPlayed"。

### 7.4 R6 历史赛季 RP 不对
- **现象**：与 R6Tracker 差几百 RP。
- **根因**：优先用了 `rankPoints`——它既不是 TN 的 MAX 也不是 FINAL（像中间快照）。
- **修法**：优先 `maxRankPoints`（= TN **MAX** 列；网站前端也是这么取的）。
  TN 的 FINAL 我们复现不出来，所以只做 MAX 口径。
- **防回归**：测试断言"两者都有时取最大"；`rp_show` 字段即展示值。

### 7.5 胜率差 1 个百分点
- **现象**：37 场 17 胜 19 负，我们 46% / TN 47%。
- **根因**：分母用了 `matchesPlayed`（含 1 场无结果）。
- **修法**：分母用「胜 + 负」。
- **防回归**：测试用 112/219 精确断言。

### 7.6 Arenyze 免费额度被打爆
- **现象**：所有查询 429；连段位表都拉不到 → 段位名全空。
- **根因**：免费档 **20/分、120/时、300/天、2,000/月**；实测反复查一天就超。
- **修法**：TTL 5 分钟 + SWR；429 按 `Retry-After` 冷却；额度见底自动拉长到 30 分钟；
  段位表落盘。**不要拿线上 key 反复实测**（用离线 mock / 已缓存数据）。
- **防回归**：`_test_r6stats` 覆盖"429 记恢复时间""限流期间不再打接口"。

### 7.7 知识库泛词误召回（"今日运势" → 星穹列车）
- **现象**：只问"今日运势"，回复里出现「星穹列车/开拓者意志」。
- **根因**：BM25 只看词形；"运势"命中《蔚蓝档案》"四**运势**神"，"开拓"命中《崩坏星穹铁道》开拓者。
- **修法**：加「必须提到作品/角色名」门槛 + 跨作品隔离（§4.2）。
- **防回归**：`_test_kb` 有 8 项（含"日常泛词不触发""跨作品不串味"）。

### 7.8 单字词条让门槛失效
- **现象**：门槛加了之后，「这图里的角色是谁」仍然触发检索。
- **根因**：《鸣潮》语料有个单字词条 **「角」**，被"**角**色"包含。
- **修法**：名字（含文件名）**少于 2 字不参与门槛**；另把"角色/设定/介绍…"放进泛词表。
- **防回归**：`_test_kb` 有对应断言（从线上真实语料发现的）。

### 7.9 复读裸词触发
- **现象**：群友随口一句"复读 xxx"机器人就学舌。
- **根因**：`ECHO_RE`/`ECHO_CN_RE` 写的是 `^/?`（斜杠可选），但帮助里说"只有添加/删除/来只可省斜杠"。
- **修法**：改为 `^/复读`、`^/echo`（必须带斜杠）。
- **防回归**：`_test_commands` 有 6 项（裸词不触发 + 带斜杠正常）。

### 7.10 "公告没推送"——其实是三层原因叠一起
- **现象**：群里很久没收到明日方舟公告。
- **根因**：① 推送**按群开关**（默认关）② 只推标题含关键词的，那几天都是**联动/创作征集**类被过滤
  ③ QQ 主动消息**每群每月 4 条**。
- **修法**：先 `/公告` 确认开关（会回"本来就是开的"）；再核关键词；放宽到 `更新|维护|停机|版本|活动|联动|上线|开放|预告`。
- **防回归**：排查顺序固定为「开关 → 关键词 → 平台配额 → 日志里有没有『已推送到群』」。

### 7.11 抓 R6Tracker / 用育碧官方 API 都走不通
- **现象**：想换数据源，结果 403/429/空数据。
- **根因**：
  - TN 旧页面 `/profile/pc/<name>` 已换成同一套 SPA（`trn-defstat` 元素 0 个，开源爬虫全失效）；
    TN 的 API 对**机房 IP** 直接 403 `You've Been Blocked` + Cloudflare 挑战；且明文禁爬。
  - 育碧官方 API：机房 IP 按 IP 限流（`errorCode 1101`，连登录 429），家宽吃 DataDome；
    匿名 appId 拿票已关闭（要邮箱密码）。
- **修法**：结论是**不需要它们**——准确时长就在 Arenyze 的 `overview` 段里（§3.1）。
- **防回归**：别再往这两个方向投入；`r6official.py` 只当 Plan B。

### 7.12 R6 外链格式写错
- **现象**：`/r6网站` 给的 R6Tracker 链接打不开。
- **根因**：写成 `/profile/pc/<名字>`。
- **修法**：`/r6siege/profile/{ubi|psn|xbl}/{名字}/overview`（PC 是 **ubi**）。
- **防回归**：`_test_r6stats` 断言 URL 形状。

### 7.13 日志误判：`摘要: [QQ小程序]…` 不是公告
- **现象**：排查"公告没推"时看到一堆 `摘要: 《明日方舟》…`，以为抓取在跑。
- **根因**：那是**人格看图**（QQ 卡片/小程序分享）的描述日志。
- **修法**：公告只看 `[公告]` 前缀的行。

### 7.14 行为改了但文案没改
- **现象**：复读加了斜杠要求，但帮助里没提；反过来也曾出现帮助写了、代码没做。
- **约定**：**改行为 → 同步 `HELP_TEXT` / `HELP_TOPICS` / `README.md`**，并在 changes 里写清。

---

## 8. 待办 / 观察项

| 项 | 说明 |
|---|---|
| `QQ_BOT_R6_KEY_EXPIRE` | 换新 key 后没更新，仍是旧值 `2026-11-11`；拿到新到期日记得改（管理员会在到期前被提醒） |
| Arenyze 用量 | 定期跑 `/me/usage`，若长期吃紧：① 去掉常用干员改 1 call/查 ② 或上 PRO（€3.99/月，50,000 次） |
| 公告关键词 | 若活动推送太吵/太少，改 `.env` 的 `QQ_BOT_NEWS_KEYWORDS` 后重启 |
| 群主权限 | 开「机器人主动发言」可解除每月 4 条主动消息限制 |
| `/今日运势` | 可做成图片卡片、加"本周运势"或"漂流瓶"玩法 |
| KB 泛词表 | 发现新的误伤词条名，加进 `_GENERIC_NAMES` |
| R6 卡片 | 可选加"数据更新于 X 分钟前" |
