"""Write-back stage - writes classified items to vault pools. Migrates write_back.py."""

import json
import re
from datetime import datetime
from pathlib import Path

from .. import config, db
from ..vault import safe_slug, make_frontmatter, fetch_unit_content, find_pooled_file
from ..models import EventType, PipelineStage


def write_seed(rec: dict) -> Path:
    verdict = rec.get("verdict", {})
    source = rec["source"]
    title = rec["title"]
    slug = safe_slug(f'{rec["unit_id"]}-{title}')
    sub = config.SEED_ROOT / source
    sub.mkdir(parents=True, exist_ok=True)
    out = sub / f"{slug}.md"
    fm = make_frontmatter({
        "type": "seed",
        "source": source,
        "source_path": rec["source_path"],
        "tg_message_time": rec.get("tg_message_time"),
        "unit_id": rec["unit_id"],
        "parsed_at": datetime.now().strftime("%Y-%m-%d"),
        "verdict": verdict.get("verdict"),
        "category": verdict.get("category"),
        "trigger": verdict.get("trigger"),
        "reason": verdict.get("reason"),
        "confidence": verdict.get("confidence"),
        "priority": verdict.get("priority", "normal"),
        "status": "pending",
        "title": title,
    })
    body = fetch_unit_content(rec)
    out.write_text(fm + f"\n# {title}\n\n" + body, encoding="utf-8")
    return out


def write_asset(rec: dict) -> Path:
    verdict = rec.get("verdict", {})
    category = verdict.get("category") or "other"
    safe_cat = safe_slug(category, 30)
    sub = config.ASSET_ROOT / safe_cat
    sub.mkdir(parents=True, exist_ok=True)
    title = rec["title"]
    slug = safe_slug(f'{rec["unit_id"]}-{title}')
    out = sub / f"{slug}.md"
    fm = make_frontmatter({
        "type": "asset",
        "asset_category": category,
        "source": rec["source"],
        "source_path": rec["source_path"],
        "unit_id": rec["unit_id"],
        "parsed_at": datetime.now().strftime("%Y-%m-%d"),
        "reason": verdict.get("reason"),
        "confidence": verdict.get("confidence"),
        "title": title,
    })
    body = fetch_unit_content(rec)
    summary = body[:300].replace("\n", " | ")
    out.write_text(
        fm + f"\n# {title}\n\n**Reason**: {verdict.get('reason','')}\n\n"
        f"**Preview**: {summary}\n\n[source]({rec['source_path']})\n",
        encoding="utf-8")
    return out


def write_archive(rec: dict) -> Path:
    verdict = rec.get("verdict", {})
    path_str = rec.get("source_path", "")
    m = re.search(r"(20\d{2}-\d{2})", path_str)
    bucket = m.group(1) if m else datetime.now().strftime("%Y-%m")
    sub = config.ARCHIVE_ROOT / bucket
    sub.mkdir(parents=True, exist_ok=True)
    title = rec["title"]
    slug = safe_slug(f'{rec["unit_id"]}-{title}')
    out = sub / f"{slug}.md"
    fm = make_frontmatter({
        "type": "archive",
        "source": rec["source"],
        "source_path": rec["source_path"],
        "unit_id": rec["unit_id"],
        "parsed_at": datetime.now().strftime("%Y-%m-%d"),
        "verdict": "archive",
        "category": verdict.get("category"),
        "reason": verdict.get("reason"),
        "title": title,
    })
    out.write_text(fm + f"\n# {title}\n\n_metadata only.\n", encoding="utf-8")
    return out


def run(rewrite: bool = False, db_path: Path = None) -> dict:
    items = db.query_items(limit=100000, db_path=db_path)
    classified = [i for i in items if i.get("verdict") in ("seed", "asset", "archive")]
    run_id = db.create_run(stage=PipelineStage.POOL.value, db_path=db_path)
    counters = {"seed": 0, "asset": 0, "archive": 0, "skip_existing": 0, "error": 0}
    for item in classified:
        rec = {**item, "verdict": {
            "verdict": item["verdict"],
            "category": item.get("category", ""),
            "trigger": item.get("trigger", ""),
            "reason": item.get("reason", ""),
            "confidence": item.get("confidence", ""),
            "priority": item.get("priority", "normal"),
        }}
        if not rewrite and find_pooled_file(item["unit_id"], item["title"]):
            counters["skip_existing"] += 1
            continue
        try:
            if item["verdict"] == "seed":
                write_seed(rec)
                counters["seed"] += 1
            elif item["verdict"] == "asset":
                write_asset(rec)
                counters["asset"] += 1
            elif item["verdict"] == "archive":
                write_archive(rec)
                counters["archive"] += 1
            db.update_item_status(item["unit_id"], "pooled", db_path=db_path)
            db.log_event(EventType.ITEM_POOLED.value, item_id=item["unit_id"],
                         payload={"verdict": item["verdict"]}, db_path=db_path)
        except Exception as e:
            counters["error"] += 1
            print(f"[write_back] err: {e} on {item['unit_id']}")
    db.complete_run(run_id, artifacts=json.dumps(counters), db_path=db_path)
    print(f"[write_back] {counters}")
    return counters
