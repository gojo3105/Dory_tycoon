"""Turns one sentence into a chain of tasks the twelve roles can run.

    "Game02 만들어."
      -> CEO states the objective and what would count as success
      -> Producer breaks it into tasks, each with a role and an allowlist
      -> validated, then written to TASKBOARD.json
      -> the existing `team run --task <id>` runs them, one at a time

WHY THIS IS DETERMINISTIC AND NOT A MODEL CALL. Section 8 of the
implementation document asks for structured JSON and for every generated task
to be validated before insertion. The validation is the load-bearing half: a
planner that invents an allowlist, or names a role that cannot do the work,
produces a chain that fails one task at a time at run time, hours later. So
the plan is built from tables here - the workflow order in AGENTS.json, and
the role allowlists below - and checked through the same dispatcher the
runner uses. A model can be layered on later to write better goals; it would
pass through this same validator, which is the point of keeping them apart.

WHY THE ALLOWLIST IS NOT GENERATED. ROLE_FILES below is fixed, exactly like
orders.DEPARTMENTS. A planner free to choose `files` could hand a task the
right to edit anything, and teamwork.run_task's diff check - the one real
boundary in this project - would then be checking against a boundary the
planner drew for itself. The sentence chooses the WORK; it never chooses the
permissions.

WHAT IT DOES NOT DO. It writes no code, runs nothing, and commits nothing. It
appends tasks to the board and stops. Execution stays where it already is.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from company.orchestrator import agent_dispatcher as dispatcher
from company.orchestrator.agent_registry import AgentRegistry
from company.orchestrator.orders import clean_order_text, title_from
from company.orchestrator.teamwork import TODO, Task, TaskBoard


class PlanRejected(ValueError):
    """The sentence cannot become a plan. The message is shown to the user."""


# What each role may edit. Fixed, for the reason in the module docstring.
ROLE_FILES: dict[str, tuple[str, ...]] = {
    # Only five roles have can_modify_code true in AGENTS.json. The other
    # seven plan, direct and review - their deliverable is a document, so
    # they get docs/ and nothing under Assets/. Giving technical_director
    # Assets/GameFactory/Editor was the first version of this table, and the
    # validator refused the plan it produced, which is what the validator is
    # for.
    "game_director": ("docs/**",),
    "technical_director": ("docs/**",),
    "art_director": ("docs/**",),
    "quality_reviewer": ("docs/**",),
    "release_engineer": ("docs/**", "Reports/**"),
    "gameplay_engineer": ("Assets/GameFactory/Gameplay/**",
                          "Assets/GameFactory/Modules/**"),
    "systems_engineer": ("Assets/GameFactory/Core/**",),
    "ui_ux_engineer": ("Assets/GameFactory/UI/**",),
    "level_designer": ("Assets/GameFactory/LevelGeneration/**", "GameSpecs/*.json"),
    "qa_engineer": ("Assets/GameFactory/Tests/**",),
    "producer": (),
    "ceo": (),
}

# The task type each role is given when the planner creates its step. Every
# value is in that role's allowed_task_types in AGENTS.json, and
# test_manager_planning asserts that against the registry - so an edit there
# which breaks a pairing is caught by the suite rather than by a run.
ROLE_TASK_TYPE: dict[str, str] = {
    "game_director": "game_design",
    "technical_director": "architecture",
    "gameplay_engineer": "gameplay_code",
    "systems_engineer": "core_system",
    "ui_ux_engineer": "ui_code",
    "level_designer": "level_design",
    "qa_engineer": "test",
    "quality_reviewer": "code_review",
    "release_engineer": "build",
}

# Steps that make up a plan, in order. Taken from AGENTS.json's own workflow
# rather than invented: ceo and producer do the planning that produced this
# list, so they are not steps in it.
DEFAULT_STEPS: tuple[tuple[str, str], ...] = (
    ("game_director", "핵심 루프와 성공 조건을 정한다"),
    ("technical_director", "생성기와 GameSpec 구조를 정한다"),
    ("gameplay_engineer", "게임플레이를 구현한다"),
    ("ui_ux_engineer", "화면과 조작 UI를 붙인다"),
    ("qa_engineer", "테스트를 만든다"),
    ("quality_reviewer", "구현을 검토한다"),
    ("release_engineer", "빌드하고 APK를 확인한다"),
)

PLAN_ID = re.compile(r"^PLAN-[A-Za-z0-9]+-\d{2}$")


@dataclass
class Objective:
    """What the CEO turns one sentence into."""
    raw: str
    objective: str
    success: list[str]
    priority: int = 50

    def as_dict(self) -> dict[str, Any]:
        return {"raw": self.raw, "objective": self.objective,
                "success": list(self.success), "priority": self.priority}


@dataclass
class Plan:
    project: str
    objective: Objective
    tasks: list[Task] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "objective": self.objective.objective,
            "success": list(self.objective.success),
            "tasks": [t.to_dict() for t in self.tasks],
            "warnings": list(self.warnings),
        }

    def as_json(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, indent=2)


def state_objective(raw: str, project: str) -> Objective:
    """The CEO step: one sentence in, an objective and success criteria out.

    Deliberately plain. The success criteria are this project's own
    definition of done - CLAUDE.md section 8's first principle is that an
    APK that does not exist is not a build - rather than anything inferred
    from the sentence, which would be a guess dressed as a requirement.
    """
    text = clean_order_text(raw)
    return Objective(
        raw=text,
        objective=text,
        success=[
            "핵심 루프가 실제로 플레이된다",
            "Unity 테스트가 통과한다",
            "디스크에 APK 파일이 실제로 생성된다",
        ],
    )


def next_plan_id(board: TaskBoard, project: str, index: int,
                 taken: set[str] | None = None) -> str:
    """PLAN-<project>-<nn>, skipping ids already in use.

    `taken` is the ids assigned earlier in the plan being built right now.
    Without it a second plan for the same project collided with ITSELF: the
    board does not yet hold this plan's tasks, so every step looked up the
    same free number and got it.
    """
    slug = re.sub(r"[^A-Za-z0-9]", "", project) or "X"
    used = {t.id for t in board.tasks} | (taken or set())
    number = index
    while f"PLAN-{slug}-{number:02d}" in used:
        number += 1
    return f"PLAN-{slug}-{number:02d}"


def build_plan(board: TaskBoard, registry: AgentRegistry, goal: str,
               project: str, *, steps: tuple[tuple[str, str], ...] = DEFAULT_STEPS,
               now: datetime | None = None) -> Plan:
    """The Producer step: an objective becomes an ordered chain of tasks.

    Each task depends on the one before it. That is what stops two writing
    agents touching the shared working tree at once - the chain is serial by
    construction, and unmet_dependencies already blocks a task whose
    predecessor has not finished.
    """
    if not project or not re.match(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$", project):
        raise PlanRejected(f"'{project}' 은 쓸 수 없는 프로젝트 이름입니다.")

    objective = state_objective(goal, project)
    plan = Plan(project=project, objective=objective)

    previous = ""
    for index, (role, what) in enumerate(steps, start=1):
        files = ROLE_FILES.get(role, ())
        if not files:
            # ceo and producer are the only empty entries, and neither is a
            # step - they are the planning that produced this list.
            plan.warnings.append(
                f"{role} 에 수정 허용 파일이 없어 이 단계는 건너뜁니다.")
            continue

        task_id = next_plan_id(board, project, index,
                               {t.id for t in plan.tasks})
        task = Task(
            id=task_id,
            title=f"[{project}] {what}",
            title_ko=what,
            owner="codex",
            status=TODO,
            goal=(f"{objective.objective}\n\n"
                  f"이 작업은 그중 '{what}' 단계다. "
                  f"프로젝트: {project}."),
            files=list(files),
            acceptance=[
                "이 단계의 결과가 다음 단계에서 쓸 수 있는 상태다.",
                "수정 허용 파일 목록 밖은 건드리지 않았다.",
            ],
            depends_on=[previous] if previous else [],
            agent_role=role,
            department=registry.department(role),
            task_type=ROLE_TASK_TYPE.get(role, ""),
            priority=objective.priority,
            handoff_from=steps[index - 2][0] if index >= 2 else "producer",
            handoff_to=steps[index][0] if index < len(steps) else "ceo",
            notes=[
                f"manager.build_plan 이 '{objective.raw}' 한 문장에서 만든 계획의 "
                f"{index}/{len(steps)} 단계.",
            ],
        )
        plan.tasks.append(task)
        previous = task_id

    if not plan.tasks:
        raise PlanRejected("계획에 넣을 수 있는 단계가 하나도 없습니다.")
    return plan


def validate(plan: Plan, registry: AgentRegistry) -> None:
    """Check every task before any of them reaches the board.

    All-or-nothing on purpose. A plan half-written to TASKBOARD.json, with
    the rest refused, leaves a chain whose later links do not exist - and
    the earlier ones would run anyway and hand off to nothing.
    """
    seen: set[str] = set()
    for task in plan.tasks:
        if not PLAN_ID.match(task.id):
            raise PlanRejected(f"'{task.id}' 은 계획이 만들 수 있는 id 형식이 아닙니다.")
        if task.id in seen:
            raise PlanRejected(f"'{task.id}' 이 계획 안에서 중복됩니다.")
        seen.add(task.id)

        if task.owner != "codex":
            raise PlanRejected(f"'{task.id}' 의 owner 는 codex 여야 합니다.")

        # The same dispatcher the runner uses. Checking with a second, kinder
        # copy of the rules here is how a plan passes validation and then
        # fails at run time.
        try:
            dispatcher.dispatch(task, registry)
        except dispatcher.DispatchError as exc:
            raise PlanRejected(f"'{task.id}': {exc}") from exc

        allowed = ROLE_FILES.get(task.agent_role, ())
        if list(task.files) != list(allowed):
            raise PlanRejected(
                f"'{task.id}' 의 수정 허용 파일이 {task.agent_role} 의 고정 목록과 "
                "다릅니다. 계획은 권한을 정할 수 없습니다.")

        for dependency in task.depends_on:
            if dependency not in seen:
                raise PlanRejected(
                    f"'{task.id}' 이 아직 없는 '{dependency}' 에 의존합니다.")


def commit_plan(board: TaskBoard, plan: Plan, *, save: bool = True) -> Plan:
    """Append a validated plan to the board. Does not run anything."""
    existing = {t.id for t in board.tasks}
    clash = [t.id for t in plan.tasks if t.id in existing]
    if clash:
        raise PlanRejected(f"작업판에 이미 있는 id 입니다: {', '.join(clash)}")
    board.tasks.extend(plan.tasks)
    if save:
        board.save()
    return plan


def plan_from_sentence(board: TaskBoard, registry: AgentRegistry, goal: str,
                       project: str, *, save: bool = True) -> Plan:
    """The whole path: sentence -> objective -> tasks -> validated -> board."""
    plan = build_plan(board, registry, goal, project)
    validate(plan, registry)
    return commit_plan(board, plan, save=save)
