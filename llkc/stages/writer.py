"""Writer stage - generates 4-angle draft candidates. Migrates writer_agent.py."""

import re
import sys
import time
from datetime import date
from pathlib import Path

from .. import config, db
from ..llm_client import call_llm, extract_json_array
from ..models import EventType, PipelineStage, DraftStatus
from ..vault import parse_frontmatter


FREE_WRITE_RE = re.compile(r"##\s*Free Write\s*\n(.+?)\n##\s*Today", re.DOTALL)
PLACEHOLDER_RE = re.compile(r"^\s*<!--.*?-->\s*$", re.MULTILINE)
ITALIC_HINT_RE = re.compile(r"^\s*_[^_\n]+_\s*$", re.MULTILINE)


def extract_free_write(text: str) -> str:
    m = FREE_WRITE_RE.search(text)
    if not m:
        return ""
    body = m.group(1)
    body = PLACEHOLDER_RE.sub("", body)
    body = ITALIC_HINT_RE.sub("", body)
    body = re.sub(r"^-{3,}\s*$", "", body, flags=re.MULTILINE)
    return body.strip()


def extract_seeds_section(text: str) -> str:
    occurrences = [m.start() for m in re.finditer(r"##\s*Today", text)]
    if len(occurrences) < 2:
        return ""
    return text[occurrences[1]:].strip()


def generate_drafts(free_write: str, seeds_section: str, target_date: str,
                     model: str = None, db_path: Path = None) -> dict:
    system_prompt = config.WRITER_PROMPT_PATH.read_text(encoding="utf-8")

    # Stage 5: inject published drafts as few-shot examples
    few_shot_block = ""
    try:
        published = db.get_drafts(status="published", db_path=db_path)
        if published:
            top = published[:3]
            few_shot_lines = ["\n## 历史终稿参考(few-shot)\n"]
            for p in top:
                few_shot_lines.append(
                    f"### {p.get('angle_name','')}\n"
                    f"**headline**: {p.get('headline','')}\n"
                    f"**body**: {(p.get('body','') or '')[:200]}...\n"
                )
            few_shot_block = "\n".join(few_shot_lines) + "\n"
    except Exception:
        pass

    user_msg = (
        f"# Daily Thinking ({target_date})\n\n"
        f"## Free Write\n\n{free_write}\n\n"
        f"## Seeds\n\n{seeds_section}\n\n"
        f"{few_shot_block}"
        f"---\n\nGenerate 4 angle drafts per writer_v0.1 spec."
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_msg},
    ]
    result = call_llm(
        messages, model=model or config.WRITER_MODEL,
        api_base=config.WRITER_API_BASE, api_key=config.WRITER_API_KEY,
        temperature=0.7, max_tokens=config.WRITER_MAX_TOKENS,
        timeout=config.WRITER_TIMEOUT, max_retry=2,
    )
    if not result["ok"]:
        return {"ok": False, "error": result.get("error")}
    try:
        drafts = extract_json_array(result["text"])
    except Exception as e:
        return {"ok": False, "error": f"json: {e}",
                "raw": result["text"][:300]}
    return {"ok": True, "drafts": drafts, "usage": result.get("usage", {})}


def render_draft_md(draft: dict, target_date: str) -> str:
    angle_id = draft.get("angle_id", "?")
    angle_name = draft.get("angle_name", "")
    headline = draft.get("headline", "")
    body = draft.get("body") or draft.get("draft") or ""
    hook = draft.get("hook", "")
    image_count = draft.get("image_count", 0)
    linked = draft.get("linked_seeds", [])
    fm = (
        "---\n"
        f"type: draft\n"
        f"date: {target_date}\n"
        f"angle_id: {angle_id}\n"
        f"angle_name: {angle_name}\n"
        f"linked_seeds: [{', '.join(repr(x) for x in linked)}]\n"
        f"image_count: {image_count}\n"
        f"status: candidate\n"
        "---\n"
    )
    md = (
        fm
        + f"\n# {angle_id} - {angle_name}\n\n"
        + f"## Hook\n\n> {headline}\n\n"
        + f"## Body\n\n{body}\n\n"
        + f"## Ending\n\n> {hook}\n\n"
        + f"## Linked Seeds\n\n"
    )
    for uid in linked:
        md += f"- [[{uid}]]\n"
    return md


def normalize_drafts(drafts) -> list[dict]:
    """Validate the writer contract and canonicalize model `draft` to DB `body`."""
    if not isinstance(drafts, list):
        raise ValueError(f"expected list, got {type(drafts).__name__}")
    if len(drafts) != 4:
        raise ValueError(f"expected 4 drafts, got {len(drafts)}")

    normalized = []
    for index, draft in enumerate(drafts, 1):
        if not isinstance(draft, dict):
            raise ValueError(f"draft {index} is not an object")
        item = dict(draft)
        item["body"] = item.get("body") or item.get("draft") or ""
        if not item.get("angle_id"):
            raise ValueError(f"draft {index} has no angle_id")
        if not item.get("headline"):
            raise ValueError(f"draft {index} has no headline")
        if not item["body"].strip():
            raise ValueError(f"draft {index} has empty body")
        normalized.append(item)
    return normalized


def run(target_date: str = None, model: str = None, force: bool = False,
        allow_empty: bool = False, db_path: Path = None) -> dict:
    target_date = target_date or date.today().isoformat()
    daily_doc = config.THINKING_ROOT / f"{target_date}.md"

    free_write = ""
    seeds_section = ""

    if daily_doc.exists():
        text = daily_doc.read_text(encoding="utf-8")
        free_write = extract_free_write(text)
        seeds_section = extract_seeds_section(text)

    # DB free_write takes priority (user edits in web UI)
    db_entry = db.get_daily_thinking(target_date, db_path=db_path)
    if db_entry and db_entry.get("free_write"):
        free_write = db_entry["free_write"]

    # Build seeds section from DB if not available from markdown
    if not seeds_section and db_entry:
        import json
        seed_ids = json.loads(db_entry.get("seed_ids") or "[]")
        seed_lines = []
        for sid in seed_ids:
            item = db.get_item(sid, db_path=db_path)
            if item:
                seed_lines.append(
                    f"- **{item.get('title', sid)}** [{item.get('source', '')}]"
                    + (f" — {item['trigger']}" if item.get('trigger') else "")
                )
        seeds_section = "\n".join(seed_lines)

    if not free_write and not allow_empty:
        return {"ok": False, "error": "free write is empty, use allow_empty=True to proceed"}

    existing = db.get_drafts(date=target_date, db_path=db_path)
    if existing and not force:
        return {"ok": False, "error": f"{len(existing)} drafts already exist, use force=True to replace"}

    run_id = db.create_run(thinking_date=target_date,
                           stage=PipelineStage.DRAFT_GENERATE.value, db_path=db_path)
    t0 = time.time()
    try:
        result = generate_drafts(free_write, seeds_section, target_date, model, db_path)
        if not result["ok"]:
            raise RuntimeError(result.get("error", "writer generation failed"))
        drafts = normalize_drafts(result["drafts"])

        if force:
            db.delete_drafts(target_date, db_path=db_path)
        for draft in drafts:
            draft["date"] = target_date
            draft["status"] = DraftStatus.CANDIDATE.value
            db.insert_draft(draft, db_path=db_path)

        drafts_dir = config.DRAFTS_ROOT / target_date
        drafts_dir.mkdir(parents=True, exist_ok=True)
        for draft in drafts:
            aid = draft.get("angle_id", "X")
            md = render_draft_md(draft, target_date)
            (drafts_dir / f"draft-{aid}.md").write_text(md, encoding="utf-8")

        db.log_event(
            EventType.DRAFT_GENERATED.value,
            run_id=run_id,
            payload={"count": len(drafts), "date": target_date},
            db_path=db_path,
        )
        db.complete_run(run_id, artifacts=str(drafts_dir), db_path=db_path)
        elapsed = time.time() - t0
        return {"ok": True, "drafts": len(drafts), "path": str(drafts_dir),
                "elapsed": round(elapsed, 1),
                "tokens": result.get("usage", {}).get("total_tokens")}
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        db.fail_run(run_id, error, db_path=db_path)
        return {"ok": False, "error": error}
