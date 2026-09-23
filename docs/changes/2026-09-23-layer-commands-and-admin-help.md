# 2026-09-23 层选择指令、删图库层、管理员帮助、命名与限额

## 需求

- `/id` 换成更长的指令名（避免被普通用户随手用）。
- 管理员 `/help` 返回**全部**可用指令；群里只返回用户指令。
- 新增管理员删图库层：`/删除图库`（无参列出 图库→群openid/公开），
  `/删除图库 <图库名> <群openid|公开>` 删除该层。
- 图库名不允许纯数字、不允许空格。
- `/来只 [关键词] [私有/公开/全部]`，默认全部。
- `/删除 <图库名> [私有/公开/全部]`，默认全部（私有优先、没有再公开）。
- 私有层在别的群完全不可见（已成立）。
- `/公开` 也可由管理员私聊设置（`/公开 图库名 群openid`）。
- 群聊 `/私有` 不允许第二个参数。
- `/查询标识` 不进普通帮助，仅在管理员私聊帮助显示；所有指令都透露给管理员。
- `/图库` 每页上限 50 → **25**；`/批量添加` 一次最多 **5** 张。

## 实现

### 指令改名与帮助

- `WHOAMI_RE` 从 `^/(id|whoami|我的id)` 改为 `^/查询标识`；旧 `/id` 不再识别。
- `send_help(message, full=False)`：`full` 用 `ADMIN_HELP_TEXT`。
  - `/help`：`scope=="c2c" and is_admin` 时给完整表，否则用户表。
  - 单聊空内容同理；群聊空内容只给用户表。

### 层选择（取图/删除）

- `LAYER_WORDS = {私有:private, 公开:public, 全部:all}`。
- `ImageStore.random_image(keyword, group, layer)`：
  - `all`：`owner_group IN ('', g)`；`private`：`owner_group = g`；`public`：`owner_group = ''`。
  - 无关键词时：`private`=有本群私有层图的图；`public`=没有非公开层关键词的图（含无关键词）。
- `ImageStore.delete_from_gallery(..., layer)`：
  - `all`=私有优先、没有再公开；`private`=仅本群私有；`public`=仅公开。

### 删除图库层（管理员）

- `ImageStore.delete_gallery_layer(keyword, owner_group)`：删除该层所有图片关联，
  清掉对应的私有标记，孤儿图片与空关联一并清理。
- `ImageStore.all_gallery_layers()`：`[(图库名, 张数, owner_group)]`。
- `bot.do_delete_gallery`：非管理员拒绝；无参列出；两参删除。
- 分派顺序：`DELETE_GALLERY_RE` 必须在 `DELETE_RE` **之前**，否则 `/删除图库` 会被
  `/删除` 抢先匹配。

### 命名与限额

- `image_store.is_valid_gallery_name`：非空且非纯数字；`add_from_attachment`、
  `link_keywords` 校验，违反抛 `ValueError`（机器人回显原因）。
- `GALLERY_PAGE_SIZE = 25`；`BATCH_ADD_MAX = 5`（超出直接取消并提示）。

### 群聊 /私有 第二参数

- 群聊分支 `len(words) >= 2` 直接拒绝。

## 改动文件

- `image_store.py`：`is_valid_gallery_name`、`random_image(layer)`、
  `delete_from_gallery(layer)`、`delete_gallery_layer`、`all_gallery_layers`、
  `DeleteResult.count`、add/link 命名校验。
- `bot.py`：常量、正则、`ADMIN_HELP_TEXT`、`send_help(full)`、`do_delete_gallery`、
  `do_random_image`/`do_delete_image` 层解析、`do_set_private`/`do_set_public`、
  `do_whoami` 改名、分派与帮助调用、批量上限。
- `_test_layers.py`：新增（层选择、删图库层、数字名、批量上限、查询标识、私有第二参数）。
- `_test_privacy.py`：更新 `/公开` 私聊断言。
- `README.md`、`CHANGELOG.md`、`deploy.sh`、`.gitignore` 同步。

## 测试

10 项测试全绿。
