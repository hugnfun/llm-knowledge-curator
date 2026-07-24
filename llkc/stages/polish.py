"""Polish stage - takes a selected draft and polishes it via LLM."""

import json
from pathlib import Path

from .. import config, db
from ..llm_client import call_llm, extract_json
from ..models import EventType, PipelineStage


def polish_draft(draft_id: str, db_path: Path = None) -> dict:
    draft = db.get_draft(draft_id, db_path=db_path) if hasattr(db, "get_draft") else None
    if not draft:
        # Fallback: query drafts and find by id
        all_drafts = db.get_drafts(db_path=db_path)
        draft = next((d for d in all_drafts if d["id"] == draft_id), None)
    if not draft:
        return {"ok": False, "error": f"draft {draft_id} not found"}
    if draft.get("status") not in ("selected", "candidate"):
        return {"ok": False, "error": f"draft status is {draft.get('status')}, need selected or candidate"}

    prompt = config.POLISH_PROMPT_PATH.read_text(encoding="utf-8")
    user_msg = (
        f"# 待润色 Draft\n\n"
        f"- angle_name: {draft.get('angle_name', '')}\n"
        f"- angle_id: {draft.get('angle_id', '')}\n"
        f"- image_count: {draft.get('image_count', 0)}\n\n"
        f"## Headline\n{draft.get('headline', '')}\n\n"
        f"## Body\n{draft.get('body', '')}\n\n"
        f"## Hook\n{draft.get('hook', '')}\n\n"
        f"---\n\n按 polish_v0.1 规范润色,**只返回一个 JSON 对象**"
    )
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": user_msg},
    ]
    result = call_llm(
        messages, temperature=0.3, max_tokens=1200,
        timeout=config.WRITER_TIMEOUT, max_retry=2,
    )
    if not result["ok"]:
        return {"ok": False, "error": result.get("error")}
    try:
        polished = extract_json(result["text"])
    except Exception as e:
        return {"ok": False, "error": f"json: {e}", "raw": result["text"][:200]}

    # Store polished version, preserving original body
    db.update_draft_polish(
        draft_id,
        headline=polished.get("headline", draft.get("headline", "")),
        body=polished.get("body", draft.get("body", "")),
        hook=polished.get("hook", draft.get("hook", "")),
        original_body=draft.get("body", ""),
        db_path=db_path,
    )
    db.log_event(EventType.DRAFT_GENERATED.value,
                 item_id=draft_id,
                 payload={"action": "polish", "changes": polished.get("changes", "")},
                 db_path=db_path)

    return {
        "ok": True,
        "draft_id": draft_id,
        "headline": polished.get("headline", ""),
        "body": polished.get("body", "")[:100] + "...",
        "hook": polished.get("hook", ""),
        "changes": polished.get("changes", ""),
    }


def run(draft_id: str = None, db_path: Path = None) -> dict:
    if not draft_id:
        # Auto-polish all selected drafts
        selected = db.get_drafts(status="selected", db_path=db_path)
        if not selected:
            return {"ok": False, "error": "no selected drafts to polish"}
        results = []
        for d in selected:
            r = polish_draft(d["id"], db_path)
            results.append(r)
        return {"ok": True, "polished": len([r for r in results if r.get("ok")]),
                "failed": len([r for r in results if not r.get("ok")]),
                "results": results}
    return polish_draft(draft_id, db_path)


def publish_draft(draft_id: str, db_path: Path = None) -> dict:
    """Archive a published draft: save original + final + diff to vault."""
    import difflib
    from .. import config
    all_drafts = db.get_drafts(db_path=db_path)
    draft = next((d for d in all_drafts if d["id"] == draft_id), None)
    if not draft:
        return {"ok": False, "error": f"draft {draft_id} not found"}

    original = draft.get("original_body") or draft.get("body", "")
    final = draft.get("body", "")
    headline = draft.get("headline", "")

    # Generate diff
    diff_lines = list(difflib.unified_diff(
        original.splitlines(keepends=True),
        final.splitlines(keepends=True),
        fromfile="original", tofile="polished", n=1
    ))
    diff_text = "".join(diff_lines) if diff_lines else "(no changes)"

    # Write to vault
    config.PUBLISHED_ROOT.mkdir(parents=True, exist_ok=True)
    safe_date = draft.get("thinking_date", "")
    aid = draft.get("angle_id", "X")
    slug = f"{safe_date}-{aid}" if safe_date else draft_id
    out = config.PUBLISHED_ROOT / f"{slug}.md"

    fm = (
        "---\n"
        f"type: published\n"
        f"draft_id: {draft_id}\n"
        f"date: {safe_date}\n"
        f"angle: {draft.get('angle_name', '')}\n"
        f"headline: {headline}\n"
        "---\n"
    )
    body = (
        f"\n# {headline}\n\n"
        f"## 终稿\n\n{final}\n\n"
        f"---\n\n"
        f"## 原稿\n\n{original}\n\n"
        f"---\n\n"
        f"## Diff\n\n```diff\n{diff_text}\n```\n"
    )
    out.write_text(fm + body, encoding="utf-8")

    db.update_draft_status(draft_id, "published", db_path=db_path)
    db.log_event(EventType.DRAFT_GENERATED.value,
                 item_id=draft_id,
                 payload={"action": "publish", "path": str(out)},
                 db_path=db_path)

    return {"ok": True, "draft_id": draft_id, "path": str(out), "diff_lines": len(diff_lines)}
