"""Tests for chain_runner: the agent's own verdict decides what happens next.

Run:  python AI_GAME_COMPANY/tests/test_chain_runner.py

Every step runs through a stub executor rather than Codex. That is not a
compromise - it is the only way to exercise the branches that matter, since
the interesting ones are failures, limits and handoffs, and a real run gives
you whichever of those it feels like on the day.

The rules being held:

  AN UNREADABLE VERDICT IS NOT SUCCESS. A final message with no STATUS means
  nobody can tell, and advancing on "could not tell" is how a broken step
  moves the chain forward.

  THE TREE OUTRANKS THE CLAIM. An agent reporting success having changed no
  files is retried, not believed.

  A HANDOFF MOVES WORK, NEVER PERMISSIONS. Rerouting changes agent_role; the
  files allowlist is untouched.

  RUNNING OUT OF SUBSCRIPTION IS WHERE IT STOPS. Not where it starts
  spending, and not something that burns the retry budget either.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from company.orchestrator import chain_runner as chain  # noqa: E402
from company.orchestrator.teamwork import BLOCKED, REVIEW, Task, TaskBoard  # noqa: E402

ROLES = {"game_director", "gameplay_engineer", "art_director",
         "qa_engineer", "release_engineer"}


def board_with(*roles: str) -> TaskBoard:
    tasks = [
        Task(id=f"PLAN-t-{i:02d}", title=f"step {i}", owner="codex",
             agent_role=role, files=["docs/**"])
        for i, role in enumerate(roles, start=1)
    ]
    return TaskBoard(path=Path(tempfile.mkdtemp()) / "b.json", tasks=tasks)


def ok(summary="STATUS: OK", changed=("docs/a.md",)):
    return chain.StepResult(ok=True, summary=summary, changed=list(changed))


class VerdictTests(unittest.TestCase):
    def test_status_words_map_to_success_or_failure(self):
        for word in ("OK", "DONE", "complete", "SUCCESS", "pass"):
            self.assertIs(True, chain.read_verdict(f"STATUS: {word}").succeeded, word)
        for word in ("FAILED", "blocked", "ERROR", "NEEDS_HUMAN_REVIEW"):
            self.assertIs(False, chain.read_verdict(f"STATUS: {word}").succeeded, word)

    def test_an_unknown_word_is_not_success(self):
        self.assertIsNone(chain.read_verdict("STATUS: 아마도").succeeded)

    def test_a_message_with_no_status_is_not_success(self):
        self.assertIsNone(chain.read_verdict("전부 잘 됐습니다!").succeeded)

    def test_decoration_around_the_sections_is_tolerated(self):
        verdict = chain.read_verdict("- **STATUS:** OK\n * HANDOFF_TO: `qa_engineer`")
        self.assertEqual("OK", verdict.status)
        self.assertEqual("qa_engineer", verdict.handoff_to)

    def test_the_first_status_wins_over_a_later_mention(self):
        verdict = chain.read_verdict("STATUS: OK\nlater STATUS: FAILED")
        self.assertEqual("OK", verdict.status)


class DecisionTests(unittest.TestCase):
    def decide(self, step, summary="", attempt=1):
        return chain.decide(step, chain.read_verdict(summary or step.summary),
                            attempt=attempt, max_retry=5, roles=ROLES)

    def test_success_with_changes_continues(self):
        action, _ = self.decide(ok())
        self.assertEqual(chain.CONTINUE, action)

    def test_success_with_no_changes_is_retried_not_believed(self):
        action, why = self.decide(chain.StepResult(ok=True, summary="STATUS: OK",
                                                   changed=[]))
        self.assertEqual(chain.RETRY, action)
        self.assertIn("바뀐 파일이 없습니다", why)

    def test_an_unreadable_verdict_is_retried_even_on_a_clean_exit(self):
        action, why = self.decide(chain.StepResult(ok=True, summary="다 했어요",
                                                   changed=["docs/a.md"]))
        self.assertEqual(chain.RETRY, action)
        self.assertIn("STATUS", why)

    def test_a_reported_failure_is_retried(self):
        action, _ = self.decide(chain.StepResult(ok=False, summary="STATUS: FAILED"))
        self.assertEqual(chain.RETRY, action)

    def test_a_handoff_to_a_real_role_reroutes(self):
        action, why = self.decide(chain.StepResult(
            ok=False, summary="STATUS: FAILED\nHANDOFF_TO: art_director\n"
                              "HANDOFF_REASON: 스프라이트가 없다"))
        self.assertEqual(chain.REROUTE, action)
        self.assertIn("art_director", why)

    def test_a_handoff_to_an_unknown_role_is_not_a_reroute(self):
        action, _ = self.decide(chain.StepResult(
            ok=False, summary="STATUS: FAILED\nHANDOFF_TO: nobody"))
        self.assertEqual(chain.RETRY, action)

    def test_a_successful_step_is_not_rerouted_away(self):
        # A handoff named alongside success is the agent saying who is next,
        # not asking to be replaced.
        action, _ = self.decide(ok("STATUS: OK\nHANDOFF_TO: qa_engineer"))
        self.assertEqual(chain.CONTINUE, action)

    def test_a_subscription_limit_stops_rather_than_retrying(self):
        action, why = self.decide(chain.StepResult(ok=False, limited=True,
                                                   error="limit"))
        self.assertEqual(chain.STOP, action)
        self.assertIn("유료 API", why)

    def test_running_past_max_retry_stops(self):
        action, _ = self.decide(chain.StepResult(ok=False, summary="STATUS: FAILED"),
                                attempt=6)
        self.assertEqual(chain.STOP, action)


class ErrorHashTests(unittest.TestCase):
    def test_the_same_failure_hashes_the_same_despite_line_numbers(self):
        self.assertEqual(chain.error_hash("error at line 42 of Player.cs"),
                         chain.error_hash("error at line 91 of Player.cs"))

    def test_different_failures_hash_differently(self):
        self.assertNotEqual(chain.error_hash("null reference"),
                            chain.error_hash("missing asmdef"))


class ChainTests(unittest.TestCase):
    def test_a_clean_chain_runs_every_step_once(self):
        target = board_with("game_director", "gameplay_engineer", "qa_engineer")
        calls: list[str] = []

        def execute(task, role):
            calls.append(task.id)
            return ok()

        result = chain.run_chain(target, "PLAN-t-", execute, roles=ROLES)
        self.assertTrue(result.finished)
        self.assertEqual(["PLAN-t-01", "PLAN-t-02", "PLAN-t-03"], calls)

    def test_a_failing_step_retries_then_stops_at_the_cap(self):
        target = board_with("gameplay_engineer")
        attempts = {"n": 0}

        def execute(task, role):
            attempts["n"] += 1
            # A different error each time, so the repeated-hash rule does not
            # fire and the retry cap is what is being measured.
            return chain.StepResult(ok=False,
                                    summary=f"STATUS: FAILED\nline {attempts['n']}x",
                                    error=f"broke in way {attempts['n']}")

        result = chain.run_chain(target, "PLAN-t-", execute, roles=ROLES,
                                 max_retry=3, stop_on_repeated_error=False)
        self.assertFalse(result.finished)
        self.assertEqual(4, attempts["n"])
        self.assertEqual(BLOCKED, target.tasks[0].status)

    def test_the_same_failure_twice_stops_without_burning_the_budget(self):
        target = board_with("gameplay_engineer")
        attempts = {"n": 0}

        def execute(task, role):
            attempts["n"] += 1
            return chain.StepResult(ok=False, summary="STATUS: FAILED",
                                    error="the identical error")

        result = chain.run_chain(target, "PLAN-t-", execute, roles=ROLES,
                                 max_retry=5)
        self.assertFalse(result.finished)
        self.assertEqual(2, attempts["n"])
        self.assertIn("반복", result.stopped_because)

    def test_a_handoff_moves_the_work_but_not_the_allowlist(self):
        target = board_with("gameplay_engineer")
        before = list(target.tasks[0].files)
        seen: list[str] = []

        def execute(task, role):
            seen.append(role)
            if len(seen) == 1:
                return chain.StepResult(
                    ok=False,
                    summary="STATUS: FAILED\nHANDOFF_TO: art_director\n"
                            "HANDOFF_REASON: 스프라이트가 없다",
                    error="no sprite")
            return ok()

        result = chain.run_chain(target, "PLAN-t-", execute, roles=ROLES)
        self.assertTrue(result.finished)
        self.assertEqual(["gameplay_engineer", "art_director"], seen)
        self.assertEqual("art_director", target.tasks[0].agent_role)
        self.assertEqual("gameplay_engineer", target.tasks[0].handoff_from)
        self.assertEqual(before, target.tasks[0].files)

    def test_a_limit_stops_the_chain_immediately(self):
        target = board_with("game_director", "gameplay_engineer")
        calls: list[str] = []

        def execute(task, role):
            calls.append(task.id)
            return chain.StepResult(ok=False, limited=True, error="limit")

        result = chain.run_chain(target, "PLAN-t-", execute, roles=ROLES)
        self.assertFalse(result.finished)
        self.assertEqual(["PLAN-t-01"], calls)
        self.assertIn("한도", result.stopped_because)

    def test_finished_steps_are_skipped_on_a_resumed_chain(self):
        target = board_with("game_director", "gameplay_engineer")
        target.tasks[0].status = REVIEW
        calls: list[str] = []

        def execute(task, role):
            calls.append(task.id)
            return ok()

        chain.run_chain(target, "PLAN-t-", execute, roles=ROLES)
        self.assertEqual(["PLAN-t-02"], calls)

    def test_every_step_is_recorded_with_what_was_decided(self):
        target = board_with("game_director")
        result = chain.run_chain(target, "PLAN-t-", lambda t, r: ok(), roles=ROLES)
        self.assertEqual(1, result.ran)
        self.assertEqual(chain.CONTINUE, result.records[0].action)
        self.assertEqual("game_director", result.records[0].role)

    def test_an_empty_prefix_is_reported_not_treated_as_done(self):
        result = chain.run_chain(board_with("game_director"), "NOPE-",
                                 lambda t, r: ok(), roles=ROLES)
        self.assertFalse(result.finished)
        self.assertIn("없습니다", result.stopped_because)

    def test_the_chain_runs_nothing_itself(self):
        source = Path(chain.__file__).read_text(encoding="utf-8")
        for forbidden in ("import subprocess", "Popen(", "codex.implement(",
                          "git commit", "git push"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
