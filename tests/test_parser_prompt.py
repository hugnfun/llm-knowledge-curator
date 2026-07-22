import json
import re
import unittest
from unittest import mock

from llkc import config
from llkc.stages import parser


class ParserV03PromptTests(unittest.TestCase):
    def test_config_uses_v03_prompt(self):
        self.assertEqual(config.PARSER_PROMPT_PATH.name, "parser_v0.3.md")
        self.assertTrue(config.PARSER_PROMPT_PATH.exists())

    def test_all_few_shot_outputs_are_valid_json(self):
        prompt = config.PARSER_PROMPT_PATH.read_text(encoding="utf-8")
        samples = re.findall(r"输出:\n(\{.*?\})\n```", prompt, flags=re.DOTALL)
        self.assertEqual(len(samples), 7)
        for sample in samples:
            parsed = json.loads(sample)
            self.assertIn(parsed["verdict"], {"seed", "asset", "archive"})

    def test_classifier_requests_v03_contract(self):
        captured = {}

        def fake_call(messages, **kwargs):
            captured["messages"] = messages
            return {
                "ok": True,
                "text": json.dumps({
                    "verdict": "archive",
                    "category": "",
                    "trigger": "",
                    "reason": "测试",
                    "confidence": "high",
                    "priority": "normal",
                }, ensure_ascii=False),
            }

        unit = {
            "unit_id": "test-1",
            "source": "test",
            "title": "测试",
            "source_path": "test.md",
            "char_len": 2,
        }
        parser.SYSTEM_PROMPT = ""
        with (
            mock.patch.object(parser, "fetch_unit_content", return_value="内容"),
            mock.patch.object(parser, "call_llm", side_effect=fake_call),
        ):
            result = parser.classify_unit(unit)

        self.assertTrue(result["ok"])
        self.assertIn("v0.3", captured["messages"][1]["content"])


if __name__ == "__main__":
    unittest.main()
