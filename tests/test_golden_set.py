"""Tests for the v0.3 golden set: structure, domain consistency, decision-tree invariants."""

import json
import unittest
from pathlib import Path
from collections import Counter

GOLDEN_PATH = Path(__file__).parent / "golden_set" / "golden.json"
SAMPLES_PATH = Path(__file__).parent / "golden_set" / "samples.json"

SEED_CATEGORIES = {"养狗对立", "对立", "共鸣补充"}
ASSET_CATEGORIES = {"案例", "方法论", "工具", "教程"}
VALID_VERDICTS = {"seed", "asset", "archive"}
VALID_PRIORITIES = {"high", "normal"}
VALID_CONFIDENCES = {"high", "medium", "low"}
VALID_STEPS = {
    "step1_dog", "step2_anti_seed", "step3_tool",
    "step4_focus", "step5_tutorial", "step6_archive",
}


class GoldenSetStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
        cls.samples = json.loads(SAMPLES_PATH.read_text(encoding="utf-8"))

    def test_has_40_items(self):
        self.assertEqual(len(self.golden), 40)

    def test_unit_ids_match_samples(self):
        sample_ids = [s["unit_id"] for s in self.samples]
        golden_ids = [g["unit_id"] for g in self.golden]
        self.assertEqual(sample_ids, golden_ids)

    def test_every_item_has_required_fields(self):
        for g in self.golden:
            with self.subTest(unit_id=g["unit_id"]):
                self.assertIn(g["golden"]["verdict"], VALID_VERDICTS)
                self.assertIn(g["golden"]["priority"], VALID_PRIORITIES)
                self.assertIn(g["golden"]["confidence"], VALID_CONFIDENCES)
                self.assertIn(g["golden"]["decision_step"], VALID_STEPS)

    def test_category_matches_verdict_domain(self):
        """v0.3: category must be in the correct domain for its verdict."""
        for g in self.golden:
            gv = g["golden"]
            with self.subTest(unit_id=g["unit_id"]):
                if gv["verdict"] == "seed":
                    self.assertIn(gv["category"], SEED_CATEGORIES)
                elif gv["verdict"] == "asset":
                    self.assertIn(gv["category"], ASSET_CATEGORIES)
                elif gv["verdict"] == "archive":
                    self.assertEqual(gv["category"], "")

    def test_trigger_required_for_seed(self):
        for g in self.golden:
            gv = g["golden"]
            if gv["verdict"] == "seed":
                with self.subTest(unit_id=g["unit_id"]):
                    self.assertTrue(gv["trigger"], "seed must have non-empty trigger")

    def test_reason_required_for_all(self):
        for g in self.golden:
            with self.subTest(unit_id=g["unit_id"]):
                self.assertTrue(g["golden"]["reason"], "every item must have a reason")

    def test_step3_implies_high_priority(self):
        """Decision tree invariant: Step 3 (tool meta-topic) always sets priority=high."""
        for g in self.golden:
            gv = g["golden"]
            if gv["decision_step"] == "step3_tool":
                with self.subTest(unit_id=g["unit_id"]):
                    self.assertEqual(gv["priority"], "high")

    def test_step1_implies_seed_and_dog_category(self):
        for g in self.golden:
            gv = g["golden"]
            if gv["decision_step"] == "step1_dog":
                with self.subTest(unit_id=g["unit_id"]):
                    self.assertEqual(gv["verdict"], "seed")
                    self.assertEqual(gv["category"], "养狗对立")

    def test_step6_implies_archive(self):
        for g in self.golden:
            gv = g["golden"]
            if gv["decision_step"] == "step6_archive":
                with self.subTest(unit_id=g["unit_id"]):
                    self.assertEqual(gv["verdict"], "archive")

    def test_distribution_within_reasonable_bounds(self):
        """Golden set is a curated test set, not full inventory, so bounds are wider."""
        vc = Counter(g["golden"]["verdict"] for g in self.golden)
        total = len(self.golden)
        seed_pct = vc["seed"] / total
        archive_pct = vc["archive"] / total
        # Test set has more seeds than full inventory, but still minority
        self.assertLess(seed_pct, 0.35, f"seed {seed_pct:.0%} too high for test set")
        self.assertGreater(archive_pct, 0.10, f"archive {archive_pct:.0%} too low")


if __name__ == "__main__":
    unittest.main()
