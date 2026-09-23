# 2026-09-23 入库缩略图 + `/图库 <关键词>` 预览图 + 列表每页 30

## 需求

- 入库时生成一张「超级压缩」的预览缩略图，体积 ≤ 100KB。
- `/图库 <关键词>` 回复库中图片的**缩略图**，一页 8 张。
- `/图库` 列表每页 25 → **30**。

## 缩略图

- `image_store.make_thumbnail(data) -> bytes | None`：
  - Pillow 打开（动图取首帧），透明底合成白底，`thumbnail` 到长边 ≤ 512；
  - 逐级降尺寸 (512→384→256→192→128)、降质量 (80→68→56→45→35)，
    直到 JPEG ≤ `MAX_THUMB_BYTES = 100KB`。
- 存储：`data/thumbs/<sha前2位>/<sha>.jpg`（`ImageStore.thumbs_dir`，默认 images 同级）。
- **入库时生成**（`_persist` 末尾 `_write_thumbnail`）。
- `get_thumbnail(record)`：文件在就直接返回；**旧图没有则现生成**（懒生成，兼容历史数据）。
- `thumb_path` 由 sha 推导，无需新表字段。

## `/图库 <关键词> [页码]`

- 解析：`/图库 猫`＝第 1 页；`/图库 猫 2`＝第 2 页；每页 `GALLERY_IMG_PAGE = 8`。
- 取该词在当前群可见的全部图片（公开层 + 本群私有层），分页取本页记录。
- 发送缩略图后，再被动回复一行汇总（张数/页码/关联词）。

### 放在同一条消息里（拼图）

官方 API 一条消息只能带一张图，所以本页 8 张缩略图**拼成一张网格图**
（`make_contact_sheet`，4 列、左上角标 1..8 序号），作为**一条被动回复**发出。

好处：只占 1 次被动回复，不受「每条消息最多 5 次被动回复」限制，
也不依赖主动消息权限。发送失败（或无 api）时降级为文字清单。

## 改动文件

- `image_store.py`：`make_thumbnail`、`THUMB_MAX_SIDE`/`MAX_THUMB_BYTES`、`thumbs_dir`、
  `_thumb_path`、`_write_thumbnail`、`get_thumbnail`、`_persist` 生成缩略图。
- `bot.py`：`GALLERY_IMG_PAGE`、`GALLERY_PAGE_SIZE=30`、`_send_preview_sheet`、
  `_text_image_list`、`do_gallery` 预览+分页、`handle_command` 透传 api/scope/scene_id、
  帮助文案。
- `image_store.make_contact_sheet`：一页缩略图 → 一张网格 JPEG。
- `_test_thumb.py`：新增（缩略图体积、两页发图、无权限降级、列表页大小）。
- `README.md`、`CHANGELOG.md`、`deploy.sh`、`.gitignore` 同步。

## 测试

11 项测试全绿。
