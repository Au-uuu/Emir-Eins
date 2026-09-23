# 2026-09-21 群私有图库 + 图库列表改用 ` / ` 分隔

## 需求

- 给图库加「群私有」：`/私有 <图库名>` 把图库标记为**当前群私有**（**不需要引用图片**，任何人都能用）。
- 标记为群私有后：只能在**指定群**被 `/来只` 抽到，也只在该群 `/图库` 中展示。
- `/图库` 展示的图库之间**不要换行**，用 ` / ` 隔开。

## 设计

### 数据模型

新增表 `gallery_privacy(keyword PRIMARY KEY, group_openid, added_at)`：

- 一条记录 = 该图库（主关键词）被绑定到某个群。
- 没有记录 = 公开（任何群、单聊都可见）。
- 一个图库最多绑定一个群；再次 `/私有` 会改绑到当前群。

### 可见性规则

- 关键词对群 G 可见 ⟺ 没有隐私记录，或隐私记录的群就是 G。
- 图片对群 G 可见 ⟺ 它没有任何「对 G 不可见」的关键词（无关键词语的图视为公开）。
- 单聊没有群标识，用空串处理，因此**看不到任何群私有内容**。

所有读路径都按群过滤：

| 方法 | 过滤 |
|---|---|
| `random_image(kw, group)` | 指定词时校验可见性；随机时只从可见图里挑 |
| `search_images(kw, limit, group)` | 同上 |
| `list_galleries(..., group)` / `gallery_count(group)` | 只列可见图库 |
| `list_keywords(limit, group)` | 只列可见关键词 |
| `stats(group)` | 只统计可见图片/关键词 |

写路径拦截：

- `add_from_attachment(..., group_openid)`：不能往「别群的私有图库」加图，抛 `ValueError`。
- `delete_from_gallery(..., group_openid)`：别群私有图库返回新状态 `private_other`。

### 指令

- `/私有 <图库名>`：设为当前群私有。仅群聊可用；图库不存在会提示。
- `/公开 <图库名>`：取消群私有，恢复公开。
- 图库名会先解析到主关键词（别名也能用）。
- `/图库` 列表里，本群的私有图库带 `[私有]` 标记。

### 展示格式

`/图库` 列表从「每行一个」改为**一行、用 ` / ` 分隔**：

```
图库列表（按图片数降序）：
猫(12)-猫咪-喵喵… / 狗(9)-狗狗 / 鸟(4)
```

分页页眉/页脚不变（页眉在消息开头，页脚另起一行）。

## 改动文件

- `image_store.py`
  - `_SCHEMA` 加 `gallery_privacy` 表。
  - 可见性 SQL 辅助 `_kw_visible_sql` / `_image_visible_sql` / `_keyword_visible` / `_group_of`。
  - 读路径全部加 `group_openid` 参数并过滤；写路径加拦截。
  - 新增 `set_gallery_private` / `set_gallery_public` / `gallery_private_group` / `gallery_privacy_map`。
  - `DeleteResult` 增加 `private_other` 状态。
- `bot.py`
  - `PRIVATE_RE` / `PUBLIC_RE`、`do_set_private` / `do_set_public` 及分派。
  - `do_gallery` / `do_random_image` / `do_delete_image` / `do_add_image` 透传群标识。
  - `build_gallery_list_text(page, group)`：` / ` 分隔 + `[私有]` 标记。
  - 帮助按钮 `/图库`、`/来只` 透传群；`HELP_TEXT` 增加私有用法。
- `_test_privacy.py`：新增，覆盖可见性、跨群增删拦截、别名、指令层、列表标记。
- `_test_gallery.py`：适配 ` / ` 分隔的新格式。
- `README.md`、`docs/CHANGELOG.md`、`deploy/deploy.sh`、`.gitignore` 同步。

## 测试

```
.\.venv\Scripts\python.exe _test_privacy.py      # exit 0
.\.venv\Scripts\python.exe _test_reply_policy.py # exit 0
.\.venv\Scripts\python.exe _test_admin.py        # exit 0
.\.venv\Scripts\python.exe _test_gallery.py      # exit 0
.\.venv\Scripts\python.exe _test_delete.py       # exit 0
.\.venv\Scripts\python.exe _test_links.py        # exit 0
.\.venv\Scripts\python.exe _test_store.py        # exit 0
.\.venv\Scripts\python.exe _test_commands.py     # exit 0
```

## 备注

- 一个图库只能绑定一个群；若两个群都想要同名的私有图库，需要各自用不同图库名。
- 群标识用 `group_openid`（机器人视角下每个群唯一且稳定），与 QQ 群号无关。
- 「未关联关键词」的图片没有图库、也就没有私有标记，视为公开。
