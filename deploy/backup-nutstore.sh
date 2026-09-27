#!/usr/bin/env bash
#
# 坚果云 WebDAV 增量备份：/opt/qqbot/data -> nutstore:qqbot-backup/
#
# 设计要点：
#   1. 图片按 SHA-256 命名、内容永不改变，所以增量上传只会传新增文件
#   2. 用 rclone copy 而不是 sync —— 远端只增不减，服务器上误删了云上还在
#   3. 数据库用 SQLite 在线备份 API 取一致性快照，避免抓到写一半的库
#   4. flock 防重入：上一次还没跑完就跳过本次
#   5. 请求数克制：坚果云免费版限制每 30 分钟 600 次请求，并发开小一点
#
# 用法：
#   backup-nutstore.sh          正常备份
#   backup-nutstore.sh --dry    演练，只对比不传输
#
# 依赖：rclone（apt 安装）、/opt/qqbot/.venv 里的 python
#
set -uo pipefail

DATA_DIR="${QQBOT_DATA_DIR:-/opt/qqbot/data}"
REMOTE="${NUTSTORE_REMOTE:-nutstore:qqbot-backup}"
STAGE="${QQBOT_STAGE_DIR:-/var/lib/qqbot-backup}"
LOG="${QQBOT_BACKUP_LOG:-/var/log/qqbot-backup.log}"
LOCK="/var/run/qqbot-backup.lock"
PY="${QQBOT_PYTHON:-/opt/qqbot/.venv/bin/python}"

DRY=""
[ "${1:-}" = "--dry" ] && DRY="--dry-run"

mkdir -p "$STAGE"
touch "$LOG"
exec >>"$LOG" 2>&1

echo "===== $(date '+%F %T') 备份开始${DRY:+ (演练)} ====="

# --- 防重入 ---
exec 9>"$LOCK"
if ! flock -n 9; then
    echo "已有备份任务在运行，本次跳过"
    exit 0
fi

rc=0

# --- 0) 确保远端根目录存在（坚果云不会自动创建父目录）---
rclone mkdir "$REMOTE" $DRY 2>/dev/null || true

# --- 1) 图片 + 缩略图：增量上传 ---
for d in images thumbs; do
    [ -d "$DATA_DIR/$d" ] || continue
    echo "--- 同步 $d ---"
    rclone copy "$DATA_DIR/$d" "$REMOTE/$d" \
        --transfers 2 --checkers 4 \
        --retries 5 --low-level-retries 10 \
        -v $DRY || rc=$?
done

# --- 2) 数据库：先取一致性快照，再上传 ---
echo "--- 数据库快照 ---"
if "$PY" - "$DATA_DIR/images.db" "$STAGE/images.db" <<'PYEOF'
import sqlite3
import sys

src_path, dst_path = sys.argv[1], sys.argv[2]
src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
dst = sqlite3.connect(dst_path)
with dst:
    src.backup(dst)
dst.close()
src.close()
print(f"db snapshot ok -> {dst_path}")
PYEOF
then
    rclone copyto "$STAGE/images.db" "$REMOTE/images.db" \
        --retries 5 --low-level-retries 10 -v $DRY || rc=$?
else
    echo "!! 数据库快照失败，云端保留上一次的版本"
    rc=1
fi

if [ "$rc" -ne 0 ]; then
    echo "===== $(date '+%F %T') 备份结束：有错误 rc=$rc ====="
    exit "$rc"
fi
echo "===== $(date '+%F %T') 备份完成 ====="
