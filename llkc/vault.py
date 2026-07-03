"""Shared vault path/content utilities — extracted from build_index, parser, write_back, daily_thinking."""

import re
from pathlib import Path
from typing import Optional

from . import config

FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
TG_MSG_HEADER = re.compile(r"^## (\d{2}:\d{2})\s*$", re.MULTILINE)
PREVIEW_LIMIT = 500


def extract_title(text: str, fallback: str) -> str:
    fm = FM_RE.match(text)
    if fm:
        m = re.search(r"^title:\s*(.+)$", fm.group(1), re.MULTILINE)
        if m:
            return m.group(1).strip().strip('"\'')
    for line in text.splitlines():
        m = re.match(r"^#\s+(.+)$", line)
        if m:
            return m.group(1).strip()
    return fallback


def strip_frontmatter(text: str) -> str:
    return re.sub(r"^---\n.*?\n---\n", "", text, count=1, flags=re.DOTALL)


def make_preview(text: str, limit: int = PREVIEW_LIMIT) -> str:
    body = strip_frontmatter(text)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return body[:limit].replace("\n", " | ")


def parse_frontmatter(text: str) -> dict:
    m = FM_RE.match(text)
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        v = v.strip()
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            v = v[1:-1]
        out[k.strip()] = v
    return out


def make_frontmatter(d: dict) -> str:
    lines = ["---"]
    for k, v in d.items():
        if v is None:
            continue
        if isinstance(v, list):
            import json
            lines.append(f"{k}: [{', '.join(json.dumps(x, ensure_ascii=False) for x in v)}]")
        elif isinstance(v, bool):
            lines.append(f"{k}: {str(v).lower()}")
        elif isinstance(v, (int, float)):
            lines.append(f"{k}: {v}")
        else:
            s = str(v).replace('"', "'")
            if "\n" in s or ":" in s or "#" in s:
                lines.append(f'{k}: "{s}"')
            else:
                lines.append(f"{k}: {s}")
    lines.append("---\n")
    return "\n".join(lines)


def safe_slug(text: str, limit: int = 50) -> str:
    text = re.sub(r"[\s/\\<>:\"|?*\n\r]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-_.")
    return text[:limit] if text else "untitled"


def split_telegram_messages(text: str) -> list[tuple[str, str]]:
    headers = [(m.group(1), m.start(), m.end()) for m in TG_MSG_HEADER.finditer(text)]
    if not headers:
        return []
    msgs = []
    for i, (t, start, end) in enumerate(headers):
        next_start = headers[i + 1][1] if i + 1 < len(headers) else len(text)
        body = text[end:next_start].strip()
        body = re.sub(r"^---+\s*$", "", body, flags=re.MULTILINE).strip()
        if body:
            msgs.append((t, body))
    deduped = []
    for t, body in msgs:
        if deduped and deduped[-1][1] == body:
            continue
        deduped.append((t, body))
    return deduped


def fetch_unit_content(unit: dict) -> str:
    """Read full content for a unit from the vault file system."""
    path = Path(unit["abs_path"])
    if not path.exists():
        raise FileNotFoundError(unit["abs_path"])
    text = path.read_text(encoding="utf-8", errors="ignore")
    if unit.get("source") != "telegram":
        return text
    idx = unit.get("tg_message_idx")
    if not idx:
        return text
    headers = [(m.group(1), m.start(), m.end()) for m in TG_MSG_HEADER.finditer(text)]
    if not headers:
        return text
    target = min(idx - 1, len(headers) - 1)
    _, _, end = headers[target]
    next_start = headers[target + 1][1] if target + 1 < len(headers) else len(text)
    body = text[end:next_start].strip()
    body = re.sub(r"^---+\s*$", "", body, flags=re.MULTILINE).strip()
    return body


def find_seed_file(unit_id: str) -> Optional[Path]:
    if not config.SEED_ROOT.exists():
        return None
    for f in config.SEED_ROOT.rglob(f"{unit_id}*.md"):
        return f
    return None


def find_pooled_file(unit_id: str, title: str) -> Optional[Path]:
    """Check if a unit has already been written to a vault pool."""
    slug = safe_slug(f'{unit_id}-{title}')
    fname = f"{slug}.md"
    if (config.SEED_ROOT / unit_id.split("-")[0] / fname).exists():
        pass
    for root in (config.SEED_ROOT, config.ASSET_ROOT, config.ARCHIVE_ROOT):
        if not root.exists():
            continue
        for candidate in root.rglob(f"{unit_id}-*.md"):
            if candidate.name == fname:
                return candidate
    return None
