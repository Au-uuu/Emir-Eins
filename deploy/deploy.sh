#!/usr/bin/env bash
#
# QQ 机器人一键部署脚本（Ubuntu / Debian）
#
# 用法：
#   sudo bash deploy.sh
#
# 做的事：
#   1. 安装系统依赖（python3 / venv / pip）
#   2. 把当前目录的项目复制到 /opt/qqbot
#   3. 建立虚拟环境并安装依赖
#   4. 注册并启动 systemd 服务（开机自启 + 崩溃自动重启）
#
# 幂等：重复执行不会破坏已有数据（data/ 目录只在不存在时初始化）

set -euo pipefail

APP_DIR=/opt/qqbot
SERVICE_NAME=qqbot
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

log()  { echo -e "\033[1;32m[deploy]\033[0m $*"; }
warn() { echo -e "\033[1;33m[deploy]\033[0m $*"; }
die()  { echo -e "\033[1;31m[deploy]\033[0m $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "请用 root 运行：sudo bash deploy.sh"

# ---------------------------------------------------------------------------
# 0. 修正换行符
# ---------------------------------------------------------------------------
# 从 Windows 传过来的文件可能带 CRLF，会导致 systemd 解析 ExecStart 失败。
# 这里统一转成 LF。
for f in "$SRC_DIR/deploy/qqbot.service" "$SRC_DIR"/bot.py "$SRC_DIR"/*.py; do
    [[ -f "$f" ]] && sed -i 's/\r$//' "$f" 2>/dev/null || true
done

# ---------------------------------------------------------------------------
# 1. 系统依赖
# ---------------------------------------------------------------------------
log "更新软件源并安装依赖..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip ca-certificates curl >/dev/null

PY_VER=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
log "系统 Python 版本：$PY_VER"

# ---------------------------------------------------------------------------
# 2. 复制项目
# ---------------------------------------------------------------------------
log "部署到 $APP_DIR ..."
mkdir -p "$APP_DIR"

# 保留服务器上已有的 data（图片库），首次部署才从源码复制
if [[ -d "$APP_DIR/data" ]]; then
    warn "检测到已有 data/ 目录，保留服务器上的图片库不覆盖"
    KEEP_DATA=1
else
    KEEP_DATA=0
fi

cd "$SRC_DIR"
for item in bot.py image_store.py uploader.py raw_events.py watchdog.py \
            requirements.txt .env README.md smoke_test.py; do
    if [[ -e "$item" ]]; then
        cp -f "$item" "$APP_DIR/"
    else
        warn "缺少文件：$item"
    fi
done

# 复制后再统一一次换行符，避免 CRLF 引起各种诡异问题
find "$APP_DIR" -maxdepth 1 -type f \( -name '*.py' -o -name '.env' \) \
    -exec sed -i 's/\r$//' {} + 2>/dev/null || true

# 安全检查：绝不把私钥留在服务器上
KEY_FOUND=$(find "$APP_DIR" -maxdepth 2 -type f \
    \( -name '*.pem' -o -name '*.key' -o -name '*.ppk' -o -name 'id_rsa' \) \
    2>/dev/null | head -5)
if [[ -n "$KEY_FOUND" ]]; then
    warn "检测到私钥文件，已从部署目录删除（不应上传到服务器）："
    echo "$KEY_FOUND" | while read -r k; do echo "    - $k"; rm -f "$k"; done
fi

# 数据与日志目录
mkdir -p "$APP_DIR/data" "$APP_DIR/logs"

if [[ $KEEP_DATA -eq 0 && -d "$SRC_DIR/data" ]]; then
    log "初始化图片库数据..."
    cp -a "$SRC_DIR/data/." "$APP_DIR/data/" 2>/dev/null || true
fi

# 测试脚本（可选，但保留便于排查）
for f in _test_store.py _test_commands.py _test_links.py _test_delete.py \
         _test_gallery.py _test_admin.py _test_reply_policy.py \
         _test_privacy.py _test_add.py _test_layers.py _test_thumb.py \
         _test_cards.py; do
    [[ -e "$SRC_DIR/$f" ]] && cp -f "$SRC_DIR/$f" "$APP_DIR/" || true
done

[[ -f "$APP_DIR/.env" ]] || die "缺少 .env（凭据文件），请先创建"

# ---------------------------------------------------------------------------
# 3. 虚拟环境与依赖
# ---------------------------------------------------------------------------
if [[ ! -x "$APP_DIR/.venv/bin/python" ]]; then
    log "创建虚拟环境..."
    python3 -m venv "$APP_DIR/.venv"
fi

log "安装 Python 依赖（qq-botpy / python-dotenv / Pillow）..."
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

# ---------------------------------------------------------------------------
# 4. systemd 服务
# ---------------------------------------------------------------------------
log "注册 systemd 服务..."
install -m 644 "$SRC_DIR/deploy/qqbot.service" "/etc/systemd/system/${SERVICE_NAME}.service"
systemctl daemon-reload
systemctl enable "$SERVICE_NAME" >/dev/null
systemctl restart "$SERVICE_NAME"

sleep 6

# ---------------------------------------------------------------------------
# 5. 自检
# ---------------------------------------------------------------------------
log "运行自检..."
if "$APP_DIR/.venv/bin/python" "$APP_DIR/smoke_test.py"; then
    log "自检通过"
else
    warn "自检未通过，请看下方日志"
fi

echo
log "服务状态："
systemctl --no-pager --lines=12 status "$SERVICE_NAME" || true

cat <<EOF

============================================================
部署完成

  查看实时日志： journalctl -u ${SERVICE_NAME} -f
  查看最近日志： journalctl -u ${SERVICE_NAME} -n 50
  重启机器人：   systemctl restart ${SERVICE_NAME}
  停止机器人：   systemctl stop ${SERVICE_NAME}
  开机自启：     已启用

图片库位置：${APP_DIR}/data
  备份： tar -czf ~/qqbot-data-\$(date +%F).tar.gz -C ${APP_DIR} data
============================================================
EOF
