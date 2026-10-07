"""临时 v2：完整打印最近 3 个事件的 payload 关键字段。跑完即删。"""
import io
import json

lines = io.open(
    "/opt/qqbot/logs/raw_events.jsonl", encoding="utf-8", errors="replace"
).read().strip().splitlines()
print("TOTAL", len(lines))


def trunc(v, n=220):
    s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
    return s[:n] + ("…" if len(s) > n else "")


for line in lines[-4:]:
    p = json.loads(line)
    d = p.get("d") or {}
    print("=" * 70)
    print("id:", d.get("id"))
    for k in sorted(d.keys()):
        v = d[k]
        if k in ("id",):
            continue
        print(f"  {k}: {trunc(v)}")
