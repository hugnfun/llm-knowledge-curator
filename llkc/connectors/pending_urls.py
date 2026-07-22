"""Drain captured URLs into the regular URL ingest pipeline."""

from __future__ import annotations

import os
from pathlib import Path

from .. import db
from . import url_ingest


DEFAULT_LIMIT = int(os.environ.get("LLKC_PENDING_URL_LIMIT", "20"))
DEFAULT_MAX_ATTEMPTS = int(os.environ.get("LLKC_PENDING_URL_MAX_ATTEMPTS", "3"))
DEFAULT_STALE_SECONDS = int(os.environ.get("LLKC_PENDING_URL_STALE_SECONDS", "3600"))


def run(
    *,
    limit: int = DEFAULT_LIMIT,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    stale_after_seconds: int = DEFAULT_STALE_SECONDS,
    db_path: Path | None = None,
) -> dict:
    """Claim and ingest queued URLs without letting one failure stop the batch."""
    db.init_db(db_path)
    claimed = db.claim_pending_urls(
        limit=limit,
        max_attempts=max_attempts,
        stale_after_seconds=stale_after_seconds,
        db_path=db_path,
    )
    summary = {
        "claimed": len(claimed),
        "completed": 0,
        "failed": 0,
        "dead": 0,
        "items": [],
        "errors": [],
    }

    for pending in claimed:
        pending_id = pending["id"]
        try:
            result = url_ingest.ingest_url(pending["url"], db_path=db_path)
            if not result.ok or result.error:
                raise RuntimeError(result.error or "URL ingest failed without an error message")
        except Exception as exc:
            status = db.fail_pending_url(
                pending_id,
                str(exc),
                max_attempts=max_attempts,
                db_path=db_path,
            )
            summary[status] += 1
            summary["errors"].append({"pending_url_id": pending_id, "error": str(exc)})
            db.log_event(
                "PendingURL.Failed",
                payload={
                    "pending_url_id": pending_id,
                    "attempt": pending["attempts"],
                    "status": status,
                    "error": str(exc),
                },
                db_path=db_path,
            )
            continue

        db.complete_pending_url(pending_id, item_id=result.unit_id, db_path=db_path)
        summary["completed"] += 1
        summary["items"].append({
            "pending_url_id": pending_id,
            "unit_id": result.unit_id,
            "source_type": result.source_type,
            "inbox_path": result.inbox_path,
        })
        db.log_event(
            "PendingURL.Processed",
            item_id=result.unit_id or None,
            payload={
                "pending_url_id": pending_id,
                "source_type": result.source_type,
                "inbox_path": result.inbox_path,
            },
            db_path=db_path,
        )

    summary["queue"] = db.count_pending_urls(db_path=db_path)
    return summary
