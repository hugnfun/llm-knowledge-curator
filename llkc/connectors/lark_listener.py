"""Consume Feishu bot message events and enqueue shared URLs for later ingest."""

from __future__ import annotations

import html
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import TextIO
from urllib.parse import urlsplit, urlunsplit

from .. import db


EVENT_KEY = "im.message.receive_v1"
URL_RE = re.compile(r"https?://[^\s<>\"'\[\]{}\u4e00-\u9fff]+", re.IGNORECASE)
TRAILING_PUNCTUATION = ".,;:!?，。；：！？、】》）)]}"
DEFAULT_MESSAGE_TYPES = {"text", "post"}


def _csv_env(name: str) -> set[str]:
    return {value.strip() for value in os.environ.get(name, "").split(",") if value.strip()}


def extract_urls(content: str) -> list[str]:
    """Extract unique HTTP(S) URLs from human-readable Feishu message text."""
    seen = set()
    urls = []
    for match in URL_RE.finditer(html.unescape(content or "")):
        url = match.group(0).rstrip(TRAILING_PUNCTUATION)
        parts = urlsplit(url)
        if not parts.hostname or url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def normalize_url(url: str) -> str:
    """Normalize only stable URL components; preserve signed query strings."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    hostname = (parts.hostname or "").lower()
    if not scheme or not hostname:
        raise ValueError(f"invalid URL: {url}")
    port = parts.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        hostname = f"{hostname}:{port}"
    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    return urlunsplit((scheme, hostname, path, parts.query, ""))


def capture_event(
    event: dict,
    *,
    db_path: Path | None = None,
    allowed_chat_ids: set[str] | None = None,
    allowed_sender_ids: set[str] | None = None,
    allowed_message_types: set[str] | None = None,
) -> dict:
    """Validate one flattened ``im.message.receive_v1`` event and enqueue URLs."""
    allowed_chat_ids = allowed_chat_ids if allowed_chat_ids is not None else _csv_env(
        "LLKC_LARK_CHAT_IDS"
    )
    allowed_sender_ids = allowed_sender_ids if allowed_sender_ids is not None else _csv_env(
        "LLKC_LARK_SENDER_IDS"
    )
    allowed_message_types = allowed_message_types or DEFAULT_MESSAGE_TYPES

    message_type = str(event.get("message_type") or "")
    chat_id = str(event.get("chat_id") or "")
    sender_id = str(event.get("sender_id") or "")
    if message_type not in allowed_message_types:
        return {"captured": 0, "duplicates": 0, "ignored": "message_type"}
    if allowed_chat_ids and chat_id not in allowed_chat_ids:
        return {"captured": 0, "duplicates": 0, "ignored": "chat_id"}
    if allowed_sender_ids and sender_id not in allowed_sender_ids:
        return {"captured": 0, "duplicates": 0, "ignored": "sender_id"}

    captured = []
    duplicates = []
    for url in extract_urls(str(event.get("content") or "")):
        normalized = normalize_url(url)
        row, created = db.enqueue_pending_url(
            url,
            normalized,
            source="lark",
            source_event_id=str(event.get("event_id") or ""),
            source_message_id=str(event.get("message_id") or event.get("id") or ""),
            source_create_time=str(event.get("create_time") or event.get("timestamp") or ""),
            chat_id=chat_id,
            sender_id=sender_id,
            db_path=db_path,
        )
        target = captured if created else duplicates
        target.append({"id": row["id"], "url": row["url"]})
        if created:
            db.log_event(
                "PendingURL.Captured",
                payload={
                    "pending_url_id": row["id"],
                    "url": url,
                    "source": "lark",
                    "source_event_id": event.get("event_id", ""),
                    "source_message_id": event.get("message_id") or event.get("id", ""),
                    "chat_id": chat_id,
                    "sender_id": sender_id,
                },
                db_path=db_path,
            )
    return {
        "captured": len(captured),
        "duplicates": len(duplicates),
        "urls": captured,
        "duplicate_urls": duplicates,
    }


def _stderr_reader(stream: TextIO, ready: threading.Event, lines: list[str]) -> None:
    for raw_line in stream:
        line = raw_line.rstrip("\n")
        lines.append(line)
        if len(lines) > 100:
            del lines[:-100]
        print(line, file=sys.stderr, flush=True)
        if line.startswith(f"[event] ready event_key={EVENT_KEY}"):
            ready.set()


def run_listener(
    *,
    lark_cli: str | None = None,
    max_events: int = 0,
    timeout: str = "",
    ready_timeout: float = 30,
    db_path: Path | None = None,
) -> dict:
    """Run the long-lived lark-cli consumer until exit or interruption."""
    command = [
        lark_cli or os.environ.get("LLKC_LARK_CLI", "lark-cli"),
        "event", "consume", EVENT_KEY, "--as", "bot",
    ]
    if max_events:
        command.extend(["--max-events", str(max_events)])
    if timeout:
        command.extend(["--timeout", timeout])

    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"lark-cli not found: {command[0]}") from exc

    assert process.stdout is not None
    assert process.stderr is not None
    ready = threading.Event()
    stderr_lines: list[str] = []
    stderr_thread = threading.Thread(
        target=_stderr_reader,
        args=(process.stderr, ready, stderr_lines),
        daemon=True,
    )
    stderr_thread.start()

    deadline = time.monotonic() + ready_timeout
    while not ready.wait(0.1):
        if process.poll() is not None:
            detail = "\n".join(stderr_lines[-20:]) or f"exit code {process.returncode}"
            raise RuntimeError(f"Feishu event listener failed before ready:\n{detail}")
        if time.monotonic() >= deadline:
            process.terminate()
            raise RuntimeError(f"Feishu event listener was not ready within {ready_timeout}s")

    stats = {"events": 0, "captured": 0, "duplicates": 0, "invalid": 0}
    try:
        for line in process.stdout:
            if not line.strip():
                continue
            stats["events"] += 1
            try:
                event = json.loads(line)
                result = capture_event(event, db_path=db_path)
            except Exception as exc:
                stats["invalid"] += 1
                print(f"[lark-listener] event skipped: {exc}", file=sys.stderr, flush=True)
                continue
            stats["captured"] += result.get("captured", 0)
            stats["duplicates"] += result.get("duplicates", 0)
            print(json.dumps({"event": stats["events"], **result}, ensure_ascii=False), flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        if process.poll() is None and process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        try:
            return_code = process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.terminate()
            return_code = process.wait(timeout=5)
        stderr_thread.join(timeout=1)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()

    if return_code != 0:
        detail = "\n".join(stderr_lines[-20:])
        raise RuntimeError(f"Feishu event listener exited with {return_code}:\n{detail}")
    return stats
