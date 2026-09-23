# 变更记录

每次改动单独一份记录，放在 `docs/changes/`，本文件只做索引。

| 日期 | 变更 | 记录 |
|---|---|---|
| 2026-09-21 | 关键词关联（别名）：`/关联`、`/取消关联`、`/查找关联` | [keyword-links](changes/2026-09-21-keyword-links.md) |
| 2026-09-21 | 取消关联改为只移出单个词，主词移出时自动改选新主、集合与图片池不变 | [unlink-reassign-main](changes/2026-09-21-unlink-reassign-main.md) |
| 2026-09-21 | 按图库删除图片 `/删除`（引用图片），空关键词自动清理 | [delete-from-gallery](changes/2026-09-21-delete-from-gallery.md) |
| 2026-09-21 | `/图库` 改为输出所有图库（按图片数降序、每库最多 2 个关联词） | [gallery-list](changes/2026-09-21-gallery-list.md) |
| 2026-09-21 | 管理员机制（私聊口令登记）+「集合最后一个词」删除保护 | [admin-and-last-keyword-guard](changes/2026-09-21-admin-and-last-keyword-guard.md) |
| 2026-09-21 | 图库列表分页：`/图库 <页码>`，每页 50，多页时末尾提示、第 2 页起显示页码 | [gallery-pagination](changes/2026-09-21-gallery-pagination.md) |
| 2026-09-21 | 回复策略：不再回「收到：」，错误指令静默无视，空 @ 视为 `/help` | [reply-policy](changes/2026-09-21-reply-policy.md) |
| 2026-09-21 | 群私有图库 `/私有`、`/公开`；`/图库` 列表改用 ` / ` 分隔 | [group-private-gallery](changes/2026-09-21-group-private-gallery.md) |
| 2026-09-22 | 入库提示合并格式/大小；同消息附图添加须带 `/`；新增 `/批量添加` | [add-format-batch](changes/2026-09-22-add-format-batch.md) |
| 2026-09-22 | 移除 `/图库 -k`（与 `/图库` 重复） | [remove-gallery-k](changes/2026-09-22-remove-gallery-k.md) |
| 2026-09-22 | 收紧斜杠：仅 添加/删除/来只 可省略；帮助补充 `/图库 统计` 与快捷规则 | [require-slash](changes/2026-09-22-require-slash.md) |
| 2026-09-22 | 新增 `/id` 查询 openid；管理员可私聊 `/私有 图库名 群openid` | [openid-and-remote-private](changes/2026-09-22-openid-and-remote-private.md) |
| 2026-09-22 | 入库提示：大小在前、格式放括号 `大小：x KB（JPEG）` | [add-line-order](changes/2026-09-22-add-line-order.md) |
| 2026-09-22 | 图库分层模型：公开层 + 每群私有层（`(关键词,群)`），支持多群各自私有 | [gallery-layer-model](changes/2026-09-22-gallery-layer-model.md) |
| 2026-09-22 | 私有标识改 `名称[数量]`；修复分层重构遗留的 5 处 bug | [private-marker-and-layer-fixes](changes/2026-09-22-private-marker-and-layer-fixes.md) |
| 2026-09-23 | 层选择指令、`/删除图库`、管理员完整帮助、命名与限额 | [layer-commands-and-admin-help](changes/2026-09-23-layer-commands-and-admin-help.md) |
| 2026-09-23 | 自检修复：单聊私有取图误命中公开；跨层关键词名泄露 | [self-review-fixes](changes/2026-09-23-self-review-fixes.md) |
| 2026-09-23 | 入库缩略图（≤100KB）+ `/图库 关键词` 预览图（每页 8）；列表每页 30 | [thumbnails-and-gallery-preview](changes/2026-09-23-thumbnails-and-gallery-preview.md) |
| 2026-09-23 | 游戏名片：`/添加名片`、`/游戏名片`（竖排拼图）、`/删除名片` | [game-cards](changes/2026-09-23-game-cards.md) |
| 2026-09-23 | 名片修复：图片识别放宽（content_type 缺失）、禁纯数字备注、拼图 1920 | [card-image-parse-and-width](changes/2026-09-23-card-image-parse-and-width.md) |

## 约定

- 文件名：`YYYY-MM-DD-简短英文名.md`。
- 内容至少包含：需求、设计、改动文件、测试方式、备注。
- 行为/契约变化必须同步更新 `README.md`。
