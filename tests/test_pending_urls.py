import tempfile
import unittest
from pathlib import Path
from unittest import mock

from llkc import db, pipeline
from llkc.connectors import pending_urls, url_ingest


class PendingURLConnectorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "llkc.db"
        db.init_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def enqueue(self, url="https://example.com/article"):
        return db.enqueue_pending_url(url, url, db_path=self.db_path)[0]

    def test_successful_ingest_completes_queue_item(self):
        row = self.enqueue()
        result = url_ingest.IngestResult(
            ok=True,
            source_type="generic",
            unit_id="url-1",
            inbox_path="00-Inbox/URL-Ingest/test.md",
            title="Test",
        )
        with mock.patch.object(pending_urls.url_ingest, "ingest_url", return_value=result) as ingest:
            summary = pending_urls.run(db_path=self.db_path)

        ingest.assert_called_once_with(row["url"], db_path=self.db_path)
        self.assertEqual(summary["completed"], 1)
        saved = db.query_pending_urls(db_path=self.db_path)[0]
        self.assertEqual(saved["status"], "completed")
        self.assertEqual(saved["item_id"], "url-1")

    def test_failure_retries_then_moves_to_dead(self):
        self.enqueue()
        failure = url_ingest.IngestResult(ok=False, source_type="generic", error="boom")
        with mock.patch.object(pending_urls.url_ingest, "ingest_url", return_value=failure):
            first = pending_urls.run(max_attempts=2, db_path=self.db_path)
            second = pending_urls.run(max_attempts=2, db_path=self.db_path)
            third = pending_urls.run(max_attempts=2, db_path=self.db_path)

        self.assertEqual(first["failed"], 1)
        self.assertEqual(second["dead"], 1)
        self.assertEqual(third["claimed"], 0)
        saved = db.query_pending_urls(db_path=self.db_path)[0]
        self.assertEqual(saved["status"], "dead")
        self.assertEqual(saved["attempts"], 2)

    def test_stale_processing_item_is_recovered(self):
        row = self.enqueue()
        with db.get_conn(self.db_path) as connection:
            connection.execute(
                """UPDATE pending_urls
                   SET status='processing', attempts=1,
                       processed_at='2000-01-01T00:00:00+00:00'
                   WHERE id=?""",
                (row["id"],),
            )

        claimed = db.claim_pending_urls(
            limit=1, max_attempts=3, stale_after_seconds=1, db_path=self.db_path
        )
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0]["status"], "processing")
        self.assertEqual(claimed[0]["attempts"], 2)


class IncrementalPipelineOrderTests(unittest.TestCase):
    def test_pending_urls_run_before_inbox_scan(self):
        calls = []

        def pending_run(**_kwargs):
            calls.append("pending_urls")
            return {"claimed": 0}

        def scan(**_kwargs):
            calls.append("scan")
            return []

        def pool(**_kwargs):
            calls.append("pool")
            return {"written": 0}

        with (
            mock.patch.object(pipeline.pending_urls, "run", side_effect=pending_run),
            mock.patch.object(pipeline.obsidian_inbox, "scan_inbox", side_effect=scan),
            mock.patch.object(pipeline.db, "query_items", return_value=[]),
            mock.patch.object(pipeline.write_back_stage, "run", side_effect=pool),
        ):
            result = pipeline.run_incremental(db_path=self._temp_db())

        self.assertEqual(calls, ["pending_urls", "scan", "pool"])
        self.assertEqual(result["scan"], {"units": 0})

    def _temp_db(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        path = Path(temp_dir.name) / "llkc.db"
        db.init_db(path)
        return path


if __name__ == "__main__":
    unittest.main()
