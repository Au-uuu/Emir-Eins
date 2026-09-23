# 2026-09-21 `/图库` 改为输出图库列表

## 需求

- `/图库` 输出**所有存在的图库**，按图片数量降序排列。
- 每个图库显示：`名称(图片数)-关联词-关联词`。
- 关联词过多时每个图库**只显示两个**（超出用 `…` 省略）。
- 完整关联词在 `/图库 <关键词>` 时展示。

示例：`图库A(12)-关联词1-关联词2 / 图库B(10)-关联词 / 图库C(9)`

## 设计

- 「图库」= `keywords` 表里出现过的关键词（即至少有 1 张图）。别名不单独成库，
  作为其主关键词的「关联词」由 `keyword_links` 反查得到。
- 新增 `ImageStore.list_galleries(limit)`：按 `COUNT(*) DESC, keyword` 排序，
  再逐个补上 `keyword_links` 里的别名。
- `/图库` 默认输出改为图库列表（每库最多 2 个关联词）。
- `/图库 <关键词>` 在原有图片列表之外，**完整展示该图库的关联词**
  （别名会显示主关键词与同组词）。
- 原「统计」输出挪到 `/图库 统计`（别名 `-s` / `stats`），信息不丢。
- `/图库 -k` 保留（仅关键词 + 数量）。
- 帮助面板按钮「图库统计」改名「图库列表」，点击后与 `/图库` 一致。

## 输出示例

```
图库列表（按图片数降序）：
猫(12)-猫咪-喵喵…
狗(9)-狗狗
鸟(4)
```

## 改动文件

- `image_store.py`：新增 `list_galleries` / `_list_galleries_sync`。
- `bot.py`
  - 新增 `build_gallery_list_text()`（供指令与按钮共用）、`_association_note()`。
  - `do_gallery` 重排分支：默认列表 / `<关键词>` 图片+完整关联 / `统计` / `-k`。
  - `_handle_help_button` 的 `/图库` 改为输出图库列表。
  - `HELP_TEXT`、按钮文案更新。
- `_test_gallery.py`：新增，覆盖排序、计数、关联词截断、完整展示、空图库。
- `_test_commands.py`：`/图库` 默认输出断言改为「图库列表」，统计改测 `/图库 统计`。
- `README.md`：指令表与说明同步。
- `deploy/deploy.sh`、`.gitignore`：纳入新测试。

## 测试

```
.\.venv\Scripts\python.exe _test_gallery.py   # exit 0
.\.venv\Scripts\python.exe _test_delete.py    # exit 0
.\.venv\Scripts\python.exe _test_links.py     # exit 0
.\.venv\Scripts\python.exe _test_store.py     # exit 0
.\.venv\Scripts\python.exe _test_commands.py  # exit 0
```

## 备注

- 图库列表最多展示 50 个（`list_galleries(limit=50)`）；超出部分暂不追加提示，
  如需可加「…共 N 个图库」。
- 统计信息仍在 `/图库 统计`，若希望统计回到 `/图库` 顶部（列表之前）可再调整。
