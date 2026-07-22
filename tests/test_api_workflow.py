import tempfile
import unittest
from pathlib import Path
from unittest import mock

from llkc import config, db
from llkc.api import server


class APIWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "llkc.db"
        self.thinking_root = self.root / "thinking"
        db.init_db(self.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_free_write_is_saved_and_synced_without_regex_backreference_errors(self):
        target_date = "2026-07-23"
        self.thinking_root.mkdir(parents=True)
        doc_path = self.thinking_root / f"{target_date}.md"
        doc_path.write_text(
            "---\ntype: daily_thinking\n---\n\n## Free Write\n\n旧内容\n\n---\n\n## Today's 5 Seeds\n",
            encoding="utf-8",
        )
        free_write = r"包含 \1 和 \g<1> 的真实文本"

        with (
            mock.patch.object(config, "DB_PATH", self.db_path),
            mock.patch.object(config, "THINKING_ROOT", self.thinking_root),
        ):
            result = server.update_free_write(
                target_date, server.FreeWriteUpdate(free_write=free_write),
            )

        self.assertTrue(result["ok"])
        self.assertTrue(result["vault_synced"])
        self.assertIn(free_write, doc_path.read_text(encoding="utf-8"))
        entry = db.get_daily_thinking(target_date, db_path=self.db_path)
        self.assertEqual(entry["free_write"], free_write)

    def test_pipeline_run_detail_returns_run_and_events(self):
        with mock.patch.object(config, "DB_PATH", self.db_path):
            run_id = db.create_run(stage="classify", db_path=self.db_path)
            db.log_event("Test.Event", run_id=run_id, db_path=self.db_path)
            detail = server.get_run_detail(run_id)

        self.assertEqual(detail["run"]["id"], run_id)
        self.assertEqual(detail["events"][0]["event_type"], "Test.Event")


if __name__ == "__main__":
    unittest.main()
