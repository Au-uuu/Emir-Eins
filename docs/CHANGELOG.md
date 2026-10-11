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
| 2026-10-08 | `/来只` 查不到图时的提示去掉「层」的说法，并像 `/图库` 那样推荐相近关键词 | [random-image-no-result-copy](changes/2026-10-08-random-image-no-result-copy.md) |
| 2026-10-08 | WD14 打标结果进日志（可观测）+ 按图片内容缓存省 2 秒；修一处依赖环境的测试断言 | [tagger-observability-and-cache](changes/2026-10-08-tagger-observability-and-cache.md) |
| 2026-10-09 | 离线打标 WD14 → Camie Tagger v2：角色标签 2.6 万+、角色 F1 0.83，重写预处理与标签解析 | [camie-tagger](changes/2026-10-09-camie-tagger.md) |
| 2026-10-09 | 打标注册表化：Camie 默认 + AnimeTimm swinv2_base 可选，补每标签 best_threshold；按服务器 4C/3.6G 选型 | [tagger-registry](changes/2026-10-09-tagger-registry.md) |
| 2026-10-09 | 视觉链路可切厂商：文本(DashScope)/视觉(智谱 GLM-4.6V-Flash)分离配置，图片支持裸 base64 | [vision-vl-provider](changes/2026-10-09-vision-vl-provider.md) |
| 2026-10-09 | 两阶段看图（可选 `QQ_BOT_VL_DESCRIBE=1`）：GLM 客观描述 → qwen 转人设；并支持视频识别 | [vision-vl-provider](changes/2026-10-09-vision-vl-provider.md) |
| 2026-10-09 | 识别修复：删角色卡误导性速查表（陈千语→佩丽卡）；打标新增「低置信角色候选」软线索 | [anime-recognition-fixes](changes/2026-10-09-anime-recognition-fixes.md) |
| 2026-10-09 | 角色译名接社区词库（Danbooru 中英对照）：修「curren_chan→当前酱」；卡片改为信任线索/接受同人差异 | [anime-name-dictionary](changes/2026-10-09-anime-name-dictionary.md) |
| 2026-10-09 | 角色卡 §8 去掉「本机的自拍」措辞（不再提及自拍），改为中性表述 | [persona-no-selfie](changes/2026-10-09-persona-no-selfie.md) |
| 2026-10-09 | 文本链路可切厂商：接入 DeepSeek（`QQ_BOT_TEXT_*` 覆盖，默认仍 qwen-flash-character） | [deepseek-provider](changes/2026-10-09-deepseek-provider.md) |
| 2026-10-09 | 本地知识库检索（文字 RAG，轻量 BM25）：`data/kb/*.md` 资料注入提示词 | [local-kb-rag](changes/2026-10-09-local-kb-rag.md) |
| 2026-10-09 | 知识库内容：从萌娘百科抓世界观+角色设定，用便宜 Qwen 模型整理（覆盖方舟/终末地/原神/赛马娘/星铁/R6围攻） | [kb-from-wiki](changes/2026-10-09-kb-from-wiki.md) |
| 2026-10-09 | 角色卡：回复中不得提及「打标器/识别线索」，非动漫图直接无视线索（打标仍照跑） | [no-tagger-talk](changes/2026-10-09-no-tagger-talk.md) |
| 2026-10-10 | 联网搜索（两步式）：命中时效性提问时单独检索、只注入事实摘要；角色模型切 qwen-flash | [web-search](changes/2026-10-10-web-search.md) |
| 2026-10-10 | 看图行为重写：回应优先、打标线索弱化（KB 图像识别因误报已回退） | [image-reply-respond-first](changes/2026-10-10-image-reply-respond-first.md) |
| 2026-10-10 | 知识库采集修复：resolve 改用「限定标题+links」（碧蓝航线 22→418）；模型池/--works/额度观测；扩面魔裁+大肥鱼 | [kb-build-fixes](changes/2026-10-10-kb-build-fixes.md) |
| 2026-10-11 | 知识库纳入坚果云异地备份（`deploy/backup-nutstore.sh` 增 `kb/`） | [kb-backup](changes/2026-10-11-kb-backup.md) |

## 约定

- 文件名：`YYYY-MM-DD-简短英文名.md`。
- 内容至少包含：需求、设计、改动文件、测试方式、备注。
- 行为/契约变化必须同步更新 `README.md`。
