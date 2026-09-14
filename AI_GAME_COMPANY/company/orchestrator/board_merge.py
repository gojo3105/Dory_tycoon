"""Three-way merge for the shared task board.

Used for two different collisions that turn out to be the same shape.

  A GIT MERGE. The board is edited on both sides by design: Claude writes
  specs and review notes upstream, and every `team run` on the build PC writes
  status, changed_files and notes locally. Git sees one JSON file and calls it
  a conflict, which stopped the scheduled sync three times in a row on
  2026-09-05 - and a stopped sync is how a whole day's work went unnoticed
  once already. tools/merge-taskboard.py is the CLI for this.

  A SAVE. TaskBoard.load reads the file into memory and TaskBoard.save writes
  the whole list back, so anything added to the file in between is gone. On
  2026-09-15 a `team chain` run held the board for twenty minutes while a
  local model timed out twice, then saved - and three task specs written in
  that window disappeared. sync-and-run.ps1 committed the loss as "chore: task
  board updated by a run". Nothing was broken; the run simply did not know.

The merge is safe because of what the board actually is:

  NOTES ARE APPEND-ONLY. Both sides' notes are kept, in base order first,
  then each side's additions. Nothing a person or a run wrote is dropped.

  A STATUS CHANGED BY A RUN OUTRANKS ONE CHANGED BY HAND. When only one side
  moved a status, that side wins. When BOTH moved it, LOCAL wins - local is
  the machine that actually ran the task - and the discarded value is written
  into the notes rather than vanishing. This file never silently loses a
  claim about what happened.

  RESULT FIELDS PREFER EVIDENCE. changed_files and last_run take whichever
  side is non-empty; if both are, local wins, for the same reason.

  A FIELD ONE SIDE CLEARED STAYS CLEARED, when the other side left it alone.
  Otherwise approving a review pause would put the review note straight back.

Tasks present on only one side are kept. Unknown top-level keys are kept.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

# Ordered worst-to-best only for reporting; it is NOT used to auto-promote a
# status, because "further along" is a judgement a person or a run makes.
STATUS_ORDER = ("todo", "in_progress", "blocked", "review", "done")

#: Fields whose text a spec owns. Upstream - "remote" - is where specs are
#: written, so it wins on the wording of the task itself.
SPEC_FIELDS = ("title", "title_ko", "goal", "files", "acceptance", "depends_on")


def merge_notes(base: list[str], local: list[str], remote: list[str]) -> list[str]:
    """Base order first, then each side's additions, without duplicates."""
    merged: list[str] = []
    seen: set[str] = set()
    for note in list(base) + list(local) + list(remote):
        if note not in seen:
            seen.add(note)
            merged.append(note)
    return merged


def merge_task(base: dict[str, Any] | None, local: dict[str, Any],
               remote: dict[str, Any]) -> dict[str, Any]:
    base = base or {}
    merged = OrderedDict(remote)
    merged.update({k: v for k, v in local.items() if k not in merged})

    # A key local no longer has, that remote still carries unchanged since
    # base, was cleared on purpose. The chain does exactly this: approving a
    # review pops task.extra["review_note"], and without this the note - and
    # so the panel's 계속 진행 button - would come back on a finished task.
    # Only when remote left it alone: if remote CHANGED it, somebody actively
    # set it and that is a real disagreement, where remote wins as usual.
    for key in set(base) - set(local):
        if key in merged and remote.get(key) == base.get(key):
            del merged[key]

    for field in SPEC_FIELDS:
        if field in remote:
            merged[field] = remote[field]
        elif field in local:
            merged[field] = local[field]

    notes = merge_notes(base.get("notes", []), local.get("notes", []),
                        remote.get("notes", []))

    base_status = base.get("status")
    local_status = local.get("status", base_status)
    remote_status = remote.get("status", base_status)

    if local_status == remote_status:
        merged["status"] = local_status
    elif remote_status == base_status:
        merged["status"] = local_status          # only local moved
    elif local_status == base_status:
        merged["status"] = remote_status         # only remote moved
    else:
        # Both moved. The machine that ran the task is the better witness,
        # and the other claim is recorded rather than dropped.
        merged["status"] = local_status
        notes.append(
            f"MERGE {base_status or '?'} -> local '{local_status}' kept, "
            f"upstream said '{remote_status}'. Both sides changed this status; "
            "the side that ran the task won. Check which is right.")

    merged["notes"] = notes
    for field in ("changed_files", "last_run"):
        merged[field] = local.get(field) or remote.get(field) or base.get(field) or (
            [] if field == "changed_files" else "")
    return merged


def tasks_by_id(board: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {t.get("id"): t for t in board.get("tasks", []) if t.get("id")}


def merge_boards(base: dict[str, Any], local: dict[str, Any],
                 remote: dict[str, Any]) -> dict[str, Any]:
    """Three-way merge that never drops a task. See merge_for_save for deletes."""
    merged = OrderedDict((k, v) for k, v in remote.items() if k != "tasks")
    for key, value in local.items():
        if key != "tasks" and key not in merged:
            merged[key] = value

    base_tasks = tasks_by_id(base)
    local_tasks = tasks_by_id(local)
    remote_tasks = tasks_by_id(remote)

    order: list[str] = []
    for board in (remote, local):
        for task in board.get("tasks", []):
            task_id = task.get("id")
            if task_id and task_id not in order:
                order.append(task_id)

    tasks = []
    for task_id in order:
        in_local, in_remote = local_tasks.get(task_id), remote_tasks.get(task_id)
        if in_local and in_remote:
            tasks.append(merge_task(base_tasks.get(task_id), in_local, in_remote))
        else:
            # Added on one side only - keep it as written.
            tasks.append(in_local or in_remote)
    merged["tasks"] = tasks
    return merged


def merge_for_save(base: dict[str, Any], local: dict[str, Any],
                   remote: dict[str, Any]) -> dict[str, Any]:
    """merge_boards, plus the deletions a git merge deliberately ignores.

    "Never drop a task" is the right rule for a git merge, where a missing
    task is far more likely to be an old branch than a decision. It is the
    wrong rule at save time: TaskBoard.delete exists, the dashboard has a
    button for it, and a delete that came back on the next save would be a
    worse bug than the one this is fixing.

    Base is what the file said when this process read it, so the two are
    distinguishable:

      gone from local  - this process deleted it, and knew what it was doing.
      gone from remote - somebody else deleted it while we held the file.
        Honoured only when our copy is untouched since base. If we wrote a
        note or a status onto it meanwhile, it stays: losing the record of a
        run is the one thing this whole file exists to prevent, and a task
        that reappears is a question rather than a loss.
    """
    merged = merge_boards(base, local, remote)
    base_tasks = tasks_by_id(base)
    local_tasks = tasks_by_id(local)
    remote_tasks = tasks_by_id(remote)

    deleted = set(base_tasks) - set(local_tasks)
    deleted |= {task_id for task_id in set(base_tasks) - set(remote_tasks)
                if local_tasks.get(task_id) == base_tasks.get(task_id)}
    if deleted:
        merged["tasks"] = [t for t in merged["tasks"]
                           if t.get("id") not in deleted]
    return merged
