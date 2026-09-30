#!/usr/bin/env bash
#
# 生成敏感词表：data/sensitive_words.txt
#
# 数据来源：konsheng/Sensitive-lexicon（MIT）的四个分类
#   政治类型 / 反动词库 / 贪腐词库 / 暴恐词库
#
# 为什么走 jsDelivr：国内服务器直连 GitHub raw 常被墙，jsDelivr CDN 可直连。
#
# 只取「政治/反动/贪腐/暴恐」四类，**故意不取**民生、色情、非法网址等大类 ——
# 民生类含大量日常词会疯狂误杀；本机器人的目标只是「不碰键政」。
#
# 放行名单见 deploy/sensitive_allow.txt（这些词会被剔除）。
#
# 用法：sudo bash deploy/fetch_sensitive_words.sh
#
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/qqbot}"
OUT="${OUT:-$APP_DIR/data/sensitive_words.txt}"
ALLOW="${ALLOW:-$APP_DIR/deploy/sensitive_allow.txt}"
PY="${PY:-$APP_DIR/.venv/bin/python}"
BASE="${BASE:-https://cdn.jsdelivr.net/gh/konsheng/Sensitive-lexicon@master/Vocabulary}"

mkdir -p "$(dirname "$OUT")"

OUT="$OUT" ALLOW="$ALLOW" BASE="$BASE" "$PY" - <<'PYEOF'
import os
import time
import urllib.parse
import urllib.request

out_path = os.environ["OUT"]
allow_path = os.environ["ALLOW"]
base = os.environ["BASE"].rstrip("/")

FILES = ["政治类型.txt", "反动词库.txt", "贪腐词库.txt", "暴恐词库.txt"]
MIN_LEN = 2  # 单字词误杀率太高（「党」会命中「党员」等），不收录

# jsDelivr 从国内服务器访问偶发 SSL 超时，多镜像 + 重试
MIRRORS = [
    base,
    "https://fastly.jsdelivr.net/gh/konsheng/Sensitive-lexicon@master/Vocabulary",
    "https://gcore.jsdelivr.net/gh/konsheng/Sensitive-lexicon@master/Vocabulary",
]

# 与 sensitive.py 的 normalize() 保持一致：全角转半角 + 去空白/分隔符 + 小写
STRIP = " \t\r\n\u3000·*.-_+|/\\~#$%^&()[]{}<>《》「」【】，。、！？!?,.;:'\"“”‘’…—–"
# 注意：translate 的字典键必须是字符序号(int)，不能用 chr()
FULLWIDTH = {0xFF01 + i: chr(0x21 + i) for i in range(94)}
FULLWIDTH[0x3000] = " "
STRIP_TABLE = {ord(c): None for c in STRIP}


def normalize(s: str) -> str:
    return s.translate(FULLWIDTH).lower().translate(STRIP_TABLE)


def fetch(name: str) -> str:
    """取一个词库文件：逐个镜像重试，全部失败才抛异常。"""
    last_exc: Exception | None = None
    for mirror in MIRRORS:
        url = f"{mirror}/{urllib.parse.quote(name)}"
        for attempt in (1, 2):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=60) as resp:
                    return resp.read().decode("utf-8", "replace")
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                print(f"      重试 {attempt}/2 {mirror.split('/')[2]}：{type(exc).__name__}")
                time.sleep(2)
    raise last_exc  # type: ignore[misc]


allow = set()
if os.path.isfile(allow_path):
    with open(allow_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#"):
                allow.add(normalize(line))
    print(f"放行名单：{len(allow)} 条（{allow_path}）")

words: set[str] = set()
failed: list[str] = []
for name in FILES:
    try:
        body = fetch(name)
    except Exception as exc:  # noqa: BLE001
        # 单个文件拿不到不致命：已拿到的词仍然有效，但要显式告警，
        # 避免"看起来成功、其实漏了一整类词"这种静默缺口。
        failed.append(name)
        print(f"  !! {name} 拉取失败：{type(exc).__name__} {exc}")
        continue
    got = 0
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        w = normalize(line)
        if len(w) < MIN_LEN or w in allow or w in words:
            continue
        words.add(w)
        got += 1
    print(f"  {name}: +{got}（累计 {len(words)}）")

if not words:
    raise SystemExit("所有词库都没拉到，不覆盖现有词表")

with open(out_path, "w", encoding="utf-8") as fh:
    fh.write("# 由 deploy/fetch_sensitive_words.sh 生成，请勿手工编辑\n")
    fh.write("# 来源：konsheng/Sensitive-lexicon (MIT)\n")
    for w in sorted(words):
        fh.write(w + "\n")

print(f"完成：{len(words)} 条 -> {out_path}")
if failed:
    print(f"⚠️ 以下词库本次未取到，词表不完整：{'、'.join(failed)}")
PYEOF
