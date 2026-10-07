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
| 2026-09-23 | `/图库 关键词` 精确查不到时模糊推荐相关关键词 | [gallery-fuzzy-search](changes/2026-09-23-gallery-fuzzy-search.md) |
| 2026-09-27 | 图库异地备份到坚果云（WebDAV）：增量上传、远端只增不减、数据库一致性快照 | [nutstore-backup](changes/2026-09-27-nutstore-backup.md) |
| 2026-09-28 | 单聊只发图片时不再回复 `/help`（图片消息正文为空被误判为「空内容」） | [c2c-image-silent](changes/2026-09-28-c2c-image-silent.md) |
| 2026-09-28 | 修正 Noto CJK 取到日文字形；新增文本转图片模块（尚未接入 `bot.py`） | [font-face-and-text-image](changes/2026-09-28-font-face-and-text-image.md) |
| 2026-09-30 | 人格聊天：单聊/群@ 非指令文本接入 `qwen-flash-character`，按会话隔离记忆 + 敏感词过滤 | [persona-chat](changes/2026-09-30-persona-chat.md) |
| 2026-10-02 | `/help` 改为发送图片（带背景图、JPEG 压缩）；移除从未生效的帮助按钮；file_info 缓存把上传 2 秒降到 0 | [help-image](changes/2026-10-02-help-image.md) |
| 2026-10-02 | 游戏公告推送：`/公告` 开关/查询 + 轮询推送明日方舟/终末地更新公告（每群×每游戏独立，默认关） | [ark-news-push](changes/2026-10-02-ark-news-push.md) |
| 2026-10-04 | @全体成员的消息一律不响应（防「空正文」误判成 `/help` 刷帮助图） | [ignore-at-everyone](changes/2026-10-04-ignore-at-everyone.md) |
| 2026-10-04 | 公告推送：修复重启会吞公告的隐患（首轮判定改用持久化 seen 表）+ 关键词放宽到含「活动预告」 | [ark-news-push](changes/2026-10-02-ark-news-push.md) |
| 2026-10-04 | 架构调研落地：群消息前置抽取、@全体成员真实 payload 形态、公告推送配额认知修正（22009 识别） | [arch-review-optimizations](changes/2026-10-04-arch-review-optimizations.md) |
| 2026-10-05 | 人格聊天看图：带图消息切换 `qwen3-vl-flash`（同角色卡同历史），单聊发图会回复；隐私协议同步改写 | [persona-vision](changes/2026-10-05-persona-vision.md) |
| 2026-10-07 | 看图升级：单发 GIF 抽首/中/尾 3 帧（混发仍看首帧），动图「梗在后面」能看懂了 | [gif-frames](changes/2026-10-07-gif-frames.md) |
| 2026-10-07 | 角色卡重写：新增看图行为（图≠本机自拍、先识图后入戏）与群聊守则（短回复/防复读/恋爱脑只对主人）；删「自拍配菜」示例台词 | [persona-card-group](changes/2026-10-07-persona-card-group.md) |
| 2026-10-07 | `/help` 加入 AI 聊天介绍：【闲聊】小节（单聊直说/群 @、看图玩法、图片不入库） | [help-chat-section](changes/2026-10-07-help-chat-section.md) |
| 2026-10-07 | 动漫角色识别：角色卡补「尽力报名、不编名字」；视觉模型升级 `qwen3-vl-plus`（实测不瞎猜不自拍） | [anime-recognition](changes/2026-10-07-anime-recognition.md) |
| 2026-10-07 | 修复回复被静默吞掉：敏感词表误杀角色标签【抗议】（含游行/示威）加入放行名单；丢弃日志附回复原文 | [sensitive-allow-tags](changes/2026-10-07-sensitive-allow-tags.md) |
| 2026-10-07 | 引用消息接入：引用文本喂给模型、合并转发卡片（聊天记录）展开内容可读、卡片里的图能看 | [quoted-message-context](changes/2026-10-07-quoted-message-context.md) |
| 2026-10-07 | 动漫识别强化：图库指纹反查把群标注喂给模型 + 角色速查表（佩丽卡/阿米娅）+ 描述规避政治词 | [gallery-hints-anime](changes/2026-10-07-gallery-hints-anime.md) |
| 2026-10-07 | 撤销图库反查（群标注不可靠）；接入 WD14 本地打标：已收录角色出「识别线索」，实测初音 0.99/周边 0.94 | [wd14-tagger](changes/2026-10-07-wd14-tagger.md) |

## 约定

- 文件名：`YYYY-MM-DD-简短英文名.md`。
- 内容至少包含：需求、设计、改动文件、测试方式、备注。
- 行为/契约变化必须同步更新 `README.md`。
