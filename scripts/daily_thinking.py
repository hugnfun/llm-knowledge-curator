#!/usr/bin/env python3
"""
daily_thinking.py — 生成 Daily Thinking 模板

策略:
  - 输出: vault/02-思考/yyyy-mm-dd.md (已存在则报错退出,除非 --force)
  - 内容: 模板顶部留自由写区,底部贴 5 条随机 seed 素材作为引子
  - seed 来源: 01-灵感库 下所有子目录的 .md 文件
  - 每条素材展示: 标题 + trigger + reason + obsidian 内链
  - 模板顶部 frontmatter 标记 date + 抽到的 unit_ids,便于以后回溯

用法:
  python3 scripts/daily_thinking.py              # 当天
  python3 scripts/daily_thinking.py --date 2026-06-30
  python3 scripts/daily_thinking.py --force      # 覆盖同日文件
  python3 scripts/daily_thinking.py --seeds 10   # 改变素材数量
"""

import os
import re
import sys
import json
import random
import argparse
from pathlib import Path
from datetime import datetime, date

VAULT = Path(os.path.expanduser(
    "~/Library/Mobile Documents/iCloud~md~obsidian/Documents/LLM知识库"
))
SEED_ROOT = VAULT / "01-灵感库"
THINKING_ROOT = VAULT / "02-思考"

FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_frontmatter(text: str) -> dict:
    """简单 YAML frontmatter 解析(只支持 k: v 单行,不支持嵌套/数组,够用)"""
    m = FM_RE.match(text)
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        v = v.strip()
        # 去掉外层引号
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            v = v[1:-1]
        out[k.strip()] = v
    return out


def load_seeds() -> list[dict]:
    """加载所有 seed 文件,返回 [{path, fm, ...}]"""
    seeds = []
    if not SEED_ROOT.exists():
        return seeds
    for f in SEED_ROOT.rglob("*.md"):
        try:
            text = f.read_text(encoding="utf-8")
            fm = parse_frontmatter(text)
            if not fm:
                continue
            seeds.append({
                "path": f,
                "rel_path": f.relative_to(VAULT),
                "fm": fm,
                "unit_id": fm.get("unit_id", "?"),
                "title": fm.get("title", f.stem),
                "source": fm.get("source", "?"),
                "category": fm.get("category", "?"),
                "trigger": fm.get("trigger", ""),
                "reason": fm.get("reason", ""),
                "priority": fm.get("priority", "normal"),
                "parsed_at": fm.get("parsed_at", ""),
            })
        except Exception as e:
            print(f"[warn] 解析失败 {f.name}: {e}", file=sys.stderr)
    return seeds


def format_seed_block(s: dict) -> str:
    """单条素材展示块"""
    # 用 obsidian 内链 [[相对路径|显示名]] 让用户能跳过去
    link_target = str(s["rel_path"]).replace(".md", "")
    title_disp = s["title"][:60]
    return (
        f"### {s['unit_id']} · {s['source']} · {s['category']}"
        f"{' · ⭐priority:high' if s['priority'] == 'high' else ''}\n\n"
        f"**标题**: {title_disp}\n\n"
        f"**触发点 (trigger)**: {s['trigger']}\n\n"
        f"**判别理由 (reason)**: {s['reason']}\n\n"
        f"**原文**: [[{link_target}|→ 打开]]\n"
    )


def make_thinking_doc(seeds: list[dict], target_date: str) -> str:
    """组装模板"""
    today_str = target_date
    unit_ids = [s["unit_id"] for s in seeds]
    sources = sorted(set(s["source"] for s in seeds))
    categories = sorted(set(s["category"] for s in seeds))

    fm = (
        "---\n"
        "type: daily_thinking\n"
        f"date: {today_str}\n"
        f"seeds: [{', '.join(repr(u) for u in unit_ids)}]\n"
        f"seed_sources: [{', '.join(repr(s) for s in sources)}]\n"
        f"seed_categories: [{', '.join(repr(c) for c in categories)}]\n"
        "status: draft\n"
        "---\n"
    )

    body = (
        f"\n# Daily Thinking · {today_str}\n\n"
        f"## 今日自由写\n\n"
        f"_在下方写下任何你今天想到的、最近在琢磨的、被外部信息撞到的想法。底下 5 条素材是引子,不用每条都回应,但允许它们勾起新的思路。_\n\n"
        f"<!-- 你的思考写在这里 -->\n\n"
        f"\n\n"
        f"---\n\n"
        f"## 今日 5 条随机灵感素材\n\n"
        f"_从 seed 池中多样化抽取(每 category ≤2 / 每 source ≤2),与今日主题无关联。可作为引子或忽略。_\n\n"
    )
    blocks = []
    for i, s in enumerate(seeds, 1):
        blocks.append(f"## {i}/5\n\n" + format_seed_block(s))
    return fm + body + "\n\n---\n\n".join(blocks) + "\n"


def diversified_sample(seeds: list[dict], n: int,
                       per_cat_cap: int = 2, per_src_cap: int = 2) -> list[dict]:
    """多样化抽样:
    - 随机打散后顺序扫描,加入候选前检查同 category / 同 source 数量上限。
    - 凑不够 n 条则逐步放宽 cap(+1)直到能凑够。
    - 保证返回 exactly n 条(只要 seed 池 >= n)。
    """
    if len(seeds) <= n:
        return list(seeds)
    cap_cat, cap_src = per_cat_cap, per_src_cap
    while True:
        pool = seeds.copy()
        random.shuffle(pool)
        picked = []
        cat_count, src_count = {}, {}
        for s in pool:
            c, src = s["category"], s["source"]
            if cat_count.get(c, 0) >= cap_cat:
                continue
            if src_count.get(src, 0) >= cap_src:
                continue
            picked.append(s)
            cat_count[c] = cat_count.get(c, 0) + 1
            src_count[src] = src_count.get(src, 0) + 1
            if len(picked) >= n:
                break
        if len(picked) >= n:
            return picked
        # 凑不够 → 放宽 cap
        cap_cat += 1
        cap_src += 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=date.today().isoformat(),
                    help="日期,默认今天 yyyy-mm-dd")
    ap.add_argument("--seeds", type=int, default=5,
                    help="抽取的素材数量,默认 5")
    ap.add_argument("--force", action="store_true",
                    help="目标文件已存在则覆盖(默认拒绝,保护已有思考)")
    ap.add_argument("--seed", type=int, default=None,
                    help="随机种子,便于复现(测试用)")
    args = ap.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    THINKING_ROOT.mkdir(parents=True, exist_ok=True)
    target = THINKING_ROOT / f"{args.date}.md"

    if target.exists() and not args.force:
        print(f"[REFUSE] {target.name} 已存在 (保护已有思考)。要覆盖加 --force。", file=sys.stderr)
        sys.exit(2)

    seeds = load_seeds()
    if len(seeds) < args.seeds:
        print(f"[FATAL] seed 池只有 {len(seeds)} 条, 不足 {args.seeds}", file=sys.stderr)
        sys.exit(1)

    sample = diversified_sample(seeds, args.seeds)
    doc = make_thinking_doc(sample, args.date)
    target.write_text(doc, encoding="utf-8")

    print(f"✓ 生成: {target}")
    print(f"  从 {len(seeds)} 条 seed 中抽样 {len(sample)} 条:")
    for s in sample:
        prio_mark = "⭐" if s["priority"] == "high" else " "
        print(f"    {prio_mark} {s['unit_id']:<18s} [{s['source']:<11s}] {s['category']:<15s} {s['title'][:40]}")


if __name__ == "__main__":
    main()
