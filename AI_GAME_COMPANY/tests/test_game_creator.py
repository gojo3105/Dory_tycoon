from __future__ import annotations

import json
import shutil
import subprocess
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

    def test_plan_can_create_multiple_mobile_genres(self):
        cases = [
            ("rpg", "RPG", "퀘스트"),
            ("fps", "FPS", "슈팅"),
            ("idle", "Idle", "오프라인 보상"),
        ]
        for genre, label, expected_feature in cases:
            with self.subTest(genre=genre):
                plan = creator.plan_game(self.repo, {
                    "idea": f"도리 {label} 모바일 게임",
                    "genre": genre,
                })
                self.assertEqual(label, plan.spec["game"]["genre"])
                self.assertEqual(label, plan.genre_label)
                self.assertTrue(
                    any(expected_feature in feature for feature in plan.features))
                self.assertEqual("Dori_Default", plan.spec["theme"]["character"])

    def test_style_difficulty_and_theme_can_vary(self):
        plan = creator.plan_game(self.repo, {
            "idea": "얼음 던전에서 로그라이트 전투를 반복하는 모바일 게임",
            "genre": "rpg",
            "style": "roguelite",
            "difficulty": "Expert",
            "theme": "Dungeon",
        })
        self.assertEqual("roguelite", plan.style)
        self.assertEqual("로그라이트 도전", plan.style_label)
        self.assertEqual("Expert", plan.spec["level"]["difficulty"])
        self.assertEqual("Dungeon", plan.spec["theme"]["environment"])
        self.assertEqual(130, plan.spec["level"]["length"])

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
        self.assertEqual("ai-game/game02", plan.git_branch)
        self.assertEqual("not_git", plan.git_status)
        self.assertEqual("GameSpecs/game02_REQUIREMENTS.md", plan.requirements_path)
        self.assertEqual(6, len(plan.growth_paths))
        guide = self.repo / "GameSpecs" / "game02_REQUIREMENTS.md"
        self.assertTrue(guide.is_file())
        self.assertIn("꼭 지켜야 하는 필수 부분", guide.read_text(encoding="utf-8"))
        self.assertTrue(all((self.repo / path).is_file() for path in plan.growth_paths))
        self.assertTrue((self.repo / "Growth" / "game02" / "STORE_LISTING.json").is_file())
        self.assertFalse(path.with_suffix(".json.tmp").exists())

    def test_create_switches_to_a_per_game_git_branch_when_possible(self):
        subprocess.run(["git", "init"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True, capture_output=True)
        subprocess.run([
            "git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
            "commit", "-m", "init",
        ], cwd=self.repo, check=True, capture_output=True)
        plan = creator.create_game(self.repo, {"idea": "도리 방치형 RPG", "genre": "idle"})
        branch = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=self.repo,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=True,
        ).stdout.strip()
        self.assertEqual("ai-game/game02", branch)
        self.assertEqual("created_and_switched", plan.git_status)
        self.assertTrue((self.repo / plan.requirements_path).is_file())

    def test_same_idea_is_deterministic(self):
        payload = {"idea": "빠른 네온 도시 탈출", "difficulty": "Hard"}
        first = creator.plan_game(self.repo, payload).spec
        second = creator.plan_game(self.repo, payload).spec
        self.assertEqual(first, second)

    def test_rejects_unknown_choices_and_oversized_text(self):
        with self.assertRaises(creator.GameCreationError):
            creator.plan_game(self.repo, {"idea": "runner", "style": "shell"})
        with self.assertRaises(creator.GameCreationError):
            creator.plan_game(self.repo, {"idea": "runner", "genre": "console"})
        with self.assertRaises(creator.GameCreationError):
            creator.plan_game(self.repo, {"idea": "runner", "difficulty": "Impossible"})
        with self.assertRaises(creator.GameCreationError):
            creator.plan_game(self.repo, {"idea": "runner", "theme": "Mars"})
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
