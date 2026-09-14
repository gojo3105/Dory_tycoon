#!/usr/bin/env python3
"""Three-way merge for TASKBOARD.json, so a sync is never blocked by it.

WHY THIS EXISTS. The board is edited on both sides by design: Claude writes
specs and review notes upstream, and every `team run` on the build PC writes
status, changed_files and notes locally. Git sees one JSON file and calls it
a conflict, which stopped the scheduled sync three times in a row on
2026-09-05 - and a stopped sync is how a whole day's work went unnoticed
once already.

The merge itself lives in company/orchestrator/board_merge.py, because
TaskBoard.save turned out to need exactly the same thing: a base, a local, a
remote, and a rule for what to do when they disagree. Two copies of that rule
would drift, and the board is the one file here where a drift means somebody's
work quietly disappears.

Exits 0 on success, 2 when it cannot parse an input - in which case the
caller must leave the conflict to a person.

Usage:  merge-taskboard.py <base> <local> <remote> <output>
"""

from __future__ import annotations

import json
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Any

# Runnable as a plain script from the repo root, which is how sync-and-run.ps1
# calls it, without AI_GAME_COMPANY being on PYTHONPATH.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from company.orchestrator.board_merge import (  # noqa: E402
    STATUS_ORDER, merge_boards, merge_for_save, merge_notes, merge_task,
)

__all__ = ["STATUS_ORDER", "merge_boards", "merge_for_save", "merge_notes",
           "merge_task", "load", "main"]


def load(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8-sig") as handle:
        return json.load(handle, object_pairs_hook=OrderedDict)


def main(argv: list[str]) -> int:
    if len(argv) != 5:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    try:
        base, local, remote = (load(argv[i]) for i in (1, 2, 3))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"merge-taskboard: cannot read an input ({exc}) - leaving the "
              "conflict for a person.", file=sys.stderr)
        return 2

    # merge_boards, not merge_for_save: in a git merge a task missing from one
    # side is far more likely to be an old branch than a decision to delete,
    # and this tool's whole promise is that it drops nothing.
    merged = merge_boards(base, local, remote)
    with open(argv[4], "w", encoding="utf-8") as handle:
        json.dump(merged, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(f"merge-taskboard: merged {len(merged.get('tasks', []))} tasks into {argv[4]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
