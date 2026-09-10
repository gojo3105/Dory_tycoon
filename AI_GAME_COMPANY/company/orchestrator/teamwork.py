"""The shared task board Claude and Codex both work from.

WHY THIS EXISTS. Codex was wired in as a reviewer only: it read the tree and
commented, and every line of code still came from Claude. That makes Codex a
second opinion, not a second developer. This module is the other half - a
board both agents read, tasks with an explicit owner, and a runner that hands
a Codex-owned task to `codex exec --sandbox workspace-write` so Codex writes
the code itself.

The board is a plain JSON file (config/TASKBOARD.json) on purpose. It is in
git, so a handoff survives a container being reclaimed, the user can read and
edit it without running anything, and "who is doing what" has one answer both
agents and the human see.

THREE GUARDS, because letting an agent write to the tree unsupervised is the
risky part:

  FILE ALLOWLIST     Every task names the files it may touch. After the run
                     the actual git diff is checked against that list, and a
                     task that wandered outside it is marked BLOCKED rather
                     than done. Nothing is reverted automatically - the other
                     agent may have uncommitted work in the same tree, and
                     silently discarding it would be worse than the overreach.

  NEVER AUTO-COMMIT  This module stages nothing and commits nothing. CLAUDE.md
                     is explicit that commits and pushes need the user, and an
                     agent that commits its own work removes the one review
                     point that catches the rest.

  DONE MEANS VERIFIED  run_task never sets status "done". It sets "review",
                     with the changed files recorded. Something that actually
                     compiles the project (orchestrator build) decides done -
                     the same section 38 rule that stops an APK-less build
                     being called a success.
"""

from __future__ import annotations

import fnmatch
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CLAUDE = "claude"
CODEX = "codex"
OWNERS = (CLAUDE, CODEX)

TODO = "todo"
IN_PROGRESS = "in_progress"
REVIEW = "review"
BLOCKED = "blocked"
DONE = "done"
STATUSES = (TODO, IN_PROGRESS, REVIEW, BLOCKED, DONE)


class TaskNotFound(KeyError):
    """No task with that id on the board."""


class NotCodexOwned(RuntimeError):
    """Refusing to hand Claude's task to Codex, or the other way round."""


class AllowlistViolation(RuntimeError):
    """The run changed files the task never claimed."""


@dataclass
class Task:
    id: str
    title: str
    owner: str = CODEX
    status: str = TODO
    # The page the user reads is Korean; the prompt Codex reads is English.
    # Both are titles for the same task, so both live on it.
    title_ko: str = ""
    goal: str = ""
    # Repo-relative glob patterns. fnmatch, not regex: these are written by
    # hand into a JSON file and "Assets/**/*.cs" should mean what it looks like.
    files: list[str] = field(default_factory=list)
    acceptance: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    last_run: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Task":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id, "title": self.title, "owner": self.owner,
            "status": self.status, "goal": self.goal, "files": self.files,
            "acceptance": self.acceptance, "depends_on": self.depends_on,
            "notes": self.notes, "changed_files": self.changed_files,
            "last_run": self.last_run,
        }
        if self.title_ko:
            data["title_ko"] = self.title_ko
        return data

    def covers(self, repo_relative_path: str) -> bool:
        """True if this path is inside the task's declared allowlist."""
        # Normalised to forward slashes before matching: git reports them that
        # way even on Windows, and the patterns are written that way too.
        path = repo_relative_path.replace("\\", "/")
        for pattern in self.files:
            normalised = pattern.replace("\\", "/")
            if fnmatch.fnmatch(path, normalised):
                return True
            # "Assets/GameFactory/UI/" as a shorthand for everything under it.
            if normalised.endswith("/") and path.startswith(normalised):
                return True
        return False


DEFAULT_BOARD_COMMENT = (
    "Shared task board for Claude and Codex. 'owner' decides who implements a "
    "task; 'files' is the allowlist its diff is checked against. Run one with: "
    "python -m company.orchestrator.main team run --task <id>"
)


@dataclass
class TaskBoard:
    path: Path
    tasks: list[Task] = field(default_factory=list)
    # Every top-level key that is not "tasks", kept verbatim. The board is a
    # hand-annotated handoff document as much as a data file, and a save()
    # that rewrote _comment from a constant silently reverted every note a
    # person had put there - which is how CODEX-BOARD1 came to carry a note
    # that begins "recorded here because _comment gets overwritten".
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "TaskBoard":
        if not path.is_file():
            return cls(path=path, tasks=[])
        # utf-8-sig: the board can be edited on the PC, where editors add a BOM.
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        meta = {k: v for k, v in data.items() if k != "tasks"}
        return cls(path=path, tasks=[Task.from_dict(t) for t in data.get("tasks", [])],
                   meta=meta)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {}
        # Defaults only fill a gap; anything the file already had wins.
        payload["_comment"] = self.meta.get("_comment", DEFAULT_BOARD_COMMENT)
        payload["_version"] = self.meta.get("_version", 1)
        for key, value in self.meta.items():
            if key not in payload:
                payload[key] = value
        payload["tasks"] = [t.to_dict() for t in self.tasks]
        self.path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    def get(self, task_id: str) -> Task:
        for task in self.tasks:
            if task.id == task_id:
                return task
        raise TaskNotFound(f"no task '{task_id}' on {self.path}")

    def open_for(self, owner: str) -> list[Task]:
        return [t for t in self.tasks
                if t.owner == owner and t.status in (TODO, IN_PROGRESS)]

    def unmet_dependencies(self, task: Task) -> list[str]:
        """Dependency ids that are not DONE yet. A missing id counts as unmet."""
        unmet = []
        for dependency in task.depends_on:
            try:
                if self.get(dependency).status != DONE:
                    unmet.append(dependency)
            except TaskNotFound:
                unmet.append(f"{dependency} (not on the board)")
        return unmet


# ---- git ----------------------------------------------------------------

# Tracked files that TOOLS rewrite, not agents. Unity re-resolves packages
# whenever the Editor is open and rewrites packages-lock.json as a side
# effect; the first real `team run` saw exactly that land in its diff, match
# no allowlist, and report a correct piece of work as BLOCKED. These are
# excluded from the allowlist check and reported separately, so a genuine
# change to one is still visible rather than silently dropped.
TOOL_OWNED_PATHS: tuple[str, ...] = (
    "Packages/packages-lock.json",
    # Written by the orchestrator's own run log and by the scheduled sync's
    # report scripts, which keep running while Codex works. On 2026-09-02
    # two finished Codex runs (AICTL1, LIVE1) were reported BLOCKED because
    # a concurrent `dashboard` action wrote Reports/runs/*.txt into the diff.
    "Reports/runs/*",
    "Reports/sync-status/*",
    "Reports/errors/*",
    "Reports/build-status/*",
)


def is_tool_owned(repo_relative_path: str) -> bool:
    path = repo_relative_path.replace("\\", "/")
    return any(fnmatch.fnmatch(path, pattern) for pattern in TOOL_OWNED_PATHS)


def dirty_paths_or_none(repo_root: Path) -> set[str] | None:
    """changed_paths(), or None when git cannot answer.

    For callers that only want a better answer when one is available - a
    prompt builder must not be able to fail the run because git is missing
    or the path is not a repository.
    """
    try:
        return changed_paths(repo_root)
    except (OSError, subprocess.CalledProcessError):
        return None


def head_commit(repo_root: Path) -> str:
    """The current commit, or '' when git cannot answer.

    Used to notice that something COMMITTED during a run - see run_task.
    """
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(repo_root), capture_output=True,
            text=True, check=True, encoding="utf-8", errors="replace")
    except (OSError, subprocess.CalledProcessError):
        return ""
    return completed.stdout.strip()


def paths_between(repo_root: Path, before: str, after: str) -> list[str]:
    """Files changed by the commits made between two revisions."""
    if not before or not after or before == after:
        return []
    try:
        completed = subprocess.run(
            ["git", "diff", "--name-only", f"{before}..{after}"], cwd=str(repo_root),
            capture_output=True, text=True, check=True, encoding="utf-8",
            errors="replace")
    except (OSError, subprocess.CalledProcessError):
        return []
    return sorted(line.strip() for line in completed.stdout.splitlines() if line.strip())


def changed_paths(repo_root: Path) -> set[str]:
    """Every path git sees as modified, added, deleted or untracked.

    --porcelain rather than a diff: an untracked new file is exactly what an
    agent asked to add a script produces, and `git diff` would not list it.
    """
    completed = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=str(repo_root), capture_output=True, text=True, check=True,
        # Explicit: a path with a non-ASCII character in it would otherwise be
        # decoded with the machine's locale encoding and raise, which would
        # take down run_task before Codex was even invoked.
        encoding="utf-8", errors="replace",
    )

    paths: set[str] = set()
    for line in completed.stdout.splitlines():
        if len(line) < 4:
            continue
        entry = line[3:]
        # Renames arrive as "old -> new"; both sides were touched.
        if " -> " in entry:
            before, after = entry.split(" -> ", 1)
            paths.add(_unquote(before))
            paths.add(_unquote(after))
        else:
            paths.add(_unquote(entry))
    return paths


def _unquote(path: str) -> str:
    """git quotes paths containing non-ASCII or spaces. Strip the quotes."""
    path = path.strip()
    if len(path) >= 2 and path.startswith('"') and path.endswith('"'):
        return path[1:-1]
    return path


# ---- prompt --------------------------------------------------------------

# Codex starts each run with no memory of this project, so the rules it has to
# follow travel with the task. These are the ones from CLAUDE.md that a
# well-meaning agent breaks by default.
HOUSE_RULES = """\
PROJECT RULES (from CLAUDE.md - follow these exactly):
- Unity 6000.5.9f1, C#, 2D (Rigidbody2D/Collider2D), uGUI. Android portrait.
- Use current Unity APIs. Rigidbody2D.linearVelocity, NOT the obsolete .velocity.
- Do not hardcode values that belong in the GameSpec JSON. Structural wiring
  goes in SetReferences/SetTargets methods (edit time); numeric tuning goes in
  Configure methods (runtime).
- Editor scripts live in Assets/GameFactory/Editor and may use UnityEditor.
  Runtime scripts must NEVER reference UnityEditor - it does not exist in a
  player build and will break the Android build.
- If you use a new Unity built-in module API, add that module to
  Packages/manifest.json. Do not hand-edit packages-lock.json.
- Files under scripts/**/*.ps1 must be ASCII only. Windows PowerShell 5.1
  misreads non-ASCII in a BOM-less UTF-8 .ps1 and the script fails to parse.
- Do not delete .meta files. Do not delete files in bulk.
- Do not commit, stage, push, or change git history. Leave your work in the
  working tree; a human reviews and commits it.
- Do not add third-party packages or download assets.
- Comments explain WHY a non-obvious choice was made, not what the line does.

YOU CANNOT RUN UNITY. There is no Editor available to this run, so you cannot
compile or play-test. Review your own C# by hand: check every type name,
namespace, method signature and component reference against the files that
define them. If you are unsure whether an API exists, say so in your final
message rather than guessing.
"""


def concurrent_work(task: Task, board: TaskBoard,
                    repo_root: Path | None = None) -> list[str]:
    """Lines naming other agents' work that is actually in flight.

    With a repository to look at, a task counts only when at least one of its
    declared files is dirty right now, and those files are named - a REVIEW
    task whose work was committed hours ago is not "being edited", and saying
    so warned Codex off perfectly stable files. Without git (no repo_root, or
    git unavailable) it falls back to the status-based reading rather than
    raising: a prompt builder must not be able to fail the run.
    """
    others = [t for t in board.tasks if t.owner != task.owner and t.id != task.id]
    dirty = dirty_paths_or_none(repo_root) if repo_root is not None else None

    if dirty is None:
        return [f"  - [{t.owner}] {t.id}: {t.title}"
                for t in others if t.status in (IN_PROGRESS, REVIEW)]

    lines = []
    for other in others:
        touched = sorted(p for p in dirty if other.covers(p))
        if touched:
            lines.append(f"  - [{other.owner}] {other.id}: {other.title}")
            lines.extend(f"      {p}" for p in touched)
    return lines


def build_prompt(task: Task, board: TaskBoard, repo_root: Path | None = None) -> str:
    """The full instruction handed to `codex exec`.

    Deliberately verbose about the allowlist: the check afterwards is a
    backstop, and a task that states its boundaries up front rarely trips it.
    """
    allowlist = "\n".join(f"  - {pattern}" for pattern in task.files) or "  (none declared)"
    acceptance = "\n".join(f"  {i}. {line}" for i, line in enumerate(task.acceptance, 1)) \
        or "  (none declared)"
    notes = "\n".join(f"  - {line}" for line in task.notes)

    concurrent = "\n".join(concurrent_work(task, board, repo_root))

    sections = [
        f"TASK {task.id}: {task.title}",
        "",
        "GOAL",
        f"  {task.goal}",
        "",
        "FILES YOU MAY CHANGE - staying inside this list is part of the task.",
        "If the work genuinely needs a file that is not listed, STOP, change",
        "nothing further, and say which file and why in your final message.",
        allowlist,
        "",
        "ACCEPTANCE CRITERIA",
        acceptance,
    ]

    if notes:
        sections += ["", "NOTES", notes]

    if concurrent:
        sections += [
            "",
            "ANOTHER AGENT IS EDITING THIS SAME WORKING TREE RIGHT NOW:",
            concurrent,
            "Do not touch files outside your allowlist - you would be editing",
            "on top of work in progress.",
        ]

    sections += [
        "",
        HOUSE_RULES,
        "",
        "FINAL MESSAGE: list every file you changed and, in one line each, what",
        "you changed in it. If you could not finish, say exactly what is left",
        "and why. Do not claim a change compiles - you could not compile it.",
    ]
    return "\n".join(sections)


# ---- running -------------------------------------------------------------


@dataclass
class TaskRun:
    task: Task
    ok: bool
    changed: list[str]
    outside_allowlist: list[str]
    summary: str
    output_path: Path | None = None
    # Tool-rewritten files that changed during the run (see TOOL_OWNED_PATHS).
    # Not the agent's doing and not a reason to block, but not hidden either.
    tool_churn: list[str] = field(default_factory=list)
    # Files that were COMMITTED while the run was in flight - by the scheduled
    # sync, or by a person. The work is real; it just left the working tree.
    committed_during_run: list[str] = field(default_factory=list)


def run_task(board: TaskBoard, task_id: str, codex: Any, repo_root: Path,
             timeout_seconds: int | None = None) -> TaskRun:
    """Hand one Codex-owned task to Codex, then check what it actually did.

    Raises rather than returning a soft failure when the task should never have
    been started (wrong owner, unmet dependency); returns a TaskRun with ok
    False when Codex ran but the result cannot be accepted.
    """
    task = board.get(task_id)

    if task.owner != CODEX:
        raise NotCodexOwned(
            f"task {task.id} is owned by '{task.owner}', not codex. Change the "
            "owner on the board first if the handoff is intended."
        )
    if task.status == DONE:
        raise NotCodexOwned(f"task {task.id} is already done.")

    unmet = board.unmet_dependencies(task)
    if unmet:
        raise NotCodexOwned(
            f"task {task.id} depends on {', '.join(unmet)}, which is not done yet."
        )

    task.status = IN_PROGRESS
    board.save()

    # Snapshot AFTER the status save, not before it. The board lives in the
    # repository, so saving it dirties a tracked file; taken the other way
    # round, that write lands in the diff below, matches no task's allowlist,
    # and blocks every run on the board's own bookkeeping.
    #
    # Codex editing the board DURING its run still shows up here, is still
    # outside every allowlist, and still blocks - which is what should happen.
    before = changed_paths(repo_root)
    # A commit made WHILE the run works moves its edits out of the working
    # tree, so the after-minus-before diff comes back empty and the run reads
    # as "changed nothing". That is what happened to CODEX-TESTCMD1 on
    # 2026-09-05: Codex did the whole task, a commit landed mid-run, and the
    # result was reported BLOCKED. Remembering HEAD is what tells the two
    # apart - a run that did nothing from one whose work was carried off.
    head_before = head_commit(repo_root)

    try:
        result = codex.implement(build_prompt(task, board, repo_root),
                                 timeout_seconds=timeout_seconds)
    except BaseException as exc:
        # BaseException, not Exception: Ctrl+C raises KeyboardInterrupt, which
        # is NOT an Exception, so the narrower catch let an interrupted run
        # leave the board saying IN_PROGRESS forever. That happened for real
        # to CODEX-AICTL1 on 2026-09-05 - the board claimed work was in flight
        # while nothing was running. Whatever ends the run, the board must
        # stop claiming otherwise. Re-raised: the caller still reports it.
        if isinstance(exc, KeyboardInterrupt):
            reason = ("BLOCKED: stopped by the user (Ctrl+C) before the run reported back. "
                      "Codex may already have edited files - check `git status` before "
                      "re-running, or its work will be mixed into the next run's diff.")
        else:
            reason = f"BLOCKED: the run raised {type(exc).__name__}: {exc}"
        task.status = BLOCKED
        task.notes.append(reason[:600])
        board.save()
        raise

    after = changed_paths(repo_root)
    # Only what THIS run introduced. Files already dirty before it started
    # belong to whoever was working in the tree, not to Codex.
    introduced = sorted(after - before)
    tool_churn = [p for p in introduced if is_tool_owned(p)]
    changed = [p for p in introduced if not is_tool_owned(p)]
    outside = sorted(p for p in changed if not task.covers(p))

    # Deliberately NOT run through the allowlist check: a commit made during
    # the run can carry anyone's work, so blaming this task for what is in it
    # would be a guess. It is enough to know the tree did not stay still.
    committed = [p for p in paths_between(repo_root, head_before,
                                          head_commit(repo_root))
                 if not is_tool_owned(p)]

    task.changed_files = changed or committed
    task.last_run = str(result.output_path) if result.output_path else ""
    task.notes = list(task.notes)

    if outside:
        task.status = BLOCKED
        task.notes.append(
            "BLOCKED: the run changed files outside the allowlist: "
            + ", ".join(outside)
        )
        board.save()
        return TaskRun(task=task, ok=False, changed=changed, outside_allowlist=outside,
                       summary=result.last_message, output_path=result.output_path,
                       tool_churn=tool_churn)

    if not changed and committed:
        # The tree is clean but commits landed while the run worked. Its edits
        # are in them. Report it as review with the commit named, rather than
        # as "changed nothing" - which is what threw away a finished task once.
        task.status = REVIEW
        task.notes.append(
            "REVIEW: the working tree is clean, but these were COMMITTED while "
            "the run was in flight, so its edits are in that commit rather than "
            "in the tree: " + ", ".join(committed[:12])
            + ("..." if len(committed) > 12 else "")
            + ". Check that commit before re-running - the work may already be done.")
        board.save()
        return TaskRun(task=task, ok=True, changed=[], outside_allowlist=[],
                       summary=result.last_message, output_path=result.output_path,
                       tool_churn=tool_churn, committed_during_run=committed)

    if not changed:
        # Codex answering without editing anything is a real outcome - usually
        # it decided the task was already done or it got stuck. Either way it
        # is not progress, and calling it done would be the section 38 failure.
        task.status = BLOCKED
        task.notes.append("BLOCKED: the run finished but changed no files.")
        board.save()
        return TaskRun(task=task, ok=False, changed=[], outside_allowlist=[],
                       summary=result.last_message, output_path=result.output_path,
                       tool_churn=tool_churn)

    # REVIEW, never DONE: nothing here compiled the project.
    task.status = REVIEW
    board.save()
    return TaskRun(task=task, ok=True, changed=changed, outside_allowlist=[],
                   summary=result.last_message, output_path=result.output_path,
                   tool_churn=tool_churn, committed_during_run=committed)
