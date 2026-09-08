"""Turns a sentence typed into the control panel into work the AI can do.

WHY THIS FILE EXISTS AT ALL. The panel used to offer five buttons. That is
fine for "build" and useless for "make the jump feel heavier" - the whole
point of having an AI employee is that you can say the second one. So there
has to be a path from free text to work being done.

THE RULE THAT MAKES IT SAFE. CLAUDE.md section 10: the browser sends an
action NAME, the server owns the argv, and there is no code path where text
from a request becomes a command. A box that typed text into a shell would
break that outright, and localhost is not a boundary - any page open in the
user's browser can POST here.

So the text never becomes a command. It becomes a TASK on the board: data in
a JSON file, which Codex then reads as its brief. What actually executes is
the same fixed `team run --task <id>` that the Codex button already ran, with
an id this module generated. Three things therefore cannot come from the
request, and all three are enforced below rather than documented:

  THE ID          generated here as ORDER-<date>-<nn>, so it matches server.py's
                  SAFE_ID by construction and no request can shape it.
  THE ALLOWLIST   read from the DEPARTMENTS table below. An order says which
                  department it is for; it can never say which files are
                  writable. That is what keeps run_task's diff check meaningful.
  THE OWNER       forced to the department's seat. An order cannot hand itself
                  to a different agent than the one that department employs.

WHY DEPARTMENTS AND NOT A FREE PATH LIST. run_task blocks any diff outside
task.files, so a task with no allowlist is a task that always ends BLOCKED. A
real company solves this the same way: you send the request to the department
whose job it is, and the department's remit is the boundary. Here the remit is
literally the allowlist - which makes the office view on the dashboard an
honest picture of who may touch what, not decoration.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from company.orchestrator.teamwork import CODEX, TODO, Task, TaskBoard

# Orders are numbered per day, so the id says when it was placed without
# anyone having to look it up. Two digits is 99 orders in one day; past that
# the counter keeps going and the id gets a character longer, which is fine -
# SAFE_ID allows 64.
ORDER_PREFIX = "ORDER"
ORDER_ID = re.compile(r"^ORDER-\d{8}-\d{2,}$")

# Long enough for a real instruction with context, short enough that the board
# stays a document a person can read. The panel says the same number.
MAX_ORDER_CHARS = 2000
MIN_ORDER_CHARS = 8


class OrderRejected(ValueError):
    """The order cannot become a task. The message is shown to the user."""


@dataclass(frozen=True)
class Department:
    """One room in the office, and the files its occupant may write.

    `files` is the whole safety story: it is copied onto the task verbatim and
    run_task refuses any change outside it. Widening one of these lists is a
    deliberate act, the same as editing a policy flag.
    """
    id: str
    label: str
    seat: str                        # the agent that works here
    summary: str                     # what to send here, in the user's words
    files: tuple[str, ...] = ()
    acceptance: tuple[str, ...] = ()
    # An order to this department cannot be run by this machine at all. The
    # room is still drawn and still explains itself - a department that
    # silently vanished would be a worse answer than one that says why.
    unavailable: str = ""

    def seat_owner(self) -> str:
        """Which board `owner` value this department's seat corresponds to.

        Only Codex writes code (rule 1), so every implementing department maps
        to CODEX. Gemini's room is refused before this is ever asked.
        """
        return CODEX


# Fixed table. Nothing in a request adds to it, and an unknown department id
# is refused rather than defaulted - defaulting would let a typo send an order
# to whichever room happened to be first.
_CODE_ACCEPTANCE = (
    "Every C# file you touch still compiles by inspection: check each type, "
    "namespace, method signature and component name against the file that "
    "defines it.",
    "No value that belongs in the GameSpec JSON is hardcoded.",
    "You changed nothing outside the file list above.",
)

DEPARTMENTS: dict[str, Department] = {
    "gameplay": Department(
        id="gameplay",
        label="게임개발팀",
        seat="Codex CLI",
        summary="움직임, 점프, 충돌, 코인, 게임 규칙",
        files=(
            "Assets/GameFactory/Gameplay/**",
            "Assets/GameFactory/Core/**",
            "Assets/GameFactory/Modules/**",
        ),
        acceptance=_CODE_ACCEPTANCE,
    ),
    "ui": Department(
        id="ui",
        label="UI팀",
        seat="Codex CLI",
        summary="화면, 버튼, 글자, 상점, 안내 문구",
        files=(
            "Assets/GameFactory/UI/**",
        ),
        acceptance=_CODE_ACCEPTANCE,
    ),
    "level": Department(
        id="level",
        label="레벨생성팀",
        seat="Codex CLI",
        summary="맵 생성, 장애물 배치, 씬·프리팹 생성기",
        files=(
            "Assets/GameFactory/LevelGeneration/**",
            "Assets/GameFactory/Editor/**",
        ),
        acceptance=_CODE_ACCEPTANCE,
    ),
    "qa": Department(
        id="qa",
        label="품질관리팀",
        seat="Codex CLI",
        summary="테스트 추가·수정",
        files=(
            "Assets/GameFactory/Tests/**",
        ),
        acceptance=(
            "The test fails before the fix and passes after it, or it is not a "
            "test for this change.",
            "You changed nothing outside the file list above.",
        ),
    ),
    "plan": Department(
        id="plan",
        label="기획팀",
        seat="Codex CLI",
        summary="GameSpec 수치, 기획 문서",
        files=(
            "GameSpecs/*.json",
            "docs/**",
        ),
        acceptance=(
            "A GameSpec change keeps every field the validator requires, with "
            "the types GameSpecData.cs declares.",
            "You changed nothing outside the file list above.",
        ),
    ),
    # Rule 2: design is Gemini's work, not Codex's. Drawn as a room so the
    # office is not a lie about who does what, and refused with the reason
    # rather than quietly missing.
    "design": Department(
        id="design",
        label="디자인팀",
        seat="Gemini",
        summary="캐릭터·배경 그림 (Gemini 담당)",
        unavailable=(
            "디자인은 Gemini 몫이고, GEMINI_API_KEY 가 아직 없습니다. "
            "키를 넣으면 이 방이 열립니다."
        ),
    ),
}


def department(department_id: str) -> Department:
    """Look one up, or refuse. Never defaults."""
    found = DEPARTMENTS.get(department_id)
    if found is None:
        raise OrderRejected(f"'{department_id}' 부서는 없습니다.")
    return found


def clean_order_text(text: str) -> str:
    """The instruction, checked for length only.

    Deliberately NOT sanitised beyond whitespace and control characters: this
    string is going into a JSON file and then into a prompt, never onto a
    command line, so there is no shell metacharacter to defuse. Pretending to
    escape it would suggest the danger lies here rather than in argv, which is
    where section 10 actually puts it.
    """
    # Control characters would make the board unreadable and serve no purpose
    # in an instruction. Tab and newline survive - an order can have lines.
    stripped = "".join(c for c in (text or "")
                       if c in "\t\n" or ord(c) >= 0x20)
    stripped = stripped.strip()

    if len(stripped) < MIN_ORDER_CHARS:
        raise OrderRejected(
            f"지시가 너무 짧습니다. 무엇을 어떻게 바꿀지 {MIN_ORDER_CHARS}자 이상 적어주세요.")
    if len(stripped) > MAX_ORDER_CHARS:
        raise OrderRejected(
            f"지시가 너무 깁니다 ({len(stripped)}자). {MAX_ORDER_CHARS}자 안으로 줄여주세요.")
    return stripped


def next_order_id(board: TaskBoard, now: datetime | None = None) -> str:
    """ORDER-<yyyymmdd>-<nn>, counting only today's orders.

    Scans the board rather than keeping a counter in a file: the board is the
    record, and a counter that drifted from it would hand out an id that is
    already taken - which TaskBoard.get would then resolve to the wrong task.
    """
    stamp = (now or datetime.now()).strftime("%Y%m%d")
    today = f"{ORDER_PREFIX}-{stamp}-"
    used = {t.id for t in board.tasks}

    number = 1
    while f"{today}{number:02d}" in used:
        number += 1
    return f"{today}{number:02d}"


def title_from(text: str, limit: int = 72) -> str:
    """A one-line title for the board, taken from the order's first line."""
    first = next((line.strip() for line in text.splitlines() if line.strip()), text)
    if len(first) <= limit:
        return first
    # Cut on a space when there is one nearby, so the title does not end
    # mid-word. Korean has few spaces, hence the fallback.
    cut = first[:limit]
    space = cut.rfind(" ")
    return (cut[:space] if space > limit * 0.6 else cut).rstrip() + "…"


@dataclass
class PlacedOrder:
    task: Task
    dept: Department
    # True when the board already had this exact instruction open for this
    # department. Not an error - the user may well mean it - but the panel
    # says so rather than quietly stacking duplicates.
    duplicate_of: str = ""
    warnings: list[str] = field(default_factory=list)


def place_order(board: TaskBoard, department_id: str, text: str,
                now: datetime | None = None, *, save: bool = True) -> PlacedOrder:
    """Write one order onto the board as a Codex-owned task.

    Raises OrderRejected for anything a person should see and fix; the caller
    shows the message. Saves the board unless told not to, so a test can check
    the task without writing to the repository.
    """
    dept = department(department_id)
    if dept.unavailable:
        raise OrderRejected(dept.unavailable)
    if not dept.files:
        # A department with no allowlist could only ever produce a BLOCKED run.
        # Refusing here says so; letting it through would waste a Codex run and
        # report the failure as if the instruction were at fault.
        raise OrderRejected(
            f"{dept.label}에는 아직 담당 파일 목록이 없어서 일을 맡길 수 없습니다.")

    instruction = clean_order_text(text)
    order_id = next_order_id(board, now)

    duplicate = next(
        (t.id for t in board.tasks
         if t.status in (TODO,) and t.goal.strip() == instruction
         and list(t.files) == list(dept.files)),
        "",
    )

    task = Task(
        id=order_id,
        # English for Codex, Korean kept alongside for the page - the same
        # split the board already uses.
        title=f"[{dept.id}] {title_from(instruction)}",
        title_ko=title_from(instruction),
        owner=dept.seat_owner(),
        status=TODO,
        goal=instruction,
        files=list(dept.files),
        acceptance=list(dept.acceptance),
        notes=[
            f"제어판 명령창에서 {dept.label}으로 접수. "
            f"지시 원문은 goal 에 그대로 있습니다.",
            "ORDERED VIA CONTROL PANEL - the goal above is the user's own words, "
            "not a specification written for you. If it is ambiguous, say what "
            "you assumed in your final message rather than guessing silently.",
        ],
    )
    board.tasks.append(task)
    if save:
        board.save()

    return PlacedOrder(task=task, dept=dept, duplicate_of=duplicate)


def board_path(company_root: Path) -> Path:
    return company_root / "config" / "TASKBOARD.json"
