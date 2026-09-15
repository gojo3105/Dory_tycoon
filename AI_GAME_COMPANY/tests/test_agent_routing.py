import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from company.orchestrator.agent_dispatcher import (  # noqa: E402
    AgentDispatchError, AgentDispatcher, ReadOnlyRoleError, WritingAgentBusy,
)
from company.orchestrator.agent_registry import AgentRegistry  # noqa: E402
from company.orchestrator.teamwork import Task  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]


class AgentRoutingTests(unittest.TestCase):
    def setUp(self):
        self.registry = AgentRegistry.load(ROOT / "config" / "AGENTS.json")
        self.dispatcher = AgentDispatcher(self.registry)

    def test_specialist_categories_route_to_expected_roles(self):
        self.assertEqual("gameplay_engineer", self.dispatcher.route("gameplay_code").id)
        self.assertEqual("ui_ux_engineer", self.dispatcher.route("ui_code").id)
        self.assertEqual("qa_engineer", self.dispatcher.route("qa").id)
        self.assertEqual("release_engineer", self.dispatcher.route("build").id)
        self.assertEqual("art_director", self.dispatcher.route("store_listing").id)

    def test_explicit_invalid_or_mismatched_role_is_rejected(self):
        with self.assertRaises(AgentDispatchError):
            self.dispatcher.route("unknown")
        with self.assertRaises(Exception):
            self.dispatcher.resolve(Task(
                id="X", title="x", agent_role="ui_ux_engineer",
                task_type="gameplay_code", files=["Assets/GameFactory/UI/X.cs"]))

    def test_read_only_role_cannot_edit_production_code(self):
        with self.assertRaises(ReadOnlyRoleError):
            self.dispatcher.resolve(Task(
                id="X", title="x", agent_role="quality_reviewer",
                task_type="code_review",
                files=["Assets/GameFactory/Core/GameManager.cs"]))

    def test_read_only_role_may_write_its_report(self):
        role = self.dispatcher.resolve(Task(
            id="X", title="x", agent_role="quality_reviewer",
            task_type="code_review", files=["Reports/quality/game01.json"]))
        self.assertEqual("quality_reviewer", role.id)

    def test_only_one_writing_role_reserves_the_shared_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dispatcher = AgentDispatcher(self.registry, root)
            task = Task(id="X", title="x", agent_role="gameplay_engineer",
                        task_type="gameplay_code", files=["Assets/GameFactory/Gameplay/X.cs"])
            with dispatcher.writing_slot(task):
                with self.assertRaises(WritingAgentBusy):
                    with dispatcher.writing_slot(task):
                        pass
            self.assertFalse((root / "codex-agent-write.lock").exists())


if __name__ == "__main__":
    unittest.main()
