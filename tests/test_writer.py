import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from llkc import config, db
from llkc.stages import writer


def _drafts():
    return [
        {
            "angle_id": angle,
            "angle_name": f"角度 {angle}",
            "headline": f"标题 {angle}",
            "draft": f"这是角度 {angle} 的完整正文。",
            "hook": f"钩子 {angle}",
            "image_count": 3,
            "linked_seeds": [],
        }
        for angle in "ABCD"
    ]


class WriterWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "llkc.db"
        self.thinking_root = self.root / "thinking"
        self.drafts_root = self.root / "drafts"
        db.init_db(self.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, target_date="2026-07-23", force=False):
        db.upsert_daily_thinking(
            target_date,
            [],
            free_write="这是今天的自由写。",
            db_path=self.db_path,
        )
        with (
            mock.patch.object(config, "THINKING_ROOT", self.thinking_root),
            mock.patch.object(config, "DRAFTS_ROOT", self.drafts_root),
            mock.patch.object(
                writer,
                "generate_drafts",
                return_value={"ok": True, "drafts": _drafts(), "usage": {"total_tokens": 42}},
            ),
        ):
            return writer.run(target_date=target_date, force=force, db_path=self.db_path)

    def test_draft_field_is_persisted_as_body_and_run_completes(self):
        result = self._run()

        self.assertTrue(result["ok"])
        self.assertEqual(result["drafts"], 4)
        rows = db.get_drafts(date="2026-07-23", db_path=self.db_path)
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(row["body"].startswith("这是角度") for row in rows))
        self.assertIn("这是角度 A 的完整正文", (self.drafts_root / "2026-07-23" / "draft-A.md").read_text())

        run = db.query_runs(stage="draft_generate", limit=1, db_path=self.db_path)[0]
        self.assertEqual(run["status"], "done")
        events = db.query_events(run_id=run["id"], db_path=self.db_path)
        self.assertEqual(json.loads(events[0]["payload"])["date"], "2026-07-23")

    def test_existing_drafts_require_force(self):
        self.assertTrue(self._run()["ok"])
        result = self._run()
        self.assertFalse(result["ok"])
        self.assertIn("force=True", result["error"])
        self.assertEqual(len(db.get_drafts(date="2026-07-23", db_path=self.db_path)), 4)

    def test_downstream_exception_marks_run_failed(self):
        db.upsert_daily_thinking(
            "2026-07-24", [], free_write="自由写", db_path=self.db_path,
        )
        with (
            mock.patch.object(config, "THINKING_ROOT", self.thinking_root),
            mock.patch.object(config, "DRAFTS_ROOT", self.drafts_root),
            mock.patch.object(
                writer,
                "generate_drafts",
                return_value={"ok": True, "drafts": _drafts(), "usage": {}},
            ),
            mock.patch.object(db, "log_event", side_effect=RuntimeError("event failed")),
        ):
            result = writer.run(target_date="2026-07-24", db_path=self.db_path)

        self.assertFalse(result["ok"])
        run = db.query_runs(stage="draft_generate", limit=1, db_path=self.db_path)[0]
        self.assertEqual(run["status"], "failed")
        self.assertIn("event failed", run["error"])

    def test_stale_running_runs_are_recovered(self):
        run_id = db.create_run(stage="draft_generate", db_path=self.db_path)
        stale = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        with db.get_conn(self.db_path) as conn:
            conn.execute("UPDATE pipeline_runs SET started_at=? WHERE id=?", (stale, run_id))

        self.assertEqual(db.fail_stale_runs(3600, db_path=self.db_path), 1)
        run = db.get_run(run_id, db_path=self.db_path)
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["error"], "recovered stale running task")


if __name__ == "__main__":
    unittest.main()
