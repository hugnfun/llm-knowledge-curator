"""Migration script - imports existing verdicts.jsonl + inbox_index.json into SQLite."""

import json
from pathlib import Path
from datetime import datetime
from llkc import config, db
from llkc.models import EventType


def migrate(verdicts_path: Path = None, index_path: Path = None):
    verdicts_path = verdicts_path or config.OUTPUT_DIR / "verdicts.jsonl"
    index_path = index_path or config.OUTPUT_DIR / "inbox_index.json"

    config.ensure_dirs()
    db.init_db()

    # 1. Load inbox index (unit metadata)
    units_by_id = {}
    if index_path.exists():
        units = json.loads(index_path.read_text(encoding="utf-8"))
        for u in units:
            units_by_id[u["unit_id"]] = u
        print(f"[migrate] loaded {len(units_by_id)} units from index")
    else:
        print(f"[migrate] WARNING: {index_path} not found")

    # 2. Load verdicts
    verdicts = []
    if verdicts_path.exists():
        for line in verdicts_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                verdicts.append(json.loads(line))
        print(f"[migrate] loaded {len(verdicts)} verdicts")
    else:
        print(f"[migrate] WARNING: {verdicts_path} not found")

    # 3. Merge and upsert items
    migrated = 0
    skipped = 0
    with db.get_conn() as c:
        for v in verdicts:
            uid = v["unit_id"]
            unit = units_by_id.get(uid, {})
            verdict_data = v.get("verdict", {})
            item = {
                "unit_id": uid,
                "source": v.get("source", unit.get("source", "unknown")),
                "source_path": v.get("source_path", unit.get("source_path", "")),
                "abs_path": v.get("abs_path", unit.get("abs_path", "")),
                "title": v.get("title", unit.get("title", "")),
                "preview": unit.get("preview", ""),
                "char_len": v.get("char_len", unit.get("char_len", 0)),
                "tg_message_idx": v.get("tg_message_idx"),
                "tg_message_time": v.get("tg_message_time"),
                "verdict": verdict_data.get("verdict", "pending"),
                "category": verdict_data.get("category", ""),
                "trigger": verdict_data.get("trigger", ""),
                "reason": verdict_data.get("reason", ""),
                "confidence": verdict_data.get("confidence", ""),
                "priority": verdict_data.get("priority", "normal"),
                "status": "pooled" if verdict_data.get("verdict") in ("seed", "asset", "archive") else "pending",
                "parsed_at": datetime.now().strftime("%Y-%m-%d"),
                "pooled_at": datetime.now().strftime("%Y-%m-%d"),
                "raw_content": None,
            }
            db.upsert_item(c, item)
            migrated += 1

        # Also add units that have no verdict yet
        for uid, unit in units_by_id.items():
            if uid not in {v["unit_id"] for v in verdicts}:
                item = {
                    "unit_id": uid,
                    "source": unit.get("source", "unknown"),
                    "source_path": unit.get("source_path", ""),
                    "abs_path": unit.get("abs_path", ""),
                    "title": unit.get("title", ""),
                    "preview": unit.get("preview", ""),
                    "char_len": unit.get("char_len", 0),
                    "tg_message_idx": unit.get("tg_message_idx"),
                    "tg_message_time": unit.get("tg_message_time"),
                    "verdict": "pending",
                    "category": None,
                    "trigger": None,
                    "reason": None,
                    "confidence": None,
                    "priority": "normal",
                    "status": "pending",
                    "parsed_at": None,
                    "pooled_at": None,
                    "raw_content": None,
                }
                db.upsert_item(c, item)
                skipped += 1

    # 4. Log migration event
    db.log_event("System.Migrated", payload={"migrated": migrated, "pending": skipped})

    stats = db.get_stats()
    print(f"\n[migrate] complete:")
    print(f"  migrated (with verdict): {migrated}")
    print(f"  pending (no verdict): {skipped}")
    print(f"  total in DB: {stats['total_items']}")
    print(f"  verdict distribution: {stats['items']}")
    return stats


if __name__ == "__main__":
    migrate()
