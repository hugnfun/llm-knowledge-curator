"""FastAPI server - REST API for the content factory GUI."""

import json
from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import config, db
from ..models import STAGE_ORDER, PipelineStage
from ..connectors import obsidian_inbox
from ..connectors import url_ingest
from ..stages import parser as parser_stage
from ..stages import write_back as write_back_stage
from ..stages import daily_thinking as daily_thinking_stage
from ..stages import writer as writer_stage
from .. import pipeline

app = FastAPI(title="LLM Knowledge Curator", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    config.ensure_dirs()
    db.init_db()
    db.fail_stale_runs()


@app.get("/")
def root():
    return FileResponse(str(Path(__file__).parent.parent.parent / "web" / "index.html"))


# --- Items ---

class ItemVerdictUpdate(BaseModel):
    verdict: str
    category: str = ""
    trigger: str = ""
    reason: str = ""
    confidence: str = ""
    priority: str = "normal"
    summary: str = ""
    tags: list[str] = []


class ItemStatusUpdate(BaseModel):
    status: str


class OverrideRequest(BaseModel):
    verdict: str
    reason: str = ""


@app.get("/api/items")
def list_items(verdict: Optional[str] = None, source: Optional[str] = None,
               status: Optional[str] = None, priority: Optional[str] = None,
               limit: int = Query(100, le=1000), offset: int = 0):
    items = db.query_items(verdict=verdict, source=source, status=status,
                           priority=priority, limit=limit, offset=offset)
    return {"items": items, "count": len(items)}


@app.get("/api/items/{unit_id}")
def get_item(unit_id: str):
    item = db.get_item(unit_id)
    if not item:
        raise HTTPException(404, "item not found")
    events = db.query_events(item_id=unit_id, limit=20)
    return {"item": item, "events": events}


@app.patch("/api/items/{unit_id}/verdict")
def patch_item_verdict(unit_id: str, body: ItemVerdictUpdate):
    db.update_item_verdict(unit_id, body.verdict, body.category, body.trigger,
                           body.reason, body.confidence, body.priority,
                           summary=body.summary, tags=body.tags)
    db.log_event("Item.Classified", item_id=unit_id,
                 payload={"verdict": body.verdict, "manual": True})
    return {"ok": True}


@app.patch("/api/items/{unit_id}/status")
def patch_item_status(unit_id: str, body: ItemStatusUpdate):
    db.update_item_status(unit_id, body.status)
    return {"ok": True}


@app.post("/api/items/{unit_id}/override")
def override_item(unit_id: str, body: OverrideRequest):
    db.override_verdict(unit_id, body.verdict, body.reason)
    db.log_event("Item.Override", item_id=unit_id,
                 payload={"new_verdict": body.verdict, "reason": body.reason})
    return {"ok": True}


# --- Pipeline ---

class PipelineRunRequest(BaseModel):
    action: str  # "incremental" | "classify" | "pool" | "daily_thinking" | "writer"
    date: Optional[str] = None
    n_seeds: int = 5
    force: bool = False
    model: Optional[str] = None
    allow_empty: bool = False


@app.get("/api/pipeline/runs")
def list_runs(stage: Optional[str] = None, status: Optional[str] = None,
              limit: int = Query(50, le=500)):
    runs = db.query_runs(stage=stage, status=status, limit=limit)
    return {"runs": runs, "count": len(runs)}


@app.get("/api/pipeline/runs/{run_id}")
def get_run_detail(run_id: str):
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    return {"run": run, "events": db.query_events(run_id=run_id, limit=50)}


@app.post("/api/pipeline/run")
def run_pipeline(body: PipelineRunRequest):
    if body.action == "incremental":
        result = pipeline.run_incremental()
    elif body.action == "classify":
        result = parser_stage.run()
    elif body.action == "pool":
        result = write_back_stage.run()
    elif body.action == "daily_thinking":
        result = daily_thinking_stage.run(target_date=body.date,
                                          n_seeds=body.n_seeds, force=body.force)
    elif body.action == "writer":
        result = writer_stage.run(target_date=body.date, model=body.model,
                                   force=body.force, allow_empty=body.allow_empty)
    else:
        raise HTTPException(400, f"unknown action: {body.action}")
    return {"ok": result.get("ok", True), "result": result}


@app.get("/api/pipeline/overview")
def pipeline_overview():
    return pipeline.get_pipeline_overview()


@app.get("/api/pipeline/stages")
def list_stages():
    return {"stages": [s.value for s in STAGE_ORDER]}


# --- Daily Thinking ---

class FreeWriteUpdate(BaseModel):
    free_write: str


class DailyThinkingRequest(BaseModel):
    date: Optional[str] = None
    n_seeds: int = 5
    force: bool = False


@app.get("/api/daily-thinking")
def list_daily_thinking(limit: int = 30):
    return {"entries": db.list_daily_thinking(limit=limit)}


@app.get("/api/daily-thinking/{target_date}")
def get_daily_thinking(target_date: str):
    entry = db.get_daily_thinking(target_date)
    if not entry:
        raise HTTPException(404, "not found")
    seed_ids = json.loads(entry.get("seed_ids") or "[]")
    seeds = []
    for sid in seed_ids:
        item = db.get_item(sid)
        if item:
            seeds.append(item)
    return {"entry": entry, "seeds": seeds}


@app.post("/api/daily-thinking/generate")
def generate_daily_thinking(body: DailyThinkingRequest):
    result = daily_thinking_stage.run(target_date=body.date, n_seeds=body.n_seeds,
                                      force=body.force)
    return result


@app.patch("/api/daily-thinking/{target_date}/free-write")
def update_free_write(target_date: str, body: FreeWriteUpdate):
    entry = db.get_daily_thinking(target_date)
    if not entry:
        # No thinking session yet — create one with empty seeds so free-write is not lost
        db.upsert_daily_thinking(target_date, [], free_write=body.free_write, status="draft")
    else:
        db.update_free_write(target_date, body.free_write)
    # Sync free-write back to the Obsidian vault markdown file
    sync_error = ""
    try:
        _sync_free_write_to_vault(target_date, body.free_write)
    except Exception as exc:
        sync_error = str(exc)
    db.log_event("UserThinking.Submitted",
                 payload={"char_count": len(body.free_write), "date": target_date,
                          "vault_synced": not bool(sync_error)})
    return {"ok": True, "vault_synced": not bool(sync_error),
            "sync_error": sync_error or None}


# --- Drafts ---

class DraftGenerateRequest(BaseModel):
    date: Optional[str] = None
    model: Optional[str] = None
    force: bool = False
    allow_empty: bool = False


class DraftStatusUpdate(BaseModel):
    status: str



# --- Daily Brief ---

@app.get("/api/daily-brief")
def list_daily_brief(limit: int = Query(30, le=100)):
    briefs = db.list_daily_brief(limit=limit)
    return {"briefs": briefs}


@app.get("/api/daily-brief/{target_date}")
def get_daily_brief(target_date: str):
    brief = db.get_daily_brief(target_date)
    if not brief:
        raise HTTPException(404, "brief not found")
    raw = json.loads(brief.get("raw_json") or "{}") if brief.get("raw_json") else {}
    return {"brief": brief, "parsed": raw}


@app.post("/api/daily-brief/generate")
def generate_daily_brief(target_date: Optional[str] = None):
    from ..stages import daily_brief
    result = daily_brief.run(target_date=target_date)
    return result


@app.get("/api/drafts")
def list_drafts(date: Optional[str] = None, status: Optional[str] = None):
    return {"drafts": db.get_drafts(date=date, status=status)}


@app.post("/api/drafts/generate")
def generate_drafts(body: DraftGenerateRequest):
    result = writer_stage.run(target_date=body.date, model=body.model,
                               force=body.force, allow_empty=body.allow_empty)
    return result


@app.patch("/api/drafts/{draft_id}/status")
def patch_draft_status(draft_id: str, body: DraftStatusUpdate):
    db.update_draft_status(draft_id, body.status)
    db.log_event("Draft.Selected", payload={"draft_id": draft_id, "status": body.status})
    # Auto-trigger polish when status -> selected
    if body.status == "selected":
        try:
            from ..stages import polish as polish_stage
            result = polish_stage.polish_draft(draft_id)
            return {"ok": True, "status": body.status, "polish": result}
        except Exception as e:
            return {"ok": True, "status": body.status, "polish_error": str(e)}
    return {"ok": True}


@app.post("/api/drafts/{draft_id}/polish")
def polish_draft_endpoint(draft_id: str):
    from ..stages import polish as polish_stage
    result = polish_stage.polish_draft(draft_id)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "polish failed"))
    return result


# --- Stats ---

@app.get("/api/stats")
def get_stats():
    stats = db.get_stats()
    return stats


# --- Inbox ---

@app.get("/api/inbox/scan")
def scan_inbox():
    units = obsidian_inbox.scan_inbox(persist=True)
    summary = obsidian_inbox.write_index(units)
    return {"units": len(units), "summary": summary}


# --- URL Ingest ---

class UrlIngestRequest(BaseModel):
    url: str
    note: Optional[str] = None
    auto_classify: bool = False


@app.post("/api/ingest/url")
def ingest_url(body: UrlIngestRequest):
    res = url_ingest.ingest_url(body.url)
    if not res.ok:
        return {"ok": False, "error": res.error, "source_type": res.source_type}
    # Optionally trigger classify on this unit right away
    if body.auto_classify and res.unit_id:
        try:
            parser_stage.run()
        except Exception as e:
            return {**res.to_dict(), "classify_error": str(e)}
    return res.to_dict()


@app.get("/api/ingest/classify_url")
def classify_url_only(url: str):
    return {"url": url, "source_type": url_ingest.classify_url(url)}


# --- Health ---

@app.get("/api/health")
def health():
    queue = db.count_pending_urls()
    dead_urls = db.query_pending_urls(status="dead", limit=10)
    return {
        "status": "ok",
        "vault": str(config.VAULT_ROOT),
        "db": str(config.DB_PATH),
        "prompts": {
            "parser": config.PARSER_PROMPT_PATH.exists(),
            "writer": config.WRITER_PROMPT_PATH.exists(),
        },
        "pending_url_queue": queue,
        "dead_urls": [
            {"id": d["id"], "url": d["url"], "attempts": d["attempts"],
             "last_error": (d.get("last_error") or "")[:200]}
            for d in dead_urls
        ],
    }


@app.get("/api/pending-urls")
def list_pending_urls(status: Optional[str] = None, limit: int = Query(100, le=500)):
    """List pending URL queue entries for monitoring."""
    return {"urls": db.query_pending_urls(status=status, limit=limit)}


_web_root = Path(__file__).parent.parent.parent / "web"


def _sync_free_write_to_vault(target_date: str, free_write: str):
    """Update or create the daily-thinking markdown file in the Obsidian vault."""
    import re
    doc_path = config.THINKING_ROOT / f"{target_date}.md"
    if doc_path.exists():
        text = doc_path.read_text(encoding="utf-8")
        # Replace content between "## Free Write" and the next "---" separator
        pattern = r"(##\s*Free Write\s*\n)(.*?)(\n---\s*\n)"
        if re.search(pattern, text, re.DOTALL):
            text = re.sub(
                pattern,
                lambda match: match.group(1) + free_write + match.group(3),
                text,
                flags=re.DOTALL,
            )
        else:
            # Append Free Write section if missing
            text += f"\n## Free Write\n\n{free_write}\n"
        doc_path.write_text(text, encoding="utf-8")
    else:
        # Create a minimal daily thinking doc
        config.THINKING_ROOT.mkdir(parents=True, exist_ok=True)
        doc = (
            "---\n"
            "type: daily_thinking\n"
            f"date: {target_date}\n"
            "status: draft\n"
            "---\n\n"
            f"# Daily Thinking - {target_date}\n\n"
            "## Free Write\n\n"
            f"{free_write}\n"
        )
        doc_path.write_text(doc, encoding="utf-8")


if _web_root.exists():
    app.mount("/static", StaticFiles(directory=str(_web_root)), name="static")
