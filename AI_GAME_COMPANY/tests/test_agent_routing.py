"""Tests for agent_dispatcher: who runs a task, and who may not.

Run:  python AI_GAME_COMPANY/tests/test_agent_routing.py

The dispatcher decides WHO writes. It must never become a way to decide WHAT
may be written - the files allowlist is the only thing that does that, and a
role that could widen it would quietly undo teamwork.run_task's diff check.
Several tests below exist only to hold that line.

The other thing held here is backward compatibility. Thirty tasks on the
board predate the twelve roles entirely; they have to keep loading, running
and saving exactly as before, and adding the role fields must not rewrite a
single one of them.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from company.orchestrator import agent_dispatcher as dispatcher  # noqa: E402
from company.orchestrator.agent_registry import AgentRegistry  # noqa: E402
from company.orchestrator.teamwork import Task, TaskBoard, build_prompt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "config" / "AGENTS.json"


def registry() -> AgentRegistry:
    return AgentRegistry.load(REGISTRY)


class RoutingTests(unittest.TestCase):
    """A task with no role is routed by what it says it is."""

    def route(self, title: str) -> str:
        return dispatcher.dispatch(Task(id="T", title=title), registry()).agent_id

    def test_each_category_reaches_its_specialist(self):
        cases = {
            "점프를 더 무겁게": "gameplay_engineer",
            "상점 버튼 UI 정리": "ui_ux_engineer",
            "PlayMode 테스트 추가": "qa_engineer",
            "APK 배포 준비": "release_engineer",
            "코드 리뷰": "quality_reviewer",
            "배경 아트 교체": "art_director",
            "레벨 배치 수정": "level_designer",
            "저장 시스템 정리": "systems_engineer",
            "씬 생성기 구조 변경": "technical_director",
            "밸런스 기획": "game_director",
        }
        for title, expected in cases.items():
            self.assertEqual(expected, self.route(title), f"{title!r} routed wrong")

    def test_an_explicit_role_is_never_overridden_by_the_title(self):
        # The title says UI, the task says systems. The task wins - guessing
        # over a stated answer would make agent_role unreliable to set.
        task = Task(id="T", title="상점 버튼 UI 정리", agent_role="systems_engineer")
        self.assertEqual("systems_engineer",
                         dispatcher.dispatch(task, registry()).agent_id)

    def test_an_unroutable_task_is_an_error_not_a_default(self):
        with self.assertRaises(dispatcher.DispatchError):
            dispatcher.dispatch(Task(id="T", title="zzzzz"), registry())

    def test_a_default_role_is_used_only_when_asked_for(self):
        resolved = dispatcher.dispatch(Task(id="T", title="zzzzz"), registry(),
                                       default_role="producer")
        self.assertEqual("producer", resolved.agent_id)
        self.assertTrue(resolved.inferred)

    def test_routing_reports_whether_it_guessed(self):
        stated = dispatcher.dispatch(
            Task(id="T", title="x", agent_role="ceo"), registry())
        guessed = dispatcher.dispatch(Task(id="T", title="점프 수정"), registry())
        self.assertFalse(stated.inferred)
        self.assertTrue(guessed.inferred)


class RefusalTests(unittest.TestCase):
    """Every refusal the dispatcher owes the project."""

    def test_an_unknown_role_is_refused_and_names_the_real_ones(self):
        with self.assertRaises(dispatcher.DispatchError) as caught:
            dispatcher.dispatch(
                Task(id="T", title="x", agent_role="gameplay_enginer"), registry())
        self.assertIn("gameplay_engineer", str(caught.exception))

    def test_a_role_cannot_take_a_task_type_it_is_not_for(self):
        with self.assertRaises(dispatcher.DispatchError):
            dispatcher.dispatch(
                Task(id="T", title="x", agent_role="art_director",
                     task_type="gameplay_code"), registry())

    def test_a_read_only_role_may_not_edit_production_code(self):
        # The point of can_modify_code false: a reviewer that fixes what it
        # reviews is reviewing its own work.
        for role in ("quality_reviewer", "ceo"):
            with self.assertRaises(dispatcher.ReadOnlyRoleError, msg=role):
                dispatcher.dispatch(
                    Task(id="T", title="fix it", agent_role=role,
                         files=["Assets/GameFactory/Gameplay/Player.cs"]),
                    registry())

    def test_a_read_only_role_may_write_documents(self):
        # can_modify_code is about CODE. A design or review role has to be
        # able to write its output down, or every planning task would finish
        # having changed nothing and run_task would call it BLOCKED.
        resolved = dispatcher.dispatch(
            Task(id="T", title="설계", agent_role="technical_director",
                 task_type="architecture", files=["docs/**"]),
            registry())
        self.assertEqual("technical_director", resolved.agent_id)

    def test_production_paths_are_what_count_not_the_task_type(self):
        self.assertTrue(dispatcher.writes_production_code(["Assets/x.cs"]))
        self.assertTrue(dispatcher.writes_production_code(["GameSpecs/game02.json"]))
        self.assertFalse(dispatcher.writes_production_code(["docs/PLAN.md"]))
        self.assertFalse(dispatcher.writes_production_code(["Reports/x.txt"]))
        self.assertFalse(dispatcher.writes_production_code([]))

    def test_a_read_only_role_may_still_take_work_that_writes_nothing(self):
        resolved = dispatcher.dispatch(
            Task(id="T", title="review the diff", agent_role="quality_reviewer"),
            registry())
        self.assertEqual("quality_reviewer", resolved.agent_id)

    def test_every_refusal_is_one_exception_family(self):
        # Callers catch DispatchError. validate_task_type throws from the
        # registry, and if that leaked through, one call site would eventually
        # forget to catch it.
        bad = [
            Task(id="T", title="x", agent_role="nope"),
            Task(id="T", title="x", agent_role="art_director", task_type="gameplay_code"),
            Task(id="T", title="x", agent_role="ceo",
                 files=["Assets/GameFactory/Core/GameManager.cs"]),
            Task(id="T", title="zzzzz"),
        ]
        for task in bad:
            with self.assertRaises(dispatcher.DispatchError):
                dispatcher.dispatch(task, registry())


class BoundaryTests(unittest.TestCase):
    """A role decides who writes. It never decides what may be written."""

    def test_dispatch_does_not_touch_the_files_allowlist(self):
        files = ["Assets/GameFactory/Gameplay/Player.cs"]
        task = Task(id="T", title="점프", agent_role="gameplay_engineer",
                    task_type="player", files=list(files))
        dispatcher.dispatch(task, registry())
        self.assertEqual(files, task.files)

    def test_preferred_paths_are_not_an_allowlist(self):
        # AGENTS.json gives each role preferred_paths. Those are advice in the
        # prompt; if they ever became the allowlist, a role could write
        # anywhere inside its own department without the board saying so.
        raw = json.loads(REGISTRY.read_text(encoding="utf-8-sig"))
        agent = next(a for a in raw["agents"] if a["id"] == "gameplay_engineer")
        self.assertIn("preferred_paths", agent)
        task = Task(id="T", title="점프", agent_role="gameplay_engineer",
                    task_type="player", files=[])
        resolved = dispatcher.dispatch(task, registry())
        section = dispatcher.role_section(resolved)
        self.assertNotIn("FILES YOU MAY CHANGE", section)


class PromptTests(unittest.TestCase):
    """The role goes in front of the goal, and HOUSE_RULES survive it."""

    def board(self) -> TaskBoard:
        return TaskBoard(path=Path(tempfile.mkdtemp()) / "b.json", tasks=[])

    def test_a_role_task_gets_its_brief_before_the_goal(self):
        task = Task(id="T", title="점프", goal="무겁게",
                    agent_role="gameplay_engineer", task_type="player")
        prompt = build_prompt(task, self.board(), registry=registry())
        self.assertIn("CURRENT COMPANY ROLE", prompt)
        self.assertIn("Agent ID: gameplay_engineer", prompt)
        self.assertLess(prompt.index("CURRENT COMPANY ROLE"), prompt.index("GOAL"))

    def test_house_rules_are_still_mandatory(self):
        task = Task(id="T", title="점프", goal="무겁게",
                    agent_role="gameplay_engineer", task_type="player")
        prompt = build_prompt(task, self.board(), registry=registry())
        self.assertIn("PROJECT RULES", prompt)
        self.assertIn("linearVelocity", prompt)

    def test_a_read_only_role_is_told_so_in_its_prompt(self):
        task = Task(id="T", title="review", goal="look", agent_role="quality_reviewer")
        prompt = build_prompt(task, self.board(), registry=registry())
        self.assertIn("YOU MAY NOT EDIT PRODUCTION CODE", prompt)

    def test_a_bad_role_degrades_to_a_note_rather_than_killing_the_run(self):
        # The role is context; the goal and the allowlist are the job. A task
        # with a typo'd role should still run, with the failure stated.
        task = Task(id="T", title="점프", goal="무겁게", agent_role="nope")
        prompt = build_prompt(task, self.board(), registry=registry())
        self.assertIn("was not applied", prompt)
        self.assertIn("GOAL", prompt)
        self.assertIn("PROJECT RULES", prompt)

    def test_without_a_registry_the_prompt_is_exactly_what_it_was(self):
        task = Task(id="T", title="점프", goal="무겁게",
                    agent_role="gameplay_engineer")
        self.assertNotIn("CURRENT COMPANY ROLE",
                         build_prompt(task, self.board()))


class BackwardCompatibilityTests(unittest.TestCase):
    """The board predates all of this and must not notice it arrived."""

    def test_a_task_without_a_role_gains_no_fields_when_saved(self):
        plain = Task(id="OLD", title="old one").to_dict()
        for name in ("agent_role", "department", "task_type", "priority",
                     "handoff_from", "handoff_to"):
            self.assertNotIn(name, plain,
                             f"{name} was added to a task that never had it")

    def test_the_real_board_round_trips_unchanged(self):
        # The strongest version of the claim: load the actual board, save it,
        # and require the bytes back. Anything else means adding the fields
        # rewrote thirty tasks.
        source = ROOT / "config" / "TASKBOARD.json"
        before = source.read_text(encoding="utf-8-sig")
        target = Path(tempfile.mkdtemp()) / "TASKBOARD.json"
        target.write_text(before, encoding="utf-8")
        board = TaskBoard.load(target)
        board.save()
        self.assertEqual(json.loads(before),
                         json.loads(target.read_text(encoding="utf-8")))

    def test_role_fields_survive_a_round_trip_when_they_are_set(self):
        target = Path(tempfile.mkdtemp()) / "TASKBOARD.json"
        board = TaskBoard(path=target, tasks=[
            Task(id="NEW", title="t", agent_role="qa_engineer",
                 task_type="test_code", priority=40,
                 handoff_from="gameplay_engineer", handoff_to="quality_reviewer"),
        ])
        board.save()
        reloaded = TaskBoard.load(target).get("NEW")
        self.assertEqual("qa_engineer", reloaded.agent_role)
        self.assertEqual("test_code", reloaded.task_type)
        self.assertEqual(40, reloaded.priority)
        self.assertEqual("quality_reviewer", reloaded.handoff_to)


if __name__ == "__main__":
    unittest.main(verbosity=2)
