"""Tests for the two chain steps that Unity runs rather than an agent writes.

Run:  python3 AI_GAME_COMPANY/tests/test_executors.py

Unity cannot run here, so these use the seam UnityRunner already has: the
PowerShell call is injected, and the fake does what the real step would have
done - writes an APK, a build report, an NUnit XML, or deliberately does not.
Everything between that and the chain's StepResult is real code.

The cases worth the most are the ones where a lesser answer looks fine:

  * a build that exits 0, writes a report, and leaves LAST WEEK's APK on disk.
    policy.assert_build_verification passes it - all three of its conditions
    hold - and release_gate is the only thing that catches it.
  * a test suite that never produced an NUnit XML. NOT_VERIFIED, not PASS.
  * Codex's own "STATUS: OK" sitting in the same summary as a failing gate.
    read_verdict takes the first STATUS it finds, so which one is first
    decides whether a gate can be overruled by the agent it is checking.
"""

from __future__ import annotations

import itertools
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from company.orchestrator.agent_registry import AgentRegistry  # noqa: E402
from company.orchestrator.chain_runner import StepResult, read_verdict  # noqa: E402
from company.orchestrator.executors import (  # noqa: E402
    BuildExecutor, TestExecutor, findings_from, game_id_of, quote_foreign,
    step_kind,
)
from company.orchestrator.manager import CompanyManager  # noqa: E402
from company.orchestrator.policy import Policy  # noqa: E402
from company.orchestrator.teamwork import Task  # noqa: E402
from company.orchestrator.unity_runner import (  # noqa: E402
    ENTRY_BUILD, ENTRY_GENERATE, ENTRY_VALIDATE, UnityResult, UnityRunner,
)

ROOT = Path(__file__).resolve().parents[1]
REAL_POLICY = ROOT / "config" / "company_policy.json"

APK = "Builds/game02/game02.apk"


def nunit_xml(path: Path, passed: int, failed: int, skipped: int = 0,
              failures: tuple[tuple[str, str], ...] = ()) -> None:
    """The shape unity_runner._apply_test_results parses."""
    cases = "".join(
        f'<test-case fullname="{name}" result="Failed">'
        f'<failure><message>{message}</message></failure></test-case>'
        for name, message in failures)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<test-run passed="{passed}" failed="{failed}" skipped="{skipped}">'
        f'{cases}</test-run>', encoding="utf-8")


class FakeUnity:
    """Stands in for wait-for-unity.ps1 at the seam UnityRunner already has.

    Each call receives the generated script, which names the step, so the fake
    can do what that step would have done. `writes` says which side effects
    happen; leaving one out is how the missing-APK and missing-report cases are
    built, and those are the cases that matter.
    """

    def __init__(self, repo_root: Path, *, exit_codes: dict[str, int] | None = None,
                 writes: tuple[str, ...] = ("report", "apk"),
                 results: dict[str, tuple] | None = None):
        self.repo_root = Path(repo_root)
        self.exit_codes = exit_codes or {}
        self.writes = writes
        self.results = results or {}
        self.calls: list[str] = []

    @staticmethod
    def step_of(script_text: str) -> str:
        if ENTRY_GENERATE in script_text:
            return "generate"
        if ENTRY_VALIDATE in script_text:
            return "validate"
        if ENTRY_BUILD in script_text:
            return "build"
        if "'PlayMode'" in script_text:
            return "test-playmode"
        if "'EditMode'" in script_text:
            return "test-editmode"
        return "unknown"

    def __call__(self, script_path: Path, script_text: str):
        step = self.step_of(script_text)
        self.calls.append(step)
        code = self.exit_codes.get(step, 0)

        if step == "build" and code == 0:
            if "report" in self.writes:
                report = self.repo_root / "Logs" / "unity-build.log"
                report.parent.mkdir(parents=True, exist_ok=True)
                report.write_text("Build succeeded\n", encoding="utf-8")
            if "apk" in self.writes:
                apk = self.repo_root / APK
                apk.parent.mkdir(parents=True, exist_ok=True)
                apk.write_bytes(b"PK\x03\x04 not really an apk")
        if step.startswith("test-") and step in self.results:
            passed, failed, skipped, failures = self.results[step]
            nunit_xml(self.repo_root / "Logs" / f"unity-{step}.xml",
                      passed, failed, skipped, failures)
        return code, "", ""


class ExecutorTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.policy = Policy.load(REAL_POLICY)
        self.counter = itertools.count()

    def unity(self, root: Path | None = None, **kwargs) -> tuple[UnityRunner, FakeUnity]:
        root = root or self.root
        fake = FakeUnity(root, **kwargs)
        runner = UnityRunner(repo_root=root, policy=self.policy,
                             unity_path="C:/fake/Unity.exe", runner=fake)
        return runner, fake

    def fresh(self) -> Path:
        """A tree no earlier build in this test has written an APK into."""
        root = self.root / f"tree{next(self.counter)}"
        root.mkdir()
        return root

    @staticmethod
    def release_task(task_id: str = "GAME02-RELEASE") -> Task:
        return Task(id=task_id, title="release", task_type="build",
                    agent_role="release_engineer")

    @staticmethod
    def qa_task(task_id: str = "GAME02-QA") -> Task:
        return Task(id=task_id, title="qa", task_type="qa",
                    agent_role="qa_engineer")


class BuildExecutorTests(ExecutorTestCase):
    def build(self, root: Path | None = None, **kwargs):
        root = root or self.root
        runner, fake = self.unity(root, **kwargs)
        executor = BuildExecutor(repo_root=root, policy=self.policy, runner=runner)
        return executor(self.release_task(), "release_engineer"), fake

    def test_a_real_build_reports_the_artifact_it_produced(self):
        step, fake = self.build()
        self.assertTrue(step.ok, step.error)
        self.assertIn("GATE: RELEASE_CANDIDATE", step.summary)
        self.assertIn("SHA256: ", step.summary)
        self.assertIn("SIZE: ", step.summary)
        self.assertIn(f"APK: {APK}", step.summary)
        # The artifact is the step's change. Without it decide() sees a step
        # that claims success while the tree did not move, and retries a
        # forty-minute Gradle build that worked.
        self.assertEqual([APK], step.changed)
        self.assertEqual(["generate", "validate", "build"], fake.calls)

    def test_no_apk_is_artifact_missing_and_not_a_finished_step(self):
        step, _ = self.build(writes=("report",))
        self.assertFalse(step.ok)
        self.assertIn("STATUS: FAILED", step.summary)
        self.assertIn("GATE: ARTIFACT_MISSING", step.summary)
        self.assertEqual([], step.changed)

    def test_last_weeks_apk_does_not_count_as_this_build(self):
        """The case policy cannot catch: all three conditions hold, and lie.

        exit code 0, a build report on disk, an APK on disk - the three things
        assert_build_verification checks - and the APK is from a previous run
        because this build never wrote one. release_gate's current_run is the
        only thing between that and a chain reporting a release.
        """
        stale = self.root / APK
        stale.parent.mkdir(parents=True, exist_ok=True)
        stale.write_bytes(b"yesterday")
        old = time.time() - 7 * 24 * 3600
        os.utime(stale, (old, old))

        step, _ = self.build(writes=("report",))
        self.assertFalse(step.ok)
        self.assertIn("GATE: ARTIFACT_MISSING", step.summary)
        self.assertIn("current run", step.summary)

    def test_a_failed_build_process_is_not_a_compile_failure(self):
        step, fake = self.build(exit_codes={"build": 1})
        self.assertFalse(step.ok)
        self.assertIn("GATE: UNITY_BUILD_FAILURE", step.summary)
        self.assertIn("DETAIL: build exit=1", step.summary)

    def test_a_missing_build_report_is_a_build_failure(self):
        step, _ = self.build(writes=("apk",))
        self.assertFalse(step.ok)
        self.assertIn("GATE: UNITY_BUILD_FAILURE", step.summary)

    def test_a_failure_before_the_build_is_a_compile_failure(self):
        step, fake = self.build(exit_codes={"validate": 1})
        self.assertFalse(step.ok)
        self.assertIn("GATE: COMPILE_FAILURE", step.summary)
        # And the build never started: there was nothing to build.
        self.assertEqual(["generate", "validate"], fake.calls)

    def test_no_unity_path_refuses_without_inventing_a_category(self):
        executor = BuildExecutor(repo_root=self.root, policy=self.policy,
                                 unity_path=None)
        step = executor(self.release_task(), "release_engineer")
        self.assertFalse(step.ok)
        self.assertIn("STATUS: FAILED", step.summary)
        # No evidence to judge is a different thing from evidence that failed,
        # so no gate category is claimed.
        self.assertNotIn("GATE:", step.summary)
        self.assertIn("detect-environment", step.error)

    def test_an_id_that_names_no_game_refuses_rather_than_guessing(self):
        runner, fake = self.unity()
        executor = BuildExecutor(repo_root=self.root, policy=self.policy,
                                 runner=runner)
        step = executor(self.release_task("CODEX-BUILDGATE1"), "release_engineer")
        self.assertFalse(step.ok)
        self.assertEqual([], fake.calls)

    def test_the_chain_can_read_both_verdicts(self):
        # Separate trees: a second build in the same one would find the first
        # build's APK, which is the stale-artifact case rather than this one.
        good, _ = self.build(self.fresh())
        bad, _ = self.build(self.fresh(), writes=("report",))
        self.assertIs(True, read_verdict(good.summary).succeeded)
        self.assertIs(False, read_verdict(bad.summary).succeeded)
        # A gate is a measurement, not a colleague asking for help: decide()
        # reroutes the work when it reads a HANDOFF_TO, and a failing build
        # should be retried or stopped, not handed to somebody.
        self.assertEqual("", read_verdict(bad.summary).handoff_to)


def wrote(summary: str = "STATUS: OK\nHANDOFF_TO: release_engineer",
          changed=("Assets/GameFactory/Tests/PlayMode/Game02Tests.cs",),
          ok: bool = True, limited: bool = False) -> StepResult:
    """A finished write phase, as CodexExecutor would report one."""
    def phase(task, role):
        return StepResult(ok=ok, summary=summary, changed=list(changed),
                          limited=limited,
                          error="" if ok else "codex failed")
    return phase


class TestExecutorTests(ExecutorTestCase):
    PASSING = {"test-editmode": (12, 0, 0, ()),
               "test-playmode": (7, 0, 1, ())}

    def qa(self, *, write_phase=None, **kwargs):
        runner, fake = self.unity(**kwargs)
        executor = TestExecutor(repo_root=self.root, policy=self.policy,
                                write_phase=write_phase or wrote(), runner=runner)
        return executor(self.qa_task(), "qa_engineer"), fake

    def test_written_tests_that_pass_pass(self):
        step, fake = self.qa(results=self.PASSING)
        self.assertTrue(step.ok, step.error)
        self.assertIn("GATE: PASS", step.summary)
        self.assertIn("SUITE: test-playmode passed=7 failed=0 skipped=1",
                      step.summary)
        self.assertEqual(["Assets/GameFactory/Tests/PlayMode/Game02Tests.cs"],
                         step.changed)
        self.assertEqual(["generate", "test-editmode", "test-playmode"], fake.calls)

    def test_a_suite_that_produced_nothing_is_not_verified(self):
        """No NUnit XML means nobody checked, which is not the same as PASS."""
        step, _ = self.qa()  # results={} - the fake writes no XML
        self.assertFalse(step.ok)
        self.assertIn("GATE: NOT_VERIFIED", step.summary)

    def test_failing_test_names_reach_the_summary(self):
        failing = dict(self.PASSING)
        failing["test-playmode"] = (6, 1, 0, (
            ("GameFactory.Tests.Game02.CoinPickup", "expected 10 but was 0"),))
        step, _ = self.qa(results=failing)
        self.assertFalse(step.ok)
        self.assertIn("GATE: BLOCKED", step.summary)
        self.assertIn("GameFactory.Tests.Game02.CoinPickup", step.summary)

    def test_codex_saying_ok_cannot_overrule_a_failing_gate(self):
        """The write phase's report is kept, and cannot be read as a verdict.

        read_verdict takes the FIRST STATUS in a summary. Codex's report ends
        in its own STATUS: OK and often a HANDOFF_TO, so pasting it under a
        gate verdict would hand routing straight back to the agent whose claim
        the gate exists to check.
        """
        step, _ = self.qa()  # no XML -> NOT_VERIFIED, while Codex said OK
        verdict = read_verdict(step.summary)
        self.assertIs(False, verdict.succeeded)
        self.assertEqual("", verdict.handoff_to)
        # Kept, just defanged - somebody reading the board still sees it.
        self.assertIn("[STATUS]: OK", step.summary)

    def test_a_codex_limit_pauses_instead_of_running_the_old_suite(self):
        step, fake = self.qa(write_phase=wrote(ok=False, limited=True))
        self.assertTrue(step.limited)
        self.assertEqual([], fake.calls)

    def test_tests_that_were_not_written_are_not_run(self):
        step, fake = self.qa(write_phase=wrote(ok=False, summary="STATUS: FAILED"))
        self.assertFalse(step.ok)
        self.assertEqual([], fake.calls)
        self.assertIs(False, read_verdict(step.summary).succeeded)

    def test_a_scene_that_will_not_generate_blocks_before_any_suite(self):
        step, fake = self.qa(exit_codes={"generate": 1})
        self.assertFalse(step.ok)
        self.assertIn("GATE: BLOCKED", step.summary)
        self.assertEqual(["generate"], fake.calls)

    def test_the_written_tests_are_still_reported_when_unity_is_missing(self):
        executor = TestExecutor(repo_root=self.root, policy=self.policy,
                                write_phase=wrote(), unity_path=None)
        step = executor(self.qa_task(), "qa_engineer")
        self.assertFalse(step.ok)
        # The work happened even though it could not be verified; reporting no
        # changes would make decide() treat a written suite as a dead step.
        self.assertEqual(["Assets/GameFactory/Tests/PlayMode/Game02Tests.cs"],
                         step.changed)


class FindingsTests(unittest.TestCase):
    def test_counted_failures_with_no_names_are_still_failures(self):
        result = UnityResult(step="test-playmode", exit_code=1, ok=False,
                             passed=3, failed=2, skipped=0, failures=[])
        findings = list(findings_from([result]))
        self.assertEqual(["FAIL"], [f.result for f in findings])
        self.assertIn("2건 실패", findings[0].summary)

    def test_passing_every_test_and_still_exiting_nonzero_is_a_blocker(self):
        result = UnityResult(step="test-editmode", exit_code=3, ok=False,
                             passed=5, failed=0, skipped=0, failures=[])
        findings = list(findings_from([result]))
        self.assertEqual([("FAIL", "BLOCKER")],
                         [(f.result, f.severity) for f in findings])

    def test_no_suite_at_all_is_not_verified(self):
        self.assertEqual(["NOT_VERIFIED"],
                         [f.result for f in findings_from([])])

    def test_the_scene_step_is_not_mistaken_for_an_unverified_suite(self):
        generate = UnityResult(step="generate", exit_code=0, ok=True)
        suite = UnityResult(step="test-playmode", exit_code=0, ok=True,
                            passed=4, failed=0, skipped=0)
        self.assertEqual([], list(findings_from([generate, suite])))


class RoutingTests(unittest.TestCase):
    """The planner's task_type strings and the chain's routing must agree.

    Renaming a task_type in manager.py without touching step_kind would leave
    the release step going to an agent again, silently, and the chain would go
    back to finishing on a document about a build. This reads the real plan.
    """

    def setUp(self):
        self.plan = CompanyManager(
            AgentRegistry.load(ROOT / "config" / "AGENTS.json")
        ).plan("game02", "Game02 만들어.")

    def kind_of(self, suffix: str) -> str:
        task = next(t for t in self.plan.tasks if t.id.endswith(suffix))
        return step_kind(task)

    def test_the_release_step_runs_unity(self):
        self.assertEqual("build", self.kind_of("-RELEASE"))

    def test_the_qa_step_runs_the_tests(self):
        self.assertEqual("test", self.kind_of("-QA"))

    def test_the_five_authoring_steps_still_go_to_an_agent(self):
        for suffix in ("-DESIGN", "-ARCH", "-SYSTEMS", "-UI", "-REVIEW"):
            self.assertEqual("", self.kind_of(suffix), suffix)

    def test_a_game_id_is_read_from_the_plan_id(self):
        self.assertEqual("game02", game_id_of(self.plan.tasks[-1]))
        self.assertEqual("", game_id_of(Task(id="CODEX-BUILDGATE1", title="x")))


class QuoteForeignTests(unittest.TestCase):
    def test_every_decoration_read_verdict_accepts_is_defanged(self):
        for line in ("STATUS: OK", "- STATUS: OK", "**STATUS:** OK",
                     "  > STATUS = OK", "# STATUS: OK"):
            with self.subTest(line=line):
                self.assertIsNone(read_verdict(quote_foreign(line)).succeeded)

    def test_ordinary_prose_survives(self):
        text = "빌드는 통과했지만 코인 스프라이트가 머리보다 큽니다."
        self.assertEqual(text, quote_foreign(text))


if __name__ == "__main__":
    unittest.main(verbosity=2)
