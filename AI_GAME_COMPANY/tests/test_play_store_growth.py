import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from company.orchestrator import game_creator  # noqa: E402
from company.orchestrator.play_store_growth import (  # noqa: E402
    PRODUCT_TARGETS, TECHNICAL_GATES, evaluate_metrics, expected_paths,
    write_growth_package,
)


REPO = Path(__file__).resolve().parents[2]


class PlayStoreGrowthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "GameSpecs").mkdir()
        (self.root / "GameSpecs" / "game01.json").write_text(
            (REPO / "GameSpecs" / "game01.json").read_text(encoding="utf-8"),
            encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_every_game_gets_a_complete_growth_package(self):
        plan = game_creator.plan_game(self.root, {"idea": "도리 방치형 공장", "genre": "idle"})
        paths = write_growth_package(self.root, plan)
        self.assertEqual(expected_paths("game02"), paths)
        self.assertTrue(all((self.root / path).is_file() for path in paths))
        listing = json.loads((self.root / paths[1]).read_text(encoding="utf-8"))
        experiments = json.loads((self.root / paths[3]).read_text(encoding="utf-8"))
        self.assertLessEqual(len(listing["app_name"]), 30)
        self.assertEqual(3, len(experiments["experiments"]))

    def test_unknown_metrics_do_not_become_success(self):
        result = evaluate_metrics({})
        self.assertFalse(result.ready)
        self.assertEqual(0, result.score)
        self.assertTrue(result.gaps)

    def test_all_measured_targets_can_pass(self):
        metrics = {}
        for name, limit in TECHNICAL_GATES.items():
            metrics[name] = limit
        for name, limit in PRODUCT_TARGETS.items():
            metrics[name] = limit
        result = evaluate_metrics(metrics)
        self.assertTrue(result.ready)
        self.assertEqual(100, result.score)


if __name__ == "__main__":
    unittest.main()
