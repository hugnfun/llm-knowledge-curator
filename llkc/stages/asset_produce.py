"""Asset production stage - generates image prompts from polished draft."""

import json
import re
from pathlib import Path

from .. import config, db
from ..llm_client import call_llm, extract_json
from ..models import EventType, PipelineStage

IMAGE_PLACEHOLDER_RE = re.compile(r"图\s*(\d+)\s*[:：]\s*(.+?)(?=图\s*\d+|$)", re.DOTALL)


def extract_image_placeholders(body: str) -> list[dict]:
    """Extract '图 N: description' placeholders from draft body."""
    matches = []
    for m in IMAGE_PLACEHOLDER_RE.finditer(body):
        matches.append({"index": int(m.group(1)), "desc": m.group(2).strip()[:100]})
    return matches


def generate_image_prompts(draft: dict, db_path: Path = None) -> dict:
    """Call LLM to generate detailed image prompts for each placeholder."""
    body = draft.get("body", "")
    placeholders = extract_image_placeholders(body)
    if not placeholders:
        return {"ok": True, "prompts": [], "note": "no image placeholders found"}

    user_msg = (
        f"# 小绿书配图 Prompt 生成\n\n"
        f"## 文章标题\n{draft.get('headline', '')}\n\n"
        f"## 文章正文\n{body[:800]}\n\n"
        f"## 图片占位\n"
    )
    for p in placeholders:
        user_msg += f"- 图 {p['index']}: {p['desc']}\n"
    user_msg += (
        f"\n为每张图生成一个详细的图片生成 prompt(英文+中文),"
        f"包含风格、构图、色调、主体描述。\n"
        f"只返回 JSON 数组: [{{\"index\": 1, \"prompt_en\": \"...\", \"prompt_zh\": \"...\"}}]"
    )
    messages = [
        {"role": "system", "content": "你是小绿书配图策划,擅长把文字描述转成可视化图片 prompt。"},
        {"role": "user", "content": user_msg},
    ]
    result = call_llm(messages, temperature=0.5, max_tokens=1500, timeout=60, max_retry=2)
    if not result["ok"]:
        return {"ok": False, "error": result.get("error")}
    try:
        prompts = extract_json(result["text"])
        if isinstance(prompts, list):
            return {"ok": True, "prompts": prompts}
        elif isinstance(prompts, dict) and "prompts" in prompts:
            return {"ok": True, "prompts": prompts["prompts"]}
        else:
            return {"ok": True, "prompts": [prompts]}
    except Exception as e:
        return {"ok": False, "error": f"json: {e}", "raw": result["text"][:200]}


def run(draft_id: str, db_path: Path = None) -> dict:
    """Generate image prompts for a polished draft."""
    all_drafts = db.get_drafts(db_path=db_path)
    draft = next((d for d in all_drafts if d["id"] == draft_id), None)
    if not draft:
        return {"ok": False, "error": f"draft {draft_id} not found"}
    if draft.get("status") not in ("polished", "selected", "candidate"):
        return {"ok": False, "error": f"draft status is {draft.get('status')}"}

    result = generate_image_prompts(draft, db_path)
    if not result.get("ok"):
        return result

    # Log event
    db.log_event(EventType.DRAFT_GENERATED.value,
                 item_id=draft_id,
                 payload={"action": "asset_produce", "count": len(result["prompts"])},
                 db_path=db_path)

    return {
        "ok": True,
        "draft_id": draft_id,
        "image_count": len(result["prompts"]),
        "prompts": result["prompts"],
    }
