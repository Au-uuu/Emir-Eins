"""临时：解析 raw_events.jsonl 最近事件的结构（合并转发卡片专项）。跑完即删。"""
import io
import json

path = "/opt/qqbot/logs/raw_events.jsonl"
lines = io.open(path, encoding="utf-8", errors="replace").read().strip().splitlines()
print("TOTAL_EVENTS", len(lines))


def walk(el, depth, out):
    if depth > 6 or not isinstance(el, dict):
        return
    t = el.get("type") or el.get("elem_type") or "?"
    keys = [k for k in el.keys() if k != "type"]
    text = el.get("text") or el.get("content") or ""
    text = text[:60] if isinstance(text, str) and text else ""
    out.append("  " * depth + f"[{t}] keys={keys[:10]} text={text!r}")
    for k in ("msg_elements", "elements", "children"):
        for sub in el.get(k) or []:
            walk(sub, depth + 1, out)


for line in lines[-10:]:
    try:
        p = json.loads(line)
    except Exception:
        continue
    d = p.get("d") or {}
    out = []
    walk(d.get("msg_elements") or [], 0, out)
    print("----", p.get("eventType") or p.get("op") or "?", "| content:", repr((d.get("content") or "")[:60]), "| id:", d.get("id", "")[:10])
    if d.get("attachments"):
        print("  attachments:", json.dumps(d["attachments"], ensure_ascii=False)[:200])
    for o in out[:16]:
        print(o)
