"""Who actually performs a chain step, and what happens when Codex runs out.

chain_runner decides what to do next; this decides who does it. The split
matters because the fallback question is a policy question, not a routing one.

WHAT OLLAMA CAN AND CANNOT REPLACE. company_policy's on_codex_limit names its
fallback `local_ai_review` - review, not authorship - and the hardware agrees:
Qwen3-VL:4b on this machine has no dedicated GPU and answers a short prompt in
about ten seconds. Handing it the C# that Codex writes would be slow and
worse. But seven of the twelve roles do not write code at all; AGENTS.json
gives them can_modify_code false and their deliverable is a document. Those
are exactly the steps a local model can carry.

So a Codex limit does not stop the chain dead. It stops the five WRITING
steps and lets the seven design, review and reporting steps continue, which
is the difference between a plan that is half done and a plan that is stuck at
step one. The five that need Codex are reported as needing Codex, not quietly
downgraded.

AND TWO STEPS ARE NOT AN AGENT'S JOB AT ALL. GAMENN-QA and GAMENN-RELEASE ask
whether the thing works, which is a question about evidence rather than about
authorship. BuildExecutor and TestExecutor run Unity and hand what comes back
to quality_gates, which already held both decisions and which, until these
were written, nothing in the tree called.

NOTHING HERE PRETENDS. If the local model produces no readable STATUS, the
executor writes NEEDS_HUMAN_REVIEW and says in the document that the verdict
came from this file rather than from the model. chain_runner then routes on a
truthful "nobody could tell" instead of an invented success.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from company.orchestrator.agent_dispatcher import AgentDispatcher
from company.orchestrator.chain_runner import StepResult, read_verdict
from company.orchestrator.manager import GAME_ID
from company.orchestrator.quality_gates import (
    QUALITY_WEIGHTS, Finding, GateValidationError, ReleaseEvidence, qa_gate,
    quality_gate, release_gate)
from company.orchestrator.unity_runner import BUILD_REPORT, UnityRunner

#: Where a document-writing role leaves its output. Inside docs/, which is
#: what ROLE_FILES gives those roles, so the allowlist still holds.
DOC_DIR = "docs/agent"

#: Local generation needs room to think before it answers - Qwen3-VL is a
#: thinking model and its reasoning is billed against this budget. 64 tokens
#: produced an empty `response` and done_reason "length", which looks exactly
#: like a broken model and is not one.
LOCAL_NUM_PREDICT = 6000
LOCAL_TIMEOUT = 600.0


class ExecutorUnavailable(RuntimeError):
    """This executor cannot run at all. Distinct from a step that failed."""


@dataclass
class CodexExecutor:
    """Runs a step as Codex does today, through teamwork.run_task.

    A thin wrapper on purpose: every guard that path already has - the
    allowlist diff check, REVIEW instead of DONE, no commit - stays where it
    is rather than being reimplemented next to the chain.
    """
    board: Any
    codex: Any
    repo_root: Path
    registry: Any = None
    timeout_seconds: int | None = None

    def __call__(self, task: Any, role: str) -> StepResult:
        from company.orchestrator.agent_dispatcher import (
            AgentDispatchError, WritingAgentBusy)
        from company.orchestrator.codex_runner import CodexLimited
        from company.orchestrator.teamwork import run_task

        dispatcher = AgentDispatcher(self.registry, self.repo_root)
        try:
            # writing_slot is a real mkdir mutex on the shared tree. The chain
            # is serial by construction, but that only holds for steps THIS
            # chain runs - somebody typing `team run` beside it is exactly the
            # collision the lock exists for, and a property nobody enforces is
            # not a property.
            with dispatcher.writing_slot(task):
                run = run_task(self.board, task.id, self.codex, self.repo_root,
                               timeout_seconds=self.timeout_seconds)
        except WritingAgentBusy as exc:
            # Not a failure of the work: somebody else holds the tree. Pause
            # like a limit does, so the chain resumes when they are done.
            return StepResult(ok=False, limited=True, error=str(exc),
                              summary="STATUS: BLOCKED")
        except AgentDispatchError as exc:
            return StepResult(ok=False, error=str(exc))
        except CodexLimited as exc:
            return StepResult(ok=False, limited=True, error=str(exc),
                              summary="STATUS: BLOCKED")
        except Exception as exc:  # noqa: BLE001 - a step failing is data
            return StepResult(ok=False, error=f"{type(exc).__name__}: {exc}")

        return StepResult(ok=run.ok, summary=run.summary,
                          changed=list(run.changed),
                          error="" if run.ok else "; ".join(run.outside_allowlist))


@dataclass
class OllamaExecutor:
    """Runs a document step on the local model, writing the result to docs/.

    The model produces text; this writes that text to a file, because a step
    that changes nothing is not progress and chain_runner treats it that way.
    The file lives under the role's own allowlist, so nothing here widens what
    a role may touch.
    """
    repo_root: Path
    registry: Any
    model: str
    url: str = "http://127.0.0.1:11434"
    doc_dir: str = DOC_DIR

    def _ask(self, prompt: str) -> tuple[str, str]:
        """(text, done_reason). The reason matters as much as the text."""
        payload = {
            "model": self.model,
            "prompt": prompt,
            "think": False,
            "stream": False,
            "options": {"num_predict": LOCAL_NUM_PREDICT},
        }
        request = urllib.request.Request(
            f"{self.url}/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=LOCAL_TIMEOUT) as response:
                body = json.loads(response.read())
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise ExecutorUnavailable(f"로컬 모델을 부르지 못했습니다: {exc}") from exc
        return (str(body.get("response") or "").strip(),
                str(body.get("done_reason") or ""))

    def __call__(self, task: Any, role: str) -> StepResult:
        try:
            agent = AgentDispatcher(self.registry, self.repo_root).resolve(task)
        except Exception as exc:  # noqa: BLE001
            return StepResult(ok=False, error=f"역할을 정할 수 없습니다: {exc}")

        prompt = "\n\n".join([
            "CURRENT COMPANY ROLE",
            f"Agent: {agent.display_name}",
            f"Agent ID: {agent.id}",
            f"Department: {agent.department}",
            "",
            agent.prompt.strip(),
            "TASK",
            f"  {task.goal}",
            "",
            "출력은 한국어 마크다운 문서 하나. 마지막에 반드시 이 세 줄을 포함할 것:",
            "STATUS: OK 또는 FAILED",
            "HANDOFF_TO: 다음 담당 역할 id",
            "HANDOFF_REASON: 한 줄 이유",
        ])

        try:
            answer, done_reason = self._ask(prompt)
        except ExecutorUnavailable as exc:
            return StepResult(ok=False, error=str(exc))

        if not answer:
            return StepResult(ok=False, error="로컬 모델이 빈 응답을 냈습니다.")

        # done_reason "length" means the model was cut off mid-answer. The
        # first version of this shipped a 676-byte design document that ended
        # mid-word and still carried STATUS: OK, because the model wrote its
        # status early and ran out before the rest. Reporting that as success
        # would advance the chain on a truncated design - the same shape as
        # believing an exit code over the working tree.
        truncated = done_reason == "length"

        verdict = read_verdict(answer)
        note = ""
        if truncated:
            note = ("\n\n---\n"
                    "STATUS: FAILED\n"
                    f"HANDOFF_REASON: 로컬 모델 출력이 {LOCAL_NUM_PREDICT} 토큰에서 "
                    "잘렸습니다. 이 문서는 완결되지 않았습니다 (done_reason=length).\n")
        elif verdict.succeeded is None:
            # Said so, rather than inventing a verdict. chain_runner will
            # route on "could not tell", which is the truth.
            note = ("\n\n---\n"
                    f"STATUS: NEEDS_HUMAN_REVIEW\n"
                    f"HANDOFF_REASON: 로컬 모델({self.model})이 STATUS 를 내지 않아 "
                    "이 판정은 모델이 아니라 executors.py 가 붙인 것입니다.\n")

        target = self.repo_root / self.doc_dir / f"{task.id}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        header = (f"# {task.id} — {task.title_ko or task.title}\n\n"
                  f"역할: {agent.display_name} ({agent.id})  \n"
                  f"작성: 로컬 모델 {self.model} (Codex 한도로 인한 폴백)\n\n---\n\n")
        target.write_text(header + answer + note, encoding="utf-8")

        relative = f"{self.doc_dir}/{task.id}.md"
        # The file is written either way - a truncated draft is worth keeping
        # and reading - but the step is not reported as ok.
        return StepResult(ok=not truncated, summary=answer + note,
                          changed=[relative],
                          error="출력이 잘렸습니다." if truncated else "")


def writing_roles(registry: Any) -> set[str]:
    """Roles that AGENTS.json says may modify code."""
    return {a.id for a in registry.list_agents() if a.can_modify_code}


def pick_executor(codex_executor: Callable[..., StepResult],
                  local_executor: Callable[..., StepResult] | None,
                  registry: Any, *, codex_limited: bool = False,
                  ) -> Callable[[Any, str], StepResult]:
    """Return the executor to use, honouring the Codex limit.

    Before the limit, everything goes to Codex - the local model is a
    fallback, not a cost saving, and routing design work away from the better
    agent while it is available would be a downgrade nobody asked for.

    After it, the seven document roles go local and the five writing roles
    report that they need Codex. They are not quietly downgraded: a C# step
    answered by a local model would look like progress and would not be.
    """
    writers = writing_roles(registry)

    def choose(task: Any, role: str) -> StepResult:
        if not codex_limited or role in writers:
            if codex_limited:
                return StepResult(
                    ok=False,
                    error=(f"{role} 은 코드를 쓰는 역할이라 Codex 가 필요한데 "
                           "구독 한도에 걸려 있습니다."),
                    summary="STATUS: BLOCKED\nHANDOFF_REASON: Codex 한도")
            return codex_executor(task, role)
        if local_executor is None:
            return StepResult(ok=False,
                              error="로컬 모델 폴백이 설정되지 않았습니다.")
        return local_executor(task, role)

    return choose


# ---------------------------------------------------------------------------
# THE TWO STEPS THAT ARE NOT AUTHORING JOBS.
#
# A plan has seven steps and five of them are somebody writing something.
# GAMENN-QA (task_type "qa") and GAMENN-RELEASE (task_type "build") are not:
# they are claims about whether the thing works, and a claim is only worth the
# evidence under it.
#
# Routed by role they went to an agent anyway. release_engineer has
# can_modify_code false, so once Codex hit its limit the LAST step of every
# chain was answered by a 4B local model writing a document about a build that
# never ran - and chain_runner believed that document's own STATUS: OK. A plan
# could reach "chain complete" with no APK anywhere on disk. CLAUDE.md section
# 8's first principle, broken by structure rather than by a bug.
#
# quality_gates.py already decided both questions and nothing in the tree
# called it. So what follows gathers evidence and translates a GateResult; it
# decides nothing. A second, differently worded copy of the rules is exactly
# the drift that once had the panel and the CLI disagreeing about RAM.
# ---------------------------------------------------------------------------

#: The keys chain_runner.read_verdict looks for, matched the way it matches
#: them, so quote_foreign can neutralise everything it would otherwise read.
_CONTRACT_KEY = re.compile(
    r"^(\s*(?:[-*#>\s]*))(STATUS|HANDOFF_TO|HANDOFF_REASON)(\s*[:=])",
    re.IGNORECASE | re.MULTILINE)


def quote_foreign(text: str) -> str:
    """Somebody else's report, with the contract keys defanged.

    read_verdict takes the FIRST STATUS in a summary. Pasting Codex's report -
    which ends in its own STATUS: OK - underneath a gate verdict would hand the
    routing decision straight back to the agent whose claim the gate exists to
    check, and a HANDOFF_TO buried in it would reroute a failing step to
    whoever that agent felt like naming. Bracketing the keys keeps the text
    readable by people and unreadable as a verdict.
    """
    return _CONTRACT_KEY.sub(
        lambda m: f"{m.group(1)}[{m.group(2)}]{m.group(3)}", text or "")


def gate_summary(gate: Any, *, detail: Iterable[str] = (), status: str = "",
                 reason: str = "") -> str:
    """A GateResult as the contract block chain_runner reads.

    No HANDOFF_TO line: decide() reroutes the work when it sees one, and a
    gate is not an agent asking for a colleague - it is a measurement.
    HANDOFF_REASON is different and safe: decide() reads it only for the
    message it shows, and that message is what the 계속 진행 button says.

    `status` overrides the OK/FAILED reading for a result that is neither.
    Per executor on purpose - the QA step's NOT_VERIFIED means a suite did not
    run, a machine fault worth retrying, while the review step's means nobody
    looked at the screen, which only a person can settle.
    """
    lines = [f"STATUS: {status or ('OK' if gate.passed else 'FAILED')}",
             f"GATE: {gate.status}"]
    if reason:
        lines.append(f"HANDOFF_REASON: {reason}")
    lines += [f"REASON: {item}" for item in gate.reasons]
    lines += [line for line in detail if line]
    return "\n".join(lines)


#: task_type values the planner emits for the three steps a gate decides,
#: mapped to which executor they need. A dict rather than an `if` because the
#: planner and the chain have to agree on these strings and a test can read the
#: same table both of them use - renaming a task_type in manager.py without
#: touching this is how a wired gate quietly comes loose again.
GATED_STEPS = {"build": "build", "qa": "test", "test": "test",
               "code_review": "review"}


def step_kind(task: Any) -> str:
    """Which executor a step needs: "build", "test", "review", or "".

    Asked of the WORK, not of the job title. Role does not distinguish these:
    the release step belongs to release_engineer, whose can_modify_code is
    false, so routing by role sent it to the document fallback and the chain
    finished on a document about a build that never ran.
    """
    return GATED_STEPS.get((getattr(task, "task_type", "") or "").strip().lower(), "")


def game_id_of(task: Any) -> str:
    """The game a plan step belongs to, read from its id.

    Plans are GAMENN-STEP, so the id states it. Read from the id rather than
    from the goal text because the goal is a sentence somebody wrote and the
    id is a fact. Returns "" for anything else, and the callers refuse rather
    than guess which game to build.
    """
    head = str(getattr(task, "id", "") or "").split("-")[0].lower()
    return head if GAME_ID.fullmatch(head) else ""


def _sha256_of(path: Path) -> str:
    """Short digest of an artifact, so the claim stays checkable afterwards."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()[:16].upper()


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _no_unity() -> StepResult:
    """No editor path: refuse, and do not dress it up as a gate category.

    wait-for-unity.ps1 launches $env:UNITY_PATH. Without one it starts nothing
    and returns quickly, which looks like a fast build. There is no evidence
    to judge here, which is a different thing from evidence that failed, so no
    GATE line is emitted.
    """
    return StepResult(
        ok=False, summary="STATUS: FAILED",
        error=("Unity 편집기 경로를 모릅니다. "
               "AI_GAME_COMPANY/tools/detect-environment.ps1 을 먼저 실행하거나 "
               "--unity-path 를 넘기세요."))


@dataclass
class BuildExecutor:
    """GAMENN-RELEASE: run the real pipeline, let release_gate judge it.

    Runs what `orchestrator build --game` runs, fills a ReleaseEvidence from
    what came back, and reports whatever release_gate says. Every pass/fail
    decision belongs to the gate; this is evidence gathering and translation.

    Nothing here checks an allowlist, unlike CodexExecutor: a build is not an
    agent editing files. It writes Logs/, Library/ and Builds/ as Unity always
    has, and none of that is a diff for anybody to review.
    """
    repo_root: Path
    policy: Any
    unity_path: str | None = None
    #: A ready UnityRunner. Tests inject one; real runs build their own.
    runner: Any = None

    def _unity(self) -> Any:
        if self.runner is not None:
            return self.runner
        return UnityRunner(repo_root=self.repo_root, policy=self.policy,
                           unity_path=self.unity_path)

    def _evidence(self, results: list[Any], apk: Path | None,
                  started: float) -> ReleaseEvidence:
        """What happened, in the four terms release_gate asks about.

        exit_code, not ok: run_pipeline sets the build step's `ok` to False
        when verify_build refuses a missing APK, so reading `ok` here would
        file a build that compiled and linked under COMPILE_FAILURE and hide
        the real category, which is ARTIFACT_MISSING.
        """
        build = next((r for r in results if r.step == "build"), None)
        before = [r for r in results if r.step != "build"]
        report = self.repo_root / BUILD_REPORT
        return ReleaseEvidence(
            # generate and validate are what runs before the compiler, so
            # their failures are the ones release_gate files as COMPILE_FAILURE.
            errors=tuple(f"{r.step}: {r.detail or 'failed'}"
                         for r in before if not r.ok),
            process_ok=build is not None and build.exit_code == 0,
            unity_report_ok=report.is_file() and report.stat().st_size > 0,
            apk_path=apk,
            # An APK from last week is not evidence that this build worked.
            # release_gate already has a field for it; it only needed telling.
            current_run=bool(apk and apk.is_file()
                             and apk.stat().st_mtime >= started - 1),
        )

    def __call__(self, task: Any, role: str) -> StepResult:
        game = game_id_of(task)
        if not game:
            return StepResult(
                ok=False, summary="STATUS: FAILED",
                error=f"'{task.id}' 이 GAMENN-단계 형식이 아니라 "
                      "어느 게임을 빌드할지 알 수 없습니다.")
        if self.runner is None and not self.unity_path:
            return _no_unity()

        started = time.time()
        try:
            results, apk = self._unity().run_pipeline(game)
        except Exception as exc:  # noqa: BLE001 - a failed build is data
            return StepResult(ok=False, summary="STATUS: FAILED",
                              error=f"{type(exc).__name__}: {exc}")

        gate = release_gate(self._evidence(results, apk, started))
        detail = [f"DETAIL: {r.step} exit={r.exit_code} {r.detail}".rstrip()
                  for r in results if not r.ok]
        if gate.passed and apk is not None:
            detail += [f"APK: {_relative(apk, self.repo_root)}",
                       f"SIZE: {apk.stat().st_size / (1024 * 1024):.2f} MB",
                       f"SHA256: {_sha256_of(apk)}"]
        return StepResult(
            ok=gate.passed,
            summary=gate_summary(gate, detail=detail),
            # The artifact IS this step's change. Leave it out and decide()
            # sees "said it succeeded, tree did not move" and retries a build
            # that worked - forty minutes of Gradle for nothing.
            changed=([_relative(apk, self.repo_root)]
                     if gate.passed and apk is not None else []),
            error="" if gate.passed else "; ".join(gate.reasons))


def findings_from(results: Iterable[Any]) -> Iterator[Finding]:
    """UnityResult objects as qa_gate findings.

    A suite with no counts is NOT_VERIFIED, never PASS. That is the whole
    reason qa_gate has three results instead of two: "nobody checked" is a
    real answer, and folding it into either of the others throws away the only
    honest one.
    """
    suites = 0
    for result in results:
        if not str(result.step).startswith("test-"):
            # run_tests generates the scene first. Not a suite, so it cannot
            # be NOT_VERIFIED: it either worked or it ended the run before any
            # test existed to be unverified.
            if not result.ok:
                yield Finding("FAIL", "BLOCKER",
                              f"{result.step}: {result.detail or 'failed'}")
            continue
        suites += 1
        if result.passed is None and result.failed is None:
            # unity_runner already refuses to read an exit code as a pass when
            # the NUnit XML is missing or unparsable. This is that case.
            yield Finding("NOT_VERIFIED", "MAJOR",
                          f"{result.step}: {result.detail or 'NUnit 결과가 없습니다.'}")
            continue
        for name, message in result.failures or ():
            # Name first: it survives the truncation, and the name is what
            # somebody needs to open the test.
            yield Finding("FAIL", "CRITICAL", f"{name}: {message}"[:300])
        if result.failed and not result.failures:
            yield Finding("FAIL", "CRITICAL",
                          f"{result.step}: {result.failed}건 실패, 이름을 읽지 못했습니다.")
        if not result.ok and not result.failed:
            # Every test it ran passed and it still exited non-zero.
            yield Finding("FAIL", "BLOCKER",
                          f"{result.step}: {result.detail or 'run failed'}")
    if not suites:
        yield Finding("NOT_VERIFIED", "MAJOR", "실행된 테스트 스위트가 없습니다.")


@dataclass
class TestExecutor:
    """GAMENN-QA: Codex writes the tests, then the tests actually run.

    Two phases, because either alone is a claim rather than evidence. Test
    code nobody executed proves nothing, and a suite run without the new tests
    proves something about yesterday.
    """
    repo_root: Path
    policy: Any
    #: How the test code gets written - the chain's ordinary agent step, so a
    #: Codex limit, the writing mutex and the allowlist diff all still apply
    #: exactly as they do to every other authoring step.
    write_phase: Callable[[Any, str], StepResult]
    unity_path: str | None = None
    platform: str = "both"
    runner: Any = None

    def _unity(self) -> Any:
        if self.runner is not None:
            return self.runner
        return UnityRunner(repo_root=self.repo_root, policy=self.policy,
                           unity_path=self.unity_path)

    def __call__(self, task: Any, role: str) -> StepResult:
        written = self.write_phase(task, role)
        if written.limited:
            # Pause the chain exactly as any other writing step does. Running
            # the existing suite here would print a green result about tests
            # that were never written, which is the most misleading thing this
            # step could do.
            return written
        if not written.ok:
            # Tests that were not written cannot pass, so there is nothing to
            # run and nothing to gate.
            return StepResult(
                ok=False, changed=list(written.changed),
                summary=("STATUS: FAILED\n"
                         "REASON: 테스트를 작성하지 못해 실행하지 않았습니다.\n\n"
                         + quote_foreign(written.summary)),
                error=written.error or "테스트 작성 단계가 실패했습니다.")

        game = game_id_of(task)
        if not game:
            return StepResult(
                ok=False, changed=list(written.changed),
                summary="STATUS: FAILED",
                error=f"'{task.id}' 이 GAMENN-단계 형식이 아니라 "
                      "어느 게임을 테스트할지 알 수 없습니다.")
        if self.runner is None and not self.unity_path:
            refused = _no_unity()
            # The written tests are still real work; say so, or the chain
            # reports the step as having changed nothing.
            return StepResult(ok=False, summary=refused.summary,
                              changed=list(written.changed), error=refused.error)

        try:
            results = self._unity().run_tests(game, self.platform)
        except Exception as exc:  # noqa: BLE001
            return StepResult(ok=False, changed=list(written.changed),
                              summary="STATUS: FAILED",
                              error=f"{type(exc).__name__}: {exc}")

        gate = qa_gate(list(findings_from(results)))
        detail = [f"SUITE: {r.step} passed={r.passed} failed={r.failed} "
                  f"skipped={r.skipped}"
                  for r in results if str(r.step).startswith("test-")]
        return StepResult(
            ok=gate.passed, changed=list(written.changed),
            summary=(gate_summary(gate, detail=detail)
                     + "\n\n" + quote_foreign(written.summary)),
            error="" if gate.passed else "; ".join(gate.reasons))


# ---------------------------------------------------------------------------
# THE REVIEW STEP, AND THE ONE QUESTION A MODEL CANNOT ANSWER FROM CODE.
#
# quality_gate scores seven weighted dimensions against a hundred and refuses
# a graphics, ui_ux or animation_vfx score unless visually_inspected is true.
# That refusal is the best thing in quality_gates.py: it is a guard against an
# agent that never saw the screen inventing a number for how the game looks.
#
# Which makes the honest answer here uncomfortable. NOTHING IN THIS REPOSITORY
# TAKES A SCREENSHOT. Unity runs -batchmode -nographics, which renders nothing
# by definition, and grep finds no capture anywhere. So on a machine where
# nobody has looked, the correct result is NOT_VERIFIED - not a score, and not
# a pass.
#
# A step that always fails would be useless, so NOT_VERIFIED is routed to the
# 계속 진행 button rather than to a retry: "nobody looked at the screen" is a
# question only a person can answer, and asking Codex the same thing a second
# time produces the same silence. That is different from the QA step, where
# NOT_VERIFIED means a suite did not run - a machine fault worth retrying.
#
# And there IS a way to answer it today. Drop a screenshot at
# Reports/quality/<game>-screen.png - a photo of the phone running the APK is
# exactly the evidence the gate is asking for - and this shows it to the local
# vision model before the reviewer writes anything. visually_inspected then
# becomes true because something really did look, and the summary names the
# model and the file so the score can be doubted later.
# ---------------------------------------------------------------------------

#: Where the reviewer's scores go. The plan's allowlist for the review step is
#: exactly this one file.
QUALITY_REPORT = "Reports/quality/{game}.json"
#: What a person or a build drops in for the visual dimensions to be scorable.
SCREEN_IMAGE = "Reports/quality/{game}-screen"
SCREEN_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")
#: What the vision model said, kept as a file so the reviewer can cite it and
#: a person can check it against the picture.
INSPECTION_DOC = "Reports/quality/{game}-inspection.md"

#: A description, not an essay. Measured on this PC 2026-09-15: Qwen3-VL:4b
#: runs at 8-10 tokens/second on CPU, so this is about two minutes, and the
#: timeout leaves room for loading 3.3 GB of weights that may not be resident.
INSPECT_NUM_PREDICT = 1200
INSPECT_TIMEOUT = 900.0

#: Prefix for the note this executor leaves on the task, so the reviewer reads
#: it in its prompt. Replaced rather than appended on a retry.
INSPECTION_NOTE = "INSPECTION:"

VISION_PROMPT = "\n".join([
    "이 이미지는 모바일 게임 화면입니다.",
    "실제로 보이는 것만 적으세요. 보이지 않는 것은 추측하지 마세요.",
    "",
    "1. 화면에 무엇이 있는지 - 요소, 배치, 색",
    "2. 읽기 어렵거나, 겹치거나, 잘린 부분",
    "3. 세로 모바일 화면으로서 손가락으로 누르기 어려워 보이는 부분",
])

SCORE_SHAPE = (
    '{"gameplay": 0-20, "graphics": 0-20, "ui_ux": 0-15, "animation_vfx": 0-15, '
    '"audio": 0-10, "stability": 0-10, "mobile": 0-10}'
)


@dataclass(frozen=True)
class Inspection:
    """Whether something actually looked at the screen, and what it saw.

    `looked` is the whole point. It is set from what this code did, never from
    what an agent said it did - an agent claiming an inspection is exactly the
    thing quality_gate exists to refuse.
    """
    looked: bool
    model: str = ""
    image: str = ""
    text: str = ""
    detail: str = ""

    @property
    def line(self) -> str:
        if self.looked:
            return f"INSPECTION: {self.model} 가 {self.image} 를 봤습니다."
        return f"INSPECTION: 없음 - {self.detail}"


def find_screen(repo_root: Path, game: str) -> Path | None:
    """The newest screenshot a person or a build left for this game."""
    stem = repo_root / SCREEN_IMAGE.format(game=game)
    found = [stem.with_suffix(suffix) for suffix in SCREEN_SUFFIXES
             if stem.with_suffix(suffix).is_file()
             and stem.with_suffix(suffix).stat().st_size > 0]
    return max(found, key=lambda p: p.stat().st_mtime) if found else None


@dataclass
class ReviewExecutor:
    """GAMENN-REVIEW: the reviewer scores, quality_gate decides, nobody guesses.

    Order matters. The inspection runs FIRST so its result can be written onto
    the task as a note, which build_prompt puts in front of the reviewer - a
    reviewer that has been told what a vision model saw can score the visual
    dimensions, and one that has been told nobody looked knows to leave them
    null. Running it afterwards would mean scoring first and looking second.
    """
    repo_root: Path
    write_phase: Callable[[Any, str], StepResult]
    #: The local vision model, or "" when none is usable. No image is ever
    #: sent anywhere but the local endpoint.
    model: str = ""
    url: str = "http://127.0.0.1:11434"
    target: int = 85

    # -- looking -----------------------------------------------------------

    def _ask_about(self, image: Path) -> tuple[str, str]:
        payload = {
            "model": self.model,
            "prompt": VISION_PROMPT,
            "images": [base64.b64encode(image.read_bytes()).decode("ascii")],
            "think": False,
            "stream": False,
            "options": {"num_predict": INSPECT_NUM_PREDICT},
        }
        request = urllib.request.Request(
            f"{self.url}/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=INSPECT_TIMEOUT) as response:
            body = json.loads(response.read())
        return (str(body.get("response") or "").strip(),
                str(body.get("done_reason") or ""))

    def inspect(self, game: str) -> Inspection:
        image = find_screen(self.repo_root, game)
        if image is None:
            return Inspection(False, detail=(
                f"{SCREEN_IMAGE.format(game=game)}.png 가 없습니다. "
                "이 저장소에는 스크린샷을 찍는 코드가 없습니다"
                "(Unity 가 -nographics 로 도는 한 만들 수 없습니다). "
                "기기에서 APK 를 띄우고 찍은 사진을 그 경로에 두면 "
                "다음 실행에서 화면 점수를 매길 수 있습니다."))
        if not self.model:
            return Inspection(False, image=_relative(image, self.repo_root),
                              detail="로컬 비전 모델이 없어 이미지를 보지 못했습니다.")
        try:
            text, done_reason = self._ask_about(image)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return Inspection(False, model=self.model,
                              image=_relative(image, self.repo_root),
                              detail=f"비전 모델을 부르지 못했습니다: {exc}")
        if not text:
            return Inspection(False, model=self.model,
                              image=_relative(image, self.repo_root),
                              detail="비전 모델이 빈 응답을 냈습니다.")
        # Truncated is still looked-at: the model had the image in front of it.
        # Said out loud rather than hidden, because a description that stops
        # mid-sentence is a weaker basis for a score.
        detail = ("설명이 잘렸습니다 (done_reason=length)."
                  if done_reason == "length" else "")
        return Inspection(True, model=self.model,
                          image=_relative(image, self.repo_root),
                          text=text, detail=detail)

    def _record(self, task: Any, game: str, inspection: Inspection) -> None:
        """Put the inspection where both the reviewer and a person can read it."""
        if inspection.looked:
            doc = self.repo_root / INSPECTION_DOC.format(game=game)
            doc.parent.mkdir(parents=True, exist_ok=True)
            doc.write_text(
                f"# {game} 화면 관찰\n\n"
                f"모델: {inspection.model}  \n"
                f"이미지: {inspection.image}  \n"
                f"{inspection.detail}\n\n---\n\n{inspection.text}\n",
                encoding="utf-8")
            note = (f"{INSPECTION_NOTE} {inspection.model} 가 "
                    f"{inspection.image} 를 봤습니다. 관찰 내용은 "
                    f"{INSPECTION_DOC.format(game=game)} 에 있습니다. "
                    "graphics / ui_ux / animation_vfx 점수는 그 문서를 근거로 "
                    "매기세요.")
        else:
            note = (f"{INSPECTION_NOTE} 아무도 화면을 보지 않았습니다 "
                    f"({inspection.detail}) 따라서 graphics / ui_ux / "
                    "animation_vfx 는 반드시 null 로 두세요. 본 적 없는 것에 "
                    "점수를 매기면 게이트가 거부합니다.")
        # Replaced, not appended: a retry would otherwise stack a near-identical
        # paragraph onto the prompt every attempt.
        task.notes[:] = [n for n in task.notes if not n.startswith(INSPECTION_NOTE)]
        task.notes.append(note)

    # -- scoring -----------------------------------------------------------

    def _read_scores(self, game: str) -> tuple[dict[str, Any] | None, str]:
        path = self.repo_root / QUALITY_REPORT.format(game=game)
        if not path.is_file():
            return None, (f"{QUALITY_REPORT.format(game=game)} 이 없습니다. "
                          f"일곱 차원 점수를 담은 JSON 을 쓰세요: {SCORE_SHAPE}. "
                          "본 적 없는 차원은 null.")
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            return None, f"{QUALITY_REPORT.format(game=game)} 을 읽지 못했습니다: {exc}"
        if not isinstance(data, dict):
            return None, f"{QUALITY_REPORT.format(game=game)} 이 JSON 객체가 아닙니다."
        # Exactly the seven the gate weighs. A dimension the reviewer left out
        # is None, which is NOT_VERIFIED - the honest reading of "not scored".
        # Extra keys (its own notes, a summary) are ignored rather than
        # rejected: the file is for people too.
        return {name: data.get(name) for name in QUALITY_WEIGHTS}, ""

    def __call__(self, task: Any, role: str) -> StepResult:
        game = game_id_of(task)
        if not game:
            return StepResult(
                ok=False, summary="STATUS: FAILED",
                error=f"'{task.id}' 이 GAMENN-단계 형식이 아니라 "
                      "어느 게임을 검토할지 알 수 없습니다.")

        inspection = self.inspect(game)
        self._record(task, game, inspection)

        written = self.write_phase(task, role)
        if written.limited:
            return written
        if not written.ok:
            return StepResult(
                ok=False, changed=list(written.changed),
                summary=("STATUS: FAILED\n"
                         "REASON: 검토 문서를 쓰지 못해 점수를 읽을 수 없습니다.\n"
                         f"{inspection.line}\n\n"
                         + quote_foreign(written.summary)),
                error=written.error or "검토 단계가 실패했습니다.")

        scores, problem = self._read_scores(game)
        if scores is None:
            # Not the gate's business: there is nothing to judge, which is a
            # different thing from a judgement that came out badly.
            return StepResult(
                ok=False, changed=list(written.changed),
                summary=(f"STATUS: FAILED\nREASON: {problem}\n"
                         f"{inspection.line}\n\n"
                         + quote_foreign(written.summary)),
                error=problem)

        try:
            gate = quality_gate(scores, visually_inspected=inspection.looked,
                                target=self.target)
        except GateValidationError as exc:
            # The refusal doing its job - most often a reviewer that scored
            # graphics without anything having looked. Reported as the failure
            # it is, NOT worked around by nulling the scores first: blanking
            # them would turn the guard off and call the result NOT_VERIFIED,
            # which is precisely the weakening this step exists to prevent.
            return StepResult(
                ok=False, changed=list(written.changed),
                summary=(f"STATUS: FAILED\nGATE: REFUSED\nREASON: {exc}\n"
                         f"{inspection.line}\n\n"
                         + quote_foreign(written.summary)),
                error=str(exc))

        # NOT_VERIFIED goes to a person, not to a retry. Asking the same agent
        # again produces the same silence; what is missing is somebody looking
        # at the game. chain_runner parks the task with a review_note and the
        # panel grows a 계속 진행 button, which is exactly this case.
        if gate.passed:
            status = "OK"
        elif gate.status == "NOT_VERIFIED":
            status = "NEEDS_HUMAN_REVIEW"
        else:
            status = "FAILED"

        detail = [inspection.line]
        detail += [f"SCORE: {name} = "
                   f"{'점수 없음' if scores[name] is None else scores[name]}"
                   f"/{weight}"
                   for name, weight in QUALITY_WEIGHTS.items()]
        reason = ""
        if status == "NEEDS_HUMAN_REVIEW":
            reason = ("점수가 매겨지지 않은 항목: " + ", ".join(gate.reasons)
                      + ". 사람이 게임을 보고 판단해야 합니다.")
        return StepResult(
            ok=gate.passed, changed=list(written.changed),
            summary=(gate_summary(gate, detail=detail, status=status,
                                  reason=reason)
                     + "\n\n" + quote_foreign(written.summary)),
            error="" if gate.passed else "; ".join(gate.reasons))
