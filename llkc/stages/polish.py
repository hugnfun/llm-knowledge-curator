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
