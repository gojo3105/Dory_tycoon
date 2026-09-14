import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from company.orchestrator.agent_registry import AgentRegistry  # noqa: E402
from company.orchestrator.manager import (  # noqa: E402
    CompanyManager, PlanValidationError,
)
from company.orchestrator.teamwork import TaskBoard  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]


class ManagerPlanningTests(unittest.TestCase):
    def setUp(self):
        self.registry = AgentRegistry.load(ROOT / "config" / "AGENTS.json")
        self.manager = CompanyManager(self.registry)

    def test_one_sentence_produces_structured_seven_stage_plan(self):
        plan = self.manager.plan("game02", "Game02 만들어.")
        self.assertEqual("game02", plan.project)
        self.assertEqual("Game02 만들어.", plan.raw_goal)
        self.assertEqual(7, len(plan.tasks))
        self.assertEqual("game_director", plan.tasks[0].agent_role)
        self.assertEqual("release_engineer", plan.tasks[-1].agent_role)
        self.assertTrue(all(task.owner == "codex" for task in plan.tasks))

    def test_generated_dependencies_are_sequential_and_acyclic(self):
        plan = self.manager.plan("game02", "Game02 만들어.")
        for previous, current in zip(plan.tasks, plan.tasks[1:]):
            self.assertEqual([previous.id], current.depends_on)
        self.manager.validate(plan)

    def test_cycle_and_duplicate_existing_id_are_rejected(self):
        plan = self.manager.plan("game02", "Game02 만들어.")
        plan.tasks[0].depends_on = [plan.tasks[-1].id]
        with self.assertRaises(PlanValidationError):
            self.manager.validate(plan)
        clean = self.manager.plan("game03", "Game03 만들어.")
        with self.assertRaises(PlanValidationError):
            self.manager.validate(clean, {clean.tasks[0].id})

    def test_invalid_project_goal_and_path_are_rejected(self):
        with self.assertRaises(PlanValidationError):
            self.manager.plan("../../game02", "만들어")
        with self.assertRaises(PlanValidationError):
            self.manager.plan("game02", "")
        plan = self.manager.plan("game02", "Game02 만들어.")
        plan.tasks[0].files = ["../secret"]
        with self.assertRaises(PlanValidationError):
            self.manager.validate(plan)

    def test_plan_is_written_and_tasks_are_added_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            board = TaskBoard(path=root / "TASKBOARD.json")
            plan = self.manager.plan("game02", "Game02 만들어.")
            self.manager.add_to_board(plan, board)
            path = self.manager.write_plan(plan, root / "plans" / "game02.json")
            self.assertEqual(7, len(TaskBoard.load(board.path).tasks))
            self.assertEqual("game02", json.loads(path.read_text(encoding="utf-8"))["project"])
            with self.assertRaises(PlanValidationError):
                self.manager.add_to_board(plan, board)


if __name__ == "__main__":
    unittest.main()
