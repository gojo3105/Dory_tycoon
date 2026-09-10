from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from company.orchestrator import game_creator as creator  # noqa: E402


REPO = Path(__file__).resolve().parents[2]


class GameCreatorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        (self.repo / "GameSpecs").mkdir()
        shutil.copy(REPO / "GameSpecs" / "game01.json",
                    self.repo / "GameSpecs" / "game01.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_plan_uses_next_slot_and_never_changes_the_character(self):
        plan = creator.plan_game(self.repo, {"idea": "중력이 뒤집히는 우주 러너"})
        self.assertEqual("game02", plan.game_id)
        self.assertEqual("gravity", plan.style)
        self.assertEqual("Dori_Default", plan.spec["theme"]["character"])
        self.assertTrue(plan.spec["mechanics"]["gravitySwitch"])
        self.assertEqual("Runner", plan.spec["game"]["genre"])

    def test_preview_does_not_write_a_file(self):
        creator.plan_game(self.repo, {"idea": "코인을 많이 모으는 게임"})
        self.assertFalse((self.repo / "GameSpecs" / "game02.json").exists())

    def test_create_writes_valid_json_atomically(self):
        plan = creator.create_game(self.repo, {
            "idea": "사탕 왕국에서 코인을 모은다",
            "style": "treasure",
            "difficulty": "Easy",
            "theme": "Candy",
            "title": "도리 캔디 러시",
        })
        path = self.repo / "GameSpecs" / "game02.json"
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(plan.spec, saved)
        self.assertEqual("도리 캔디 러시", saved["game"]["title"])
        self.assertFalse(path.with_suffix(".json.tmp").exists())

    def test_same_idea_is_deterministic(self):
        payload = {"idea": "빠른 네온 도시 탈출", "difficulty": "Hard"}
        first = creator.plan_game(self.repo, payload).spec
        second = creator.plan_game(self.repo, payload).spec
        self.assertEqual(first, second)

    def test_rejects_unknown_choices_and_oversized_text(self):
        with self.assertRaises(creator.GameCreationError):
            creator.plan_game(self.repo, {"idea": "runner", "style": "shell"})
        with self.assertRaises(creator.GameCreationError):
            creator.plan_game(self.repo, {"idea": "x" * 801})

    def test_tenth_slot_is_the_last_supported_slot(self):
        for number in range(2, 11):
            (self.repo / "GameSpecs" / f"game{number:02d}.json").write_text(
                "{}", encoding="utf-8")
        with self.assertRaises(creator.GameCreationError):
            creator.next_game_id(self.repo)


if __name__ == "__main__":
    unittest.main(verbosity=2)
