"""Long-lived worker that drains the pending-URL queue every N seconds.

Designed to run as a LaunchAgent (com.llkc.pending-url-worker) alongside the
lark-url-listener.  The listener captures URLs into the SQLite queue; this
worker ingests them into the Obsidian inbox and triggers classification.

Architecture
------------
    [Feishu bot] -> lark-listener -> pending_urls (SQLite queue)
                                           |
                                     pending_worker (this file)
                                           |
              +----------------------------+----------------------------+
              |                            |                            |
        url_ingest.ingest_url        parser_stage.run()         write_back_stage.run()
        (fetch + write inbox)        (LLM classify)              (pool to vault)

The 06:00 cron (cron_incremental_v2.sh) remains as a safety-net backup.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from .. import config, db
from . import pending_urls

LOG = logging.getLogger("pending_worker")

DEFAULT_INTERVAL = int(os.environ.get("LLKC_WORKER_INTERVAL", "20"))
DEFAULT_LIMIT = int(os.environ.get("LLKC_PENDING_URL_LIMIT", "20"))
DEFAULT_MAX_ATTEMPTS = int(os.environ.get("LLKC_PENDING_URL_MAX_ATTEMPTS", "3"))
DEFAULT_STALE_SECONDS = int(os.environ.get("LLKC_PENDING_URL_STALE_SECONDS", "3600"))

# Only one classification pass at a time.
_classify_lock = threading.Lock()
_stop_event = threading.Event()


def _setup_logging():
    """Configure stderr logging so LaunchAgent logs are readable."""
    if not LOG.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        LOG.addHandler(handler)
    LOG.setLevel(logging.INFO)


def _trigger_classify(db_path: Optional[Path] = None) -> bool:
    """Run classification + pooling in background if not already running.

    Returns True if a classify pass was started, False if skipped.
    """
    if not _classify_lock.acquire(blocking=False):
        LOG.debug("classify already in progress, skipping")
        return False

    def _do_classify():
        try:
            from ..stages import parser as parser_stage
            from ..stages import write_back as write_back_stage

            pending = db.query_items(verdict="pending", limit=100000, db_path=db_path)
            if not pending:
                LOG.info("no pending items to classify")
                return
            LOG.info("classifying %d pending items", len(pending))
            result = parser_stage.run(db_path=db_path)
            LOG.info("classify done: %s", json.dumps(result, ensure_ascii=False))
            pool_result = write_back_stage.run(db_path=db_path)
            LOG.info("pool done: %s", json.dumps(pool_result, ensure_ascii=False))
        except Exception as exc:
            LOG.error("classify/pool failed: %s", exc, exc_info=True)
        finally:
            _classify_lock.release()

    t = threading.Thread(target=_do_classify, daemon=True, name="classify")
    t.start()
    return True


def _check_dead_urls(db_path: Optional[Path] = None) -> list[dict]:
    """Return dead URLs (exhausted retries) and log an alert event."""
    dead = db.query_pending_urls(status="dead", limit=100, db_path=db_path)
    if dead:
        LOG.warning("%d dead URLs in queue (exhausted retries)", len(dead))
        for d in dead:
            db.log_event(
                "PendingURL.Dead",
                payload={
                    "pending_url_id": d["id"],
                    "url": d["url"],
                    "attempts": d["attempts"],
                    "last_error": d.get("last_error", ""),
                },
                db_path=db_path,
            )
    return dead


def _cycle(db_path: Optional[Path] = None) -> dict:
    """Run one poll cycle: drain queue, trigger classify if needed."""
    summary = pending_urls.run(
        limit=DEFAULT_LIMIT,
        max_attempts=DEFAULT_MAX_ATTEMPTS,
        stale_after_seconds=DEFAULT_STALE_SECONDS,
        db_path=db_path,
    )
    if summary["completed"] > 0:
        LOG.info("ingested %d URL(s), triggering classify", summary["completed"])
        _trigger_classify(db_path=db_path)
    if summary["failed"] > 0 or summary["dead"] > 0:
        LOG.warning("cycle result: %s", json.dumps(summary, ensure_ascii=False))
    _check_dead_urls(db_path=db_path)
    return summary


def run_worker(
    *,
    interval: int = DEFAULT_INTERVAL,
    once: bool = False,
    db_path: Optional[Path] = None,
) -> dict:
    """Run the worker loop until SIGTERM/SIGINT or once mode.

    Returns a final stats dict.
    """
    _setup_logging()
    config.ensure_dirs()
    db.init_db(db_path)
    db.fail_stale_runs(db_path=db_path)

    def _handle_signal(signum, _frame):
        name = signal.Signals(signum).name
        LOG.info("received %s, shutting down gracefully", name)
        _stop_event.set()

    previous_term = signal.getsignal(signal.SIGTERM)
    previous_int = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    cycle_count = 0
    total_completed = 0
    total_failed = 0
    total_dead = 0

    LOG.info(
        "pending-url worker started (interval=%ds, once=%s, db=%s)",
        interval, once, db_path or config.DB_PATH,
    )

    try:
        while not _stop_event.is_set():
            cycle_count += 1
            try:
                summary = _cycle(db_path=db_path)
                total_completed += summary.get("completed", 0)
                total_failed += summary.get("failed", 0)
                total_dead += summary.get("dead", 0)
            except Exception as exc:
                LOG.error("cycle %d failed: %s", cycle_count, exc, exc_info=True)

            if once:
                break

            # Sleep in small increments so SIGTERM is responsive.
            _stop_event.wait(interval)
    finally:
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGINT, previous_int)

    final = {
        "cycles": cycle_count,
        "completed": total_completed,
        "failed": total_failed,
        "dead": total_dead,
        "queue": db.count_pending_urls(db_path=db_path),
    }
    LOG.info("worker stopped: %s", json.dumps(final, ensure_ascii=False))
    return final
