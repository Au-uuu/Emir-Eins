# 2026-09-21 按图库删除图片 + 空关键词清理

## 需求

- 新增删除图片功能：**引用一张图片**，回复 `/删除 <图库名称>`（斜杠可省）。
- 先检查图库是否存在，再检查该图库里有没有这张图的**指纹（SHA-256）**：
  - 有 → 删除；没有 → 提示用户。
- 图片按指纹只存一份；图库只存「指纹索引」，同一张图可以被多个图库存放。
  删除一张图 = **从指定图库移除**，其他图库不受影响。
- 「顺便取消把关键词删了」→ 取消关联 / 图片池迁走 / 删除图片后，把**没有任何图片的空关键词/空集合**清理掉。

## 设计

### 图库模型

- `images` 表：一张图一条记录，`sha256` 唯一（指纹，去重依据）。
- `keywords` 表：`(image_id, keyword)` —— 也就是「图库 → 指纹」的索引。
  一张图可以同时属于多个图库；一个图库可以有多张图。
- 删除只动 `keywords` 里的某一条 `(图库, 图片)`，不碰图片本身。
- 当某张图不再被**任何**图库引用时（`keywords` 里没有它的行了），才删除 `images`
  记录与磁盘文件，避免留下孤儿文件。

### 删除流程 `/删除 <图库名称>`

1. 取引用的图片（复用 `resolve_image_attachment`，支持引用消息里的 `msg_elements`）。
2. 下载 + 走与入库**完全相同**的归一化（`_normalize`），再算 SHA-256。
   > 必须共用归一化：WebP/BMP 入库时会转 PNG，若删除时按原始字节算指纹会与库里对不上。
3. 图库名解析别名到主关键词后依次判断：
   - 图库不存在（名下没有任何图片）→ `gallery_missing`
   - 图片库里根本没有这个指纹 → `image_unknown`
   - 图库存在但这张图不在其中 → `not_in_gallery`
   - 命中 → 删除该条索引；若成为孤儿则删文件；若有空集合则清理。

### 空关键词清理 `_purge_empty_collections_with`

图片都沉淀在主关键词名下，所以「主关键词名下没有图片」等价于「整个集合没有图片」。
清理时机：`取消关联`、`从图库删除` 之后。注意副作用：**没有任何图片的关联集合会被自动移除**，
因此关联最好在有图之后再建立。

## 改动文件

- `image_store.py`
  - `add_from_attachment` 抽出 `_normalize`，入库与删除共用。
  - 新增 `DeleteResult`；`UnlinkResult` 增加 `purged` 字段。
  - 新增 `fingerprint_of_attachment` / `delete_from_gallery` / `_delete_from_gallery_sync`。
  - 新增 `_purge_empty_collections_with`，并在 `_unlink_sync` 两个分支调用。
- `bot.py`
  - 新增 `DELETE_RE`、`do_delete_image`；`handle_command` 分派；`HELP_TEXT` 增补。
- `_test_delete.py`：新增，覆盖多图库共享、只解除关联、孤儿清理、三态失败、别名删除、
  空集合清理、指令层（含无斜杠）。
- `_test_links.py`：`[8]` 补一张图，避免空集合被自动清理影响用例。
- `README.md`：指令表、图库模型与删除说明。
- `deploy/deploy.sh`、`.gitignore`：带上新测试脚本与临时目录。

## 测试

```
.\.venv\Scripts\python.exe _test_delete.py    # exit 0
.\.venv\Scripts\python.exe _test_links.py     # exit 0
.\.venv\Scripts\python.exe _test_store.py     # exit 0
.\.venv\Scripts\python.exe _test_commands.py  # exit 0
```

## 备注

- 目前**任何群成员都能删除**（按需求设定）。如需限制为图片上传者，可在
  `_delete_from_gallery_sync` 里比对 `images.uploader` 与消息的 member_openid。
- 删除是「从图库移除」而非「删除整张图」；要彻底删除需要从所有引用它的图库逐一删除，
  删到最后一个时会自动清理文件。
