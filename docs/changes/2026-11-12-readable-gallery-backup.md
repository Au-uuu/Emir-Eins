# 网盘图库备份增加人类可读目录

- 日期：2026-11-12
- 脚本：`deploy/export_gallery_readable.py`、`deploy/backup-nutstore.sh`
- 测试：`_test_gallery_export.py`

## 需求

坚果云里原来只有：

```text
images/<sha前2>/<sha256>.jpg
thumbs/...
images.db
```

是按 SHA-256 存的，人能看懂才怪。希望有一个按图库/关键词分文件夹、文件名可读的视图。

## 结构

`backup-nutstore.sh` 每天 03:30 备份时，会先生成：

```text
gallery/
  公开/
    蓝色大肥鱼/
      001_蓝色大肥鱼_96b6e917.jpg
      002_蓝色大肥鱼_fa569b29.gif
    疯狂星期四/
      ...
  群_r6养老选手休息室/
    粉嫩/
      001_粉嫩_71421a54.jpg
  _未分类/
    001__未分类_e307698b.png
  _索引.csv
  _说明.md
```

- `公开/`：公开图库，按关键词分文件夹；
- `群_<群名>/`：群私有图库；群名优先用 `data/group_names.json`，
  没有则调 QQ 群信息接口补，仍失败就用 openid 前 8 位；
- `_未分类/`：`images` 表里存在、但没关联任何关键词的图片；
- `_索引.csv`：UTF-8 BOM，Excel 可直接打开；列包括图库、文件名、原文件名、
  大小、尺寸、SHA256、图片 ID、添加时间；
- `_说明.md`：目录结构说明。

文件名格式：`序号_关键词_SHA前8.扩展名`。  
同一张图属于多个图库时会在多个文件夹各出现一次（当前 258 张里只有 3 张）。

## 设计

- 原图 `images/` 仍按 SHA-256 备份，`gallery/` 只是 `images.db` 生成的派生视图；
- 导出时优先用硬链接，失败再复制，避免本地临时占双份空间；
- `gallery/` 用 `rclone sync` 保持云端目录整洁；原图 `images/` 仍然只增不减；
- 坚果云免费版每 30 分钟 600 次请求，`gallery` 同步加 `--tpslimit 0.3`，
  首次约 260 个文件会比较慢，之后只传新增/改名；
- `QQBOT_READABLE_GALLERY=0` 可关闭可读视图；
- 群名缓存 `data/group_names.json`，和用量日报共用。

## 测试

`_test_gallery_export.py`：

- 公开图库、群私有图库、未分类都能生成；
- 文件名带序号/关键词/SHA 前 8 位；
- 索引包含公开、群名、未分类；
- 非法字符（`/ : *`）被替换，空名有兜底；
- 不联网、不碰线上数据。
