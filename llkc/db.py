"""SQLite state layer -- replaces flat JSONL files with a real runtime database."""

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    unit_id     TEXT PRIMARY KEY,
    source      TEXT NOT NULL,
    source_path TEXT,
    abs_path    TEXT,
    title       TEXT,
    preview     TEXT,
    char_len    INTEGER DEFAULT 0,
    tg_message_idx   INTEGER,
    tg_message_time  TEXT,
    verdict     TEXT DEFAULT 'pending',
    category    TEXT,
    trigger     TEXT,
    reason      TEXT,
    confidence  TEXT,
    priority    TEXT DEFAULT 'normal',
    status      TEXT DEFAULT 'pending',
    parsed_at   TEXT,
    pooled_at   TEXT,
    raw_content TEXT,
    summary     TEXT,
    tags        TEXT
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id              TEXT PRIMARY KEY,
    item_id         TEXT,
    thinking_date   TEXT,
    stage           TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    started_at      TEXT,
    completed_at    TEXT,
    duration_sec    REAL,
    error           TEXT,
    artifacts       TEXT,
    idempotency_key TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     TEXT,
    item_id    TEXT,
    event_type TEXT NOT NULL,
    payload    TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS daily_thinking (
    date       TEXT PRIMARY KEY,
    seed_ids   TEXT,
    free_write TEXT,
    status     TEXT DEFAULT 'draft',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS drafts (
    id            TEXT PRIMARY KEY,
    thinking_date TEXT,
    angle_id      TEXT,
    angle_name    TEXT,
    headline      TEXT,
    body          TEXT,
    hook          TEXT,
    image_count   INTEGER DEFAULT 0,
    linked_seeds  TEXT,
    status        TEXT DEFAULT 'candidate',
    created_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS overrides (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    unit_id    TEXT NOT NULL,
    old_verdict TEXT,
    new_verdict TEXT,
    reason     TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS pending_urls (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    url                TEXT NOT NULL,
    normalized_url     TEXT NOT NULL UNIQUE,
    source             TEXT NOT NULL DEFAULT 'lark',
    source_event_id    TEXT,
    source_message_id  TEXT,
    source_create_time TEXT,
    chat_id            TEXT,
    sender_id          TEXT,
    captured_at        TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'pending',
    attempts           INTEGER NOT NULL DEFAULT 0,
    last_error         TEXT,
    item_id            TEXT,
    processed_at       TEXT
);

CREATE INDEX IF NOT EXISTS idx_items_verdict ON items(verdict);
CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);
CREATE INDEX IF NOT EXISTS idx_items_source ON items(source);
CREATE INDEX IF NOT EXISTS idx_runs_stage ON pipeline_runs(stage);
CREATE INDEX IF NOT EXISTS idx_runs_status ON pipeline_runs(status);
CREATE INDEX IF NOT EXISTS idx_runs_item ON pipeline_runs(item_id);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id);
CREATE INDEX IF NOT EXISTS idx_events_item ON events(item_id);
CREATE INDEX IF NOT EXISTS idx_drafts_date ON drafts(thinking_date);
CREATE INDEX IF NOT EXISTS idx_pending_urls_status ON pending_urls(status, captured_at);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db(db_path: Optional[Path] = None) -> Path:
    path = db_path or config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with _get_conn(path) as c:
        try:
            c.executescript(SCHEMA)
        except Exception:
            pass  # indexes may fail on old schemas; migration below fixes columns
        cols = {r[1] for r in c.execute("PRAGMA table_info(items)")}
        if "summary" not in cols:
            c.execute("ALTER TABLE items ADD COLUMN summary TEXT")
        if "tags" not in cols:
            c.execute("ALTER TABLE items ADD COLUMN tags TEXT")
    return path


@contextmanager
def get_conn(db_path: Optional[Path] = None):
    c = sqlite3.connect(str(db_path or config.DB_PATH))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    try:
        yield c
        c.commit()
    finally:
        c.close()


_get_conn = get_conn


def row_to_dict(row) -> Optional[dict]:
    return dict(row) if row else None


# --- Items ---

def upsert_item(c: sqlite3.Connection, item: dict):
    c.execute("""
        INSERT INTO items (unit_id, source, source_path, abs_path, title, preview,
                           char_len, tg_message_idx, tg_message_time, verdict,
                           category, trigger, reason, confidence, priority, status,
                           parsed_at, pooled_at, raw_content)
        VALUES (:unit_id, :source, :source_path, :abs_path, :title, :preview,
                :char_len, :tg_message_idx, :tg_message_time, :verdict,
                :category, :trigger, :reason, :confidence, :priority, :status,
                :parsed_at, :pooled_at, :raw_content)
        ON CONFLICT(unit_id) DO UPDATE SET
            source=excluded.source, source_path=excluded.source_path,
            abs_path=excluded.abs_path, title=excluded.title, preview=excluded.preview,
            char_len=excluded.char_len, tg_message_idx=excluded.tg_message_idx,
            tg_message_time=excluded.tg_message_time, verdict=excluded.verdict,
            category=excluded.category, trigger=excluded.trigger,
            reason=excluded.reason, confidence=excluded.confidence,
            priority=excluded.priority, raw_content=excluded.raw_content
    """, item)


def get_item(unit_id: str, db_path: Optional[Path] = None) -> Optional[dict]:
    with get_conn(db_path) as c:
        r = c.execute("SELECT * FROM items WHERE unit_id=?", (unit_id,)).fetchone()
        return row_to_dict(r)


def query_items(verdict=None, source=None, status=None, priority=None,
                limit=100, offset=0, db_path=None) -> list[dict]:
    sql = "SELECT * FROM items WHERE 1=1"
    params = []
    if verdict:
        sql += " AND verdict=?"; params.append(verdict)
    if source:
        sql += " AND source=?"; params.append(source)
    if status:
        sql += " AND status=?"; params.append(status)
    if priority:
        sql += " AND priority=?"; params.append(priority)
    sql += " ORDER BY priority DESC, unit_id LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    with get_conn(db_path) as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def count_items(db_path=None) -> dict:
    with get_conn(db_path) as c:
        return {r["verdict"]: r["cnt"]
                for r in c.execute("SELECT verdict, COUNT(*) cnt FROM items GROUP BY verdict")}


def update_item_verdict(unit_id, verdict, category="", trigger="", reason="",
                        confidence="", priority="normal", summary="", tags=None,
                        db_path=None):
    import json as _json
    tags_str = _json.dumps(tags, ensure_ascii=False) if tags else ""
    with get_conn(db_path) as c:
        c.execute("""UPDATE items SET verdict=?, category=?, trigger=?, reason=?,
                     confidence=?, priority=?, summary=?, tags=?, parsed_at=? WHERE unit_id=?""",
                  (verdict, category, trigger, reason, confidence, priority,
                   summary, tags_str, _now()[:10], unit_id))



def update_item_status(unit_id, status, db_path=None):
    with get_conn(db_path) as c:
        c.execute("UPDATE items SET status=? WHERE unit_id=?", (status, unit_id))


def override_verdict(unit_id, new_verdict, reason="", db_path=None):
    old = get_item(unit_id, db_path)
    old_v = old["verdict"] if old else ""
    cat = old.get("category", "") if old else ""
    pri = old.get("priority", "normal") if old else "normal"
    update_item_verdict(unit_id, new_verdict, category=cat, priority=pri, db_path=db_path)
    with get_conn(db_path) as c:
        c.execute("INSERT INTO overrides (unit_id, old_verdict, new_verdict, reason) VALUES (?,?,?,?)",
                  (unit_id, old_v, new_verdict, reason))


# --- Pipeline runs ---

def create_run(item_id=None, thinking_date=None, stage="collect",
                idempotency_key=None, db_path=None) -> str:
    run_id = uuid.uuid4().hex[:12]
    with get_conn(db_path) as c:
        c.execute("""INSERT INTO pipeline_runs
            (id, item_id, thinking_date, stage, status, started_at, idempotency_key)
            VALUES (?,?,?,?, 'running', ?, ?)""",
                  (run_id, item_id, thinking_date, stage, _now(), idempotency_key))
    return run_id


def complete_run(run_id, artifacts=None, db_path=None):
    with get_conn(db_path) as c:
        r = c.execute("SELECT started_at FROM pipeline_runs WHERE id=?", (run_id,)).fetchone()
        dur = None
        if r and r["started_at"]:
            try:
                started = datetime.fromisoformat(r["started_at"])
                dur = (datetime.now(timezone.utc) - started).total_seconds()
            except Exception:
                pass
        c.execute("""UPDATE pipeline_runs SET status='done', completed_at=?,
                     duration_sec=?, artifacts=? WHERE id=?""",
                  (_now(), dur, artifacts, run_id))


def fail_run(run_id, error, db_path=None):
    with get_conn(db_path) as c:
        c.execute("UPDATE pipeline_runs SET status='failed', completed_at=?, error=? WHERE id=?",
                  (_now(), error, run_id))


def query_runs(stage=None, status=None, limit=50, db_path=None) -> list[dict]:
    sql = "SELECT * FROM pipeline_runs WHERE 1=1"
    params = []
    if stage:
        sql += " AND stage=?"; params.append(stage)
    if status:
        sql += " AND status=?"; params.append(status)
    sql += " ORDER BY started_at DESC LIMIT ?"
    params.append(limit)
    with get_conn(db_path) as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def get_run(run_id: str, db_path=None) -> Optional[dict]:
    with get_conn(db_path) as c:
        row = c.execute("SELECT * FROM pipeline_runs WHERE id=?", (run_id,)).fetchone()
        return row_to_dict(row)


def fail_stale_runs(stale_after_seconds=3600, db_path=None) -> int:
    """Close runs abandoned by a crashed worker after a conservative timeout."""
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=stale_after_seconds)).isoformat()
    now = _now()
    with get_conn(db_path) as c:
        cursor = c.execute(
            """UPDATE pipeline_runs
               SET status='failed', completed_at=?,
                   error=CASE WHEN error IS NULL OR error=''
                              THEN 'recovered stale running task' ELSE error END
               WHERE status='running' AND started_at < ?""",
            (now, cutoff),
        )
        return cursor.rowcount


# --- Events ---

def log_event(event_type, run_id=None, item_id=None, payload=None, db_path=None):
    with get_conn(db_path) as c:
        c.execute("INSERT INTO events (run_id, item_id, event_type, payload) VALUES (?,?,?,?)",
                  (run_id, item_id, event_type,
                   json.dumps(payload, ensure_ascii=False) if payload else None))


def query_events(run_id=None, item_id=None, limit=50, db_path=None) -> list[dict]:
    sql = "SELECT * FROM events WHERE 1=1"
    params = []
    if run_id:
        sql += " AND run_id=?"; params.append(run_id)
    if item_id:
        sql += " AND item_id=?"; params.append(item_id)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with get_conn(db_path) as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


# --- Pending URL capture queue ---

def enqueue_pending_url(url, normalized_url, source="lark", source_event_id="",
                        source_message_id="", source_create_time="", chat_id="",
                        sender_id="", db_path=None) -> tuple[dict, bool]:
    """Idempotently enqueue a URL and return ``(row, was_created)``."""
    with get_conn(db_path) as c:
        cursor = c.execute(
            """INSERT OR IGNORE INTO pending_urls
               (url, normalized_url, source, source_event_id, source_message_id,
                source_create_time, chat_id, sender_id, captured_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (url, normalized_url, source, source_event_id, source_message_id,
             source_create_time, chat_id, sender_id, _now()),
        )
        row = c.execute(
            "SELECT * FROM pending_urls WHERE normalized_url=?", (normalized_url,),
        ).fetchone()
        return dict(row), cursor.rowcount == 1


def query_pending_urls(status=None, limit=100, db_path=None) -> list[dict]:
    sql = "SELECT * FROM pending_urls"
    params = []
    if status:
        sql += " WHERE status=?"
        params.append(status)
    sql += " ORDER BY captured_at, id LIMIT ?"
    params.append(limit)
    with get_conn(db_path) as c:
        return [dict(row) for row in c.execute(sql, params).fetchall()]


def claim_pending_urls(limit=20, max_attempts=3, stale_after_seconds=3600,
                       db_path=None) -> list[dict]:
    """Atomically claim retryable URLs and recover stale processing rows."""
    now = datetime.now(timezone.utc)
    stale_before = (now - timedelta(seconds=stale_after_seconds)).isoformat()
    with get_conn(db_path) as c:
        c.execute("BEGIN IMMEDIATE")
        c.execute(
            """UPDATE pending_urls
               SET status='failed', last_error='recovered stale processing task'
               WHERE status='processing' AND processed_at < ?""",
            (stale_before,),
        )
        candidates = c.execute(
            """SELECT id FROM pending_urls
               WHERE status IN ('pending', 'failed') AND attempts < ?
               ORDER BY captured_at, id LIMIT ?""",
            (max_attempts, limit),
        ).fetchall()
        if not candidates:
            return []
        ids = [row["id"] for row in candidates]
        placeholders = ",".join("?" for _ in ids)
        c.execute(
            f"""UPDATE pending_urls
                SET status='processing', attempts=attempts+1,
                    last_error=NULL, processed_at=?
                WHERE id IN ({placeholders})""",
            (_now(), *ids),
        )
        return [dict(row) for row in c.execute(
            f"SELECT * FROM pending_urls WHERE id IN ({placeholders}) ORDER BY captured_at, id",
            ids,
        ).fetchall()]


def complete_pending_url(pending_url_id, item_id="", db_path=None):
    with get_conn(db_path) as c:
        c.execute(
            """UPDATE pending_urls
               SET status='completed', item_id=?, last_error=NULL, processed_at=?
               WHERE id=?""",
            (item_id, _now(), pending_url_id),
        )


def fail_pending_url(pending_url_id, error, max_attempts=3, db_path=None) -> str:
    """Record a failure and return the resulting ``failed`` or ``dead`` status."""
    with get_conn(db_path) as c:
        row = c.execute(
            "SELECT attempts FROM pending_urls WHERE id=?", (pending_url_id,),
        ).fetchone()
        if not row:
            raise KeyError(f"pending URL not found: {pending_url_id}")
        status = "dead" if row["attempts"] >= max_attempts else "failed"
        c.execute(
            """UPDATE pending_urls
               SET status=?, last_error=?, processed_at=? WHERE id=?""",
            (status, str(error)[:2000], _now(), pending_url_id),
        )
        return status


def count_pending_urls(db_path=None) -> dict[str, int]:
    with get_conn(db_path) as c:
        return {
            row["status"]: row["count"]
            for row in c.execute(
                "SELECT status, COUNT(*) AS count FROM pending_urls GROUP BY status"
            ).fetchall()
        }


# --- Daily thinking ---

def upsert_daily_thinking(date, seed_ids, free_write="", status="draft", db_path=None):
    with get_conn(db_path) as c:
        c.execute("""INSERT INTO daily_thinking (date, seed_ids, free_write, status)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                seed_ids=excluded.seed_ids, free_write=excluded.free_write,
                status=excluded.status, updated_at=datetime('now')""",
                  (date, json.dumps(seed_ids, ensure_ascii=False), free_write, status))


def get_daily_thinking(date, db_path=None) -> Optional[dict]:
    with get_conn(db_path) as c:
        return row_to_dict(c.execute("SELECT * FROM daily_thinking WHERE date=?", (date,)).fetchone())


def update_free_write(date, free_write, db_path=None):
    with get_conn(db_path) as c:
        c.execute("UPDATE daily_thinking SET free_write=?, updated_at=datetime('now') WHERE date=?",
                  (free_write, date))


def list_daily_thinking(limit=30, db_path=None) -> list[dict]:
    with get_conn(db_path) as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM daily_thinking ORDER BY date DESC LIMIT ?", (limit,)).fetchall()]


# --- Drafts ---

def insert_draft(draft: dict, db_path=None):
    with get_conn(db_path) as c:
        c.execute("""INSERT OR REPLACE INTO drafts
            (id, thinking_date, angle_id, angle_name, headline, body, hook,
             image_count, linked_seeds, status)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
                  (draft.get("id") or uuid.uuid4().hex[:12],
                   draft.get("date", ""), draft.get("angle_id", ""),
                  draft.get("angle_name", ""), draft.get("headline", ""),
                   draft.get("body") or draft.get("draft", ""), draft.get("hook", ""),
                   draft.get("image_count", 0),
                   json.dumps(draft.get("linked_seeds", []), ensure_ascii=False),
                   draft.get("status", "candidate")))


def get_drafts(date=None, status=None, db_path=None) -> list[dict]:
    sql = "SELECT * FROM drafts WHERE 1=1"
    params = []
    if date:
        sql += " AND thinking_date=?"; params.append(date)
    if status:
        sql += " AND status=?"; params.append(status)
    sql += " ORDER BY created_at DESC"
    with get_conn(db_path) as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def delete_drafts(date: str, db_path=None) -> int:
    with get_conn(db_path) as c:
        cursor = c.execute("DELETE FROM drafts WHERE thinking_date=?", (date,))
        return cursor.rowcount


def update_draft_status(draft_id, status, db_path=None):
    with get_conn(db_path) as c:
        c.execute("UPDATE drafts SET status=? WHERE id=?", (status, draft_id))


# --- Stats ---

def get_stats(db_path=None) -> dict:
    with get_conn(db_path) as c:
        verdict_counts = {r["verdict"]: r["cnt"]
            for r in c.execute("SELECT verdict, COUNT(*) cnt FROM items GROUP BY verdict")}
        status_counts = {r["status"]: r["cnt"]
            for r in c.execute("SELECT status, COUNT(*) cnt FROM items GROUP BY status")}
        source_counts = {r["source"]: r["cnt"]
            for r in c.execute("SELECT source, COUNT(*) cnt FROM items GROUP BY source")}
        run_counts = {r["status"]: r["cnt"]
            for r in c.execute("SELECT status, COUNT(*) cnt FROM pipeline_runs GROUP BY status")}
        thinking_count = c.execute("SELECT COUNT(*) c FROM daily_thinking").fetchone()["c"]
        draft_count = c.execute("SELECT COUNT(*) c FROM drafts").fetchone()["c"]
        override_count = c.execute("SELECT COUNT(*) c FROM overrides").fetchone()["c"]
        return {
            "items": verdict_counts,
            "total_items": sum(verdict_counts.values()),
            "item_status": status_counts,
            "sources": source_counts,
            "pipeline_runs": run_counts,
            "daily_thinking_count": thinking_count,
            "draft_count": draft_count,
            "override_count": override_count,
        }
