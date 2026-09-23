# 2026-09-21 管理员机制 + 「集合最后一个词」删除保护

## 需求

- 关键词集合**只剩一个关键词**时，不允许删除（删掉这最后一个词 = 解散集合）。
- **只有 Bot 管理员**可以删掉这最后一个词。
- 提供管理员名单；管理员通过**私聊输入口令**自助登记。
- 管理员登记指令**只在私聊可用，且不对外展示**。

## 关键技术限制

QQ 官方机器人（qq-botpy）在群 / 单聊事件里**拿不到 QQ 号**，只有
`author.member_openid`（群）/ `author.user_openid`（单聊）——同一个人在不同机器人下
openid 不同，且与 QQ 号无关。因此无法用 QQ 号做管理员白名单，改用 openid，
并由用户私聊口令自助登记。

## 设计

### 管理员表

新增 `admins(openid PRIMARY KEY, added_at)`。openid 由私聊登记写入。

`ImageStore` 新增：`is_admin` / `add_admin` / `remove_admin` / `list_admins`。

### 私聊口令登记

- 配置项 `QQ_BOT_ADMIN_PASSWORD`（`.env`，敏感）。留空则关闭登记。
- 指令：`管理员 <口令>`（英文 `admin <口令>`）。
- **只在 `scope == "c2c"`（单聊）生效**；群里命中时**保持沉默**（不回复、不回显，
  避免口令外泄），且不出现在 `/帮助` 里。
- 口令正确 → 把发送者 openid 写入 `admins`。

### 「最后一个词」保护

`unlink_keyword(keyword, allow_last=False)`：

- 传别名 / 传有其余成员的主词：行为不变（移出该词，集合保留至少一个词）。
- 传一个**既不是别名也不是主**、但确有图片的独立图库 → 这就是「集合仅剩的最后一个词」：
  - 非管理员：返回 `kind="protected_last"`，拒绝执行。
  - 管理员（`allow_last=True`）：执行 `gallery_deleted`，把该关键词从所有图片上摘掉，
    不被任何图库引用的图片连同文件一并清理，并清理空关联。

> 说明：集合从 2 个词减到 1 个词是允许的（删掉的是“倒数第二个”）；只有再删这最后一个
> 才会命中保护。此时该词已不在 `keyword_links` 里，因此通过「是否为有图的独立图库」
> 来识别。

### 判定管理员

`bot.author_openid(message)` 取 `user_openid or member_openid`，再查 `admins` 表。
`do_unlink` 据此决定 `allow_last`。

## 改动文件

- `image_store.py`
  - `_SCHEMA` 加 `admins` 表。
  - `UnlinkResult` 新增 `protected_last` / `gallery_deleted` 两种 kind 与 `orphan_removed`。
  - `unlink_keyword` / `_unlink_sync` 增加 `allow_last` 与保护分支；新增 `_delete_gallery_with`。
  - 新增管理员 CRUD。
- `bot.py`
  - 配置 `QQ_BOT_ADMIN_PASSWORD`；`author_openid()`；`ADMIN_RE` / `ADMIN_EN_RE`。
  - 新增 `do_admin_setup`（仅私聊）；`handle_command` 分派并对群聊沉默。
  - `do_unlink` 接入管理员判定与拒绝/删除文案。
- `.env`：新增 `QQ_BOT_ADMIN_PASSWORD`（本地已写入，口令不入库、不提交）。
- `.env.example`：新增占位说明。
- `_test_admin.py`：新增，覆盖管理员表、口令登记、私聊/群聊分派、保护与管理员删除。
- `deploy/deploy.sh`、`.gitignore`：纳入新测试。
- `README.md`：管理员与保护规则说明（口令值不写入文档）。

## 测试

```
.\.venv\Scripts\python.exe _test_admin.py     # exit 0
.\.venv\Scripts\python.exe _test_gallery.py   # exit 0
.\.venv\Scripts\python.exe _test_delete.py    # exit 0
.\.venv\Scripts\python.exe _test_links.py     # exit 0
.\.venv\Scripts\python.exe _test_store.py     # exit 0
.\.venv\Scripts\python.exe _test_commands.py  # exit 0
```

## 备注 / 安全

- 口令 `QQ_BOT_ADMIN_PASSWORD` 存在 `.env`（已 gitignore），不要提交或外传。
- 口令是共享的「管理员注册开关」，任何知道口令的人私聊机器人即可成为管理员；
  如需撤销某人，可在代码/DB 里 `remove_admin(openid)`。
- 若要更严格的权限（如仅允许图片上传者删除图片），可再扩展。
