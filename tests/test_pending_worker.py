"""Tests for the pending-URL worker daemon."""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llkc import db, config
from llkc.connectors import pending_worker


class TestPendingWorker(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="llkc_test_")
        self.db_path = Path(self._tmp) / "test.db"
        db.init_db(self.db_path)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _enqueue_url(self, url, normalized=None):
        row, created = db.enqueue_pending_url(
            url, normalized or url, source="test", db_path=self.db_path,
        )
        return row, created

    def test_cycle_empty_queue(self):
        """Worker cycle on empty queue should be a no-op."""
        pending_worker._classify_lock = __import__("threading").Lock()
        summary = pending_worker._cycle(db_path=self.db_path)
        self.assertEqual(summary["claimed"], 0)
        self.assertEqual(summary["completed"], 0)

    def test_cycle_with_pending_url(self):
        """Worker cycle should drain a pending URL via url_ingest."""
        self._enqueue_url("https://example.com/test")

        mock_result = MagicMock()
        mock_result.ok = True
        mock_result.unit_id = "url-test-001"
        mock_result.source_type = "generic"
        mock_result.inbox_path = "00-Inbox/test.md"
        mock_result.error = ""

        with patch("llkc.connectors.pending_urls.url_ingest.ingest_url",
                    return_value=mock_result):
            with patch("llkc.connectors.pending_worker._trigger_classify") as mock_trigger:
                summary = pending_worker._cycle(db_path=self.db_path)

        self.assertEqual(summary["completed"], 1)
        self.assertEqual(summary["failed"], 0)
        mock_trigger.assert_called_once()

        queue = db.count_pending_urls(db_path=self.db_path)
        self.assertEqual(queue.get("completed"), 1)
        self.assertEqual(queue.get("pending", 0), 0)

    def test_cycle_with_failed_url(self):
        """Failed URLs should be retried, then marked dead after max_attempts."""
        self._enqueue_url("https://example.com/fail")

        with patch("llkc.connectors.pending_urls.url_ingest.ingest_url",
                    side_effect=RuntimeError("network error")):
            with patch("llkc.connectors.pending_worker._trigger_classify"):
                pending_worker._cycle(db_path=self.db_path)

        queue = db.count_pending_urls(db_path=self.db_path)
        self.assertEqual(queue.get("failed", 0), 1)

    def test_trigger_classify_skips_when_locked(self):
        """If classify is already running, a second call should be skipped."""
        import threading
        pending_worker._classify_lock = threading.Lock()
        pending_worker._classify_lock.acquire()
        try:
            result = pending_worker._trigger_classify(db_path=self.db_path)
            self.assertFalse(result)
        finally:
            pending_worker._classify_lock.release()

    def test_trigger_classify_starts_thread(self):
        """When idle, _trigger_classify should start a background thread."""
        import threading
        pending_worker._classify_lock = threading.Lock()
        with patch("llkc.connectors.pending_worker.parser_stage") if False else \
             patch.dict("llkc.connectors.pending_worker.__dict__", {}):
            pass
        # Just verify the lock is released after calling
        result = pending_worker._trigger_classify(db_path=self.db_path)
        self.assertTrue(result)
        # Lock should be released by the background thread quickly
        import time
        time.sleep(0.5)
        self.assertTrue(pending_worker._classify_lock.acquire(blocking=False))
        pending_worker._classify_lock.release()

    def test_check_dead_urls(self):
        """Dead URLs should be logged as events."""
        # Enqueue and fail enough times to mark dead
        row, _ = self._enqueue_url("https://example.com/dead")
        for _ in range(4):
            with patch("llkc.connectors.pending_urls.url_ingest.ingest_url",
                        side_effect=RuntimeError("always fails")):
                with patch("llkc.connectors.pending_worker._trigger_classify"):
                    pending_worker._cycle(db_path=self.db_path)

        dead = pending_worker._check_dead_urls(db_path=self.db_path)
        self.assertGreaterEqual(len(dead), 1)

        events = db.query_events(limit=50, db_path=self.db_path)
        dead_events = [e for e in events if e["event_type"] == "PendingURL.Dead"]
        self.assertGreaterEqual(len(dead_events), 1)

    def test_run_worker_once_mode(self):
        """Worker in once mode should run one cycle and return."""
        self._enqueue_url("https://example.com/once")

        mock_result = MagicMock()
        mock_result.ok = True
        mock_result.unit_id = "url-once-001"
        mock_result.source_type = "generic"
        mock_result.inbox_path = "00-Inbox/once.md"
        mock_result.error = ""

        with patch("llkc.connectors.pending_urls.url_ingest.ingest_url",
                    return_value=mock_result):
            with patch("llkc.connectors.pending_worker._trigger_classify"):
                result = pending_worker.run_worker(
                    interval=1, once=True, db_path=self.db_path,
                )

        self.assertEqual(result["cycles"], 1)
        self.assertEqual(result["completed"], 1)
        self.assertEqual(result["failed"], 0)


if __name__ == "__main__":
    unittest.main()
