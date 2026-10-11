"""给知识库条目补「外貌：…」行（看图链路「描述 → KB 检索」需要）。

## 为什么

视觉模型不认识 2025 年后的新角色，只能靠「视觉描述 → 知识库检索」兜底。
但条目里若没有**精确外貌**，越精确的描述反而越会匹配到外貌写得更全的别的角色——
实测：用橘雪莉的详细画像去检索，命中的是外貌描写更细的《蔚蓝档案》角色。

## 来源（按优先级）

1. 萌娘百科该角色页的「外貌 / 形象 / 外观 / 造型 / 服装」章节原文（不烧 token）；
2. 该章节过短（< min-text）时，取萌百该角色的立绘（pageimage），用视觉模型
   客观描述其外貌——新作（如魔裁）的萌百正文往往很省，只能靠立绘。

只改 `data/kb/<作品>.md` 里对应的 `## 角色` 段落；**幂等**（已有「外貌：」则跳过）。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import urllib.request

sys.path.insert(0, "/opt/qqbot")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_kb_server as B  # noqa: E402

APPEAR_HEAD = re.compile(r"外貌|形象|外观|造型|着装|服装")
VL_KEY = os.environ.get("QQ_BOT_VL_KEY") or os.environ.get("QQ_BOT_QWEN_KEY") or ""
VL_URL = os.environ.get("QQ_BOT_VL_URL") or B.QWEN_URL
VL_MODEL = (
    os.environ.get("QQ_BOT_VL_MODEL")
    or os.environ.get("QQ_BOT_QWEN_VL_MODEL")
    or "qwen3-vl-plus"
)

APPEAR_PROMPT = (
    "只输出这个角色的**外貌**：性别、发色、发型、瞳色、服装的款式与配色、"
    "标志性配件/道具，尽量具体（颜色写准，如「浅蓝」不要只写「蓝」）。"
    "用简体中文，1~3 句；不要评价、不要提性格或经历；不认识的角色不要报名字。"
)


def appearance_from_text(extract: str) -> str:
    """从萌百原文里抽出「外貌/形象」类章节的文字。"""
    cur, parts = None, []
    for line in extract.splitlines():
        m = B.HEADING_RE.match(line.strip())
        if m:
            cur = m.group(2)
            continue
        if cur and APPEAR_HEAD.search(cur) and line.strip():
            parts.append(line.strip())
    txt = "\n".join(parts)
    txt = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", txt)
    txt = re.sub(r"\{\{[^{}]*\}\}|<[^>]+>|''+", "", txt)
    txt = re.sub(r"\[https?://\S+[^\]]*\]", "", txt)
    return re.sub(r"\s+", " ", txt).strip()


def page_image(title: str) -> str | None:
    try:
        d = B._get({"action": "query", "prop": "pageimages", "titles": title,
                    "pithumbsize": "900", "format": "json"})
    except Exception:  # noqa: BLE001
        return None
    for p in d.get("query", {}).get("pages", {}).values():
        if p.get("thumbnail"):
            return p["thumbnail"]["source"]
    return None


def vl_appearance(image_url: str) -> str:
    """用视觉模型客观描述一张立绘的外貌（不报名字）。"""
    if not VL_KEY:
        return ""
    try:
        raw = urllib.request.urlopen(
            urllib.request.Request(image_url, headers=B.UA), timeout=40
        ).read()
        b64 = base64.b64encode(raw).decode()
        body = json.dumps({
            "model": VL_MODEL,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": APPEAR_PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]}],
            "max_tokens": 300,
        }).encode()
        req = urllib.request.Request(
            VL_URL, data=body,
            headers={"Authorization": f"Bearer {VL_KEY}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=90) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        return (d["choices"][0]["message"]["content"] or "").strip()
    except Exception as exc:  # noqa: BLE001
        print(f"    VL 失败：{exc}")
        return ""


def _append(block: str, app: str) -> str:
    app = re.sub(r"\s*\n\s*", " ", app).strip()
    return block + "\n外貌：" + app[:500]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--works", default=None, help="只处理这些输出文件（逗号分隔）")
    ap.add_argument("--min-text", type=int, default=40,
                    help="萌百外貌章节短于该长度时，改用立绘让视觉模型描述")
    args = ap.parse_args()
    wanted = {x.strip() for x in args.works.split(",") if x.strip()} if args.works else None

    for row in B.WORKS:
        filename, suffix, work_page, series, inc, exc = row[:6]
        manual = row[6] if len(row) > 6 else None
        if wanted and filename not in wanted:
            continue
        path = os.path.join(B.OUT_DIR, filename)
        if not os.path.isfile(path):
            print(f"跳过 {filename}（文件不存在）")
            continue
        names = list(manual) if manual else B.enumerate_names(suffix, inc, exc)
        md = open(path, encoding="utf-8").read()
        blocks = md.split("\n\n")
        idx = {}
        for i, b in enumerate(blocks):
            m = re.match(r"^## (\S+)", b)
            if m:
                idx.setdefault(m.group(1), i)
        added = skipped = 0
        for name in names:
            i = idx.get(name)
            if i is None:
                continue
            if "外貌：" in blocks[i]:
                skipped += 1
                continue
            try:
                extract = B.resolve(name, series)
            except Exception:  # noqa: BLE001
                extract = ""
            app = appearance_from_text(extract)
            src = "萌百正文"
            if len(app) < args.min_text:
                img = page_image(name)
                app, src = (vl_appearance(img) if img else ""), "立绘→视觉模型"
            if app:
                blocks[i] = _append(blocks[i], app)
                added += 1
                print(f"  [{filename}] {name} ← {src}（{len(app)} 字）")
        if added:
            open(path, "w", encoding="utf-8").write("\n\n".join(blocks))
        print(f"完成 {filename}：补 {added}，跳过 {skipped}")


if __name__ == "__main__":
    main()
