import json
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from llkc import db
from llkc.connectors import lark_listener


class LarkURLExtractionTests(unittest.TestCase):
    def test_extracts_share_urls_and_strips_chinese_punctuation(self):
        content = (
            "看看这个：https://v.douyin.com/abc123/，还有 "
            "https://www.xiaohongshu.com/explore/xyz?xsec_token=a&amp;source=share。"
        )
        self.assertEqual(
            lark_listener.extract_urls(content),
            [
                "https://v.douyin.com/abc123/",
                "https://www.xiaohongshu.com/explore/xyz?xsec_token=a&source=share",
            ],
        )

    def test_normalization_preserves_query_and_removes_fragment(self):
        self.assertEqual(
            lark_listener.normalize_url("HTTPS://Example.COM:443/path/?token=AbC#section"),
            "https://example.com/path?token=AbC",
        )


class LarkCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "llkc.db"
        db.init_db(self.db_path)
        self.event = {
            "type": "im.message.receive_v1",
            "event_id": "evt-1",
            "message_id": "om-1",
            "message_type": "text",
            "chat_id": "oc-1",
            "chat_type": "p2p",
            "sender_id": "ou-1",
            "create_time": "1780000000000",
            "content": "https://example.com/article#top",
        }

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_capture_is_idempotent_and_audited(self):
        first = lark_listener.capture_event(self.event, db_path=self.db_path)
        second = lark_listener.capture_event(self.event, db_path=self.db_path)

        self.assertEqual(first["captured"], 1)
        self.assertEqual(second["duplicates"], 1)
        rows = db.query_pending_urls(db_path=self.db_path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["normalized_url"], "https://example.com/article")
        self.assertEqual(rows[0]["source_message_id"], "om-1")
        events = db.query_events(db_path=self.db_path)
        self.assertEqual([event["event_type"] for event in events], ["PendingURL.Captured"])

    def test_filters_message_type_chat_and_sender(self):
        interactive = {**self.event, "message_type": "interactive"}
        self.assertEqual(
            lark_listener.capture_event(interactive, db_path=self.db_path)["ignored"],
            "message_type",
        )
        self.assertEqual(
            lark_listener.capture_event(
                self.event, db_path=self.db_path, allowed_chat_ids={"oc-other"}
            )["ignored"],
            "chat_id",
        )
        self.assertEqual(
            lark_listener.capture_event(
                self.event, db_path=self.db_path, allowed_sender_ids={"ou-other"}
            )["ignored"],
            "sender_id",
        )

    def test_listener_waits_for_ready_and_consumes_ndjson(self):
        fake_cli = Path(self.temp_dir.name) / "fake-lark-cli"
        fake_cli.write_text(
            textwrap.dedent(
                f"""\
                #!{sys.executable}
                import json, sys, time
                print('[event] ready event_key=im.message.receive_v1', file=sys.stderr, flush=True)
                time.sleep(0.1)
                print(json.dumps({json.dumps(self.event)}), flush=True)
                time.sleep(0.1)
                """
            ),
            encoding="utf-8",
        )
        os.chmod(fake_cli, 0o755)

        result = lark_listener.run_listener(
            lark_cli=str(fake_cli), ready_timeout=2, db_path=self.db_path
        )
        self.assertEqual(result["events"], 1)
        self.assertEqual(result["captured"], 1)


if __name__ == "__main__":
    unittest.main()
