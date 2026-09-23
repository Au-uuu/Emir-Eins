# 2026-09-22 入库提示精简、同消息附图添加、批量添加

## 需求

1. 入库提示把「格式」和「大小」并到同一行，省一行，形如：`GIF  大小：3393.3 KB`。
2. `/添加` 除了「引用一张图片」，还支持**在同一条消息里附带图片**添加；
   这种添加**必须带 `/`**（同消息附图不能省略斜杠）。
3. 新增 `/批量添加`：一次把消息里的多张图片入库。
4. 斜杠可省的范围：只有「引用回复消息的」`添加`、`删除`、`来只` 可以省略。

## 设计

### 入库提示

成功时由原来的
```
入库成功 #id
关键词：xxx
大小：12.3 KB
格式：GIF 动图
指纹：abc…
```
改为（格式与大小合并到一行）：
```
入库成功 #id
关键词：xxx
GIF 动图  大小：12.3 KB
指纹：abc…
```
格式标签优先取转换说明（如 `已由 WEBP 转为 PNG`），否则取 `mime` 大写（GIF 追加「动图」）。

### 同消息附图添加需带斜杠

`resolve_image_attachment` 返回来源 `"本条消息"`（同消息附图）或 `"引用的图片消息"`。
当来源是「本条消息」且正文不以 `/` 开头时，`do_add_image` 直接**静默忽略**。
引用图片时仍可省略斜杠（`添加 猫` 有效）。

### `/批量添加`

- 正则 `^/批量添加…`（**必须带斜杠**）。
- 图片收集 `collect_image_attachments(message)`：先取本条消息的全部图片附件；
  没有则递归收集引用消息 `msg_elements` 里的全部图片。
- 关键词与单张 `/添加` 一致，套用到每一张；逐张入库，汇总输出：
  `批量添加完成：共 N 张，新增 X 张，重复 Y 张[，失败 Z 张]`。
- 没有图片时给用法提示。
- 群全量模式下与 `/添加`、`/来只` 一样受群级限流。

## 改动文件

- `bot.py`
  - `BATCH_ADD_RE`；`collect_image_attachments` / `_collect_images_from_elements`。
  - `do_add_image`：同消息附图强制斜杠；入库提示合并格式/大小。
  - 新增 `do_batch_add`；`handle_command` 分派；群限流条件加 `BATCH_ADD_RE`。
  - `HELP_TEXT` 增补。
- `_test_add.py`：新增，覆盖斜杠规则、提示行数、批量新增/重复、分发。
- `README.md`、`docs/CHANGELOG.md`、`deploy/deploy.sh`、`.gitignore` 同步。

## 测试

```
.\.venv\Scripts\python.exe _test_add.py           # exit 0
.\.venv\Scripts\python.exe _test_privacy.py       # exit 0
.\.venv\Scripts\python.exe _test_reply_policy.py  # exit 0
.\.venv\Scripts\python.exe _test_admin.py         # exit 0
.\.venv\Scripts\python.exe _test_gallery.py       # exit 0
.\.venv\Scripts\python.exe _test_delete.py        # exit 0
.\.venv\Scripts\python.exe _test_links.py         # exit 0
.\.venv\Scripts\python.exe _test_store.py         # exit 0
.\.venv\Scripts\python.exe _test_commands.py      # exit 0
```

## 备注

- 批量添加是逐张串行下载入库，图多时耗时较长；如需并发可后续加信号量。
- 同消息附图的判定只看「本条消息自己的 attachments」；若同时引用了别的图，
  以本条消息自带的图片优先。
