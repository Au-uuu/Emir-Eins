# 2026-09-23 名片：图片识别修复、禁纯数字备注、拼图提到 1920

## 问题

用户引用图片发 `/添加名片` 失败，回“先引用一张图片”。日志：

```
[单聊] /添加名片 明日方舟B服
[名片] 未找到图片 attachments=[] msg_elements=True raw_keys=[... 'msg_elements' ...]
```

说明图片其实在 `msg_elements` 里，但没被识别——因为**真实 QQ 附件的 `content_type`
可能为空/缺失**，而当时的判断只认 `content_type` 以 `image/` 开头。

## 修复

1. **放宽图片识别**：新增 `_looks_like_image(content_type, filename, url)`，
   依次看 `content_type` 前缀、文件名扩展名、URL 扩展名（`.png/.jpg/.jpeg/.gif/.webp/.bmp`）。
   `pick_image_attachment` / `_find_image_in_elements` / `_collect_images_from_elements`
   全部改用它。
2. **禁止纯数字备注**：`_persist_card` 对 `remark.isdigit()` 抛
   `备注不能用纯数字（会和编号冲突）`，机器人回“添加失败：…”。
3. **拼图画质/尺寸**：`make_card_sheet` 宽 960 → **1920**，头部/字号/间距等比放大
   （header_h 70→120、font_size 36→62、gap 14→20），JPEG 质量 82 → **90**，
   总高上限 12000 → 16000。

## 验证

部署后日志确认单聊、群聊 `/添加名片` 均成功（来源=引用的图片消息），
`/游戏名片` 拼图发送成功。

## 改动文件

- `image_store.py`：`_persist_card` 纯数字校验；`make_card_sheet` 默认值与质量。
- `bot.py`：`_looks_like_image` 及三处调用；`do_add_card` 无图时打印诊断日志。
- `_test_cards.py`：纯数字备注、缺 content_type 兜底、宽度 1920。
- `README.md`、`CHANGELOG.md` 同步。

## 测试

12 项测试全绿。
