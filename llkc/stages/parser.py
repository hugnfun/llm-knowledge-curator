"""Parser stage - classifies inbox items using LLM. Migrates parser_runner.py."""

import json
import concurrent.futures as cf
import threading
import time
from pathlib import Path

from .. import config, db
from ..llm_client import call_llm, extract_json
from ..vault import fetch_unit_content
from ..models import EventType, PipelineStage

SYSTEM_PROMPT = ""
_lock = threading.Lock()


def _load_prompt() -> str:
    global SYSTEM_PROMPT
    if not SYSTEM_PROMPT and config.PARSER_PROMPT_PATH.exists():
        SYSTEM_PROMPT = config.PARSER_PROMPT_PATH.read_text(encoding="utf-8")
    return SYSTEM_PROMPT


def classify_unit(unit: dict) -> dict:
    try:
        content = fetch_unit_content(unit)
    except Exception as e:
        return {"unit_id": unit["unit_id"], "ok": False, "error": f"read: {e}"}
    truncated = False
    if len(content) > config.PARSER_MAX_INPUT_CHARS:
        content = content[:config.PARSER_MAX_INPUT_CHARS]
        truncated = True
    messages = [
        {"role": "system", "content": _load_prompt()},
        {"role": "user", "content": (
            f"# \u5f85\u5224\u522b\u5355\u5143\n\n"
            f"- source: {unit['source']}\n"
            f"- title: {unit['title']}\n"
            f"- source_path: {unit['source_path']}\n"
            f"- char_len: {unit['char_len']}\n\n"
            f"## \u5185\u5bb9\n\n{content}\n\n---\n\n"
            f"\u6309 v0.2 \u89c4\u8303\u5224\u522b,**\u53ea\u8fd4\u56de\u4e00\u4e2a JSON \u5bf9\u8c61**"
        )},
    ]
    result = call_llm(messages, temperature=0.2, max_tokens=800,
                      timeout=config.PARSER_TIMEOUT, max_retry=config.PARSER_MAX_RETRY)
    if not result["ok"]:
        return {"unit_id": unit["unit_id"], "ok": False, "error": result.get("error")}
    try:
        verdict = extract_json(result["text"])
    except Exception as e:
        return {"unit_id": unit["unit_id"], "ok": False,
                "error": f"json: {e}", "raw": result["text"][:200]}
    return {"unit_id": unit["unit_id"], "ok": True, "verdict": verdict, "truncated": truncated}


def _persist_verdict(unit: dict, result: dict):
    v = result["verdict"]
    db.update_item_verdict(
        unit_id=unit["unit_id"],
        verdict=v.get("verdict", "archive"),
        category=v.get("category", ""),
        trigger=v.get("trigger", ""),
        reason=v.get("reason", ""),
        confidence=v.get("confidence", "medium"),
        priority=v.get("priority", "normal"),
    )
    db.log_event(EventType.ITEM_CLASSIFIED.value, item_id=unit["unit_id"],
                 payload={"verdict": v.get("verdict"), "category": v.get("category")})


def run(concurrency: int = None, db_path: Path = None) -> dict:
    concurrency = concurrency or config.PARSER_CONCURRENCY
    items = db.query_items(verdict="pending", limit=100000, db_path=db_path)
    if not items:
        print("[parser] no pending items")
        return {"ok": 0, "fail": 0}
    run_id = db.create_run(stage=PipelineStage.CLASSIFY.value, db_path=db_path)
    t0 = time.time()
    counters = {"ok": 0, "fail": 0, "seed": 0, "asset": 0, "archive": 0}
    with cf.ThreadPoolExecutor(max_workers=concurrency) as ex:
        futures = {ex.submit(classify_unit, u): u for u in items}
        for i, fut in enumerate(cf.as_completed(futures), 1):
            unit = futures[fut]
            res = fut.result()
            if res["ok"]:
                counters["ok"] += 1
                v = res["verdict"].get("verdict", "other")
                counters[v] = counters.get(v, 0) + 1
                _persist_verdict(unit, res)
            else:
                counters["fail"] += 1
                with _lock:
                    err_path = config.OUTPUT_DIR / "parser_errors.jsonl"
                    with err_path.open("a", encoding="utf-8") as f:
                        f.write(json.dumps({"unit_id": unit["unit_id"],
                                            **res}, ensure_ascii=False) + "\n")
            if i % 20 == 0 or i == len(items):
                elapsed = time.time() - t0
                rate = i / elapsed if elapsed else 0
                eta = (len(items) - i) / rate if rate else 0
                print(f"[parser] {i}/{len(items)} ok={counters['ok']} "
                      f"fail={counters['fail']} eta={eta:.0f}s")
    db.complete_run(run_id, artifacts=json.dumps(counters), db_path=db_path)
    print(f"[parser] done in {time.time()-t0:.1f}s: {counters}")
    return counters
