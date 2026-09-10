import json
import sys
import tempfile
import unittest
from pathlib import Path

# Every other test module does this. Without it the file only runs when the
# caller already happens to be inside AI_GAME_COMPANY, so it passed by hand
# and failed in the suite.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from company.orchestrator.agent_registry import (  # noqa: E402
    AgentRegistry,
    AgentRegistryError,
    UnknownAgentError,
)


ROOT = Path(__file__).resolve().parents[1]


class AgentRegistryTests(unittest.TestCase):
    def test_project_registry_loads_all_twelve_roles(self):
        registry = AgentRegistry.load(ROOT / "config" / "AGENTS.json")
        self.assertEqual(12, len(registry.list_agents()))
        self.assertEqual("경영실", registry.department("ceo"))
        self.assertIn("Unity 모바일 게임", registry.prompt("gameplay_engineer"))

    def test_utf8_bom_is_accepted(self):
        source = (ROOT / "config" / "AGENTS.json").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "AGENTS.json"
            path.write_text(source, encoding="utf-8-sig")
            self.assertEqual(12, len(AgentRegistry.load(path).list_agents()))

    def test_duplicate_id_is_rejected(self):
        data = json.loads((ROOT / "config" / "AGENTS.json").read_text(encoding="utf-8"))
        data["agents"][1]["id"] = data["agents"][0]["id"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "AGENTS.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(AgentRegistryError):
                AgentRegistry.load(path)

    def test_missing_required_field_is_rejected(self):
        data = json.loads((ROOT / "config" / "AGENTS.json").read_text(encoding="utf-8"))
        del data["agents"][0]["department"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "AGENTS.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(AgentRegistryError):
                AgentRegistry.load(path)

    def test_unknown_agent_and_disallowed_task_type_are_rejected(self):
        registry = AgentRegistry.load(ROOT / "config" / "AGENTS.json")
        with self.assertRaises(UnknownAgentError):
            registry.get("missing")
        with self.assertRaises(AgentRegistryError):
            registry.validate_task_type("quality_reviewer", "gameplay_code")


if __name__ == "__main__":
    unittest.main()
