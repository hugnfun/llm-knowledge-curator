"""Pipeline orchestrator - event-driven state machine for the content factory."""

from pathlib import Path
from typing import Optional

from . import config, db
from .models import PipelineStage, RunStatus, EventType, STAGE_ORDER
from .connectors import obsidian_inbox
from .stages import parser as parser_stage
from .stages import write_back as write_back_stage
from .stages import daily_thinking as daily_thinking_stage
from .stages import writer as writer_stage


def run_incremental(db_path: Optional[Path] = None) -> dict:
    """Full incremental pipeline: scan inbox -> classify -> write back to vault.
    This is the cron equivalent of the old cron_incremental.sh."""
    results = {}
    results["scan"] = obsidian_inbox.scan_inbox(persist=True) if False else None

    items_before = db.count_items(db_path=db_path)
    units = obsidian_inbox.scan_inbox(persist=True)

    pending = db.query_items(verdict="pending", limit=100000, db_path=db_path)
    if not pending:
        results["classify"] = {"ok": 0, "fail": 0, "skipped": "no pending"}
    else:
        results["classify"] = parser_stage.run(db_path=db_path)

    results["pool"] = write_back_stage.run(db_path=db_path)
    return results


def run_daily_thinking(target_date: str = None, n_seeds: int = 5,
                       force: bool = False, db_path: Optional[Path] = None) -> dict:
    return daily_thinking_stage.run(target_date=target_date, n_seeds=n_seeds,
                                     force=force, db_path=db_path)


def run_writer(target_date: str = None, model: str = None, force: bool = False,
                allow_empty: bool = False, db_path: Optional[Path] = None) -> dict:
    return writer_stage.run(target_date=target_date, model=model, force=force,
                            allow_empty=allow_empty, db_path=db_path)


def get_pipeline_overview(db_path: Optional[Path] = None) -> dict:
    """Return a pipeline kanban overview: counts per stage per status."""
    stages = {}
    for stage in STAGE_ORDER:
        runs = db.query_runs(stage=stage.value, limit=10000, db_path=db_path)
        stages[stage.value] = {
            "total": len(runs),
            "running": sum(1 for r in runs if r["status"] == "running"),
            "done": sum(1 for r in runs if r["status"] == "done"),
            "failed": sum(1 for r in runs if r["status"] == "failed"),
            "recent": runs[:5],
        }
    return stages
