#!/usr/bin/env python3
"""
write_back.py — 根据 verdicts.jsonl 把判别结果落盘到 vault 三个池。

策略:
  seed    → 01-灵感库/<source>/<slug>.md  (frontmatter + 完整正文复制)
  asset   → 03-Assets/<asset_category>/<slug>.md  (frontmatter + 摘要 + 链接回 Inbox)
  archive → 04-Archive/<yyyy-mm>/<slug>.md  (仅元数据,无正文)

slug 规则: <unit_id>-<safe_title>.md (truncate 60 字符)

幂等性:
  默认行为 = 目标文件已存在则跳过(保护手动调整)
  --rewrite      = 强制覆盖所有(用于全量重建)
  --only NEW     = 只写入 verdicts.jsonl 中没在 vault 已存在的 unit (cron 增量默认推荐)
"""

import os
import re
import json
import sys
import argparse
from pathlib import Path
from datetime import datetime

PROJ = Path(os.path.expanduser("~/Documents/Project/llm-knowledge-curator"))
VAULT = Path(os.path.expanduser(
    "~/Library/Mobile Documents/iCloud~md~obsidian/Documents/LLM知识库"
))

VERDICTS = PROJ / "output" / "verdicts.jsonl"

SEED_ROOT = VAULT / "01-灵感库"
ASSET_ROOT = VAULT / "03-Assets"
ARCHIVE_ROOT = VAULT / "04-Archive"

TG_MSG_HEADER = re.compile(r"^## (\d{2}:\d{2})\s*$", re.MULTILINE)


def safe_slug(text: str, limit: int = 50) -> str:
    text = re.sub(r"[\s/\\<>:\"|?*\n\r]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-_.")
    return text[:limit] if text else "untitled"


def fetch_content(rec: dict) -> str:
    p = Path(rec["abs_path"])
    if not p.exists():
        return ""
    text = p.read_text(encoding="utf-8", errors="ignore")
    if rec["source"] != "telegram":
        return text
    idx = rec.get("tg_message_idx")
    if not idx:
        return text
    headers = [(m.group(1), m.end()) for m in TG_MSG_HEADER.finditer(text)]
    if not headers:
        return text
    target = min(idx - 1, len(headers) - 1)
    _, end = headers[target]
    next_start = headers[target + 1][1] if target + 1 < len(headers) else len(text)
    # 上一个 header 的 start
    all_starts = [m.start() for m in TG_MSG_HEADER.finditer(text)]
    next_start_actual = all_starts[target + 1] if target + 1 < len(all_starts) else len(text)
    body = text[end:next_start_actual].strip()
    body = re.sub(r"^---+\s*$", "", body, flags=re.MULTILINE).strip()
    return body


def make_frontmatter(d: dict) -> str:
    """把 dict 序列化成 YAML frontmatter,简单转义。"""
    lines = ["---"]
    for k, v in d.items():
        if v is None:
            continue
        if isinstance(v, list):
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


def write_seed(rec: dict) -> Path:
    verdict = rec["verdict"]
    source = rec["source"]
    title = rec["title"]
    slug = safe_slug(f'{rec["unit_id"]}-{title}')
    sub = SEED_ROOT / source
    sub.mkdir(parents=True, exist_ok=True)
    out = sub / f"{slug}.md"
    fm = make_frontmatter({
        "type": "seed",
        "source": source,
        "source_path": rec["source_path"],
        "tg_message_time": rec.get("tg_message_time"),
        "unit_id": rec["unit_id"],
        "parsed_at": datetime.now().strftime("%Y-%m-%d"),
        "verdict": verdict.get("verdict"),
        "category": verdict.get("category"),
        "trigger": verdict.get("trigger"),
        "reason": verdict.get("reason"),
        "confidence": verdict.get("confidence"),
        "priority": verdict.get("priority", "normal"),
        "status": "pending",
        "title": title,
    })
    body = fetch_content(rec)
    out.write_text(fm + f"\n# {title}\n\n" + body, encoding="utf-8")
    return out


def write_asset(rec: dict) -> Path:
    verdict = rec["verdict"]
    category = verdict.get("category") or "其他"
    safe_cat = safe_slug(category, 30)
    sub = ASSET_ROOT / safe_cat
    sub.mkdir(parents=True, exist_ok=True)
    title = rec["title"]
    slug = safe_slug(f'{rec["unit_id"]}-{title}')
    out = sub / f"{slug}.md"
    fm = make_frontmatter({
        "type": "asset",
        "asset_category": category,
        "source": rec["source"],
        "source_path": rec["source_path"],
        "tg_message_time": rec.get("tg_message_time"),
        "unit_id": rec["unit_id"],
        "parsed_at": datetime.now().strftime("%Y-%m-%d"),
        "reason": verdict.get("reason"),
        "confidence": verdict.get("confidence"),
        "title": title,
    })
    # asset 不复制全文,只放摘要 + 回链
    body = fetch_content(rec)
    summary = body[:300].replace("\n", " ⏎ ")
    out.write_text(
        fm + f"\n# {title}\n\n**Reason**: {verdict.get('reason','')}\n\n**Preview**: {summary}\n\n[查看原文]({rec['source_path']})\n",
        encoding="utf-8",
    )
    return out


def write_archive(rec: dict) -> Path:
    verdict = rec["verdict"]
    # 按 source_path 推日期分桶,fallback 当前月
    path_str = rec["source_path"]
    m = re.search(r"(20\d{2}-\d{2})", path_str)
    bucket = m.group(1) if m else datetime.now().strftime("%Y-%m")
    sub = ARCHIVE_ROOT / bucket
    sub.mkdir(parents=True, exist_ok=True)
    title = rec["title"]
    slug = safe_slug(f'{rec["unit_id"]}-{title}')
    out = sub / f"{slug}.md"
    fm = make_frontmatter({
        "type": "archive",
        "source": rec["source"],
        "source_path": rec["source_path"],
        "tg_message_time": rec.get("tg_message_time"),
        "unit_id": rec["unit_id"],
        "parsed_at": datetime.now().strftime("%Y-%m-%d"),
        "verdict": "archive",
        "category": verdict.get("category"),
        "reason": verdict.get("reason"),
        "title": title,
    })
    out.write_text(fm + f"\n# {title}\n\n_仅元数据,需要正文请查看原文路径。_\n", encoding="utf-8")
    return out


def find_existing(rec: dict) -> Path | None:
    """在 vault 三池里搜 unit_id 对应的物理文件,找到就返回。
    用于 cron 增量判断"是否已落盘",以及保护手动调整的文件。"""
    uid = rec["unit_id"]
    slug = safe_slug(f'{uid}-{rec["title"]}')
    fname = f"{slug}.md"
    # seed 池 path 固定 known,直接判存在
    if (SEED_ROOT / rec["source"] / fname).exists():
        return SEED_ROOT / rec["source"] / fname
    # asset/archive 子目录未知,但 slug 已 safe 过(没有 / \\ 等),
    # 且 unit_id 里不含 glob 特殊字符 ([ ] * ?),所以按 unit_id 前缀 glob 再精确比对
    glob_prefix = uid + "-*.md"
    for root in (ASSET_ROOT, ARCHIVE_ROOT):
        if not root.exists():
            continue
        for candidate in root.glob(f"*/{glob_prefix}"):
            if candidate.name == fname:
                return candidate
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rewrite", action="store_true",
                    help="强制覆盖所有(包括手动调整过的);默认跳过已存在文件")
    args = ap.parse_args()

    if not VERDICTS.exists():
        print(f"verdicts.jsonl 不存在: {VERDICTS}")
        sys.exit(1)
    counters = {"seed": 0, "asset": 0, "archive": 0,
                "skip_existing": 0, "skip_no_verdict": 0, "error": 0}
    seed_categories = {}
    asset_categories = {}
    archive_categories = {}
    dog_seeds = []

    for line in VERDICTS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            v = rec.get("verdict") or {}
            vv = v.get("verdict")
            cat = v.get("category", "未分类")
            if vv not in ("seed", "asset", "archive"):
                counters["skip_no_verdict"] += 1
                continue
            # 幂等保护:已存在则跳过(除非 --rewrite)
            if not args.rewrite and find_existing(rec):
                counters["skip_existing"] += 1
                continue
            if vv == "seed":
                write_seed(rec)
                counters["seed"] += 1
                seed_categories[cat] = seed_categories.get(cat, 0) + 1
                if "养狗" in cat:
                    dog_seeds.append(rec["unit_id"])
            elif vv == "asset":
                write_asset(rec)
                counters["asset"] += 1
                asset_categories[cat] = asset_categories.get(cat, 0) + 1
            elif vv == "archive":
                write_archive(rec)
                counters["archive"] += 1
                archive_categories[cat] = archive_categories.get(cat, 0) + 1
        except Exception as e:
            counters["error"] += 1
            print(f"[err] {e} on line: {line[:200]}")

    new_written = counters["seed"] + counters["asset"] + counters["archive"]
    print(f"\n=== 落盘统计 ({'重写模式' if args.rewrite else '增量模式(已存在则跳过)'}) ===")
    print(f"  新写入: {new_written} (seed={counters['seed']} asset={counters['asset']} archive={counters['archive']})")
    print(f"  跳过(已存在): {counters['skip_existing']}")
    print(f"  跳过(无 verdict): {counters['skip_no_verdict']}")
    print(f"  错误: {counters['error']}")
    if seed_categories:
        print(f"  seed_categories: {json.dumps(seed_categories, ensure_ascii=False)}")
    if asset_categories:
        print(f"  asset_categories: {json.dumps(asset_categories, ensure_ascii=False)}")
    if archive_categories:
        print(f"  archive_categories: {json.dumps(archive_categories, ensure_ascii=False)}")
    if dog_seeds:
        print(f"  养狗触发: {len(dog_seeds)} → {dog_seeds}")

    # 写汇总报告
    report = {
        "counters": counters,
        "seed_categories": seed_categories,
        "asset_categories": asset_categories,
        "archive_categories": archive_categories,
        "dog_rebuttal_units": dog_seeds,
        "generated_at": datetime.now().isoformat(),
    }
    (PROJ / "output" / "writeback_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
