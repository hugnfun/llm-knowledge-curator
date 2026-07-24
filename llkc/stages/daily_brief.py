"""Daily Brief stage - generates a daily summary of new items, distribution,
suggested actions, and project associations. Writes to Obsidian Daily Note + DB."""

import json
from datetime import date, timedelta
from pathlib import Path

from .. import config, db
from ..models import EventType, PipelineStage

# Project keyword map (from Stage C validation)
PROJECT_KEYWORDS = {
    "AI/Agent": ["ai", "agent", "mcp", "自动化", "工作流", "知识库", "claude", "codex", "cursor", "llm", "hermes", "openclaw"],
    "独立开发/SaaS": ["独立开发", "saas", "一人公司", "商业模式", "mvp", "订阅", "创业", "变现"],
    "小红书/自媒体": ["小红书", "小绿书", "活人感", "原生感", "自媒体", "爆文", "公众号", "写作", "短视频"],
    "抖音宠物": ["宠物", "萨摩耶", "养狗", "萌宠", "宠物账号"],
    "营销/SEO": ["营销", "用户心理", "增长", "seo", "流量", "关键词", "转化", "投流", "引流"],
    "系统思维": ["第一性原理", "系统思维", "长期主义", "底层规律", "认知框架", "反直觉", "方法论", "飞轮"],
}


def _keyword_match_projects(text: str, tags: str, category: str) -> list[str]:
    combined = ((text or "")[:500] + " " + (tags or "") + " " + (category or "")).lower()
    matches = []
    for proj, keywords in PROJECT_KEYWORDS.items():
        if any(kw.lower() in combined for kw in keywords):
            matches.append(proj)
    return matches


def _get_new_items(target_date: str, db_path: Path = None) -> list[dict]:
    """Get items parsed since the last brief (or last 24h)."""
    prev_date = (date.fromisoformat(target_date) - timedelta(days=1)).isoformat()
    items = db.query_items(limit=100000, db_path=db_path)
    new_items = []
    for item in items:
        parsed = item.get("parsed_at", "")
        if parsed and parsed >= prev_date:
            new_items.append(item)
    return new_items


def _top_seeds(items: list[dict], n: int = 5) -> list[dict]:
    seeds = [i for i in items if i.get("verdict") == "seed"]
    seeds.sort(key=lambda x: (x.get("priority") == "high", x.get("confidence") == "high"), reverse=True)
    return seeds[:n]


def _suggested_actions(items: list[dict], top_seeds: list[dict]) -> list[str]:
    actions = []
    seed_count = sum(1 for i in items if i.get("verdict") == "seed")
    high_pri = sum(1 for i in items if i.get("priority") == "high")
    if seed_count > 0:
        actions.append(f"{seed_count} 条新 seed 可进入 Daily Thinking")
    if high_pri > 0:
        actions.append(f"{high_pri} 条高优先级(工具元话题)需关注")
    if len(top_seeds) > 3:
        actions.append("Seed 积压较多,建议今天完成 thinking + draft")
    if not actions:
        actions.append("今日无新 seed,可处理历史 backlog 或写内容")
    return actions


def _project_distribution(items: list[dict]) -> dict:
    dist = {}
    for item in items:
        tags_raw = item.get("tags", "")
        try:
            tags = json.loads(tags_raw) if tags_raw else []
        except (json.JSONDecodeError, TypeError):
            tags = []
        matches = _keyword_match_projects(
            item.get("raw_content", "") or item.get("title", ""),
            " ".join(tags) if isinstance(tags, list) else str(tags),
            item.get("category", ""),
        )
        for proj in matches:
            dist[proj] = dist.get(proj, 0) + 1
    return dict(sorted(dist.items(), key=lambda x: -x[1]))


def _format_brief_md(target_date: str, items: list[dict],
                      top: list[dict], actions: list[str],
                      proj_dist: dict) -> str:
    counts = {"seed": 0, "asset": 0, "archive": 0}
    for i in items:
        v = i.get("verdict", "pending")
        if v in counts:
            counts[v] += 1

    fm = (
        "---\n"
        "type: daily_brief\n"
        f"date: {target_date}\n"
        f"new_items: {len(items)}\n"
        f"seed: {counts['seed']}\n"
        f"asset: {counts['asset']}\n"
        f"archive: {counts['archive']}\n"
        "---\n"
    )

    body = f"\n# Daily Brief - {target_date}\n\n"
    body += f"## 今日新增素材\n\n"
    body += f"- 总计 **{len(items)}** 条\n"
    body += f"- Seed: **{counts['seed']}** | Asset: **{counts['asset']}** | Archive: **{counts['archive']}**\n\n"

    if top:
        body += "## 重点 Seed\n\n"
        for i, s in enumerate(top, 1):
            prio = " *" if s.get("priority") == "high" else ""
            body += f"{i}. **{s.get('title', '?')[:50]}**{prio}\n"
            if s.get("trigger"):
                body += f"   - Trigger: {s['trigger'][:60]}\n"
            body += f"   - `[[{s['unit_id']}]]`\n"
        body += "\n"

    body += "## 建议动作\n\n"
    for a in actions:
        body += f"- {a}\n"
    body += "\n"

    if proj_dist:
        body += "## 项目关联\n\n"
        for proj, cnt in proj_dist.items():
            body += f"- {proj}: {cnt} 条\n"
        body += "\n"

    body += "---\n\n"
    body += "> Daily Brief 由 llkc 自动生成。完成 Daily Thinking 后,可用 writer 生成草稿。\n"

    return fm + body


def run(target_date: str = None, db_path: Path = None) -> dict:
    target_date = target_date or date.today().isoformat()

    items = _get_new_items(target_date, db_path)
    if not items:
        # No new items, still create an empty brief
        pass

    top = _top_seeds(items)
    actions = _suggested_actions(items, top)
    proj_dist = _project_distribution(items)

    counts = {"seed": 0, "asset": 0, "archive": 0}
    for i in items:
        v = i.get("verdict", "pending")
        if v in counts:
            counts[v] += 1

    # Write to DB
    top_seeds_json = json.dumps([{
        "unit_id": s.get("unit_id"),
        "title": s.get("title", "")[:60],
        "trigger": s.get("trigger", ""),
        "priority": s.get("priority", "normal"),
    } for s in top], ensure_ascii=False)

    db.upsert_daily_brief(
        target_date,
        new_count=len(items),
        seed_count=counts["seed"],
        asset_count=counts["asset"],
        archive_count=counts["archive"],
        top_seeds=top_seeds_json,
        actions=json.dumps(actions, ensure_ascii=False),
        project_matches=json.dumps(proj_dist, ensure_ascii=False),
        raw_json=json.dumps({
            "date": target_date,
            "total": len(items),
            "distribution": counts,
            "top_seeds": json.loads(top_seeds_json),
            "actions": actions,
            "projects": proj_dist,
        }, ensure_ascii=False),
        db_path=db_path,
    )

    # Write to Obsidian Daily Note
    md = _format_brief_md(target_date, items, top, actions, proj_dist)
    config.THINKING_ROOT.mkdir(parents=True, exist_ok=True)
    brief_path = config.THINKING_ROOT / f"brief-{target_date}.md"
    brief_path.write_text(md, encoding="utf-8")

    # Log
    run_id = db.create_run(stage=PipelineStage.DAILY_THINKING.value, db_path=db_path)
    db.log_event(EventType.DAILY_THINKING_REQUESTED.value, run_id=run_id,
                 payload={"date": target_date, "brief": True, "new_items": len(items)},
                 db_path=db_path)
    db.complete_run(run_id, artifacts=str(brief_path), db_path=db_path)

    return {
        "ok": True,
        "date": target_date,
        "new_items": len(items),
        "distribution": counts,
        "top_seeds": len(top),
        "actions": actions,
        "projects": proj_dist,
        "path": str(brief_path),
    }
