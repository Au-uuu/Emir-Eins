# 「添加」支持使用最近发送的图片

- 日期：2026-11-12
- 模块：`bot.py`
- 测试：`_test_add_recent_image.py`

## 现象

单聊里先发一张图，再发「添加疯狂星期四」，机器人不回「入库成功」，反而回了一段看图人格回复。  
日志：

```text
12:01:13 [单聊] [+图片]
12:01:30 [人格] 已回复 ...（这是对图片的回复，延迟了 17 秒）
12:01:32 [单聊] 添加疯狂星期四
12:01:32 [添加] 本条消息没有图片，忽略
```

看起来像「添加」没被识别，实际上是：

1. 「添加」命令**被识别了**；
2. 但 `do_add_image` 只认「本条消息附图」或「引用图片」，单聊里用户没有引用，所以找不到图；
3. 同时上一条图片的人格回复延迟到达，看起来像在回复「添加」。

## 修复

- 新增最近图片缓存：按 `(scope, scene_id, 用户 openid)` 记住该用户最后一张图，默认 **10 分钟**有效，最多 500 条。
- 用户在单聊/群里发图时，三个消息入口都会调用 `remember_recent_image()`。
- `do_add_image` 找不到当前图/引用图时，自动取同一用户最近一张图，来源标记为「最近发送的图片」。
- 修复单聊 `uploader_id`：原来只读 `member_openid`，单聊是 `None`；改为 `author_openid()`，群聊/单聊都能拿到。
- 成功文案会显示「（来自最近发送的图片）」。
- 帮助、`/help 图库`、README 同步更新：支持「先发图，再发 添加 关键词」。

## 引用消息的格式与重试

QQ 引用消息的事件结构（官方文档）：

```json
{
  "id": "ROBOT1.0_xxx",
  "content": "添加疯狂星期四",
  "message_type": 103,
  "msg_elements": [
    {
      "msg_idx": "REFIDX_xxx==",
      "author": {"member_openid": "..."},
      "message_type": 0,
      "content": "...",
      "attachments": [
        {"url": "...", "width": 720, "height": 1280, "content_type": "image/png"}
      ]
    }
  ],
  "message_scene": {
    "ext": ["ref_msg_idx=REFIDX_xxx==", "msg_idx=REFIDX_yyy=="]
  }
}
```

- 外层 `id` 是 msg_id（被动回复用）；
- `msg_idx` / `ref_msg_idx` 是引用索引（`REFIDX_...`），可以作为引用消息的唯一标识；
- 被引用图片在 `msg_elements[].attachments` 里，图片附件通常带 `width`/`height`。

之前的识别只看 `content_type` / 文件名 / URL 扩展名，引用消息里的图片 URL 可能没有扩展名，导致明明有 `attachments` 也被判成“不是图片”。现在：

- `_looks_like_image()` 增加 `width`/`height` 判断，并认 `qpic.cn` / `qlogo.cn` / `gtimg.cn` 图片 CDN；
- 首包没有图片时，如果消息是引用消息（`message_type=103` / 有 `msg_elements`），自动重试 4 次、约 8 秒；
- `raw_events` 按 msg_id 覆盖 payload，平台重复推送补上图片后，重试能读到新 payload；
- 重试仍拿不到图，回一句“请确认引用的是图片，或重新引用后再试”，不再完全静默。

> 如果 QQ 始终不在 `msg_elements` 里给附件，也没有“按 msg_idx 拉取历史消息”的开放接口，重试也无法凭空拿到图片；这种情况只能重新引用。

## 边界

- 缓存是**内存**的，重启进程会清空；
- 只对同一会话、同一用户生效，不会拿别人的图；
- 超过 10 分钟或 QQ 图片 URL 过期后不再使用；
- 引用图片/同消息附图仍然优先，行为不变。

## 测试

`_test_add_recent_image.py`：

- 能取回同一用户的最近图片；
- 别的用户、别的场景取不到；
- 「添加」命令会使用最近图片入库；
- 别的用户不会误用；
- 无图时保持安静；
- 过期图片不再使用。
