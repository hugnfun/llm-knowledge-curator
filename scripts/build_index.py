#!/usr/bin/env python3
"""
build_index.py — 扫描 LLM 知识库 00-Inbox 三个来源,输出待判别单元 JSON 索引。

输出: ~/Documents/Project/llm-knowledge-curator/output/inbox_index.json

单元结构: {
    "unit_id": "x-bookmarks-001",
    "source": "x-bookmarks | clippings | telegram",
    "source_path": "00-Inbox/...",  # 相对 vault 根
    "abs_path": "...",
    "tg_message_idx": null | int,     # telegram 仅 message 在文件内的序号
    "tg_message_time": null | "HH:MM",
    "title": "...",
    "preview": "...(≤500 char,正文前段)",
    "char_len": 5000
}
"""

import os
import re
import json
import hashlib
from pathlib import Path

VAULT = Path(os.path.expanduser(
    "~/Library/Mobile Documents/iCloud~md~obsidian/Documents/LLM知识库"
))
INBOX = VAULT / "00-Inbox"
OUT = Path(os.path.expanduser(
    "~/Documents/Project/llm-knowledge-curator/output"
))
OUT.mkdir(parents=True, exist_ok=True)

PREVIEW_LIMIT = 500


def extract_title(text: str, fallback: str) -> str:
    """从 markdown 抽 title:第一个 # heading,或 frontmatter title,或 fallback 文件名。"""
    # frontmatter title
    fm = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if fm:
        m = re.search(r"^title:\s*(.+)$", fm.group(1), re.MULTILINE)
        if m:
            return m.group(1).strip().strip('"\'')
    # 第一个 # 标题
    for line in text.splitlines():
        m = re.match(r"^#\s+(.+)$", line)
        if m:
            return m.group(1).strip()
    return fallback


def strip_frontmatter(text: str) -> str:
    """去掉 frontmatter 再返回正文。"""
    return re.sub(r"^---\n.*?\n---\n", "", text, count=1, flags=re.DOTALL)


def make_preview(text: str, limit: int = PREVIEW_LIMIT) -> str:
    """正文压成单行 preview。"""
    body = strip_frontmatter(text)
    # 把多空行压成单空行,去 markdown 噪声前缀
    body = re.sub(r"\n{3,}", "\n\n", body)
    body = body.strip()
    return body[:limit].replace("\n", " ⏎ ")


def collect_clippings():
    units = []
    root = INBOX / "Clippings"
    if not root.exists():
        return units
    for i, p in enumerate(sorted(root.rglob("*.md")), start=1):
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            print(f"[skip] {p}: {e}")
            continue
        units.append({
            "unit_id": f"clippings-{i:03d}",
            "source": "clippings",
            "source_path": str(p.relative_to(VAULT)),
            "abs_path": str(p),
            "tg_message_idx": None,
            "tg_message_time": None,
            "title": extract_title(text, p.stem),
            "preview": make_preview(text),
            "char_len": len(text),
        })
    return units


def collect_xbookmarks():
    units = []
    root = INBOX / "X-Bookmarks"
    if not root.exists():
        return units
    counter = 0
    for p in sorted(root.rglob("*.md")):
        # 跳过索引文件 (典型命名 _index.md / index.md / daily-summary 之类)
        if p.name.lower().startswith(("index", "_index")):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            print(f"[skip] {p}: {e}")
            continue
        counter += 1
        units.append({
            "unit_id": f"x-bookmarks-{counter:03d}",
            "source": "x-bookmarks",
            "source_path": str(p.relative_to(VAULT)),
            "abs_path": str(p),
            "tg_message_idx": None,
            "tg_message_time": None,
            "title": extract_title(text, p.stem),
            "preview": make_preview(text),
            "char_len": len(text),
        })
    return units


TG_MSG_HEADER = re.compile(r"^## (\d{2}:\d{2})\s*$", re.MULTILINE)


def split_telegram_messages(text: str):
    """按 `## HH:MM` 切分,返回 [(time, content_with_header_stripped)]。"""
    headers = [(m.group(1), m.start(), m.end()) for m in TG_MSG_HEADER.finditer(text)]
    if not headers:
        return []
    msgs = []
    for i, (t, start, end) in enumerate(headers):
        next_start = headers[i + 1][1] if i + 1 < len(headers) else len(text)
        body = text[end:next_start].strip()
        # 去掉分隔横线
        body = re.sub(r"^---+\s*$", "", body, flags=re.MULTILINE).strip()
        if body:
            msgs.append((t, body))
    # 去重:相邻完全相同 body
    deduped = []
    for t, body in msgs:
        if deduped and deduped[-1][1] == body:
            continue
        deduped.append((t, body))
    return deduped


def collect_telegram():
    units = []
    root = INBOX / "Telegram"
    if not root.exists():
        return units
    counter = 0
    for p in sorted(root.rglob("*.md")):
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            print(f"[skip] {p}: {e}")
            continue
        msgs = split_telegram_messages(text)
        for idx, (t, body) in enumerate(msgs, start=1):
            counter += 1
            # 取首行做 title
            first_line = body.split("\n", 1)[0].strip()
            title = first_line[:80] or f"{p.stem} {t}"
            units.append({
                "unit_id": f"telegram-{counter:04d}",
                "source": "telegram",
                "source_path": str(p.relative_to(VAULT)),
                "abs_path": str(p),
                "tg_message_idx": idx,
                "tg_message_time": t,
                "title": title,
                "preview": body[:PREVIEW_LIMIT].replace("\n", " ⏎ "),
                "char_len": len(body),
            })
    return units


def main():
    clippings = collect_clippings()
    xbookmarks = collect_xbookmarks()
    telegram = collect_telegram()
    all_units = clippings + xbookmarks + telegram

    # 写 JSON 索引
    out_path = OUT / "inbox_index.json"
    out_path.write_text(json.dumps(all_units, ensure_ascii=False, indent=2), encoding="utf-8")

    # 写 summary
    summary = {
        "total_units": len(all_units),
        "clippings": len(clippings),
        "x-bookmarks": len(xbookmarks),
        "telegram_messages": len(telegram),
        "telegram_files": len(set(u["abs_path"] for u in telegram)),
    }
    (OUT / "inbox_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\n索引已写: {out_path}")
    print(f"单元总数: {len(all_units)}")


if __name__ == "__main__":
    main()
