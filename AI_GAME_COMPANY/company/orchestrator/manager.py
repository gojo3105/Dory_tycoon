"""Structured CEO/Producer planning for one-sentence game orders."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from company.orchestrator.agent_dispatcher import AgentDispatcher
from company.orchestrator.agent_registry import AgentRegistry
from company.orchestrator.teamwork import CODEX, TODO, Task, TaskBoard


GAME_ID = re.compile(r"^game\d{2}$")


class PlanValidationError(ValueError):
    """A generated company plan is unsafe or cannot be executed."""


@dataclass
class CompanyPlan:
    project: str
    raw_goal: str
    objective: str
    success: list[str]
    priority: int
    tasks: list[Task] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "raw_goal": self.raw_goal,
            "objective": self.objective,
            "success": self.success,
            "priority": self.priority,
            "tasks": [task.to_dict() for task in self.tasks],
        }


class CompanyManager:
    """Creates and validates a deterministic plan before it touches the board."""

    def __init__(self, registry: AgentRegistry, repo_root: Path | None = None) -> None:
        self.registry = registry
        self.repo_root = repo_root
        self.dispatcher = AgentDispatcher(registry, repo_root)

    @staticmethod
    def _task(task_id: str, title: str, role: str, task_type: str,
              goal: str, files: list[str], acceptance: list[str],
              depends_on: list[str], priority: int) -> Task:
        return Task(
            id=task_id, title=title, title_ko=title, owner=CODEX, status=TODO,
            agent_role=role, task_type=task_type, priority=priority,
            goal=goal, files=files, acceptance=acceptance,
            depends_on=depends_on,
        )

    def plan(self, game: str, goal: str, existing_ids: set[str] | None = None) -> CompanyPlan:
        game = (game or "").strip().lower()
        raw_goal = (goal or "").strip()
        if not GAME_ID.fullmatch(game):
            raise PlanValidationError("project must be game followed by two digits")
        if not raw_goal:
            raise PlanValidationError("goal must not be empty")

        prefix = game.upper()
        objective = f"Create a verified, distinct mobile game vertical slice for {game}: {raw_goal}"
        success = [
            "core loop is playable and distinct from existing games",
            "GameSpec and generated content stay backward compatible",
            "save, tutorial, settings, missions, rewards and growth are verified",
            "automated tests pass with no BLOCKER or CRITICAL finding",
            "Android APK exists and belongs to the current build",
        ]
        p = 80
        tasks = [
            self._task(
                f"{prefix}-DESIGN", f"{game} game design", "game_director", "game_design",
                f"Turn the CEO objective into a distinct core loop, progression, difficulty and replay plan. Raw goal: {raw_goal}",
                [f"GameSpecs/{game}.json", f"docs/{prefix}_DESIGN.md"],
                ["Core fantasy, loop, failure, rewards and progression are explicit.",
                 "At least two mechanics differ from Game01."], [], p),
            self._task(
                f"{prefix}-ARCH", f"{game} technical architecture", "technical_director", "architecture",
                "Map the approved design onto existing Core, Gameplay, Modules, UI, Editor and GameSpec boundaries.",
                [f"GameSpecs/{game}.json", f"docs/{prefix}_ARCHITECTURE.md"],
                ["Existing reusable systems are identified before new modules.",
                 "Every GameSpec field change lists Data, Validator, Docs and Generator impact."],
                [f"{prefix}-DESIGN"], p - 5),
            self._task(
                f"{prefix}-SYSTEMS", f"{game} gameplay and systems implementation", "systems_engineer", "core_system",
                "Implement the approved reusable gameplay, save, economy and progression systems without weakening Game01.",
                ["Assets/GameFactory/Core/**", "Assets/GameFactory/Gameplay/**",
                 "Assets/GameFactory/Modules/**", "Assets/GameFactory/Editor/**",
                 f"GameSpecs/{game}.json", "docs/GAME_SPEC.md"],
                ["The core loop starts and reaches a reward/failure state.",
                 "Save and economy values remain isolated by game id.",
                 "Runtime code has no UnityEditor reference."],
                [f"{prefix}-ARCH"], p - 10),
            self._task(
                f"{prefix}-UI", f"{game} mobile UI and UX", "ui_ux_engineer", "ui_code",
                "Build the portrait mobile HUD, menus, tutorial, settings and progression UI for the implemented loop.",
                ["Assets/GameFactory/UI/**", "Assets/GameFactory/Editor/**",
                 "Assets/Common/Art/UI/**", f"GameSpecs/{game}.json"],
                ["Primary actions are readable and reachable on portrait mobile.",
                 "Korean labels, safe area and touch targets are usable.",
                 "UI displays state owned by gameplay/core systems."],
                [f"{prefix}-SYSTEMS"], p - 15),
            self._task(
                f"{prefix}-QA", f"{game} acceptance and regression QA", "qa_engineer", "qa",
                "Add and run acceptance and regression checks for the design, systems and UI handoff.",
                ["Assets/GameFactory/Tests/**", "AI_GAME_COMPANY/tests/**", "Reports/**"],
                ["Acceptance results use PASS, FAIL or NOT_VERIFIED.",
                 "BLOCKER and CRITICAL failures prevent release.",
                 "Game01 regression coverage is retained."],
                [f"{prefix}-UI"], p - 20),
            self._task(
                f"{prefix}-REVIEW", f"{game} independent quality review", "quality_reviewer", "code_review",
                "Review architecture, regression, GameSpec compatibility, mobile performance and evidence; write a concise quality report.",
                [f"Reports/quality/{game}.json"],
                ["Quality dimensions total 100 and target 85.",
                 "Uninspected visual dimensions are NOT_VERIFIED.",
                 "BLOCKER or CRITICAL findings stop release."],
                [f"{prefix}-QA"], p - 25),
            self._task(
                f"{prefix}-RELEASE", f"{game} Android release verification", "release_engineer", "build",
                "Run the existing generate, validate, test and Android build pipeline and verify the current APK.",
                ["Reports/errors/**", "Reports/build-status/**", "Reports/runs/**",
                 "Reports/sync-status/**", "Builds/**"],
                ["Process, Unity Build Report and current APK all prove success.",
                 "Failure has a defined build failure category.",
                 "Device-only checks remain HUMAN_GATE_DEVICE_TEST."],
                [f"{prefix}-REVIEW"], p - 30),
        ]
        for previous, current in zip(tasks, tasks[1:]):
            current.handoff_from = previous.agent_role
            previous.handoff_to = current.agent_role
        plan = CompanyPlan(game, raw_goal, objective, success, p, tasks)
        self.validate(plan, existing_ids or set())
        return plan

    def validate(self, plan: CompanyPlan, existing_ids: set[str] | None = None) -> None:
        if not GAME_ID.fullmatch(plan.project):
            raise PlanValidationError(f"invalid project id: {plan.project}")
        if not plan.objective.strip() or not plan.tasks:
            raise PlanValidationError("plan needs an objective and tasks")
        ids = [task.id for task in plan.tasks]
        if len(ids) != len(set(ids)):
            raise PlanValidationError("plan has duplicate task ids")
        overlap = set(ids) & set(existing_ids or set())
        if overlap:
            raise PlanValidationError(f"task ids already exist: {', '.join(sorted(overlap))}")
        known = set(ids)
        for task in plan.tasks:
            if not task.goal.strip():
                raise PlanValidationError(f"{task.id} has an empty goal")
            for path in task.files:
                pure = PurePosixPath(path.replace("\\", "/"))
                if pure.is_absolute() or ".." in pure.parts:
                    raise PlanValidationError(f"{task.id} has an invalid path: {path}")
            role = self.dispatcher.resolve(task)
            if role.can_modify_code and not task.files:
                raise PlanValidationError(f"write task {task.id} has an empty allowlist")
            missing = set(task.depends_on) - known
            if missing:
                raise PlanValidationError(f"{task.id} has missing dependencies: {sorted(missing)}")
        self._reject_cycles(plan.tasks)

    @staticmethod
    def _reject_cycles(tasks: list[Task]) -> None:
        edges = {task.id: list(task.depends_on) for task in tasks}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(task_id: str) -> None:
            if task_id in visiting:
                raise PlanValidationError(f"dependency cycle includes {task_id}")
            if task_id in visited:
                return
            visiting.add(task_id)
            for dependency in edges.get(task_id, []):
                visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in edges:
            visit(task_id)

    def add_to_board(self, plan: CompanyPlan, board: TaskBoard) -> None:
        self.validate(plan, {task.id for task in board.tasks})
        board.tasks.extend(plan.tasks)
        board.save()

    @staticmethod
    def write_plan(plan: CompanyPlan, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(plan.as_dict(), ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        temporary.replace(path)
        return path
