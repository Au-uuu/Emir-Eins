# -*- coding: utf-8 -*-
"""可读图库导出测试（不联网、不碰线上数据）。"""

import gzip
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

spec = importlib.util.spec_from_file_location(
    "export_gallery_readable", os.path.join(BASE, "deploy", "export_gallery_readable.py")
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

failures = 0


def check(label, ok, extra=""):
    global failures
    print(f"  [{'OK' if ok else 'FAIL'}] {label}{(' -> ' + str(extra)) if extra and not ok else ''}")
    if not ok:
        failures += 1


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="qqbot_gallery_export_")
    data = os.path.join(tmp, "data")
    out = os.path.join(tmp, "gallery")
    os.makedirs(os.path.join(data, "images", "aa"), exist_ok=True)
    files = {
        "aa/aaaa.jpg": b"jpg-one",
        "aa/bbbb.png": b"png-two",
        "aa/cccc.jpg": b"jpg-three",
    }
    for rel, blob in files.items():
        with open(os.path.join(data, "images", rel), "wb") as fh:
            fh.write(blob)

    db = os.path.join(data, "images.db")
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE images (
            id INTEGER PRIMARY KEY, sha256 TEXT, rel_path TEXT, ext TEXT,
            mime TEXT, size INTEGER, width INTEGER, height INTEGER,
            orig_filename TEXT, added_at REAL
        );
        CREATE TABLE keywords (
            image_id INTEGER, keyword TEXT, owner_group TEXT, added_at REAL
        );
        """
    )
    rows = [
        (1, "a" * 64, "aa/aaaa.jpg", ".jpg", "image/jpeg", 7, 100, 100, "猫.jpg", 1700000000),
        (2, "b" * 64, "aa/bbbb.png", ".png", "image/png", 7, 200, 200, "私藏.png", 1700000001),
        (3, "c" * 64, "aa/cccc.jpg", ".jpg", "image/jpeg", 9, 300, 300, "孤儿.jpg", 1700000002),
    ]
    conn.executemany("INSERT INTO images VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    conn.executemany(
        "INSERT INTO keywords VALUES (?,?,?,?)",
        [
            (1, "猫", "", 1700000000),
            (1, "私藏", "G1", 1700000000),
            (2, "私藏", "G1", 1700000001),
        ],
    )
    conn.commit()
    conn.close()
    with open(os.path.join(data, "group_names.json"), "w", encoding="utf-8") as fh:
        json.dump({"G1": "测试群"}, fh, ensure_ascii=False)

    code = mod.export(data, out, os.path.join(tmp, ".env"), fetch_names=False)
    check("导出成功", code == 0, code)

    public = os.path.join(out, "公开", "猫")
    private = os.path.join(out, "群_测试群", "私藏")
    orphan = os.path.join(out, "_未分类")
    check("公开图库目录", os.path.isdir(public), public)
    check("群名目录", os.path.isdir(private), private)
    check("未分类目录", os.path.isdir(orphan), orphan)
    check("公开图文件", len(os.listdir(public)) == 1, os.listdir(public))
    check("私有图文件", len(os.listdir(private)) == 2, os.listdir(private))
    check("未分类图文件", len(os.listdir(orphan)) == 1, os.listdir(orphan))
    check("文件名可读", any(n.startswith("001_猫_") and n.endswith(".jpg") for n in os.listdir(public)), os.listdir(public))
    idx = os.path.join(out, "_索引.csv")
    check("索引存在", os.path.isfile(idx))
    with open(idx, encoding="utf-8-sig") as fh:
        text = fh.read()
    check("索引含公开/群/未分类", "公开" in text and "测试群" in text and "_未分类" in text, text[:300])
    check("说明存在", os.path.isfile(os.path.join(out, "_说明.md")))

    check("非法字符被清理", mod.safe_name("a/b:c*") == "a_b_c_", mod.safe_name("a/b:c*"))
    check("空名有兜底", mod.safe_name("  ") == "未命名", mod.safe_name("  "))

    print(f"\n失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
