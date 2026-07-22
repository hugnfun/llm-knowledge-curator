"""Obsidian inbox scanner — migrates build_index.py into the llkc package."""

import json
from pathlib import Path

from .. import config, db
from ..vault import extract_title, make_preview, split_telegram_messages
from ..models import EventType, PipelineStage


def collect_clippings() -> list[dict]:
    units = []
    root = config.INBOX_ROOT / "Clippings"
    if not root.exists():
        return units
    for i, p in enumerate(sorted(root.rglob("*.md")), start=1):
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            print(f"[skip] {p}: {e}")
            continue
        units.append({
            "unit_id": f"clippings-{i:03d}",
            "source": "clippings",
            "source_path": str(p.relative_to(config.VAULT_ROOT)),
            "abs_path": str(p),
            "tg_message_idx": None,
            "tg_message_time": None,
            "title": extract_title(text, p.stem),
            "preview": make_preview(text),
            "char_len": len(text),
        })
    return units


def collect_xbookmarks() -> list[dict]:
    units = []
    root = config.INBOX_ROOT / "X-Bookmarks"
    if not root.exists():
        return units
    counter = 0
    for p in sorted(root.rglob("*.md")):
        if p.name.lower().startswith(("index", "_index")):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            print(f"[skip] {p}: {e}")
            continue
        counter += 1
        units.append({
            "unit_id": f"x-bookmarks-{counter:03d}",
            "source": "x-bookmarks",
            "source_path": str(p.relative_to(config.VAULT_ROOT)),
            "abs_path": str(p),
            "tg_message_idx": None,
            "tg_message_time": None,
            "title": extract_title(text, p.stem),
            "preview": make_preview(text),
            "char_len": len(text),
        })
    return units


def collect_telegram() -> list[dict]:
    units = []
    root = config.INBOX_ROOT / "Telegram"
    if not root.exists():
        return units
    counter = 0
    for p in sorted(root.rglob("*.md")):
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            print(f"[skip] {p}: {e}")
            continue
        msgs = split_telegram_messages(text)
        for idx, (t, body) in enumerate(msgs, start=1):
            counter += 1
            first_line = body.split("\n", 1)[0].strip()
            title = first_line[:80] or f"{p.stem} {t}"
            units.append({
                "unit_id": f"telegram-{counter:04d}",
                "source": "telegram",
                "source_path": str(p.relative_to(config.VAULT_ROOT)),
                "abs_path": str(p),
                "tg_message_idx": idx,
                "tg_message_time": t,
                "title": title,
                "preview": body[:500].replace("\n", " | "),
                "char_len": len(body),
            })
    return units


def scan_inbox(persist: bool = True, db_path: Path = None) -> list[dict]:
    """Scan all inbox sources and optionally persist to DB. Returns all units."""
    all_units = collect_clippings() + collect_xbookmarks() + collect_telegram()
    if persist:
        run_id = db.create_run(stage=PipelineStage.COLLECT.value, db_path=db_path)
        with db.get_conn(db_path) as c:
            for u in all_units:
                item = {
                    **u,
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
        for u in all_units:
            db.log_event(EventType.RAW_ITEM_CREATED.value, item_id=u["unit_id"],
                         payload={"source": u["source"], "title": u["title"]},
                         db_path=db_path)
        db.complete_run(run_id, artifacts=json.dumps({"total": len(all_units)}),
                        db_path=db_path)
    return all_units


def write_index(units: list[dict]):
    """Write the legacy inbox_index.json for backward compatibility."""
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = config.OUTPUT_DIR / "inbox_index.json"
    out_path.write_text(json.dumps(units, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        "total_units": len(units),
        "clippings": sum(1 for u in units if u["source"] == "clippings"),
        "x-bookmarks": sum(1 for u in units if u["source"] == "x-bookmarks"),
        "telegram_messages": sum(1 for u in units if u["source"] == "telegram"),
    }
    (config.OUTPUT_DIR / "inbox_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary
