# 2026-09-23 游戏名片

## 需求

- `/添加名片 <备注>`：引用一张图片，登记为**该用户**的游戏名片。
- `/游戏名片`：把该用户名片**拼成一张图**发送：保持长宽比、宽度顶满（960），
  竖向排列，每张图片**上方外面**一行 `【编号】.备注`。
- `/删除名片 <备注>` / `/删除名片 <编号>`：删自己的名片；
  引用图片后 `/删除名片`：按图片指纹删。
- 原图存数据库/磁盘，便于以后修改。
- 添加同一备注时**提示添加失败**。

## 数据模型

新增 `name_cards` 表：

| 列 | 说明 |
|---|---|
| `id` | 自增主键 |
| `owner` | 用户 openid |
| `remark` | 备注（同一 owner 唯一，≤40 字） |
| `image_id` | 指向 `images` 的原图（`ON DELETE CASCADE`） |
| `added_at` | 添加时间 |

- 图片本体仍存 `images`（原图，按 SHA-256 去重）+ `data/thumbs` 缩略图。
- 编号是**动态**的：按 `added_at` 顺序的序号（1..N），删除后会顺延。

## 拼图

`image_store.make_card_sheet(cards, width=1920, header_h=120, gap=20, font_size=62, max_height=16000)`：

- 每张图等比缩放到宽 `width`（`LANCZOS`），头部预留 `header_h` 画 `【编号】.备注`；
- 竖向堆叠；总高超过 `max_height` 时截断并返回实际张数；
- 中文字体自动探测（`QQ_BOT_FONT` 或常见 CJK 路径，服务器用 `wqy-zenhei`）。

## 指令

| 指令 | 说明 |
|---|---|
| `/添加名片 <备注>` | 必须带斜杠；引用图片后使用；备注重复 → 添加失败 |
| `/游戏名片` | 竖向拼图，一条消息（被动回复） |
| `/删除名片 <备注\|编号>` | 按备注或编号删 |
| `/删除名片` + 引用图片 | 按指纹删 |

分派顺序：`/添加名片` 必须在 `/添加` 之前；`/删除名片` 在 `/删除`、`/删除图库` 之前。

## 完整性 / 清理

- 抽出 `_ensure_image_row`，`_persist` 与名片共用图片落盘。
- 新增 `_image_referenced`（图库关键词 + 名片），所有删除路径改用
  `_gc_image_if_orphan` + `_remove_image_files`，孤儿图片与缩略图一起清理。

## 改动文件

- `image_store.py`：`name_cards` 表、`CardRecord`、`add_card`/`list_cards`/
  `delete_card_by_*`、`_ensure_image_row`、`_image_referenced`、`_gc_image_if_orphan`、
  `_remove_image_files`、`make_card_sheet`、`_load_cjk_font`。
- `bot.py`：`CARD_ADD_RE`/`CARD_RE`/`CARD_DEL_RE`、`do_add_card`/`do_game_card`/
  `do_delete_card`、`_reply_image_bytes`、分派、群限流、帮助文案。
- `_test_cards.py`：新增。README/CHANGELOG/deploy.sh/.gitignore 同步。

## 测试

12 项测试全绿；服务器自检：2 张名片、重复备注拒绝、拼图 960×1288。
