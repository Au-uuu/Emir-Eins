# R6：绑定、`/r6网站` 外链、帮助分层、错误分类

日期：2026-10-12（承接同日 `2026-10-12-r6-stats.md`）

## 需求

1. 用户绑定 ID 后，`/r6`（不带参数）直接查绑定的昵称；没绑定就给提示。
2. `/r6网站` 快捷给出 R6Tracker / stats.cc 的个人主页链接，方便查历史战绩。
3. `/彩六` 也能触发指令。
4. 帮助分层：小功能多的指令，主帮助只列常用几项，标注「更多用法：/help r6」。
5. 不要解绑——重新绑定直接覆盖。

## 实现

| 位置 | 说明 |
|---|---|
| `r6_bind.py` | 新增。`data/r6bind.db`，`openid → (platform, name, profile_id)`，upsert 即覆盖 |
| `r6stats.site_urls()` | 拼 R6Tracker / stats.cc 外链 |
| `r6stats.query()` | 新增 `profile_id`（stats.cc 用）；昵称改用接口返回的**规范写法** |
| `bot.py` | `/r6绑定`、`/r6网站`、`/r6帮助`；无参数走绑定；`_r6_split` / `_r6_fetch` |
| `bot.py` | `HELP_TOPICS`（r6 / 图库 / 公告 / 名片）+ `HELP_TOPIC_ALIASES` + `help_topic()`；派发改用 `HELP_RE` |

指令：`/r6 [平台] 昵称`、`/r6`（查绑定）、`/r6绑定 [平台] 昵称`、`/r6网站`；
别名 `/彩六`、`/彩虹六号`（**必须带斜杠**，否则群里聊到「彩六」会被当成查询）。

## 踩过的坑

### 1. R6Tracker 的 URL 格式（第一次写错了）

正确格式是：

```
https://r6.tracker.network/r6siege/profile/<平台>/<昵称>/overview
```

平台 slug 是 **`ubi` / `psn` / `xbl`**——**PC 是 `ubi`，不是 `pc`**。
昵称大小写不敏感，用接口返回的规范写法最好。

### 2. stats.cc 需要 Ubisoft 账号 UUID

```
https://stats.cc/siege/<昵称>/<profile_id>
```

因为 R6 昵称不唯一（可改名、跨平台同名），站点用「昵称 + 育碧账号 uuid」定位。
uuid 取自 Arenyze 的 `platform_families_full_profiles[0].profile_id`，
在**绑定那次查询**时顺手存进库，所以 `/r6网站` 是秒回、不用再查接口。

### 3. 「查无此人」和「接口抽风」以前是同一句话（误报）

Arenyze 会偶发 503。真实案例：用户 `/r6 R6--Bronya`，接口 503，
bot 却回「没查到…确认昵称拼写」——但该账号明明存在（PC / 黄金 3）。
用户很自然会去怀疑一个本来正确的昵称。

现在分开：

- `R6NotFound`（HTTP 404）→「这个昵称在 PC 上没有。先确认拼写；主机号请写平台」
- `R6Unavailable`（超时 / 5xx / 网络）→「接口抽风了，重试也没成功，过十几秒再发；**不是昵称写错**」

判定顺序有讲究：实测**不存在的昵称是一个接口 404、另一个 503**，
所以规则是「先看有没有 404，没有才当接口故障」，并对非 404 失败**重试一次**（1.5s 后）。

### 4. `/r6 pc`（只写平台、没写昵称）

日志里抓到 `name=pc`——旧代码把字符串 `"pc"` 当昵称查了，然后回「没查到 pc」。
现在 `_r6_split()` 识别「单独一个平台词」→ 返回空昵称 → 提示用法。

### 5. 绑定是按场景的（平台限制，绕不过）

QQ 官方机器人拿不到 QQ 号，只有 openid，而**单聊是 `user_openid`、群聊是 `member_openid`**。
所以单聊绑的不会在群里生效，换个群要重绑。回复里已写明。

### 6. 「游戏时长」被删掉了（上游数据不可靠）

用户拿 R6Tracker 对照，发现对不上，且**错得没有规律**：

| 账号 | R6Tracker | Arenyze `totalsHoursPlayed` |
|---|---|---|
| MacieJay | 9,305h | 5,887h（偏低 37%） |
| Beaulo | 10,557h | 4,794h（偏低 55%） |
| mojinqiu | 180h | 388h（偏高 115%） |

查清了它的来路：Arenyze 那个字段等于**所有 PvP 模式逐赛季相加的「会话时长」**
（排位 + 标准 + 休闲 + 活动 + 生活模式…，含排队/加载/菜单），既不是排位口径，
也和官方比赛内时长对不上。同一份数据里 `matchesPlayed` 也失真
（MacieJay 40135 场 / 4339h → 6.5 分/场，不可能）。

**当时先删掉了**（`_build()` 去掉 `total_hours`）。后来用户提示「**网页是准的、API 不准**」，
于是去扒 `r6.arenyze.com` 的前端（`dist/*.js`）——发现网站用的是**同一个 API 的另一个字段**：

```
/v2/profile → seasons.data.segments[type == "overview"].stats.timePlayed.value   // 单位：秒
```

实测与 R6Tracker **逐一对得上**：mojinqiu 180h、MacieJay 9,305h、XII-FuHua 1,266h
（同一份响应里 `totalsHoursPlayed.hours` 分别给 388 / 5,887 / 1,360 —— 全错）。

**结论：时长改回展示，取 overview 段。** 不需要爬虫、不需要代理、不需要育碧账号。

另外修了「近几季」段位显示「—」的问题：`/v2/profile` 里有些赛季只给 `maxRankPoints`
没给 `rankPoints`（实测 Y11S2），现在没有当前 RP 时用**赛季最高 RP** 当段位
（RP=1000 是定位赛基准，不算段位）。

### 6b. 官方 API 那条路：备而不用

一度以为时长只能从育碧官方 API 拿，为此注册了测试小号、接 `siegeapi`、写了
`r6official.py`（curl_cffi 过 DataDome + 代理出口）。结果：本机（腾讯云大陆机房 IP）
被育碧**按 IP 限流**（`errorCode 1101 · Too many calls per IP address`，连登录都 429，
每分钟一次连试 8 分钟全挂）；Clash 的新加坡机房节点同样 429。
找到 `overview` 字段后这条路**不再需要**——`r6official.py` 留着当 Plan B
（`enabled()` 要求邮箱+密码+代理三项齐全，默认关闭，失败一律返回 None 让 Arenyze 兜底）。

能对上 TN 的都保留：等级（mojinqiu 88 ✓）、段位/RP、本赛季场次/胜负/KD
（37 场 / 47% / 0.94 ✓ 与 TN 一致）。

## 测试

- `_test_r6bind.py`（新增）：8 项。
- `_test_r6stats.py`：新增 profile_id / 规范昵称 / 平台标签 / 外链格式 / `R6NotFound`·`R6Unavailable` / `_get` 状态码分类。
- `_test_commands.py`：新增主帮助指向、主题详情、别名、`/彩六` 命中、裸「彩六」不误触、`_r6_split`、两类失败话术。
- 服务器 22 套全绿；线上实测：`R6--Bronya → 黄金 3`、查无此人 → `R6NotFound`、
  小写自动纠正为大写、生成的外链与实际站点一致。
