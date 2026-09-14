"""Tests for the shared Claude/Codex task board.

Run:  python3 AI_GAME_COMPANY/tests/test_teamwork.py

No Codex binary is needed - a fake stands in for it. What these check is the
part that makes letting an agent write to the working tree survivable: the
allowlist is enforced against a real git diff, a run that wanders outside it
is blocked rather than accepted, nothing is ever auto-committed, and a run
that finished is still not a run that is done.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from company.orchestrator.agent_registry import AgentRegistry  # noqa: E402
from company.orchestrator.teamwork import (  # noqa: E402
    BLOCKED, CANCELLED, CODEX, DONE, IN_PROGRESS, REVIEW, TODO, TOOL_OWNED_PATHS,
    AllowlistViolation, NotCodexOwned, Task, TaskBoard, TaskMutationError, TaskNotFound,
    build_prompt, changed_paths, concurrent_work, run_task,
)

BOARD = Path(__file__).resolve().parents[1] / "config" / "TASKBOARD.json"


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True,
                   capture_output=True, text=True)


class FakeCodexAgent:
    """Stands in for CodexRunner.implement, and edits files like the real one."""

    def __init__(self, repo: Path, writes: dict[str, str] | None = None,
                 message: str = "done"):
        self.repo = repo
        self.writes = writes or {}
        self.message = message
        self.prompts: list[str] = []

    def implement(self, prompt: str, timeout_seconds: int | None = None):
        self.prompts.append(prompt)
        for relative, content in self.writes.items():
            target = self.repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        class Result:
            last_message = self.message
            output_path = None

        return Result()


class RepoTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)

        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "t@example.com")
        git(self.repo, "config", "user.name", "t")
        (self.repo / "seed.txt").write_text("seed\n", encoding="utf-8")

        # The board lives INSIDE the repository and is committed, exactly as
        # the real one does. That is not incidental to these tests: it is why
        # the snapshot has to be taken after run_task saves the status, and a
        # board kept outside the tree here would hide that.
        self.board = TaskBoard(path=self.repo / "board.json", tasks=[
            Task(id="T1", title="a task", owner=CODEX, status=TODO,
                 goal="do a thing", files=["src/allowed.cs", "docs/"]),
        ])
        self.board.save()

        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "seed")

    def tearDown(self):
        self.tmp.cleanup()


class AllowlistTests(unittest.TestCase):
    def test_exact_path_matches(self):
        task = Task(id="T", title="t", files=["Assets/A.cs"])
        self.assertTrue(task.covers("Assets/A.cs"))
        self.assertFalse(task.covers("Assets/B.cs"))

    def test_glob_matches(self):
        task = Task(id="T", title="t", files=["Assets/**/*.cs"])
        self.assertTrue(task.covers("Assets/GameFactory/UI/X.cs"))
        self.assertFalse(task.covers("Assets/GameFactory/UI/X.meta"))

    def test_trailing_slash_means_the_whole_directory(self):
        task = Task(id="T", title="t", files=["Assets/GameFactory/UI/"])
        self.assertTrue(task.covers("Assets/GameFactory/UI/X.cs"))
        self.assertTrue(task.covers("Assets/GameFactory/UI/nested/Y.cs"))
        self.assertFalse(task.covers("Assets/GameFactory/Core/X.cs"))

    def test_backslashes_are_normalised(self):
        # git reports forward slashes even on Windows, but a board edited on
        # the PC can easily end up with backslashes in a pattern.
        task = Task(id="T", title="t", files=["Assets\\GameFactory\\UI\\X.cs"])
        self.assertTrue(task.covers("Assets/GameFactory/UI/X.cs"))

    def test_no_allowlist_covers_nothing(self):
        # An empty list must not read as "anything goes" - that is the
        # deny-by-default rule the policy module uses for the same reason.
        task = Task(id="T", title="t", files=[])
        self.assertFalse(task.covers("anything.cs"))


class BoardMutationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "board.json"
        self.board = TaskBoard(path=self.path, tasks=[
            Task(id="R", title="review", status=REVIEW),
            Task(id="T", title="todo", status=TODO),
            Task(id="D", title="depends", status=TODO, depends_on=["T"]),
        ])
        self.board.save()

    def tearDown(self):
        self.tmp.cleanup()

    def test_complete_accepts_only_review_items(self):
        self.board.complete("R")
        self.assertEqual(DONE, TaskBoard.load(self.path).get("R").status)
        with self.assertRaises(TaskMutationError):
            self.board.complete("T")

    def test_cancel_preserves_the_task_as_a_hidden_audit_record(self):
        self.board.cancel("T")
        task = TaskBoard.load(self.path).get("T")
        self.assertEqual(CANCELLED, task.status)
        self.assertTrue(any("CANCELLED" in note for note in task.notes))

    def test_delete_refuses_tasks_that_are_still_dependencies(self):
        with self.assertRaises(TaskMutationError):
            self.board.delete("T")
        self.board.delete("R")
        self.assertFalse(any(task.id == "R" for task in TaskBoard.load(self.path).tasks))


class ChangedPathsTests(RepoTestCase):
    def test_sees_a_modified_tracked_file(self):
        (self.repo / "seed.txt").write_text("changed\n", encoding="utf-8")
        self.assertIn("seed.txt", changed_paths(self.repo))

    def test_sees_an_untracked_new_file(self):
        # git diff would miss this, which is exactly what an agent asked to
        # add a new script produces.
        (self.repo / "brand_new.cs").write_text("x", encoding="utf-8")
        self.assertIn("brand_new.cs", changed_paths(self.repo))

    def test_sees_a_deleted_file(self):
        (self.repo / "seed.txt").unlink()
        self.assertIn("seed.txt", changed_paths(self.repo))

    def test_clean_tree_is_empty(self):
        self.assertEqual(set(), changed_paths(self.repo))


class RunTaskTests(RepoTestCase):
    def test_a_clean_run_lands_in_review_not_done(self):
        agent = FakeCodexAgent(self.repo, {"src/allowed.cs": "// work\n"})
        run = run_task(self.board, "T1", agent, self.repo)

        self.assertTrue(run.ok)
        self.assertEqual(["src/allowed.cs"], run.changed)
        # Nothing here compiled the project, so it cannot be called done.
        self.assertEqual(REVIEW, self.board.get("T1").status)
        self.assertNotEqual(DONE, self.board.get("T1").status)

    def test_a_file_outside_the_allowlist_blocks_the_task(self):
        agent = FakeCodexAgent(self.repo, {
            "src/allowed.cs": "// work\n",
            "secrets/keystore.properties": "password\n",
        })
        run = run_task(self.board, "T1", agent, self.repo)

        self.assertFalse(run.ok)
        self.assertIn("secrets/keystore.properties", run.outside_allowlist)
        self.assertEqual(BLOCKED, self.board.get("T1").status)

    def test_an_overreaching_run_is_not_reverted(self):
        # Deliberate: the other agent may have uncommitted work in the same
        # tree, and discarding it silently would be worse than the overreach.
        agent = FakeCodexAgent(self.repo, {"stray.cs": "// stray\n"})
        run_task(self.board, "T1", agent, self.repo)
        self.assertTrue((self.repo / "stray.cs").is_file())

    def test_changing_nothing_is_blocked_not_accepted(self):
        agent = FakeCodexAgent(self.repo, {})
        run = run_task(self.board, "T1", agent, self.repo)

        self.assertFalse(run.ok)
        self.assertEqual([], run.changed)
        self.assertEqual(BLOCKED, self.board.get("T1").status)

    def test_the_boards_own_bookkeeping_is_not_blamed_on_codex(self):
        # run_task writes the board twice (in_progress, then review). The board
        # is a tracked file in the same repo, so a snapshot taken at the wrong
        # moment puts board.json in the diff, where it matches no allowlist and
        # blocks every task on the board's own status write.
        agent = FakeCodexAgent(self.repo, {"src/allowed.cs": "// work\n"})
        run = run_task(self.board, "T1", agent, self.repo)

        self.assertEqual([], run.outside_allowlist)
        self.assertNotIn("board.json", run.changed)
        self.assertTrue(run.ok)

    def test_files_already_dirty_are_not_blamed_on_codex(self):
        # Claude's own work in progress sits in the same tree. Counting it as
        # Codex's overreach would block every task while anyone else is mid-edit.
        (self.repo / "claude_wip.cs").write_text("// mine\n", encoding="utf-8")

        agent = FakeCodexAgent(self.repo, {"src/allowed.cs": "// work\n"})
        run = run_task(self.board, "T1", agent, self.repo)

        self.assertTrue(run.ok)
        self.assertNotIn("claude_wip.cs", run.changed)

    def test_nothing_is_committed(self):
        agent = FakeCodexAgent(self.repo, {"src/allowed.cs": "// work\n"})
        run_task(self.board, "T1", agent, self.repo)

        log = subprocess.run(["git", "log", "--oneline"], cwd=str(self.repo),
                             capture_output=True, text=True, check=True)
        self.assertEqual(1, len(log.stdout.strip().splitlines()),
                         "run_task must never commit - a human reviews first")

        # Nor staged: staging is half a commit and hides the change from a
        # plain `git diff`.
        staged = subprocess.run(["git", "diff", "--cached", "--name-only"],
                                cwd=str(self.repo), capture_output=True,
                                text=True, check=True)
        self.assertEqual("", staged.stdout.strip())

    def test_a_raised_error_does_not_leave_the_task_in_progress(self):
        # run_task writes IN_PROGRESS before calling Codex. When the call
        # raised - a quota error, a timeout - the board used to keep saying
        # the task was being worked on, so the next person waited for a run
        # that had already died.
        class Exploding:
            def implement(self, prompt, timeout_seconds=None):
                raise RuntimeError("quota exhausted")

        with self.assertRaises(RuntimeError):
            run_task(self.board, "T1", Exploding(), self.repo)

        reloaded = TaskBoard.load(self.board.path)
        self.assertEqual(BLOCKED, reloaded.get("T1").status)
        self.assertIn("quota exhausted", " ".join(reloaded.get("T1").notes))

    def test_work_committed_mid_run_is_not_read_as_changed_nothing(self):
        """The CODEX-TESTCMD1 case: a commit landed while Codex worked.

        The edits left the working tree, after-minus-before came back empty,
        and a finished task was reported BLOCKED - "the run changed nothing".
        """
        class CommitsWhileWorking:
            def __init__(self, repo):
                self.repo = repo

            def implement(self, prompt, timeout_seconds=None):
                target = self.repo / "src" / "allowed.cs"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("// codex did the work\n", encoding="utf-8")
                # A person, or the scheduled sync, commits mid-run.
                git(self.repo, "add", "-A")
                git(self.repo, "commit", "-qm", "background commit during the run")

                class Result:
                    last_message = "done"
                    output_path = None
                return Result()

        run = run_task(self.board, "T1", CommitsWhileWorking(self.repo), self.repo)

        self.assertTrue(run.ok)
        self.assertEqual(REVIEW, self.board.get("T1").status)
        self.assertIn("src/allowed.cs", run.committed_during_run)
        note = self.board.get("T1").notes[-1]
        self.assertIn("COMMITTED while", note)
        self.assertIn("src/allowed.cs", note)

    def test_a_run_that_truly_changed_nothing_is_still_blocked(self):
        class DoesNothing:
            def implement(self, prompt, timeout_seconds=None):
                class Result:
                    last_message = "already done"
                    output_path = None
                return Result()

        run = run_task(self.board, "T1", DoesNothing(), self.repo)
        self.assertFalse(run.ok)
        self.assertEqual(BLOCKED, self.board.get("T1").status)
        self.assertEqual([], run.committed_during_run)

    def test_ctrl_c_does_not_leave_the_task_claiming_to_be_in_progress(self):
        # KeyboardInterrupt is a BaseException, so a narrower catch missed it
        # and CODEX-AICTL1 sat at in_progress with nothing running.
        class Interrupted:
            def implement(self, prompt, timeout_seconds=None):
                raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            run_task(self.board, "T1", Interrupted(), self.repo)

        task = TaskBoard.load(self.board.path).get("T1")
        self.assertEqual(BLOCKED, task.status)
        self.assertNotEqual(IN_PROGRESS, task.status)
        self.assertTrue(any("Ctrl+C" in note for note in task.notes), task.notes)
        # And it says to look at the tree, because Codex may have written files.
        self.assertTrue(any("git status" in note for note in task.notes), task.notes)

    def test_refuses_a_task_owned_by_claude(self):
        self.board.tasks[0].owner = "claude"
        self.board.save()
        agent = FakeCodexAgent(self.repo, {"src/allowed.cs": "x"})
        with self.assertRaises(NotCodexOwned):
            run_task(self.board, "T1", agent, self.repo)
        self.assertEqual([], agent.prompts, "nothing may be sent before the check")

    def test_refuses_an_unmet_dependency(self):
        self.board.tasks.append(
            Task(id="T0", title="first", owner=CODEX, status=TODO))
        self.board.tasks[0].depends_on = ["T0"]
        self.board.save()

        agent = FakeCodexAgent(self.repo, {"src/allowed.cs": "x"})
        with self.assertRaises(NotCodexOwned) as ctx:
            run_task(self.board, "T1", agent, self.repo)
        self.assertIn("T0", str(ctx.exception))

    def test_a_dependency_that_does_not_exist_is_unmet(self):
        self.board.tasks[0].depends_on = ["GHOST"]
        self.assertEqual(["GHOST (not on the board)"],
                         self.board.unmet_dependencies(self.board.tasks[0]))

    def test_unknown_task_id(self):
        with self.assertRaises(TaskNotFound):
            self.board.get("NOPE")

    def test_the_board_records_what_changed(self):
        agent = FakeCodexAgent(self.repo, {"src/allowed.cs": "// work\n"})
        run_task(self.board, "T1", agent, self.repo)

        # Re-read from disk: the handoff has to survive this process ending.
        reloaded = TaskBoard.load(self.board.path)
        self.assertEqual(["src/allowed.cs"], reloaded.get("T1").changed_files)
        self.assertEqual(REVIEW, reloaded.get("T1").status)


class PromptTests(RepoTestCase):
    def test_the_prompt_states_the_allowlist(self):
        prompt = build_prompt(self.board.get("T1"), self.board)
        self.assertIn("src/allowed.cs", prompt)
        self.assertIn("FILES YOU MAY CHANGE", prompt)

    def test_the_prompt_carries_the_house_rules(self):
        prompt = build_prompt(self.board.get("T1"), self.board)
        # Codex starts with no memory of this project, so the rules it would
        # otherwise break by default have to travel with the task.
        self.assertIn("linearVelocity", prompt)
        self.assertIn("ASCII only", prompt)
        self.assertIn("Do not commit", prompt)

    def test_the_prompt_says_unity_cannot_be_run(self):
        prompt = build_prompt(self.board.get("T1"), self.board)
        self.assertIn("YOU CANNOT RUN UNITY", prompt)

    def test_the_prompt_warns_about_concurrent_work(self):
        self.board.tasks.append(
            Task(id="C1", title="claude's job", owner="claude", status=IN_PROGRESS))
        prompt = build_prompt(self.board.get("T1"), self.board)
        self.assertIn("C1", prompt)
        self.assertIn("SAME WORKING TREE", prompt)

    def test_role_prompt_is_injected_before_goal_and_keeps_higher_rules(self):
        registry = AgentRegistry.load(
            Path(__file__).resolve().parents[1] / "config" / "AGENTS.json")
        task = self.board.get("T1")
        task.agent_role = "gameplay_engineer"
        task.department = "게임개발부"
        task.task_type = "gameplay_code"
        prompt = build_prompt(task, self.board, self.repo, registry=registry)
        self.assertIn("Agent ID: gameplay_engineer", prompt)
        self.assertIn("Unity 모바일 게임 Gameplay Engineer", prompt)
        self.assertLess(prompt.index("ROLE INSTRUCTIONS"), prompt.index("GOAL"))
        self.assertIn("PROJECT RULES", prompt)
        self.assertIn("src/allowed.cs", prompt)


class ToolChurnTests(RepoTestCase):
    """CODEX-BOARD1 defect 1: a file a TOOL rewrote must not block the task."""

    def test_packages_lock_churn_is_reported_not_blamed(self):
        self.assertIn("Packages/packages-lock.json", TOOL_OWNED_PATHS)
        agent = FakeCodexAgent(self.repo, {
            "src/allowed.cs": "// work\n",
            "Packages/packages-lock.json": "{ \"rewritten by unity\": true }\n",
        })
        run = run_task(self.board, "T1", agent, self.repo)

        self.assertTrue(run.ok, run.outside_allowlist)
        self.assertEqual(REVIEW, self.board.get("T1").status)
        self.assertEqual(["src/allowed.cs"], run.changed)
        self.assertEqual(["Packages/packages-lock.json"], run.tool_churn)
        # Not silently dropped: it is on the run, just not on the task.
        self.assertNotIn("Packages/packages-lock.json", self.board.get("T1").changed_files)

    def test_run_log_and_report_files_written_during_the_run_do_not_block(self):
        # The orchestrator's own run log, and the sync's reports, keep being
        # written while Codex works. AICTL1 and LIVE1 were both false-blocked
        # by exactly this on 2026-09-02.
        agent = FakeCodexAgent(self.repo, {
            "src/allowed.cs": "// work\n",
            "Reports/runs/20260902T234127Z-a661c4dd.txt": "# run\n",
            "Reports/runs/latest.txt": "# run\n",
            "Reports/sync-status/latest.txt": "Outcome: OK\n",
        })
        run = run_task(self.board, "T1", agent, self.repo)

        self.assertTrue(run.ok, run.outside_allowlist)
        self.assertEqual(["src/allowed.cs"], run.changed)
        self.assertEqual(3, len(run.tool_churn))
        self.assertEqual(REVIEW, self.board.get("T1").status)

    def test_a_real_stray_file_still_blocks(self):
        agent = FakeCodexAgent(self.repo, {
            "src/allowed.cs": "// work\n",
            "Packages/packages-lock.json": "{}\n",
            "src/other.cs": "// overreach\n",
        })
        run = run_task(self.board, "T1", agent, self.repo)

        self.assertFalse(run.ok)
        self.assertEqual(["src/other.cs"], run.outside_allowlist)
        self.assertEqual(BLOCKED, self.board.get("T1").status)


class BoardMetaTests(unittest.TestCase):
    """CODEX-BOARD1 defect 2: save() must not clobber hand-edited fields."""

    def test_a_hand_edited_comment_and_unknown_keys_survive_a_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "board.json"
            path.write_text(json.dumps({
                "_comment": "edited by a person - keep me",
                "_version": 7,
                "_house_note": "also keep me",
                "tasks": [{"id": "A", "title": "a", "owner": "codex", "status": "todo",
                           "title_ko": "가"}],
            }, ensure_ascii=False), encoding="utf-8")

            board = TaskBoard.load(path)
            board.tasks[0].status = REVIEW
            board.save()

            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual("edited by a person - keep me", data["_comment"])
            self.assertEqual(7, data["_version"])
            self.assertEqual("also keep me", data["_house_note"])
            self.assertEqual("review", data["tasks"][0]["status"])
            self.assertEqual("가", data["tasks"][0]["title_ko"])
            # And the key order still puts the metadata first, tasks last.
            self.assertEqual("tasks", list(data)[-1])

    def test_a_board_with_no_comment_gets_the_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "board.json"
            TaskBoard(path=path, tasks=[Task(id="A", title="a")]).save()
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("Shared task board", data["_comment"])
            self.assertEqual(1, data["_version"])

    def test_optional_agent_and_future_fields_survive_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "board.json"
            path.write_text(json.dumps({"tasks": [{
                "id": "A", "title": "a", "owner": "codex", "status": "todo",
                "goal": "g", "files": ["x"], "acceptance": ["a"],
                "depends_on": [], "notes": [], "changed_files": [], "last_run": "",
                "agent_role": "qa_engineer", "department": "품질보증부",
                "task_type": "qa", "priority": 90,
                "future_field": {"keep": True},
            }]}, ensure_ascii=False), encoding="utf-8")
            board = TaskBoard.load(path)
            self.assertEqual("qa_engineer", board.get("A").agent_role)
            board.save()
            saved = json.loads(path.read_text(encoding="utf-8"))["tasks"][0]
            self.assertEqual("qa", saved["task_type"])
            self.assertEqual(90, saved["priority"])
            self.assertEqual({"keep": True}, saved["future_field"])


class ConcurrentWorkTests(RepoTestCase):
    """CODEX-BOARD1 defect 3: the warning tracks the tree, not the status."""

    def setUp(self):
        super().setUp()
        self.board.tasks.append(
            Task(id="C1", title="claude's job", owner="claude", status=REVIEW,
                 files=["docs/"]))
        self.board.save()
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "add C1")

    def test_a_committed_review_task_is_not_called_work_in_progress(self):
        prompt = build_prompt(self.board.get("T1"), self.board, self.repo)
        self.assertNotIn("SAME WORKING TREE", prompt)
        self.assertNotIn("C1", prompt.split("ACCEPTANCE CRITERIA")[1].split("PROJECT RULES")[0])

    def test_a_task_with_a_dirty_declared_file_is_named_with_the_file(self):
        (self.repo / "docs").mkdir(exist_ok=True)
        (self.repo / "docs" / "draft.md").write_text("wip\n", encoding="utf-8")

        lines = concurrent_work(self.board.get("T1"), self.board, self.repo)
        self.assertTrue(any("C1" in line for line in lines), lines)
        self.assertTrue(any("docs/draft.md" in line for line in lines), lines)
        prompt = build_prompt(self.board.get("T1"), self.board, self.repo)
        self.assertIn("SAME WORKING TREE", prompt)
        self.assertIn("docs/draft.md", prompt)

    def test_without_git_it_falls_back_to_status_instead_of_raising(self):
        self.board.tasks.append(
            Task(id="C2", title="in flight", owner="claude", status=IN_PROGRESS))
        with tempfile.TemporaryDirectory() as not_a_repo:
            prompt = build_prompt(self.board.get("T1"), self.board, Path(not_a_repo))
        self.assertIn("C2", prompt)
        self.assertIn("SAME WORKING TREE", prompt)

    def test_the_task_itself_is_never_listed_as_concurrent(self):
        (self.repo / "src").mkdir(exist_ok=True)
        (self.repo / "src" / "allowed.cs").write_text("mine\n", encoding="utf-8")
        lines = concurrent_work(self.board.get("T1"), self.board, self.repo)
        self.assertFalse(any("T1" in line for line in lines), lines)


class CommittedBoardTests(unittest.TestCase):
    """The real board must stay loadable - it is a handoff, not a scratch file."""

    def test_the_committed_board_parses(self):
        if not BOARD.is_file():
            self.skipTest("no board committed")
        board = TaskBoard.load(BOARD)
        self.assertTrue(board.tasks)

    def test_every_task_has_an_owner_a_status_and_an_allowlist(self):
        if not BOARD.is_file():
            self.skipTest("no board committed")
        for task in TaskBoard.load(BOARD).tasks:
            self.assertIn(task.owner, ("claude", "codex"), task.id)
            self.assertIn(task.status, (TODO, IN_PROGRESS, REVIEW, BLOCKED, DONE, CANCELLED), task.id)
            self.assertTrue(task.goal, f"{task.id} has no goal")
            # A Codex task with no allowlist could never pass the check, so it
            # would be an impossible handoff rather than a permissive one.
            if task.owner == CODEX:
                self.assertTrue(task.files, f"{task.id} has no file allowlist")
                self.assertTrue(task.acceptance, f"{task.id} has no acceptance criteria")

    def test_task_ids_are_unique(self):
        if not BOARD.is_file():
            self.skipTest("no board committed")
        ids = [t.id for t in TaskBoard.load(BOARD).tasks]
        self.assertEqual(len(ids), len(set(ids)))

    def test_dependencies_point_at_real_tasks(self):
        if not BOARD.is_file():
            self.skipTest("no board committed")
        board = TaskBoard.load(BOARD)
        ids = {t.id for t in board.tasks}
        for task in board.tasks:
            for dependency in task.depends_on:
                self.assertIn(dependency, ids, f"{task.id} depends on a missing task")


if __name__ == "__main__":
    unittest.main(verbosity=2)
