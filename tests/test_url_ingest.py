import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from llkc import config, db
from llkc.connectors import opencli_client, url_ingest


class OpenCLIClientTests(unittest.TestCase):
    def test_decode_json_ignores_update_notice_version(self):
        output = "Update available: v1.8.4 -> v1.8.6\n[{\"field\": \"title\"}]"
        self.assertEqual(opencli_client._decode_json(output), [{"field": "title"}])

    def test_adapter_gets_json_flag_but_browser_command_does_not(self):
        completed = subprocess.CompletedProcess([], 0, stdout='{"ok": true}', stderr="")
        with mock.patch.object(opencli_client.subprocess, "run", return_value=completed) as run:
            opencli_client._run(["xiaohongshu", "note", "https://example.test/note"])
            self.assertEqual(run.call_args.args[0][-2:], ["-f", "json"])

            opencli_client._run(["browser", "test", "eval", "({ok:true})"])
            self.assertNotIn("-f", run.call_args.args[0])

    def test_douyin_owned_session_is_closed(self):
        detail = {"desc": "demo", "video_url": "https://cdn.test/video.mp4"}
        with (
            mock.patch.object(opencli_client, "_run", return_value={}) as run,
            mock.patch.object(opencli_client, "_browser_eval", return_value=detail),
        ):
            self.assertEqual(
                opencli_client.douyin_video_detail("https://www.douyin.com/video/123", "dy-test"),
                detail,
            )
        self.assertEqual(run.call_args_list[-1].args[0], ["browser", "dy-test", "close"])


class UrlAdapterTests(unittest.TestCase):
    def test_xhs_adapter_maps_opencli_output_and_downloaded_media(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)

            def fake_download(_url, output_dir):
                media_dir = Path(output_dir) / "note123"
                media_dir.mkdir(parents=True)
                (media_dir / "note123_1.jpg").write_bytes(b"image")
                return [{"index": 1, "type": "image", "status": "success", "size": "5 B"}]

            note = {
                "title": "标题",
                "author": "作者",
                "content": "正文",
                "likes": "12",
                "collects": "3",
                "comments": "4",
            }
            with (
                mock.patch.object(url_ingest, "INGEST_TMP_ROOT", temp_root),
                mock.patch.object(opencli_client, "xhs_note", return_value=note),
                mock.patch.object(opencli_client, "xhs_download", side_effect=fake_download),
            ):
                result = url_ingest._adapter_xhs("https://www.xiaohongshu.com/explore/note123")

            self.assertEqual(result["title"], "标题")
            self.assertEqual(result["desc"], "正文")
            self.assertEqual(result["note_type"], "image")
            self.assertEqual(len(result["images"]), 1)

    def test_douyin_keeps_video_when_transcription_is_unavailable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            detail = {
                "aweme_id": "123",
                "desc": "演示文案",
                "author": "作者",
                "video_url": "https://cdn.test/video.mp4",
                "likes": "10",
            }

            def fake_video_download(_url, destination):
                Path(destination).write_bytes(b"video")

            with (
                mock.patch.object(url_ingest, "INGEST_TMP_ROOT", temp_root),
                mock.patch.object(opencli_client, "douyin_video_detail", return_value=detail),
                mock.patch.object(opencli_client, "download_video", side_effect=fake_video_download),
                mock.patch.object(
                    url_ingest,
                    "_qwen3tts_transcribe",
                    side_effect=RuntimeError("ASR offline"),
                ),
            ):
                result = url_ingest._adapter_douyin("https://www.douyin.com/video/123")

            self.assertTrue(Path(result["video_path"]).exists())
            self.assertEqual(result["transcript"], "")
            self.assertEqual(result["transcript_meta"]["error"], "ASR offline")


class UrlPersistenceTests(unittest.TestCase):
    def test_register_item_saves_absolute_path_for_parser(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            db_path = temp_root / "llkc.db"
            vault_root = temp_root / "vault"
            db.init_db(db_path)

            with (
                mock.patch.object(config, "DB_PATH", db_path),
                mock.patch.object(config, "VAULT_ROOT", vault_root),
            ):
                url_ingest._register_item(
                    "url-test",
                    "xhs",
                    "00-Inbox/URL-Ingest/test.md",
                    "Test",
                    "preview",
                    7,
                    "content",
                )

            with closing(sqlite3.connect(db_path)) as connection:
                row = connection.execute(
                    "SELECT abs_path, raw_content FROM items WHERE unit_id='url-test'"
                ).fetchone()
            self.assertEqual(row[0], str(vault_root / "00-Inbox/URL-Ingest/test.md"))
            self.assertEqual(row[1], "content")

    def test_ingest_url_writes_markdown_and_registers_readable_item(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            db_path = temp_root / "llkc.db"
            vault_root = temp_root / "vault"
            inbox_root = vault_root / "00-Inbox" / "URL-Ingest"
            db.init_db(db_path)
            adapter_result = {
                "title": "Test note",
                "author": "Author",
                "desc": "Full note body",
                "note_type": "text",
                "images": [],
                "video_path": None,
                "likes": "9",
            }

            with (
                mock.patch.object(config, "DB_PATH", db_path),
                mock.patch.object(config, "VAULT_ROOT", vault_root),
                mock.patch.object(url_ingest, "INGEST_INBOX_DIR", inbox_root),
                mock.patch.object(url_ingest, "_adapter_xhs", return_value=adapter_result),
            ):
                result = url_ingest.ingest_url(
                    "https://www.xiaohongshu.com/explore/note123"
                )

            self.assertTrue(result.ok)
            markdown_path = vault_root / result.inbox_path
            self.assertTrue(markdown_path.exists())
            self.assertIn("Full note body", markdown_path.read_text(encoding="utf-8"))
            item = db.get_item(result.unit_id, db_path=db_path)
            self.assertEqual(item["abs_path"], str(markdown_path))
            self.assertTrue(Path(item["abs_path"]).exists())


if __name__ == "__main__":
    unittest.main()
