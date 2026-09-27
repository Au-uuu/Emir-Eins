# 图库异地备份到坚果云

日期：2026-09-27

## 需求

服务器 `/opt/qqbot/data` 是整个图库的**唯一副本**（约 90MB / 106 张图，本地旧副本只剩 5 张），
而服务器上**一个备份都没有**：`/root` 下没有归档，root crontab 里只有腾讯云自带的 stargate 任务。
需要一层自动、异地、可恢复的备份。

同时评估过腾讯云控制台自带的「系统盘自动备份」：它基于备份点计费
（内地 0.1 元/GB/月，且**保留天数 = 备份份数**），40GB 系统盘保留 7 天约 **28 元/月**，
比服务器本身还贵；而且恢复是**整盘回滚 + 自动重启**，不能单独捞一张图。**放弃**。

## 方案

坚果云官方支持 **WebDAV**，`rclone` 原生可连，不需要第三方中间件。

- 脚本：`deploy/backup-nutstore.sh`
- 定时：root crontab `30 3 * * *`
- 远端：`nutstore:qqbot-backup/`（`images/`、`thumbs/`、`images.db`）
- 日志：`/var/log/qqbot-backup.log`

### 关键设计

1. **增量上传**：图片按内容 SHA-256 命名且永不改写，所以 rclone 的
   大小+修改时间比对天然等价于增量，每天实际只传新增文件。
2. **`copy` 而非 `sync`**：远端只增不减。若用 `sync`，服务器上误删的图会被同步删掉，
   备份就失去意义了。
3. **数据库一致性快照**：`images.db` 是活跃 SQLite，直接拷贝可能抓到写一半的库。
   改用 Python `sqlite3` 的在线备份 API（`src.backup(dst)`）写出一致性快照再上传。
4. **`flock` 防重入**：上一次没跑完则本次直接跳过。
5. **请求数克制**：并发降到 `--transfers 2 --checkers 4`，并加 `--retries 5`。

## 改动文件

| 文件 | 说明 |
|---|---|
| `deploy/backup-nutstore.sh` | 新增，备份脚本 |
| `.gitattributes` | 新增，强制 `*.sh` 使用 LF（Windows 编辑后 scp 到 Linux 才可执行） |
| `README.md` | 新增「图库备份到坚果云」章节 |
| `docs/CHANGELOG.md` | 索引 |

服务器侧（不在仓库内）：

- `apt install rclone`（v1.60.1）
- `/root/.config/rclone/rclone.conf`（600，含 obscured 应用密码）
- root crontab 增加一行

## 测试方式

1. 首次全量：日志显示 `Transferred: 106 / 106`（images）、`45 / 45`（thumbs）、`1 / 1`（db），退出码 0。
2. 数据库回验：把云端 `images.db` 下载回来，逐表比对行数 ——
   images 106 / keywords 105 / name_cards 2 / admins 2 / keyword_links 7 / gallery_privacy 8，**全部一致**。
3. 图片回验：随机抽 4 张下载回来，比对文件内容的 SHA-256 与文件名是否相符（文件名就是内容哈希），**4/4 通过**。

## 备注（踩过的坑）

- **坚果云不会自动创建父目录**：直接 `rclone copy` 到不存在的 `qqbot-backup/`
  会报 `409 Conflict / AncestorsNotFound`。脚本里先跑一次 `rclone mkdir`。
- **免费版频率限制是真实存在的**：每 30 分钟 600 次请求。
  首次全量约 400 次请求，跑完后紧接着做了一次 `rclone lsl -R` 递归验证（131 次请求），
  直接把额度打爆，触发 `503 BlockedTemporarily`，约 **24 分钟**后自动恢复。
  **结论：验证备份不要用递归列目录，只针对单个文件下载比对哈希。**

## 未做

- 服务器本地再留一份 tar（当前只有云上一份 + 服务器原件）。若有需要可再加一层本地轮转归档。
- `deploy/deploy.sh` 未包含本备份脚本的安装与 crontab 注册，目前是手动部署的。
