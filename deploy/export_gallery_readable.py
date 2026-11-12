#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把图库导出成人类看得懂的目录（供坚果云/网盘备份）。

输出结构：
    gallery/
      公开/
        蓝色大肥鱼/
          001_蓝色大肥鱼_96b6e917.jpg
      群_白、_樛木、Auuuu/
        群私有图库/
          001_群私有图库_883074c2.png
      _未分类/
      _索引.csv
      _说明.md

说明：
- 原图仍以 SHA-256 存在 data/images/，gallery/ 只是可读视图；
- 同一张图属于多个图库时会在多个目录各出现一次（当前只有 3 张）；
- 文件名 = 序号_关键词_SHA前8.扩展名，便于排序和溯源；
- 群名优先读 data/group_names.json，没有则尝试 QQ 群信息接口，再不行用 openid 前 8 位。

只依赖 Python 标准库。用法：
    python3 deploy/export_gallery_readable.py --data-dir /opt/qqbot/data --out /var/lib/qqbot-backup/gallery
    python3 deploy/export_gallery_readable.py --data-dir ... --out ... --no-fetch-names
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import sqlite3
import sys
import time
import urllib.request

IMAGE_TABLE = "images"
BAD_CHARS_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
SPACE_RE = re.compile(r"\s+")
MAX_NAME = 60


def safe_name(raw, fallback: str = "未命名") -> str:
    text = str(raw or "").strip()
    text = BAD_CHARS_RE.sub("_", text)
    text = SPACE_RE.sub(" ", text).strip(" .")
    if not text:
        text = fallback
    return text[:MAX_NAME]


def load_group_names(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_group_names(path: str, data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError as exc:
        print(f"[图库导出] 写群名缓存失败：{exc}", file=sys.stderr)


def _read_env(path: str) -> dict:
    values = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return values


def fetch_group_names(group_ids: list[str], env_path: str, cache_path: str) -> dict:
    """尽力查群名；失败返回已有缓存。"""
    cache = load_group_names(cache_path)
    missing = [g for g in group_ids if g and g not in cache]
    if not missing:
        return cache
    env = _read_env(env_path)
    appid = env.get("QQ_BOT_APPID", "")
    secret = env.get("QQ_BOT_SECRET", "")
    if not appid or not secret:
        return cache
    try:
        req = urllib.request.Request(
            "https://bots.qq.com/app/getAppAccessToken",
            data=json.dumps({"appId": appid, "clientSecret": secret}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            token = json.loads(resp.read().decode("utf-8", "replace")).get("access_token")
        if not token:
            return cache
    except Exception as exc:  # noqa: BLE001
        print(f"[图库导出] 取 access_token 失败：{exc}", file=sys.stderr)
        return cache

    changed = False
    for gid in missing:
        try:
            req = urllib.request.Request(
                f"https://api.sgroup.qq.com/v2/groups/{gid}/info",
                headers={
                    "Authorization": f"QQBot {token}",
                    "User-Agent": "qqbot-gallery-export/1.0",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
            name = str((data or {}).get("group_name") or "").strip()
            if name:
                cache[gid] = name
                changed = True
        except Exception as exc:  # noqa: BLE001
            print(f"[图库导出] 查群名失败 {gid[:8]}：{exc}", file=sys.stderr)
    if changed:
        save_group_names(cache_path, cache)
    return cache


def _link_or_copy(src: str, dst: str) -> None:
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _ext(image: dict) -> str:
    ext = str(image.get("ext") or "").strip()
    if ext and not ext.startswith("."):
        ext = "." + ext
    if not ext:
        rel = str(image.get("rel_path") or "")
        ext = os.path.splitext(rel)[1]
    return ext[:10]


def export(data_dir: str, out_dir: str, env_path: str, fetch_names: bool = True) -> int:
    db_path = os.path.join(data_dir, "images.db")
    images_root = os.path.join(data_dir, "images")
    if not os.path.isfile(db_path):
        print(f"[图库导出] 找不到 {db_path}", file=sys.stderr)
        return 1

    os.makedirs(os.path.dirname(out_dir) or ".", exist_ok=True)
    shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(out_dir, exist_ok=True)

    names_path = os.path.join(data_dir, "group_names.json")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT k.keyword, k.owner_group, i.id, i.sha256, i.rel_path, i.ext, "
            "i.mime, i.size, i.width, i.height, i.orig_filename, i.added_at "
            "FROM keywords k JOIN images i ON i.id = k.image_id "
            "ORDER BY k.owner_group, k.keyword, i.id"
        ).fetchall()
        orphan_rows = conn.execute(
            "SELECT i.id, i.sha256, i.rel_path, i.ext, i.mime, i.size, "
            "i.width, i.height, i.orig_filename, i.added_at "
            "FROM images i WHERE NOT EXISTS "
            "(SELECT 1 FROM keywords k WHERE k.image_id = i.id) ORDER BY i.id"
        ).fetchall()
    finally:
        conn.close()

    groups: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        key = (str(row["keyword"] or ""), str(row["owner_group"] or ""))
        groups.setdefault(key, []).append(dict(row))
    if orphan_rows:
        groups[("_未分类", "")] = [dict(row) for row in orphan_rows]

    group_ids = sorted({owner for _, owner in groups if owner})
    names = fetch_group_names(group_ids, env_path, names_path) if fetch_names else load_group_names(names_path)

    index_rows: list[list[str]] = []
    used_dirs: set[tuple[str, str]] = set()
    file_count = 0
    missing = 0

    for (keyword, owner), items in groups.items():
        is_orphan = keyword == "_未分类" and not owner
        if is_orphan:
            scope = "_未分类"
            gallery = ""
            target_dir = os.path.join(out_dir, "_未分类")
        else:
            scope = "公开" if not owner else "群_" + safe_name(names.get(owner) or owner[:8], "群")
            gallery = safe_name(keyword, "未命名")
            base_key = (scope, gallery)
            if base_key in used_dirs:
                for n in range(2, 100):
                    candidate = f"{gallery}_{n}"
                    if (scope, candidate) not in used_dirs:
                        gallery = candidate
                        break
            used_dirs.add((scope, gallery))
            target_dir = os.path.join(out_dir, scope, gallery)
        os.makedirs(target_dir, exist_ok=True)

        for index, image in enumerate(items, 1):
            sha = str(image.get("sha256") or "")
            rel = str(image.get("rel_path") or "").replace("\\", "/").lstrip("/")
            src = os.path.join(images_root, rel)
            if not sha or not os.path.isfile(src):
                missing += 1
                print(f"[图库导出] 缺文件：{rel}", file=sys.stderr)
                continue
            ext = _ext(image)
            filename = f"{index:03d}_{safe_name(keyword, '图')[:20]}_{sha[:8]}{ext}"
            dst = os.path.join(target_dir, filename)
            _link_or_copy(src, dst)
            file_count += 1
            width = image.get("width") or ""
            height = image.get("height") or ""
            index_rows.append([
                scope,
                keyword,
                filename,
                str(image.get("orig_filename") or ""),
                f"{int(image.get('size') or 0) / 1024:.1f}",
                f"{width}x{height}" if width or height else "",
                sha,
                str(image.get("id") or ""),
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(image.get("added_at") or 0))),
            ])

    with open(os.path.join(out_dir, "_索引.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["范围", "图库", "文件名", "原文件名", "大小KB", "尺寸", "SHA256", "图片ID", "添加时间"])
        writer.writerows(index_rows)

    with open(os.path.join(out_dir, "_说明.md"), "w", encoding="utf-8") as fh:
        fh.write(
            "# 图库可读备份\n\n"
            "- `公开/`：公开图库，按关键词分文件夹；\n"
            "- `群_xxx/`：群私有图库，按关键词分文件夹；\n"
            "- `_未分类/`：存在图片表、但没有关联任何关键词的图片；\n"
            "- `_索引.csv`：每张图对应图库、文件名、原文件名、大小、尺寸、SHA256、图片 ID、添加时间；\n\n"
            "文件名格式：`序号_关键词_SHA前8.扩展名`。\n"
            "同一张图属于多个图库时，会在多个文件夹各出现一次。\n"
            "原始文件仍按 SHA-256 存放在 `images/`；本目录只是方便浏览的视图，可由 `images.db` 重新生成。\n"
        )

    print(
        f"[图库导出] 完成：{len(groups)} 个图库层，"
        f"{file_count} 个可读文件，缺文件 {missing} 个 -> {out_dir}"
    )
    return 0 if file_count or not groups else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default="/opt/qqbot/data")
    ap.add_argument("--out", default="/var/lib/qqbot-backup/gallery")
    ap.add_argument("--env", default="/opt/qqbot/.env")
    ap.add_argument("--no-fetch-names", action="store_true")
    args = ap.parse_args()
    return export(args.data_dir, args.out, args.env, not args.no_fetch_names)


if __name__ == "__main__":
    sys.exit(main())
