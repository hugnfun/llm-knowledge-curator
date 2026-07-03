"""Daily thinking stage - generates daily thinking templates. Migrates daily_thinking.py."""

import random
from datetime import date
from pathlib import Path

from .. import config, db
from ..vault import parse_frontmatter, safe_slug
from ..models import EventType, PipelineStage, ThinkingStatus


def load_seeds_from_vault() -> list[dict]:
    seeds = []
    if not config.SEED_ROOT.exists():
        return seeds
    for f in config.SEED_ROOT.rglob("*.md"):
        try:
            text = f.read_text(encoding="utf-8")
            fm = parse_frontmatter(text)
            if not fm:
                continue
            seeds.append({
                "path": str(f),
                "rel_path": str(f.relative_to(config.VAULT_ROOT)),
                "unit_id": fm.get("unit_id", "?"),
                "title": fm.get("title", f.stem),
                "source": fm.get("source", "?"),
                "category": fm.get("category", "?"),
                "trigger": fm.get("trigger", ""),
                "reason": fm.get("reason", ""),
                "priority": fm.get("priority", "normal"),
                "parsed_at": fm.get("parsed_at", ""),
            })
        except Exception:
            pass
    return seeds


def load_seeds_from_db(db_path: Path = None) -> list[dict]:
    items = db.query_items(verdict="seed", status="pooled", limit=100000, db_path=db_path)
    return [{
        "unit_id": i["unit_id"],
        "title": i["title"],
        "source": i["source"],
        "category": i.get("category", "?"),
        "trigger": i.get("trigger", ""),
        "reason": i.get("reason", ""),
        "priority": i.get("priority", "normal"),
    } for i in items]


def diversified_sample(seeds: list[dict], n: int,
                        per_cat_cap: int = 2, per_src_cap: int = 2) -> list[dict]:
    if len(seeds) <= n:
        return list(seeds)
    cap_cat, cap_src = per_cat_cap, per_src_cap
    while True:
        pool = seeds.copy()
        random.shuffle(pool)
        picked = []
        cat_count, src_count = {}, {}
        for s in pool:
            c, src = s["category"], s["source"]
            if cat_count.get(c, 0) >= cap_cat:
                continue
            if src_count.get(src, 0) >= cap_src:
                continue
            picked.append(s)
            cat_count[c] = cat_count.get(c, 0) + 1
            src_count[src] = src_count.get(src, 0) + 1
            if len(picked) >= n:
                break
        if len(picked) >= n:
            return picked
        cap_cat += 1
        cap_src += 1


def format_seed_block(s: dict) -> str:
    link_target = s.get("rel_path", s.get("unit_id", "")).replace(".md", "")
    title_disp = s["title"][:60]
    prio = " *" if s.get("priority") == "high" else ""
    return (
        f"### {s['unit_id']} - {s['source']} - {s['category']}{prio}\n\n"
        f"**Title**: {title_disp}\n\n"
        f"**Trigger**: {s.get('trigger', '')}\n\n"
        f"**Reason**: {s.get('reason', '')}\n\n"
        f"**Source**: [[{link_target}|open]]\n"
    )


def make_thinking_doc(seeds: list[dict], target_date: str) -> str:
    unit_ids = [s["unit_id"] for s in seeds]
    sources = sorted(set(s["source"] for s in seeds))
    categories = sorted(set(s["category"] for s in seeds))
    fm = (
        "---\n"
        "type: daily_thinking\n"
        f"date: {target_date}\n"
        f"seeds: [{', '.join(repr(u) for u in unit_ids)}]\n"
        f"seed_sources: [{', '.join(repr(s) for s in sources)}]\n"
        f"seed_categories: [{', '.join(repr(c) for c in categories)}]\n"
        "status: draft\n"
        "---\n"
    )
    body = (
        f"\n# Daily Thinking - {target_date}\n\n"
        f"## Free Write\n\n"
        f"<!-- write your thoughts here -->\n\n"
        f"\n---\n\n"
        f"## Today's 5 Seeds\n\n"
    )
    blocks = []
    for i, s in enumerate(seeds, 1):
        blocks.append(f"## {i}/5\n\n" + format_seed_block(s))
    return fm + body + "\n\n---\n\n".join(blocks) + "\n"


def run(target_date: str = None, n_seeds: int = 5, force: bool = False,
        seed_val: int = None, db_path: Path = None) -> dict:
    target_date = target_date or date.today().isoformat()
    if seed_val is not None:
        random.seed(seed_val)

    target = config.THINKING_ROOT / f"{target_date}.md"
    if target.exists() and not force:
        return {"ok": False, "error": f"{target.name} exists, use force=True to overwrite"}

    seeds = load_seeds_from_db(db_path)
    if len(seeds) < n_seeds:
        seeds = load_seeds_from_vault()
    if len(seeds) < n_seeds:
        return {"ok": False, "error": f"only {len(seeds)} seeds available, need {n_seeds}"}

    sample = diversified_sample(seeds, n_seeds)
    doc = make_thinking_doc(sample, target_date)

    config.THINKING_ROOT.mkdir(parents=True, exist_ok=True)
    target.write_text(doc, encoding="utf-8")

    seed_ids = [s["unit_id"] for s in sample]
    db.upsert_daily_thinking(target_date, seed_ids, status="draft", db_path=db_path)

    run_id = db.create_run(thinking_date=target_date,
                           stage=PipelineStage.DAILY_THINKING.value, db_path=db_path)
    db.log_event(EventType.DAILY_THINKING_REQUESTED.value, run_id=run_id,
                 payload={"date": target_date, "seed_ids": seed_ids}, db_path=db_path)
    db.complete_run(run_id, artifacts=str(target), db_path=db_path)

    return {"ok": True, "path": str(target), "seeds": len(sample),
            "seed_ids": seed_ids}
