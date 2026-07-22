"""URL ingest connector — WeChat / Douyin / XHS / generic URLs to Obsidian inbox."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from .. import config, db
from ..vault import make_frontmatter, safe_slug


WECHAT_CLI = os.environ.get("LLKC_WECHAT_CLI", "wechat-article-to-markdown")
QWEN3TTS_URL = os.environ.get("QWEN3TTS_URL", "http://127.0.0.1:9999")
QWEN3TTS_TIMEOUT = int(os.environ.get("QWEN3TTS_TIMEOUT", "600"))
INGEST_TMP_ROOT = Path(os.environ.get("LLKC_INGEST_TMP", "/tmp/llkc_ingest"))
INGEST_INBOX_DIR = config.INBOX_ROOT / "URL-Ingest"


WECHAT_RE = re.compile(r"mp\.weixin\.qq\.com")
DOUYIN_RE = re.compile(r"(v\.douyin\.com|www\.douyin\.com|iesdouyin\.com)")
TIKTOK_RE = re.compile(r"(www\.tiktok\.com|vm\.tiktok\.com)")
XHS_RE = re.compile(r"(xiaohongshu\.com|xhslink\.com)")


def classify_url(url: str) -> str:
    if WECHAT_RE.search(url):
        return "wechat"
    if DOUYIN_RE.search(url):
        return "douyin"
    if TIKTOK_RE.search(url):
        return "tiktok"
    if XHS_RE.search(url):
        return "xhs"
    return "generic"


@dataclass
class IngestResult:
    ok: bool
    source_type: str = ""
    unit_id: str = ""
    inbox_path: str = ""
    title: str = ""
    has_video: bool = False
    has_transcript: bool = False
    has_images: bool = False
    original_files: list = field(default_factory=list)
    error: str = ""
    debug: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok, "source_type": self.source_type, "unit_id": self.unit_id,
            "inbox_path": self.inbox_path, "title": self.title,
            "has_video": self.has_video, "has_transcript": self.has_transcript,
            "has_images": self.has_images, "original_files": self.original_files,
            "error": self.error, "debug": self.debug,
        }


def _sanitize_title(text, fallback="untitled"):
    if not text:
        return fallback
    text = re.sub(r"\s+", " ", str(text)).strip()
    return text[:80] or fallback


def _new_workdir(prefix: str) -> Path:
    INGEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=prefix + "_", dir=str(INGEST_TMP_ROOT)))


# ---------- qwen3tts transcription ----------

def _qwen3tts_transcribe(video_or_audio: Path) -> tuple:
    """Upload + transcribe via qwen3tts server. Returns (text, meta_dict)."""
    import httpx
    if not video_or_audio.exists():
        raise RuntimeError(f"file not found: {video_or_audio}")

    with httpx.Client(timeout=180) as client:
        with open(video_or_audio, "rb") as fp:
            files = {"file": (video_or_audio.name, fp, "application/octet-stream")}
            r = client.post(f"{QWEN3TTS_URL}/upload", files=files)
        r.raise_for_status()
        up = r.json()
        remote_path = up.get("path") or up.get("input_path") or up.get("filepath") or up.get("filename")
        if not remote_path:
            raise RuntimeError(f"qwen3tts /upload unexpected response: {up}")

        r = client.post(f"{QWEN3TTS_URL}/job/start", json={
            "kind": "transcribe",
            "input_path": remote_path,
            "language": "auto",
        })
        r.raise_for_status()
        job = r.json()
        job_id = job.get("job_id")
        if not job_id:
            raise RuntimeError(f"qwen3tts /job/start returned no job_id: {job}")

    deadline = time.time() + QWEN3TTS_TIMEOUT
    last = None
    with httpx.Client(timeout=30) as client:
        while time.time() < deadline:
            r = client.get(f"{QWEN3TTS_URL}/job/{job_id}")
            r.raise_for_status()
            last = r.json()
            status = last.get("status")
            if status in ("done", "success", "completed"):
                break
            if status in ("failed", "error"):
                raise RuntimeError(f"qwen3tts transcribe failed: {last.get('error')}")
            time.sleep(2)
        else:
            raise RuntimeError("qwen3tts transcribe timed out")

    result = (last or {}).get("result") or {}
    text = result.get("text") or result.get("transcript") or ""
    tpath = result.get("transcript_path") or result.get("output_path") or result.get("filename")
    if not text and tpath:
        p = Path(tpath)
        if p.exists():
            if p.suffix == ".json":
                try:
                    j = json.loads(p.read_text(encoding="utf-8"))
                    text = j.get("text") or ""
                except Exception:
                    text = p.read_text(encoding="utf-8", errors="ignore")
            else:
                text = p.read_text(encoding="utf-8", errors="ignore")
        else:
            try:
                with httpx.Client(timeout=60) as client:
                    r = client.get(f"{QWEN3TTS_URL}/outputs/{p.name}")
                if r.status_code == 200:
                    body = r.content
                    if p.suffix == ".json":
                        try:
                            j = json.loads(body.decode("utf-8", errors="ignore"))
                            text = j.get("text") or ""
                        except Exception:
                            text = body.decode("utf-8", errors="ignore")
                    else:
                        text = body.decode("utf-8", errors="ignore")
            except Exception:
                pass
    return (text or "").strip(), result


def _transcribe_best_effort(video_or_audio: Path) -> tuple[str, dict]:
    """Keep the captured media even when the optional ASR service is offline."""
    try:
        return _qwen3tts_transcribe(video_or_audio)
    except Exception as exc:
        return "", {"error": str(exc)}


# ---------- Adapters ----------

def _adapter_wechat(url: str) -> dict:
    workdir = _new_workdir("wechat")
    # Strip proxy env vars so Camoufox connects directly.
    # WeChat servers can behave weirdly through some proxies (returns verify page
    # / empty title). Direct connection is more reliable in practice.
    child_env = {k: v for k, v in os.environ.items()
                 if k.lower() not in {"http_proxy", "https_proxy", "all_proxy"}}
    try:
        proc = subprocess.run(
            [WECHAT_CLI, url],
            cwd=str(workdir),
            capture_output=True, text=True, timeout=180,
            env=child_env,
        )
    except FileNotFoundError:
        raise RuntimeError(f"wechat CLI not found: {WECHAT_CLI}")
    except subprocess.TimeoutExpired:
        raise RuntimeError("wechat article fetch timed out (>180s)")

    # Where wechat CLI actually wrote the file. It resolves `output/` relative to
    # its own __file__ (inside uv tool site-packages), not the process cwd.
    # We try to parse the exact path from stdout, then fall back to searching
    # known locations.
    combined = (proc.stdout or "") + (proc.stderr or "")
    saved_match = re.search(r"✅\s*已保存[:：]\s*(.+?\.md)", combined)
    md_files: list = []
    if saved_match:
        p = Path(saved_match.group(1).strip())
        if p.exists():
            md_files = [p]
    if not md_files:
        # Fall back to scanning cwd/output + CLI-relative output dir
        search_roots = [workdir / "output"]
        try:
            import wechat_article_to_markdown as _wa  # type: ignore
            search_roots.append(Path(_wa.__file__).parent / "output")
        except Exception:
            pass
        for root in search_roots:
            if root.exists():
                md_files = sorted(root.rglob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
                if md_files:
                    break
    if not md_files:
        raise RuntimeError(f"wechat CLI produced no .md file. output={combined[-800:]}")

    md_path = md_files[0]
    md_text = md_path.read_text(encoding="utf-8", errors="ignore")
    title = md_path.parent.name or md_path.stem
    image_dir = md_path.parent / "images"
    images = []
    if image_dir.exists():
        for p in sorted(image_dir.iterdir()):
            if p.is_file():
                images.append(str(p))
    # Copy CLI-side output into our workdir so downstream _relocate_assets sees
    # stable paths, then remove the CLI-side copy so it doesn't accumulate.
    cli_article_dir = md_path.parent
    dest_article_dir = workdir / cli_article_dir.name
    try:
        if dest_article_dir.exists():
            shutil.rmtree(dest_article_dir)
        shutil.copytree(str(cli_article_dir), str(dest_article_dir))
        new_md = dest_article_dir / md_path.name
        new_images_dir = dest_article_dir / "images"
        images = []
        if new_images_dir.exists():
            for p in sorted(new_images_dir.iterdir()):
                if p.is_file():
                    images.append(str(p))
        md_text = new_md.read_text(encoding="utf-8", errors="ignore")
        # Clean up CLI-side original so its output/ dir doesn't grow forever
        try:
            shutil.rmtree(str(cli_article_dir))
        except Exception:
            pass
    except Exception:
        pass  # keep original refs if copy fails

    return {"title": title, "md_text": md_text, "images": images, "workdir": str(workdir)}


def _adapter_douyin(url: str) -> dict:
    if TIKTOK_RE.search(url):
        raise RuntimeError(
            "TikTok 单视频详情尚无可复用的 OpenCLI 命令；当前自动采集仅支持抖音视频。"
        )

    from . import opencli_client

    data = opencli_client.douyin_video_detail(url)
    video_url = data.get("video_url")
    if not video_url or str(video_url).startswith("blob:"):
        raise RuntimeError("抖音页面未暴露可下载的视频地址")
    workdir = _new_workdir("douyin")
    video_path = workdir / "video.mp4"
    opencli_client.download_video(str(video_url), video_path)
    transcript_text, transcript_meta = _transcribe_best_effort(video_path)
    return {
        "title": _sanitize_title(data.get("desc") or data.get("author") or "douyin"),
        "author": data.get("author", ""),
        "raw_desc": data.get("desc", ""),
        "video_path": str(video_path),
        "transcript": transcript_text,
        "transcript_meta": transcript_meta,
        "workdir": str(workdir),
        "source_id": data.get("aweme_id", ""),
        "publish_time": data.get("publish_time", ""),
        "likes": data.get("likes", ""),
        "comments": data.get("comments", ""),
        "collects": data.get("collects", ""),
        "shares": data.get("shares", ""),
        "raw_info": data,
    }


def _adapter_xhs(url: str) -> dict:
    from . import opencli_client

    workdir = _new_workdir("xhs")
    info = opencli_client.xhs_note(url)
    dl_root = workdir / "download"
    download_rows = opencli_client.xhs_download(url, dl_root)
    images: list[str] = []
    video_path = None
    if dl_root.exists():
        for p in sorted(dl_root.rglob("*")):
            if p.is_file():
                suf = p.suffix.lower()
                if suf in {".png", ".jpg", ".jpeg", ".webp", ".heic"}:
                    images.append(str(p))
                elif suf in {".mp4", ".mov", ".mkv"}:
                    video_path = str(p)

    note_type = "video" if video_path else ("image" if images else "text")
    out = {
        "title": _sanitize_title(info.get("title") or "xhs"),
        "desc": info.get("content") or "",
        "note_type": note_type,
        "author": info.get("author") or "",
        "workdir": str(workdir),
        "images": images, "video_path": video_path,
        "likes": info.get("likes", ""),
        "comments": info.get("comments", ""),
        "collects": info.get("collects", ""),
        "tags": info.get("tags", ""),
        "raw_info": {"note": info, "downloads": download_rows},
    }
    if video_path:
        transcript_text, transcript_meta = _transcribe_best_effort(Path(video_path))
        out["transcript"] = transcript_text
        out["transcript_meta"] = transcript_meta
    return out


def _adapter_generic(url: str) -> dict:
    try:
        import trafilatura
    except ImportError:
        raise RuntimeError("trafilatura not installed; pip install trafilatura")
    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        raise RuntimeError(f"failed to fetch: {url}")
    text = trafilatura.extract(downloaded, output_format="markdown", include_images=True)
    if not text:
        raise RuntimeError("trafilatura extracted no content")
    md_meta = trafilatura.extract_metadata(downloaded)
    title = ""
    author = ""
    if md_meta:
        title = md_meta.title or ""
        author = md_meta.author or ""
    return {
        "title": _sanitize_title(title, fallback="page"),
        "author": author,
        "md_text": text,
        "images": [],
        "workdir": "",
    }


# ---------- Markdown assembly ----------

def _relocate_assets(assets: list, dest_dir: Path) -> list:
    """Copy asset files into dest_dir/assets/ and return their vault-relative paths."""
    if not assets:
        return []
    assets_dir = dest_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    out = []
    for src in assets:
        try:
            s = Path(src)
            if not s.exists():
                continue
            dest = assets_dir / s.name
            counter = 1
            while dest.exists():
                dest = assets_dir / f"{s.stem}-{counter}{s.suffix}"
                counter += 1
            shutil.copy2(s, dest)
            out.append(str(dest.relative_to(config.VAULT_ROOT)))
        except Exception as e:
            print(f"[relocate skip] {src}: {e}")
    return out


def _assemble_markdown(source_type: str, url: str, data: dict,
                       dest_dir: Path, slug: str) -> tuple:
    """Build the final markdown file body from adapter output.
    Returns (md_text, frontmatter_dict, asset_rel_paths).
    """
    title = data.get("title") or "untitled"
    now = datetime.now().isoformat(timespec="seconds")

    fm = {
        "type": "url_ingest",
        "source_url": url,
        "source_type": source_type,
        "captured_at": now,
        "title": title,
        "author": data.get("author", ""),
        "status": "pending",
    }
    for metadata_key in (
        "source_id", "publish_time", "note_type", "likes", "comments",
        "collects", "shares", "tags",
    ):
        if data.get(metadata_key) not in (None, "", []):
            fm[metadata_key] = data[metadata_key]

    body_lines = [f"# {title}", ""]
    asset_rels = []

    if source_type == "wechat":
        asset_rels = _relocate_assets(data.get("images") or [], dest_dir)
        # Adjust image references in md: original CLI wrote `images/...`
        md_body = data.get("md_text", "")
        if asset_rels:
            md_body = re.sub(r"images/", "assets/", md_body)
        body_lines.append(md_body)
        fm["has_images"] = bool(asset_rels)

    elif source_type in ("douyin", "tiktok"):
        video_src = data.get("video_path")
        if video_src:
            asset_rels = _relocate_assets([video_src], dest_dir)
        transcript = data.get("transcript", "").strip()
        raw_desc = data.get("raw_desc", "").strip()
        if raw_desc:
            body_lines += ["## 原文描述", "", raw_desc, ""]
        if transcript:
            body_lines += ["## 视频转录", "", transcript, ""]
        if asset_rels:
            body_lines += ["## 原始视频", "", f"![[{asset_rels[0]}]]", ""]
        fm["has_video"] = bool(asset_rels)
        fm["has_transcript"] = bool(transcript)

    elif source_type == "xhs":
        desc = data.get("desc", "").strip()
        transcript = data.get("transcript", "").strip()
        images = data.get("images") or []
        video = data.get("video_path")
        assets_to_copy = list(images)
        if video:
            assets_to_copy.append(video)
        asset_rels = _relocate_assets(assets_to_copy, dest_dir)
        if desc:
            body_lines += ["## 帖子正文", "", desc, ""]
        if transcript:
            body_lines += ["## 视频转录", "", transcript, ""]
        img_rels = [p for p in asset_rels if Path(p).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".heic"}]
        vid_rels = [p for p in asset_rels if Path(p).suffix.lower() in {".mp4", ".mov", ".mkv"}]
        if img_rels:
            body_lines += ["## 图片资产", ""]
            for p in img_rels:
                body_lines.append(f"![[{p}]]")
            body_lines.append("")
        if vid_rels:
            body_lines += ["## 原始视频", "", f"![[{vid_rels[0]}]]", ""]
        fm["has_video"] = bool(vid_rels)
        fm["has_transcript"] = bool(transcript)
        fm["has_images"] = bool(img_rels)

    else:  # generic
        body_lines.append(data.get("md_text", ""))

    md_full = make_frontmatter(fm) + "\n".join(body_lines).rstrip() + "\n"
    return md_full, fm, asset_rels


# ---------- Persistence ----------

def _register_item(unit_id: str, source_type: str, source_path: str, title: str,
                   preview: str, char_len: int, raw_content: str,
                   db_path: Optional[Path] = None) -> None:
    """Insert a row into items so downstream classify/pool stages will pick it up."""
    with db.get_conn(db_path) as c:
        c.execute(
            "INSERT OR IGNORE INTO items "
            "(unit_id, source, source_path, abs_path, title, preview, char_len, "
            "verdict, status, raw_content) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (unit_id, source_type, source_path,
             str(config.VAULT_ROOT / source_path), title, preview, char_len,
             "pending", "new", raw_content),
        )
    db.log_event(
        "RawItem.Created",
        item_id=unit_id,
        payload={"source_type": source_type, "source_path": source_path},
        db_path=db_path,
    )


# ---------- Main entry ----------

def ingest_url(url: str, db_path: Optional[Path] = None) -> IngestResult:
    url = url.strip()
    if not url:
        return IngestResult(ok=False, error="empty url")
    if not re.match(r"^https?://", url):
        return IngestResult(ok=False, error="not an http(s) url")

    source_type = classify_url(url)

    try:
        if source_type == "wechat":
            data = _adapter_wechat(url)
        elif source_type in ("douyin", "tiktok"):
            data = _adapter_douyin(url)
        elif source_type == "xhs":
            data = _adapter_xhs(url)
        else:
            data = _adapter_generic(url)
    except Exception as e:
        return IngestResult(ok=False, source_type=source_type, error=str(e))

    # Build slug + dest dir under inbox
    now = datetime.now()
    slug = safe_slug(f"{source_type}-{now.strftime('%Y%m%d-%H%M%S')}-{data.get('title', 'untitled')}")
    dest_dir = INGEST_INBOX_DIR / now.strftime("%Y-%m") / slug
    dest_dir.mkdir(parents=True, exist_ok=True)

    md_text, fm, asset_rels = _assemble_markdown(source_type, url, data, dest_dir, slug)
    md_path = dest_dir / f"{slug}.md"
    md_path.write_text(md_text, encoding="utf-8")

    rel_path = str(md_path.relative_to(config.VAULT_ROOT))
    unit_id = f"url-{now.strftime('%Y%m%d%H%M%S')}-{source_type}-{slug[-8:]}"

    preview_body = md_text
    if len(preview_body) > 500:
        preview_body = preview_body[:500]
    try:
        _register_item(
            unit_id=unit_id,
            source_type=source_type,
            source_path=rel_path,
            title=fm.get("title", ""),
            preview=preview_body.replace("\n", " | ")[:500],
            char_len=len(md_text),
            raw_content=md_text,
            db_path=db_path,
        )
    except Exception as e:
        # DB registration is nice-to-have; the file already exists
        return IngestResult(
            ok=True, source_type=source_type, unit_id="", inbox_path=rel_path,
            title=fm.get("title", ""), original_files=asset_rels,
            has_video=bool(fm.get("has_video")),
            has_transcript=bool(fm.get("has_transcript")),
            has_images=bool(fm.get("has_images")),
            error=f"db register failed: {e}",
        )

    return IngestResult(
        ok=True,
        source_type=source_type,
        unit_id=unit_id,
        inbox_path=rel_path,
        title=fm.get("title", ""),
        has_video=bool(fm.get("has_video")),
        has_transcript=bool(fm.get("has_transcript")),
        has_images=bool(fm.get("has_images")),
        original_files=asset_rels,
    )
