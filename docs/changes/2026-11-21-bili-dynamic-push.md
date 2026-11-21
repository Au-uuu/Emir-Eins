# 明日方舟公告推送切到 B 站官方动态

- 日期：2026-11-21
- 模块：`ark_news.py`、`_test_ark_news.py`
- 影响：`/公告` 主动推送、B 站动态解析

## 原因

官网推送只按标题关键词筛「更新/维护/停机/版本」，像：

> SideStory「雪山降临1101」复刻即将于11月22日04:00开启！

这种活动预告不会推；而 B 站官方动态里除了活动公告，还有「123罗德岛」四格，
用户希望这些一起推。

## 数据源

明日方舟默认改为 B 站官方账号图文动态：

```text
GET https://api.bilibili.com/x/polymer/web-dynamic/v1/opus/feed/space
    ?host_mid=161775300&type=all
```

- 用 `opus/feed/space`，不是 `feed/space`：后者未登录经常 412，
  需要 WBI/dm_img 风控参数；前者带 `buvid3` + 浏览器头即可。
- 复用 `bili._get_json()`：已有 `buvid3` 获取、412 刷新重试逻辑。
- 只取图文，正好覆盖活动公告和 123罗德岛四格；视频/转发动态不在这里处理。
- 终末地仍走官网公告，不变。

## 推送格式

```text
【明日方舟官方动态】
{dynamic 正文}

图文地址：
https://www.bilibili.com/opus/{opus_id}

查看完整动态：
https://t.bilibili.com/{opus_id}
```

同一轮多条新动态用 `——` 分隔合并，正文超过 800 字截断加省略号。

## 去重与首轮

- B 站动态使用独立 seen 命名空间 `ak_bili`，避免与旧官网 cid 混在一起；
- 首次轮询只把现有 20 条动态标记为已见，不推送；
- 群开关仍然用 `ak`：`/公告 开 明日方舟` 不变；
- 每日推送上限、主动消息 22009 容错逻辑不变。

## 配置

```ini
QQ_BOT_NEWS_BILI=1                 # 0 = 回退官网公告
QQ_BOT_NEWS_BILI_MID=161775300     # 明日方舟官方 B 站 UID
QQ_BOT_NEWS_BILI_KEYWORDS=         # 留空 = 全部动态；可填 SideStory|复刻|123罗德岛
QQ_BOT_NEWS_KEYWORDS=更新|维护|停机|版本   # 终末地/官网仍用
```

## 测试

`_test_ark_news.py` 新增：

- `_bili_items()` 解析 opus 响应、脏数据丢弃、URL 拼接；
- `format_push_text()` 输出「明日方舟官方动态 / 图文地址 / 查看完整动态」；
- B 站关键词过滤；
- B 站轮询：首轮不推、独立 `ak_bili` seen、新动态推送、已见不重推；
- 原有官网/终末地轮询测试通过 `QQ_BOT_NEWS_BILI=0` 保留。

## 备注

- `/公告 查` 明日方舟也走 B 站动态（`QQ_BOT_NEWS_BILI=0` 回退官网）。
- B 站接口是公开但非文档保证的接口，可能改版；失败只影响明日方舟，
  终末地不受影响，日志会记录错误。
- 动态正文/图片版权归 B 站及原作者，推送只带链接，不搬运图片。
