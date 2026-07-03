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
    user_msg = (
        f"# Daily Thinking ({target_date})\n\n"
        f"## Free Write\n\n{free_write}\n\n"
        f"## Seeds\n\n{seeds_section}\n\n---\n\n"
        f"Generate 4 angle drafts per writer_v0.1 spec."
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
    body = draft.get("draft", "")
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


def run(target_date: str = None, model: str = None, force: bool = False,
        allow_empty: bool = False, db_path: Path = None) -> dict:
    target_date = target_date or date.today().isoformat()
    daily_doc = config.THINKING_ROOT / f"{target_date}.md"
    if not daily_doc.exists():
        return {"ok": False, "error": f"{daily_doc} not found, run daily_thinking first"}

    text = daily_doc.read_text(encoding="utf-8")
    free_write = extract_free_write(text)
    seeds_section = extract_seeds_section(text)

    if not free_write and not allow_empty:
        return {"ok": False, "error": "free write is empty, use allow_empty=True to proceed"}

    run_id = db.create_run(thinking_date=target_date,
                           stage=PipelineStage.DRAFT_GENERATE.value, db_path=db_path)
    t0 = time.time()
    result = generate_drafts(free_write, seeds_section, target_date, model, db_path)
    elapsed = time.time() - t0

    if not result["ok"]:
        db.fail_run(run_id, result.get("error", "unknown"), db_path=db_path)
        return result

    drafts = result["drafts"]
    if not isinstance(drafts, list):
        db.fail_run(run_id, f"expected list, got {type(drafts)}", db_path=db_path)
        return {"ok": False, "error": "LLM returned non-array"}

    for d in drafts:
        d["date"] = target_date
        d["status"] = DraftStatus.CANDIDATE.value
        db.insert_draft(d, db_path=db_path)

    drafts_dir = config.THINKING_ROOT / f"{target_date}-drafts"
    drafts_dir.mkdir(parents=True, exist_ok=True)
    for d in drafts:
        aid = d.get("angle_id", "X")
        md = render_draft_md(d, target_date)
        (drafts_dir / f"draft-{aid}.md").write_text(md, encoding="utf-8")

    db.log_event(EventType.DRAFT_GENERATED.value, run_id=run_id,
                 thinking_date=target_date,
                 payload={"count": len(drafts)}, db_path=db_path)
    db.complete_run(run_id, artifacts=str(drafts_dir), db_path=db_path)

    return {"ok": True, "drafts": len(drafts), "path": str(drafts_dir),
            "elapsed": round(elapsed, 1),
            "tokens": result.get("usage", {}).get("total_tokens")}
