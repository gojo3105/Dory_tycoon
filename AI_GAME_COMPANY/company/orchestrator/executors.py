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

NOTHING HERE PRETENDS. If the local model produces no readable STATUS, the
executor writes NEEDS_HUMAN_REVIEW and says in the document that the verdict
came from this file rather than from the model. chain_runner then routes on a
truthful "nobody could tell" instead of an invented success.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from company.orchestrator.agent_dispatcher import dispatch, role_section
from company.orchestrator.chain_runner import StepResult, read_verdict

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
        from company.orchestrator.codex_runner import CodexLimited
        from company.orchestrator.teamwork import run_task

        try:
            run = run_task(self.board, task.id, self.codex, self.repo_root,
                           timeout_seconds=self.timeout_seconds,
                           registry=self.registry)
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
            resolved = dispatch(task, self.registry)
        except Exception as exc:  # noqa: BLE001
            return StepResult(ok=False, error=f"역할을 정할 수 없습니다: {exc}")

        prompt = "\n\n".join([
            role_section(resolved),
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
                  f"역할: {resolved.agent.display_name} ({resolved.agent_id})  \n"
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
