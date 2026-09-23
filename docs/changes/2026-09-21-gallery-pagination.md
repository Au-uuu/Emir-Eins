# 2026-09-21 图库列表分页 `/图库 <页码>`

## 需求

- 图库列表每页最多 **50** 个。
- `/图库 1` 可以按页码翻页。
- 只有「显示不下」（即存在多页）时，才在**最后换行**补一行小提示；能一页放下就不发。
- 从**第 2 页**起在消息开头显示页码；第 1 页不显示。

## 设计

- 指令 `GET /图库 [页码]`：`arg.isdigit()` 时按页码处理（数字图库名会被翻页优先）。
- 每页 `GALLERY_PAGE_SIZE = 50`；页数 `ceil(gallery_count / 50)`。
- 开头：
  - 第 1 页：`图库列表（按图片数降序）：`
  - 第 n≥2 页：`第n/共m页` 换行后再跟标题。
- 末尾提示（仅当 `total > 50`）：
  - 不是最后一页：`…还有更多，发送 /图库 {n+1} 看下一页`
  - 最后一页且非第一页：`…已是最后一页，发送 /图库 {n-1} 看上一页`
- 页码超范围：`没有第 N 页，图库共 M 页。`

## 输出示例

第 1 页（多页时）：
```
图库列表（按图片数降序）：
猫(12)-猫咪-喵喵…
狗(9)-狗狗
…
…还有更多，发送 /图库 2 看下一页
```

第 2 页：
```
第2/3页
图库列表（按图片数降序）：
鸟(4)
…
…已是最后一页，发送 /图库 1 看上一页
```

## 改动文件

- `image_store.py`：`list_galleries(limit, offset)`；新增 `gallery_count()`。
- `bot.py`
  - 常量 `GALLERY_PAGE_SIZE = 50`。
  - `build_gallery_list_text(page=1)` 支持分页、页码与提示。
  - `do_gallery` 增加纯数字参数 = 翻页分支。
- `_test_gallery.py`：新增分页用例（缩小页大小跑多页、页码显示位置、末页提示、越界、单页无提示）。
- `README.md`：指令表与图库列表说明同步。
- `docs/CHANGELOG.md`：登记本变更。

## 测试

```
.\.venv\Scripts\python.exe _test_gallery.py   # exit 0
.\.venv\Scripts\python.exe _test_admin.py     # exit 0
.\.venv\Scripts\python.exe _test_delete.py    # exit 0
.\.venv\Scripts\python.exe _test_links.py     # exit 0
.\.venv\Scripts\python.exe _test_store.py     # exit 0
.\.venv\Scripts\python.exe _test_commands.py  # exit 0
```

## 备注

- 若图库名本身是纯数字，`/图库 123` 会被当作翻页；如需查看数字名图库，可改用
  `/图库 -k` 或 `/查找关联`。
- 页大小 50 是常量，觉得一页太长/太短改 `GALLERY_PAGE_SIZE` 即可。
