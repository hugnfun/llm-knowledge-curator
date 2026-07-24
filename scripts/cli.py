#!/usr/bin/env python3
"""LLM Knowledge Curator CLI - unified entry point for all pipeline operations."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import json
import sys
from datetime import date

from llkc import config, db
from llkc.connectors import obsidian_inbox
from llkc.stages import parser as parser_stage
from llkc.stages import write_back as write_back_stage
from llkc.stages import daily_thinking as daily_thinking_stage
from llkc.stages import writer as writer_stage
from llkc.stages import daily_brief as daily_brief_stage
from llkc.stages import polish as polish_stage
from llkc import pipeline


def cmd_scan(args):
    units = obsidian_inbox.scan_inbox(persist=True)
    summary = obsidian_inbox.write_index(units)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def cmd_classify(args):
    result = parser_stage.run(concurrency=args.concurrency)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_pool(args):
    result = write_back_stage.run(rewrite=args.rewrite)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_incremental(args):
    result = pipeline.run_incremental()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


def cmd_thinking(args):
    result = daily_thinking_stage.run(
        target_date=args.date, n_seeds=args.seeds, force=args.force,
        seed_val=args.seed_val)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_writer(args):
    result = writer_stage.run(
        target_date=args.date, model=args.model, force=args.force,
        allow_empty=args.allow_empty)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_brief(args):
    result = daily_brief_stage.run(target_date=args.date)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_polish(args):
    result = polish_stage.run(draft_id=args.draft_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_stats(args):
    stats = db.get_stats()
    print(json.dumps(stats, ensure_ascii=False, indent=2))


def cmd_items(args):
    items = db.query_items(
        verdict=args.verdict, source=args.source,
        priority=args.priority, limit=args.limit)
    if args.json:
        print(json.dumps(items, ensure_ascii=False, indent=2))
    else:
        for item in items:
            prio = "*" if item.get("priority") == "high" else " "
            print(f"  {prio} [{item['source']:<12s}] [{item.get('verdict','?'):<8s}] "
                  f"{item['unit_id']:<20s} {item.get('title','')[:50]}")


def cmd_migrate(args):
    from llkc.migrate import migrate
    migrate()


def cmd_lark_listen(args):
    from llkc.connectors import lark_listener
    result = lark_listener.run_listener(
        lark_cli=args.lark_cli,
        max_events=args.max_events,
        timeout=args.timeout,
        ready_timeout=args.ready_timeout,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_pending_urls(args):
    from llkc.connectors import pending_urls
    result = pending_urls.run(
        limit=args.limit,
        max_attempts=args.max_attempts,
        stale_after_seconds=args.stale_seconds,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_serve(args):
    import uvicorn
    print(f"Starting API server on {config.API_HOST}:{config.API_PORT}")
    print(f"  Web GUI: http://{config.API_HOST}:{config.API_PORT}/")
    print(f"  API docs: http://{config.API_HOST}:{config.API_PORT}/docs")
    uvicorn.run("llkc.api.server:app", host=config.API_HOST, port=config.API_PORT,
                reload=args.reload)


def cmd_worker(args):
    from llkc.connectors import pending_worker
    result = pending_worker.run_worker(
        interval=args.interval,
        once=args.once,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main():
    ap = argparse.ArgumentParser(
        prog="llkc",
        description="LLM Knowledge Curator - event-driven content factory CLI")
    sub = ap.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="Scan inbox and build index")
    p_scan.set_defaults(func=cmd_scan)

    p_classify = sub.add_parser("classify", help="Run LLM classifier on pending items")
    p_classify.add_argument("--concurrency", type=int, default=None)
    p_classify.set_defaults(func=cmd_classify)

    p_pool = sub.add_parser("pool", help="Write classified items to vault pools")
    p_pool.add_argument("--rewrite", action="store_true", help="Overwrite existing files")
    p_pool.set_defaults(func=cmd_pool)

    p_incr = sub.add_parser("incremental", help="Full incremental pipeline (scan+classify+pool)")
    p_incr.set_defaults(func=cmd_incremental)

    p_brief = sub.add_parser("brief", help="Generate daily brief")
    p_brief.add_argument("--date", default=None, help="Target date (YYYY-MM-DD)")
    p_brief.set_defaults(func=cmd_brief)

    p_polish = sub.add_parser("polish", help="Polish a selected draft")
    p_polish.add_argument("--draft-id", default=None, help="Draft ID (omit for all selected)")
    p_polish.set_defaults(func=cmd_polish)

    p_think = sub.add_parser("thinking", help="Generate daily thinking document")
    p_think.add_argument("--date", default=None)
    p_think.add_argument("--seeds", type=int, default=5)
    p_think.add_argument("--force", action="store_true")
    p_think.add_argument("--seed-val", type=int, default=None, dest="seed_val")
    p_think.set_defaults(func=cmd_thinking)

    p_write = sub.add_parser("writer", help="Generate draft candidates")
    p_write.add_argument("--date", default=None)
    p_write.add_argument("--model", default=None)
    p_write.add_argument("--force", action="store_true")
    p_write.add_argument("--allow-empty", action="store_true", dest="allow_empty")
    p_write.set_defaults(func=cmd_writer)

    p_stats = sub.add_parser("stats", help="Show database statistics")
    p_stats.set_defaults(func=cmd_stats)

    p_items = sub.add_parser("items", help="List items from database")
    p_items.add_argument("--verdict", default=None, choices=["seed", "asset", "archive", "pending"])
    p_items.add_argument("--source", default=None)
    p_items.add_argument("--priority", default=None, choices=["high", "normal"])
    p_items.add_argument("--limit", type=int, default=50)
    p_items.add_argument("--json", action="store_true")
    p_items.set_defaults(func=cmd_items)

    p_migrate = sub.add_parser("migrate", help="Import existing verdicts.jsonl into SQLite")
    p_migrate.set_defaults(func=cmd_migrate)

    p_lark = sub.add_parser("lark-listen", help="Capture URLs from Feishu bot messages")
    p_lark.add_argument("--lark-cli", default=None, help="lark-cli executable path")
    p_lark.add_argument("--max-events", type=int, default=0)
    p_lark.add_argument("--timeout", default="", help="bounded consume duration, e.g. 30s")
    p_lark.add_argument("--ready-timeout", type=float, default=30)
    p_lark.set_defaults(func=cmd_lark_listen)

    p_pending_urls = sub.add_parser("pending-urls", help="Ingest queued URLs")
    p_pending_urls.add_argument("--limit", type=int, default=20)
    p_pending_urls.add_argument("--max-attempts", type=int, default=3)
    p_pending_urls.add_argument("--stale-seconds", type=int, default=3600)
    p_pending_urls.set_defaults(func=cmd_pending_urls)

    p_serve = sub.add_parser("serve", help="Start API server + Web GUI")
    p_serve.add_argument("--reload", action="store_true")
    p_serve.set_defaults(func=cmd_serve)

    p_worker = sub.add_parser("worker", help="Run pending-URL worker daemon (LaunchAgent)")
    p_worker.add_argument("--interval", type=int, default=20, help="Poll interval in seconds")
    p_worker.add_argument("--once", action="store_true", help="Run one cycle and exit")
    p_worker.set_defaults(func=cmd_worker)

    args = ap.parse_args()
    config.ensure_dirs()
    db.init_db()
    db.fail_stale_runs()
    args.func(args)


if __name__ == "__main__":
    main()
