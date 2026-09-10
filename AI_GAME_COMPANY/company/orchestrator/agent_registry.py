"""Validated, data-driven company roles from config/AGENTS.json."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class AgentRegistryError(ValueError):
    """The role registry is missing or violates its public schema."""


class UnknownAgentError(AgentRegistryError):
    """An explicitly requested role is not registered."""


@dataclass(frozen=True)
class RegisteredAgent:
    id: str
    display_name: str
    department: str
    description: str
    prompt: str
    order: int
    can_modify_code: bool
    allowed_task_types: tuple[str, ...]


class AgentRegistry:
    """Load and query the logical company without duplicating role prompts."""

    def __init__(self, version: int, agents: list[RegisteredAgent]) -> None:
        if version != 1:
            raise AgentRegistryError(f"unsupported AGENTS.json version: {version!r}")
        if not agents:
            raise AgentRegistryError("AGENTS.json must define at least one agent")
        by_id: dict[str, RegisteredAgent] = {}
        for agent in agents:
            if agent.id in by_id:
                raise AgentRegistryError(f"duplicate agent id: {agent.id}")
            by_id[agent.id] = agent
        self.version = version
        self._agents = tuple(sorted(agents, key=lambda item: item.order))
        self._by_id = by_id

    @classmethod
    def load(cls, path: Path) -> "AgentRegistry":
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AgentRegistryError(f"cannot load {path.name}: {exc}") from exc
        if not isinstance(raw, dict):
            raise AgentRegistryError("AGENTS.json root must be an object")
        version = raw.get("_version")
        entries = raw.get("agents")
        if not isinstance(version, int) or not isinstance(entries, list):
            raise AgentRegistryError("AGENTS.json requires integer _version and agents array")
        agents = [cls._parse_agent(entry, index) for index, entry in enumerate(entries)]
        return cls(version, agents)

    @staticmethod
    def _parse_agent(raw: Any, index: int) -> RegisteredAgent:
        if not isinstance(raw, dict):
            raise AgentRegistryError(f"agents[{index}] must be an object")
        required = ("id", "display_name", "department", "description", "prompt",
                    "order", "can_modify_code", "allowed_task_types")
        missing = [key for key in required if key not in raw]
        if missing:
            raise AgentRegistryError(
                f"agents[{index}] missing required fields: {', '.join(missing)}")
        text_fields = ("id", "display_name", "department", "description", "prompt")
        if any(not isinstance(raw[key], str) or not raw[key].strip()
               for key in text_fields):
            raise AgentRegistryError(f"agents[{index}] has an empty text field")
        task_types = raw["allowed_task_types"]
        if (not isinstance(task_types, list) or not task_types or
                any(not isinstance(value, str) or not value for value in task_types)):
            raise AgentRegistryError(
                f"agents[{index}].allowed_task_types must be a non-empty string array")
        if not isinstance(raw["order"], int) or not isinstance(raw["can_modify_code"], bool):
            raise AgentRegistryError(f"agents[{index}] has invalid order or can_modify_code")
        return RegisteredAgent(
            id=raw["id"], display_name=raw["display_name"],
            department=raw["department"], description=raw["description"],
            prompt=raw["prompt"], order=raw["order"],
            can_modify_code=raw["can_modify_code"],
            allowed_task_types=tuple(task_types),
        )

    def get(self, agent_id: str) -> RegisteredAgent:
        try:
            return self._by_id[agent_id]
        except KeyError as exc:
            raise UnknownAgentError(f"unknown agent id: {agent_id}") from exc

    def list_agents(self) -> tuple[RegisteredAgent, ...]:
        return self._agents

    def prompt(self, agent_id: str) -> str:
        return self.get(agent_id).prompt

    def department(self, agent_id: str) -> str:
        return self.get(agent_id).department

    def validate_task_type(self, agent_id: str, task_type: str) -> None:
        agent = self.get(agent_id)
        if task_type and task_type not in agent.allowed_task_types:
            raise AgentRegistryError(
                f"agent {agent_id} cannot perform task type: {task_type}")
