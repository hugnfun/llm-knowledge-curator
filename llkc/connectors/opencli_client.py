"""Small, testable wrapper around the OpenCLI subprocess interface.

Xiaohongshu exposes note/detail and media-download adapters. Douyin does not
currently expose a single-video detail command, so that one gap is handled via
an owned ``opencli browser`` session and a read-only DOM evaluation.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any


OPENCLI_BIN = os.environ.get("LLKC_OPENCLI_BIN", "opencli")
OPENCLI_TIMEOUT = int(os.environ.get("LLKC_OPENCLI_TIMEOUT", "180"))
OPENCLI_RETRIES = int(os.environ.get("LLKC_OPENCLI_RETRIES", "3"))
OPENCLI_RETRY_DELAY = float(os.environ.get("LLKC_OPENCLI_RETRY_DELAY", "5"))


def _is_bridge_down(message: str) -> bool:
    lowered = message.lower()
    return any(marker in lowered for marker in (
        "not running",
        "unable to find application",
        "browserbridge.sock",
        "opencliapp",
        "failed to connect to browser bridge",
    ))


def _start_bridge() -> None:
    """Best-effort background launch of OpenCLIApp on macOS."""
    if shutil.which("open"):
        subprocess.run(
            ["open", "-gj", "-a", "OpenCLIApp"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        time.sleep(3)


def _decode_json(output: str) -> Any:
    """Decode the first JSON value while tolerating CLI notices around it."""
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for index, char in enumerate(output):
        # Every OpenCLI call in this module returns an object or an array. Only
        # scanning for container starts avoids mistaking an update notice such
        # as "v1.8.4" for the command's JSON value.
        if char not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(output[index:])
            return value
        except json.JSONDecodeError:
            continue
    raise json.JSONDecodeError("no JSON value found", output, 0)


def _run(
    args: list[str],
    *,
    timeout: int | None = None,
    expect_json: bool = True,
) -> Any:
    """Run one OpenCLI command with bounded retries.

    Adapter commands accept the universal ``-f json`` flag. Raw browser
    commands do not, and already emit JSON (or a plain string) themselves.
    """
    if not args:
        raise ValueError("opencli args must not be empty")

    command = [OPENCLI_BIN, *args]
    if args[0] != "browser":
        command.extend(["-f", "json"])

    last_error = ""
    for attempt in range(1, OPENCLI_RETRIES + 1):
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout or OPENCLI_TIMEOUT,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"OpenCLI binary not found: {OPENCLI_BIN}") from exc
        except subprocess.TimeoutExpired as exc:
            last_error = f"timed out after {timeout or OPENCLI_TIMEOUT}s"
            if attempt < OPENCLI_RETRIES:
                time.sleep(OPENCLI_RETRY_DELAY)
                continue
            raise RuntimeError(f"opencli {' '.join(args)} {last_error}") from exc

        if proc.returncode != 0:
            last_error = (proc.stderr or proc.stdout or "unknown error").strip()[-1000:]
            if _is_bridge_down(last_error):
                _start_bridge()
            if attempt < OPENCLI_RETRIES:
                time.sleep(OPENCLI_RETRY_DELAY)
                continue
            raise RuntimeError(f"opencli {' '.join(args)} failed: {last_error}")

        output = (proc.stdout or "").strip()
        if not output:
            return None
        if not expect_json:
            return output
        try:
            return _decode_json(output)
        except json.JSONDecodeError as exc:
            last_error = f"returned invalid JSON: {output[-500:]}"
            if attempt < OPENCLI_RETRIES:
                time.sleep(OPENCLI_RETRY_DELAY)
                continue
            raise RuntimeError(f"opencli {' '.join(args)} {last_error}") from exc

    raise RuntimeError(f"opencli {' '.join(args)} exhausted retries: {last_error}")


def xhs_note(note_url: str) -> dict[str, Any]:
    """Fetch and flatten Xiaohongshu note metadata."""
    rows = _run(["xiaohongshu", "note", note_url])
    if not rows:
        raise RuntimeError("opencli xiaohongshu note returned no content")
    if isinstance(rows, dict):
        return rows
    if not isinstance(rows, list):
        raise RuntimeError("opencli xiaohongshu note returned an unexpected shape")
    return {
        str(row.get("field", "")): row.get("value", "")
        for row in rows
        if isinstance(row, dict) and row.get("field")
    }


def xhs_download(note_url: str, output_dir: Path | str) -> list[dict[str, Any]]:
    """Download all media from a Xiaohongshu note."""
    rows = _run([
        "xiaohongshu", "download", note_url,
        "--output", str(output_dir),
    ])
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise RuntimeError("opencli xiaohongshu download returned an unexpected shape")
    return rows


def _browser_eval(session: str, javascript: str) -> Any:
    return _run(["browser", session, "eval", javascript])


def douyin_video_detail(video_url: str, session: str | None = None) -> dict[str, Any]:
    """Read one public Douyin video from its rendered detail page."""
    session = session or f"llkc-douyin-{uuid.uuid4().hex[:10]}"
    opened = False
    try:
        _run(["browser", session, "open", video_url])
        opened = True
        javascript = r"""
(() => {
  const text = (selector) =>
    (document.querySelector(selector)?.textContent || '').replace(/\s+/g, ' ').trim();
  const e2e = {};
  document.querySelectorAll('[data-e2e]').forEach((element) => {
    const key = element.getAttribute('data-e2e');
    const value = (element.textContent || '').replace(/\s+/g, ' ').trim();
    if (key && value && !e2e[key]) e2e[key] = value;
  });

  const video = document.querySelector('video');
  const source = video?.querySelector('source');
  const authorLink = document.querySelector('a[href*="/user/"]');
  const related = e2e['related-video'] || '';
  const relatedAuthor = related.match(/^(.+?)(?:粉丝|获赞|关注)/)?.[1] || '';
  const pathname = location.pathname.replace(/\/$/, '');

  return {
    aweme_id: pathname.split('/').pop() || '',
    desc: e2e['detail-video-info'] || text('h1') || '',
    author: text('[data-e2e="detail-video-author-name"]')
      || (authorLink?.textContent || '').trim()
      || relatedAuthor,
    likes: e2e['video-player-digg'] || '',
    comments: e2e['feed-comment-icon'] || '',
    collects: e2e['video-player-collect'] || '',
    shares: e2e['video-player-share'] || '',
    publish_time: e2e['detail-video-publish-time'] || '',
    video_url: source?.src || video?.currentSrc || video?.src || ''
  };
})()
""".strip()
        detail = _browser_eval(session, javascript)
        if isinstance(detail, str):
            try:
                detail = json.loads(detail)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Douyin detail was not JSON: {detail[:200]}") from exc
        if not isinstance(detail, dict):
            raise RuntimeError("OpenCLI returned an unexpected Douyin detail shape")
        if not detail.get("desc") and not detail.get("video_url"):
            raise RuntimeError("Douyin page loaded without accessible video content")
        return detail
    finally:
        if opened:
            try:
                _run(["browser", session, "close"], expect_json=False, timeout=15)
            except Exception:
                pass


def download_video(video_url: str, destination: Path | str) -> None:
    """Download a direct video URL extracted from an authenticated page."""
    import httpx

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136 Safari/537.36"
        ),
        "Referer": "https://www.douyin.com/",
    }
    with httpx.stream(
        "GET", video_url, headers=headers, follow_redirects=True, timeout=180,
    ) as response:
        response.raise_for_status()
        with destination.open("wb") as handle:
            for chunk in response.iter_bytes():
                handle.write(chunk)
