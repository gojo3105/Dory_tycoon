"""Validated routing and one-writer coordination for logical company roles."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from company.orchestrator.agent_registry import AgentRegistry, RegisteredAgent
from company.orchestrator.teamwork import Task


class AgentDispatchError(ValueError):
    """A task cannot be assigned without breaking the company rules."""


class ReadOnlyRoleError(AgentDispatchError):
    """A non-writing role was assigned production code."""


class WritingAgentBusy(AgentDispatchError):
    """Another writing role already owns the shared working tree."""


ROUTE_BY_TASK_TYPE: dict[str, str] = {
    "objective": "ceo",
    "priority": "ceo",
    "release_decision": "ceo",
    "planning": "producer",
    "task_breakdown": "producer",
    "routing": "producer",
    "dependency_management": "producer",
    "game_design": "game_director",
    "core_loop": "game_director",
    "difficulty": "game_director",
    "architecture": "technical_director",
    "module_design": "technical_director",
    "dependency_design": "technical_director",
    "gameplay_code": "gameplay_engineer",
    "player": "gameplay_engineer",
    "enemy": "gameplay_engineer",
    "obstacle": "gameplay_engineer",
    "combat": "gameplay_engineer",
    "physics": "gameplay_engineer",
    "game_feel": "gameplay_engineer",
    "core_system": "systems_engineer",
    "save": "systems_engineer",
    "economy": "systems_engineer",
    "progression": "systems_engineer",
    "pool": "systems_engineer",
    "audio": "systems_engineer",
    "input": "systems_engineer",
    "gamespec": "systems_engineer",
    "ui_code": "ui_ux_engineer",
    "ux": "ui_ux_engineer",
    "hud": "ui_ux_engineer",
    "menu": "ui_ux_engineer",
    "shop": "ui_ux_engineer",
    "settings": "ui_ux_engineer",
    "art_direction": "art_director",
    "asset_prompt": "art_director",
    "visual_consistency": "art_director",
    "ui_art": "art_director",
    "character_art": "art_director",
    "background_art": "art_director",
    "market_visual_research": "art_director",
    "store_listing": "art_director",
    "creative_testing": "art_director",
    "liveops_creative": "art_director",
    "level_design": "level_designer",
    "stage_data": "level_designer",
    "spawn_pattern": "level_designer",
    "difficulty_curve": "level_designer",
    "tutorial": "level_designer",
    "procedural_generation": "level_designer",
    "qa": "qa_engineer",
    "test": "qa_engineer",
    "regression": "qa_engineer",
    "bug_report": "qa_engineer",
    "acceptance_verification": "qa_engineer",
    "code_review": "quality_reviewer",
    "architecture_review": "quality_reviewer",
    "quality_score": "quality_reviewer",
    "release_review": "quality_reviewer",
    "build": "release_engineer",
    "release": "release_engineer",
    "artifact_verification": "release_engineer",
    "build_report": "release_engineer",
}


def _is_production_code(path: str) -> bool:
    normal = path.replace("\\", "/")
    if normal.startswith("Assets/GameFactory/Tests/") or normal.startswith("AI_GAME_COMPANY/tests/"):
        return False
    return (normal.endswith((".cs", ".py", ".ps1", ".yml", ".yaml")) or
            normal.startswith("Assets/GameFactory/Core/") or
            normal.startswith("Assets/GameFactory/Gameplay/") or
            normal.startswith("Assets/GameFactory/Editor/") or
            normal.startswith("AI_GAME_COMPANY/company/"))


class AgentDispatcher:
    def __init__(self, registry: AgentRegistry, repo_root: Path | None = None) -> None:
        self.registry = registry
        self.repo_root = repo_root

    def route(self, task_type: str) -> RegisteredAgent:
        agent_id = ROUTE_BY_TASK_TYPE.get(task_type)
        if not agent_id:
            raise AgentDispatchError(f"no company role routes task type: {task_type}")
        return self.registry.get(agent_id)

    def resolve(self, task: Task) -> RegisteredAgent:
        agent = (self.registry.get(task.agent_role) if task.agent_role
                 else self.route(task.task_type))
        if task.task_type:
            self.registry.validate_task_type(agent.id, task.task_type)
        if not agent.can_modify_code and any(_is_production_code(path) for path in task.files):
            raise ReadOnlyRoleError(
                f"read-only role {agent.id} cannot edit production code")
        return agent

    @contextmanager
    def writing_slot(self, task: Task) -> Iterator[RegisteredAgent]:
        """Reserve the shared tree for one code-writing role at a time."""
        agent = self.resolve(task)
        if not agent.can_modify_code or self.repo_root is None:
            yield agent
            return

        lock_parent = self.repo_root / ".git"
        if not lock_parent.is_dir():
            lock_parent = self.repo_root
        lock = lock_parent / "codex-agent-write.lock"
        try:
            lock.mkdir()
        except FileExistsError as exc:
            raise WritingAgentBusy(
                f"shared working tree is already reserved: {lock}") from exc
        try:
            yield agent
        finally:
            try:
                lock.rmdir()
            except OSError:
                pass
