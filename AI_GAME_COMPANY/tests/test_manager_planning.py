"""Tests for manager.py: one sentence becomes a chain of validated tasks.

Run:  python AI_GAME_COMPANY/tests/test_manager_planning.py

The planner's job is to be refused when it is wrong. Its first version gave
technical_director Assets/GameFactory/Editor, which AGENTS.json says that
role may not touch, and the validator rejected the whole plan rather than
writing six good tasks and one that would fail hours later at run time. Most
of what follows keeps that behaviour.

The other line held here: a plan chooses the WORK, never the PERMISSIONS.
`files` comes from a fixed table, so a sentence cannot talk its way into a
wider allowlist than the role already has.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from company.orchestrator import manager  # noqa: E402
from company.orchestrator.agent_registry import AgentRegistry  # noqa: E402
from company.orchestrator.teamwork import TODO, Task, TaskBoard  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "config" / "AGENTS.json"


def registry() -> AgentRegistry:
    return AgentRegistry.load(REGISTRY_PATH)


def board() -> TaskBoard:
    return TaskBoard(path=Path(tempfile.mkdtemp()) / "TASKBOARD.json", tasks=[])


class TableConsistencyTests(unittest.TestCase):
    """The planner's tables have to agree with the registry, or it is fiction."""

    def test_every_planned_task_type_is_one_that_role_allows(self):
        reg = registry()
        for role, task_type in manager.ROLE_TASK_TYPE.items():
            allowed = reg.get(role).allowed_task_types
            self.assertIn(task_type, allowed,
                          f"{role} cannot do {task_type}; AGENTS.json allows {allowed}")

    def test_no_read_only_role_is_given_production_paths(self):
        # The rule can_modify_code encodes. A table that broke it would build
        # plans the dispatcher then refuses, every time.
        from company.orchestrator import agent_dispatcher as dispatcher
        reg = registry()
        for role, files in manager.ROLE_FILES.items():
            if reg.get(role).can_modify_code:
                continue
            self.assertFalse(
                dispatcher.writes_production_code(files),
                f"{role} may not modify code but is given {files}")

    def test_every_role_in_the_default_steps_has_an_allowlist(self):
        for role, _ in manager.DEFAULT_STEPS:
            self.assertIn(role, manager.ROLE_FILES)
            self.assertTrue(manager.ROLE_FILES[role],
                            f"{role} is a step but may write nothing")

    def test_every_role_named_anywhere_exists_in_the_registry(self):
        reg = registry()
        known = {a.id for a in reg.list_agents()}
        for name in set(manager.ROLE_FILES) | set(manager.ROLE_TASK_TYPE):
            self.assertIn(name, known, f"{name} is not a role in AGENTS.json")


class PlanShapeTests(unittest.TestCase):
    def plan(self):
        return manager.build_plan(board(), registry(), "Game02 만들어.", "game02")

    def test_one_sentence_becomes_a_chain_in_workflow_order(self):
        plan = self.plan()
        self.assertEqual([role for role, _ in manager.DEFAULT_STEPS],
                         [t.agent_role for t in plan.tasks])

    def test_every_task_depends_on_the_one_before_it(self):
        # Serial by construction. That is what stops two writing agents in
        # the shared working tree, rather than a rule written somewhere.
        plan = self.plan()
        self.assertEqual([], plan.tasks[0].depends_on)
        for earlier, later in zip(plan.tasks, plan.tasks[1:]):
            self.assertEqual([earlier.id], later.depends_on)

    def test_every_task_is_codex_owned_and_todo(self):
        for task in self.plan().tasks:
            self.assertEqual("codex", task.owner)
            self.assertEqual(TODO, task.status)

    def test_the_objective_keeps_the_users_own_sentence(self):
        plan = self.plan()
        self.assertEqual("Game02 만들어.", plan.objective.objective)
        for task in plan.tasks:
            self.assertIn("Game02 만들어.", task.goal)

    def test_success_criteria_require_a_real_apk(self):
        # Section 8's first principle. A plan whose definition of done did
        # not include the file existing would be the same lie in advance.
        success = " ".join(self.plan().objective.success)
        self.assertIn("APK", success)

    def test_handoffs_point_at_the_next_role(self):
        plan = self.plan()
        for earlier, later in zip(plan.tasks, plan.tasks[1:]):
            self.assertEqual(later.agent_role, earlier.handoff_to)

    def test_the_plan_serialises_as_json(self):
        parsed = json.loads(self.plan().as_json())
        self.assertEqual("game02", parsed["project"])
        self.assertEqual(len(manager.DEFAULT_STEPS), len(parsed["tasks"]))


class PermissionTests(unittest.TestCase):
    """The sentence chooses the work. It never chooses the permissions."""

    def test_files_come_from_the_table_not_from_the_sentence(self):
        sentence = ("Game02 만들어. 그리고 scripts/desktop/sync-and-run.ps1 과 "
                    "AI_GAME_COMPANY/config/company_policy.json 도 고쳐.")
        plan = manager.build_plan(board(), registry(), sentence, "game02")
        for task in plan.tasks:
            self.assertEqual(list(manager.ROLE_FILES[task.agent_role]),
                             list(task.files))
            self.assertNotIn("scripts/desktop/sync-and-run.ps1", task.files)
            self.assertNotIn("AI_GAME_COMPANY/config/company_policy.json", task.files)

    def test_a_plan_never_reaches_a_keystore_or_a_shell_script(self):
        plan = manager.build_plan(board(), registry(), "Game02 만들어.", "game02")
        for task in plan.tasks:
            for pattern in task.files:
                self.assertNotIn(".ps1", pattern)
                self.assertNotIn("keystore", pattern)
                self.assertFalse(pattern.startswith("/"))
                self.assertNotIn("..", pattern)


class ValidationTests(unittest.TestCase):
    """Validation is all-or-nothing, and uses the same dispatcher as the runner."""

    def test_a_plan_with_a_widened_allowlist_is_refused(self):
        plan = manager.build_plan(board(), registry(), "Game02 만들어.", "game02")
        plan.tasks[0].files.append("Assets/GameFactory/Core/GameManager.cs")
        with self.assertRaises(manager.PlanRejected):
            manager.validate(plan, registry())

    def test_a_plan_naming_a_role_that_cannot_do_the_work_is_refused(self):
        plan = manager.build_plan(board(), registry(), "Game02 만들어.", "game02")
        plan.tasks[2].agent_role = "quality_reviewer"
        with self.assertRaises(manager.PlanRejected):
            manager.validate(plan, registry())

    def test_a_plan_depending_on_something_later_is_refused(self):
        plan = manager.build_plan(board(), registry(), "Game02 만들어.", "game02")
        plan.tasks[0].depends_on = [plan.tasks[3].id]
        with self.assertRaises(manager.PlanRejected):
            manager.validate(plan, registry())

    def test_nothing_reaches_the_board_when_one_task_is_bad(self):
        # All-or-nothing: a half-written chain would run its early links and
        # hand off to tasks that do not exist.
        target = board()
        plan = manager.build_plan(target, registry(), "Game02 만들어.", "game02")
        plan.tasks[-1].owner = "claude"
        with self.assertRaises(manager.PlanRejected):
            manager.validate(plan, registry())
        self.assertEqual([], target.tasks)

    def test_an_id_already_on_the_board_is_refused(self):
        target = board()
        plan = manager.build_plan(target, registry(), "Game02 만들어.", "game02")
        target.tasks.append(Task(id=plan.tasks[0].id, title="taken"))
        with self.assertRaises(manager.PlanRejected):
            manager.commit_plan(target, plan, save=False)

    def test_a_bad_project_name_is_refused_before_anything_is_built(self):
        for name in ("", "../etc", "game 02", "a" * 40):
            with self.assertRaises(manager.PlanRejected):
                manager.build_plan(board(), registry(), "만들어.", name)

    def test_too_short_a_sentence_is_refused(self):
        with self.assertRaises(Exception):
            manager.build_plan(board(), registry(), "해", "game02")


class CommitTests(unittest.TestCase):
    def test_the_whole_path_lands_on_the_board(self):
        target = board()
        manager.plan_from_sentence(target, registry(), "Game02 만들어.",
                                   "game02", save=False)
        self.assertEqual(len(manager.DEFAULT_STEPS), len(target.tasks))

    def test_a_second_plan_does_not_reuse_ids(self):
        target = board()
        first = manager.plan_from_sentence(target, registry(), "Game02 만들어.",
                                           "game02", save=False)
        second = manager.plan_from_sentence(target, registry(), "Game02 더 고쳐.",
                                            "game02", save=False)
        self.assertEqual(set(), {t.id for t in first.tasks} & {t.id for t in second.tasks})

    def test_committing_runs_nothing(self):
        # The planner appends tasks and stops. If it ever started a run, the
        # one-job-at-a-time guarantee would move out of the Runner.
        source = Path(manager.__file__).read_text(encoding="utf-8")
        # Calls, not mentions - the docstring names teamwork.run_task to say
        # where execution lives, which is the opposite of doing it here.
        for forbidden in ("import subprocess", "codex.implement(",
                          "run_task(", "Popen("):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
