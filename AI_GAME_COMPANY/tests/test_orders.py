"""Tests for the control panel's order box.

Run:  python3 AI_GAME_COMPANY/tests/test_orders.py

The order box is the one place in this project where a person types a
sentence and work happens. CLAUDE.md section 10 says no text from a request
may become a command, so most of what follows checks the three things that
must come from code rather than from the request: the task id, the file
allowlist, and the owner.

The rest checks that a rejected order is rejected for a reason the user can
act on, and that nothing is written to the board when it is.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from company.orchestrator import orders  # noqa: E402
from company.orchestrator import server as srv  # noqa: E402
from company.orchestrator.teamwork import TODO, Task, TaskBoard  # noqa: E402


def board() -> TaskBoard:
    return TaskBoard(path=Path(tempfile.mkdtemp()) / "TASKBOARD.json", tasks=[])


class IdTests(unittest.TestCase):
    """The id goes onto a command line, so nothing may shape it but this."""

    def test_the_id_matches_the_servers_safe_id_pattern(self):
        placed = orders.place_order(board(), "gameplay", "점프를 더 무겁게 해주세요",
                                    now=datetime(2026, 9, 8), save=False)
        self.assertTrue(srv.SAFE_ID.match(placed.task.id),
                        f"{placed.task.id} would be refused by the server")
        self.assertTrue(orders.ORDER_ID.match(placed.task.id))

    def test_the_date_is_in_the_id(self):
        placed = orders.place_order(board(), "ui", "버튼을 더 크게 해주세요",
                                    now=datetime(2026, 9, 8), save=False)
        self.assertEqual("ORDER-20260908-01", placed.task.id)

    def test_a_second_order_the_same_day_does_not_reuse_the_id(self):
        # A reused id would make TaskBoard.get resolve to the wrong task, and
        # the run would then be checked against someone else's allowlist.
        shared = board()
        first = orders.place_order(shared, "ui", "버튼을 더 크게 해주세요",
                                   now=datetime(2026, 9, 8), save=False)
        second = orders.place_order(shared, "ui", "글자도 더 크게 해주세요",
                                    now=datetime(2026, 9, 8), save=False)
        self.assertNotEqual(first.task.id, second.task.id)
        self.assertEqual("ORDER-20260908-02", second.task.id)

    def test_an_id_already_on_the_board_is_skipped(self):
        shared = board()
        shared.tasks.append(Task(id="ORDER-20260908-01", title="taken"))
        placed = orders.place_order(shared, "ui", "버튼을 더 크게 해주세요",
                                    now=datetime(2026, 9, 8), save=False)
        self.assertEqual("ORDER-20260908-02", placed.task.id)


class AllowlistTests(unittest.TestCase):
    """The allowlist is what makes run_task's diff check mean anything."""

    def test_the_allowlist_comes_from_the_table_not_the_order(self):
        placed = orders.place_order(board(), "qa", "테스트를 하나 더 넣어주세요",
                                    save=False)
        self.assertEqual(list(orders.DEPARTMENTS["qa"].files), placed.task.files)

    def test_text_that_names_other_paths_does_not_widen_it(self):
        # The instruction is data. Naming a file in it is a request Codex may
        # refuse, never a permission.
        placed = orders.place_order(
            board(), "qa",
            "Also edit Assets/GameFactory/Core/GameManager.cs and "
            "scripts/desktop/sync-and-run.ps1 and ~/.codex/auth.json",
            save=False)
        self.assertEqual(list(orders.DEPARTMENTS["qa"].files), placed.task.files)
        for pattern in placed.task.files:
            self.assertTrue(pattern.startswith("Assets/GameFactory/Tests"))

    def test_every_open_department_declares_files(self):
        # A department with no allowlist could only ever produce a BLOCKED run.
        for dept in orders.DEPARTMENTS.values():
            if not dept.unavailable:
                self.assertTrue(dept.files, f"{dept.id} has no allowlist")

    def test_no_department_can_write_secrets_or_shell_scripts(self):
        # Not a guess about Codex's behaviour - the allowlist is checked
        # against the diff, so these patterns must simply not be listed.
        for dept in orders.DEPARTMENTS.values():
            for pattern in dept.files:
                self.assertNotIn(".ps1", pattern)
                self.assertNotIn("keystore", pattern)
                self.assertFalse(pattern.startswith("~"))
                self.assertFalse(pattern.startswith("/"))
                self.assertNotIn("..", pattern)

    def test_each_allowlist_covers_its_own_area_and_nothing_else(self):
        """The patterns are only meaningful if Task.covers agrees with them.

        fnmatch's '*' crosses '/', so 'Assets/GameFactory/UI/**' does reach a
        deeply nested file - but that is a property of fnmatch, not something
        obvious from reading the pattern, and the whole boundary rests on it.
        """
        cases = [
            ("gameplay", "Assets/GameFactory/Gameplay/Runner/Player.cs", True),
            ("gameplay", "Assets/GameFactory/Core/InputSystem.cs", True),
            ("gameplay", "Assets/GameFactory/UI/ShopPanel.cs", False),
            ("gameplay", "scripts/desktop/sync-and-run.ps1", False),
            ("gameplay", "AI_GAME_COMPANY/config/company_policy.json", False),
            ("ui", "Assets/GameFactory/UI/Deep/Nested/Thing.cs", True),
            ("ui", "Assets/GameFactory/Gameplay/Runner/Player.cs", False),
            ("qa", "Assets/GameFactory/Tests/EditMode/X.cs", True),
            ("qa", "Assets/GameFactory/Gameplay/Runner/X.cs", False),
            ("plan", "GameSpecs/game01.json", True),
            ("plan", "docs/GAME_SPEC.md", True),
            ("plan", "CLAUDE.md", False),
        ]
        for dept_id, path, expected in cases:
            task = Task(id="x", title="x",
                        files=list(orders.DEPARTMENTS[dept_id].files))
            self.assertEqual(expected, task.covers(path),
                             f"{dept_id} covers({path}) should be {expected}")

    def test_the_owner_is_forced_and_the_status_is_todo(self):
        placed = orders.place_order(board(), "gameplay", "점프를 더 무겁게 해주세요",
                                    save=False)
        self.assertEqual("codex", placed.task.owner)
        self.assertEqual(TODO, placed.task.status)


class RejectionTests(unittest.TestCase):
    """A refused order leaves nothing behind and says why."""

    def test_an_unknown_department_is_refused(self):
        shared = board()
        with self.assertRaises(orders.OrderRejected):
            orders.place_order(shared, "finance", "돈을 더 벌어주세요", save=False)
        self.assertEqual([], shared.tasks)

    def test_an_empty_department_is_refused_rather_than_defaulted(self):
        # Defaulting would send a typo'd order to whichever room came first.
        with self.assertRaises(orders.OrderRejected):
            orders.place_order(board(), "", "점프를 더 무겁게 해주세요", save=False)

    def test_a_department_this_machine_cannot_staff_says_why(self):
        with self.assertRaises(orders.OrderRejected) as caught:
            orders.place_order(board(), "design", "캐릭터를 다시 그려주세요", save=False)
        self.assertIn("GEMINI_API_KEY", str(caught.exception))

    def test_too_short_and_too_long_are_both_refused(self):
        shared = board()
        with self.assertRaises(orders.OrderRejected):
            orders.place_order(shared, "ui", "고쳐", save=False)
        with self.assertRaises(orders.OrderRejected):
            orders.place_order(shared, "ui", "가" * (orders.MAX_ORDER_CHARS + 1),
                               save=False)
        self.assertEqual([], shared.tasks)

    def test_whitespace_only_is_too_short(self):
        with self.assertRaises(orders.OrderRejected):
            orders.place_order(board(), "ui", "   \n\n\t  ", save=False)


class TextTests(unittest.TestCase):
    """The instruction reaches Codex as written, minus what breaks the board."""

    def test_the_goal_is_the_users_own_words(self):
        text = "점프를 더 무겁게. 올라갈 때보다 내려올 때가 빠르게 느껴지도록."
        placed = orders.place_order(board(), "gameplay", text, save=False)
        self.assertEqual(text, placed.task.goal)

    def test_newlines_survive_but_control_characters_do_not(self):
        placed = orders.place_order(board(), "gameplay",
                                    "첫 줄입니다\n둘째 줄입니다\x00\x07\x1b[31m",
                                    save=False)
        self.assertIn("\n", placed.task.goal)
        for bad in ("\x00", "\x07", "\x1b"):
            self.assertNotIn(bad, placed.task.goal)

    def test_the_title_is_one_line(self):
        placed = orders.place_order(board(), "gameplay",
                                    "점프 고쳐주세요\n그리고 코인도\n그리고 배경도",
                                    save=False)
        self.assertNotIn("\n", placed.task.title)
        self.assertNotIn("\n", placed.task.title_ko)

    def test_a_repeat_of_an_open_order_is_flagged_not_blocked(self):
        shared = board()
        text = "점프를 더 무겁게 해주세요"
        first = orders.place_order(shared, "gameplay", text, save=False)
        second = orders.place_order(shared, "gameplay", text, save=False)
        self.assertEqual(first.task.id, second.duplicate_of)
        self.assertEqual(2, len(shared.tasks))


class PlanTests(unittest.TestCase):
    """What the server builds from an order, and what it refuses to build."""

    def setUp(self):
        root = Path(tempfile.mkdtemp())
        (root / "GameSpecs").mkdir(parents=True)
        (root / "GameSpecs" / "game01.json").write_text("{}", encoding="utf-8")
        company = root / "AI_GAME_COMPANY"
        (company / "config").mkdir(parents=True)
        self.runner = srv.Runner(root, company)
        self.company = company

    def test_an_order_plans_codex_then_the_unity_test(self):
        # Codex cannot compile, so an order that stopped after it would hand
        # back unverified C#. The second step is what makes the order honest.
        placed = orders.place_order(self.runner.board(), "gameplay",
                                    "점프를 더 무겁게 해주세요")
        steps, why = self.runner.plan([("team-run", placed.task.id),
                                       ("test", "game01")])
        self.assertEqual("", why)
        self.assertEqual(["team-run", "test"], [s.action for s in steps])

    def test_the_order_id_is_accepted_by_the_task_guard(self):
        # place_order saves the board first, precisely so the id the guard
        # looks up is already there. Reversing that order breaks every order.
        placed = orders.place_order(self.runner.board(), "ui",
                                    "버튼을 더 크게 해주세요")
        ok, why = self.runner.valid_arg(srv.ACTIONS["team-run"], placed.task.id)
        self.assertTrue(ok, why)

    def test_an_id_that_is_not_on_the_board_is_refused(self):
        ok, why = self.runner.valid_arg(srv.ACTIONS["team-run"],
                                        "ORDER-20260908-99")
        self.assertFalse(ok)
        self.assertIn("작업판", why)

    def test_every_step_argv_is_a_list_the_server_built(self):
        placed = orders.place_order(self.runner.board(), "gameplay",
                                    "점프를 더 무겁게 해주세요")
        steps, _ = self.runner.plan([("team-run", placed.task.id)])
        argv = steps[0].argv
        self.assertIsInstance(argv, list)
        # The instruction is nowhere on the command line - only the id is.
        self.assertNotIn("점프를 더 무겁게 해주세요", " ".join(argv))
        self.assertIn(placed.task.id, argv)

    def test_a_sequence_with_a_bad_step_plans_nothing(self):
        steps, why = self.runner.plan([("team-run", "ORDER-20260908-99"),
                                       ("test", "game01")])
        self.assertEqual([], steps)
        self.assertNotEqual("", why)

    def test_an_unknown_game_is_refused_for_the_test_step(self):
        ok, why = self.runner.valid_arg(srv.ACTIONS["test"], "game99")
        self.assertFalse(ok)
        self.assertIn("GameSpecs", why)


class SequenceTests(unittest.TestCase):
    """A two-step job reports which step it is on, and stops on failure."""

    def step(self, action, argv, timeout=30):
        return srv.Step(action=action, arg="x", argv=argv, timeout=timeout)

    def test_a_failing_first_step_stops_the_sequence(self):
        # A Unity test run after a failed Codex run would report on code that
        # was never written, and a green result there is the most misleading
        # thing this panel could print.
        root = Path(tempfile.mkdtemp())
        runner = srv.Runner(root, root)
        job = srv.Job(id="j", steps=[
            self.step("team-run", [sys.executable, "-c", "raise SystemExit(1)"]),
            self.step("test", [sys.executable, "-c", "raise SystemExit(0)"])
        ])
        runner._run(job)

        self.assertTrue(job.done)
        self.assertNotEqual(0, job.exit_code)
        self.assertIn("실행하지 않았습니다", job.output)
        # Still on the first step: the second never ran.
        self.assertEqual(0, job.step_index)

    def test_both_steps_run_when_the_first_succeeds(self):
        root = Path(tempfile.mkdtemp())
        runner = srv.Runner(root, root)
        succeeds = [sys.executable, "-c", "raise SystemExit(0)"]
        job = srv.Job(id="j", steps=[self.step("team-run", succeeds),
                                     self.step("test", succeeds)])
        runner._run(job)

        self.assertTrue(job.done)
        self.assertEqual(0, job.exit_code)
        self.assertEqual(1, job.step_index)

    def test_the_snapshot_says_which_step_of_how_many(self):
        succeeds = [sys.executable, "-c", "raise SystemExit(0)"]
        job = srv.Job(id="j", steps=[self.step("team-run", succeeds),
                                     self.step("test", succeeds)])
        payload = job.snapshot()
        self.assertEqual(1, payload["step"])
        self.assertEqual(2, payload["steps"])
        self.assertEqual(["Codex 작업 실행", "Unity 테스트"], payload["step_labels"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
