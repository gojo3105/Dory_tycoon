"""Decides which company role a task belongs to, and whether it may do it.

WHY THIS IS SEPARATE FROM THE REGISTRY. agent_registry.py answers questions
about the AGENTS.json data - who exists, what may they touch, what is their
prompt. This file answers the question the board actually asks: given this
task, which role runs it, and is that allowed? Keeping the two apart lets the
routing table below change without reopening the registry, and keeps the
registry a straight reader of its file.

WHAT IT REFUSES, AND WHY EACH REFUSAL EXISTS

  AN UNKNOWN ROLE is refused outright rather than defaulted. A typo in
  agent_role that silently became "gameplay_engineer" would hand a task to
  the wrong specialist with the wrong prompt and the wrong preferred paths,
  and nothing downstream would notice - the run would just be quietly wrong.

  A ROLE THAT CANNOT DO THE TASK TYPE is refused. allowed_task_types is the
  registry's statement of what each role is for; ignoring it would make the
  whole twelve-role structure decorative.

  A READ-ONLY ROLE EDITING PRODUCTION CODE is refused. quality_reviewer and
  ceo have can_modify_code false. That flag is the only thing between "a
  reviewer looked at this" and "the reviewer rewrote it and then reviewed its
  own work", which is not a review.

WHAT IT DOES NOT DO. It does not run anything. teamwork.run_task still owns
execution, the files allowlist is still the hard boundary checked against the
diff afterwards, and one-job-at-a-time is still enforced by the Runner in
server.py. A role decides WHO writes; it never widens WHAT may be written.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from company.orchestrator.agent_registry import (
    AgentRegistry,
    AgentRegistryError,
    RegisteredAgent,
    UnknownAgentError,
)


class DispatchError(RuntimeError):
    """A task cannot be handed to the role it names. The message is shown."""


class ReadOnlyRoleError(DispatchError):
    """A role with can_modify_code false claimed a task that writes code."""


# Category -> role, from section 6 of CODEX_MULTI_AGENT_IMPLEMENTATION.md.
# Order matters: the first pattern that matches wins, so specific categories
# come before broad ones. Used ONLY when a task names no role - an explicit
# agent_role is never overridden by guessing from the title.
ROUTING: tuple[tuple[str, str], ...] = (
    (r"release|배포|출시|\bapk\b|빌드", "release_engineer"),
    (r"review|리뷰|검토|audit|감사", "quality_reviewer"),
    (r"\btest\b|테스트|\bqa\b|playmode|editmode", "qa_engineer"),
    (r"level|stage|레벨|스테이지|맵", "level_designer"),
    (r"\bart\b|아트|sprite|스프라이트|그림|이미지", "art_director"),
    (r"\bui\b|hud|버튼|화면|메뉴|상점", "ui_ux_engineer"),
    (r"save|economy|경제|저장|설정|\bcore\b", "systems_engineer"),
    (r"architecture|아키텍처|구조|생성기|generator", "technical_director"),
    (r"design|기획|밸런스|게임성", "game_director"),
    (r"breakdown|분해|계획|\bplan\b", "producer"),
    (r"objective|목표|출시 결정", "ceo"),
    (r"gameplay|player|jump|점프|이동|충돌|\b적\b|obstacle", "gameplay_engineer"),
)

# What "production code" means, for can_modify_code. The paths, not the task
# type: an earlier version of this kept a hand-written list of "writing" task
# types, and AGENTS.json disagreed with it in both directions - `architecture`
# is a design output that writes a document, while `progression` belongs to
# game_director (which may not write) AND systems_engineer (which may). A type
# cannot answer the question, because the same word means different work in
# different rooms. The allowlist can: a task that may touch Assets/ is a task
# that edits the game.
PRODUCTION_PREFIXES = (
    "Assets/", "GameSpecs/", "Packages/", "ProjectSettings/",
    # The orchestrator is code too. Leaving it out let a spec assign a
    # Python implementation task to release_engineer and art_director,
    # both of which have can_modify_code false - the dispatcher passed it
    # because the files were not under Assets/. A reviewer that may not
    # rewrite the game should not be rewriting the thing that builds it.
    "AI_GAME_COMPANY/company/", "scripts/",
)


def writes_production_code(files: Any) -> bool:
    """True when any allowlist entry reaches the game itself.

    docs/ and Reports/ are deliberately not production: a design role has to
    be able to write its design down, or every planning task would finish
    having changed nothing and run_task would mark it BLOCKED.
    """
    for pattern in files or ():
        cleaned = str(pattern).replace("\\", "/").lstrip("./")
        if cleaned.startswith(PRODUCTION_PREFIXES):
            return True
    return False


@dataclass(frozen=True)
class Dispatch:
    """The resolved answer: who runs this, and how the decision was reached."""
    agent: RegisteredAgent
    task_type: str
    #: True when the role came from the routing table rather than the task.
    inferred: bool
    reason: str

    @property
    def agent_id(self) -> str:
        return self.agent.id


def registry_path(company_root: Path) -> Path:
    return company_root / "config" / "AGENTS.json"


def route_by_text(text: str) -> str:
    """The role a free-text title or goal suggests, or "" for none.

    A fallback for tasks with no agent_role. Deliberately returns "" rather
    than a default when nothing matches: guessing "gameplay_engineer" for an
    unrecognised task would put work in front of a specialist with no
    business doing it, and the caller can decide what a non-answer means.
    """
    lowered = (text or "").lower()
    for pattern, agent_id in ROUTING:
        if re.search(pattern, lowered):
            return agent_id
    return ""


def _task_field(task: Any, name: str, default: Any = "") -> Any:
    """Read a field off a Task dataclass or a plain board dict, either way."""
    if isinstance(task, dict):
        return task.get(name, default)
    return getattr(task, name, default)


def dispatch(task: Any, registry: AgentRegistry, *,
             default_role: str = "") -> Dispatch:
    """Resolve the role for one task, or raise saying why it cannot be.

    `default_role` is AGENTS.json's own default_agent_role, used only when a
    task names no role and the routing table recognises nothing. Leaving it
    empty makes an unroutable task an error instead, which is what a caller
    building a plan wants.
    """
    task_id = _task_field(task, "id", "?")
    declared = str(_task_field(task, "agent_role") or "").strip()
    task_type = str(_task_field(task, "task_type") or "").strip()
    title = str(_task_field(task, "title") or "")
    goal = str(_task_field(task, "goal") or "")

    inferred = False
    if declared:
        agent_id, reason = declared, "작업이 직접 지정한 역할"
    else:
        agent_id = route_by_text(title + " " + goal)
        if agent_id:
            inferred, reason = True, "제목·목표에서 유추한 역할"
        elif default_role:
            agent_id, inferred = default_role, True
            reason = "AGENTS.json 의 default_agent_role"
        else:
            raise DispatchError(
                f"'{task_id}' 작업에 agent_role 이 없고, 제목·목표로도 담당 "
                "역할을 정할 수 없습니다. agent_role 을 직접 지정하세요."
            )

    try:
        agent = registry.get(agent_id)
    except UnknownAgentError as exc:
        # Never defaulted: see the module docstring. A typo has to be loud.
        known = ", ".join(a.id for a in registry.list_agents())
        raise DispatchError(
            f"'{agent_id}' 은 AGENTS.json 에 없는 역할입니다. 쓸 수 있는 역할: {known}"
        ) from exc

    if task_type:
        # The registry owns this rule; re-implementing the membership test
        # here is how the two would drift apart. Its error is re-raised as a
        # DispatchError so that one caller catches one family - otherwise
        # every call site has to know that this one check throws from a
        # different module, and one of them eventually will not.
        try:
            registry.validate_task_type(agent.id, task_type)
        except AgentRegistryError as exc:
            allowed = ", ".join(agent.allowed_task_types)
            raise DispatchError(
                f"{agent.display_name}({agent.id}) 은 '{task_type}' 종류의 작업을 "
                f"맡을 수 없습니다. 가능한 종류: {allowed}"
            ) from exc

    writes = writes_production_code(_task_field(task, "files", []))
    if writes and not agent.can_modify_code:
        raise ReadOnlyRoleError(
            f"{agent.display_name}({agent.id}) 은 코드를 고칠 수 없는 역할인데 "
            f"'{task_id}' 은 코드를 고치는 작업입니다. 검토자가 자기가 고친 것을 "
            "검토하게 되므로 거부합니다."
        )

    return Dispatch(agent=agent, task_type=task_type,
                    inferred=inferred, reason=reason)


def role_section(resolved: Dispatch) -> str:
    """The CURRENT COMPANY ROLE block that goes in front of the task goal.

    Section 5 of the implementation document: HOUSE_RULES stay mandatory and
    this is added before the goal, so the role narrows what the agent does
    without loosening any project rule.
    """
    agent = resolved.agent
    lines = [
        "CURRENT COMPANY ROLE",
        f"Agent: {agent.display_name}",
        f"Agent ID: {agent.id}",
        f"Department: {agent.department}",
        "",
        agent.prompt.strip(),
    ]
    contract = list(getattr(agent, "final_response_contract", ()) or ())
    if contract:
        lines += [
            "",
            "FINAL MESSAGE must contain these sections, in this order:",
            "  " + ", ".join(contract),
        ]
    if not agent.can_modify_code:
        lines += [
            "",
            "YOU MAY NOT EDIT PRODUCTION CODE in this role. Report what you "
            "found and hand off; do not fix it yourself.",
        ]
    return "\n".join(lines)
