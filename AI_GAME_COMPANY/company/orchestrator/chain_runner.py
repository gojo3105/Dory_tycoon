"""Drives a plan to the end, letting each agent's own verdict pick what is next.

    build_plan  ->  [01 02 03 04 05 06 07]  ->  run_chain
                                                  |
                          each step: run, read the agent's final message,
                          and do what that message says to do next

WHY THERE IS NO OPINION-GATHERING STEP. An earlier shape of this had a
separate stage that collected a second judgement about every finished step.
That is a second opinion about work its own author already reported on, and
AGENTS.json already requires the author to report it: every role's
final_response_contract ends with STATUS, HANDOFF_TO and HANDOFF_REASON. So
the chain reads what the agent itself said and routes on that. One judgement,
made by whoever did the work, which is also the only one that saw it.

WHAT THE VERDICT CAN DO. Continue to the next step; ask for a retry of its
own step; or hand off to a DIFFERENT role - which is how a failed sprite
reaches art_director and a failed APK reaches release_engineer without this
file knowing anything about Gemini or Unity. Executors are passed in.

THE LIMITS ARE THE POLICY'S, NOT THIS FILE'S. retry.max_retry caps attempts.
retry.stop_on_repeated_error_hash stops a loop that is failing the same way
rather than burning the remaining tries on it. on_codex_limit.fallback_order
decides what happens when the subscription runs out - and
escalate_to_paid_api being false means running out is where it stops, not
where it starts spending.

IT STILL COMMITS NOTHING. Every step leaves its work in the tree for a person
to review, exactly as `team run` does today.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from company.orchestrator.teamwork import (
    BLOCKED,
    DONE,
    REVIEW,
    TODO,
    Task,
    TaskBoard,
)

# What a step can decide to do next.
CONTINUE = "continue"
RETRY = "retry"
REROUTE = "reroute"
#: Codex 한도. 실패가 아니라 "지금은 못 한다" 이므로 작업 상태를 건드리지
#: 않는다. 나중에 같은 체인을 다시 부르면 그 자리에서 이어진다.
PAUSE = "pause"
#: 에이전트가 스스로 확신하지 못한 경우. 사람이 "계속 진행" 을 누를 때까지
#: 멈춘다. 재시도로 같은 답을 또 받아내는 것보다 정직하다.
REVIEW_WAIT = "review_wait"
STOP = "stop"

#: STATUS words an agent may use, mapped to whether the step succeeded.
#: Unknown words are NOT treated as success - a verdict nobody can read is a
#: verdict, and guessing "probably fine" is how a broken step advances.
STATUS_OK = frozenset({"OK", "DONE", "COMPLETE", "COMPLETED", "SUCCESS", "PASS"})
STATUS_BAD = frozenset({"FAILED", "FAIL", "BLOCKED", "ERROR", "STOPPED",
                        "INCOMPLETE"})
#: Not failure and not success: the agent is saying a person should look.
#: Retrying these produces the same uncertainty again, so the chain waits.
STATUS_REVIEW = frozenset({"NEEDS_HUMAN_REVIEW", "REVIEW", "NEEDS_REVIEW",
                           "UNSURE", "UNCERTAIN"})


@dataclass
class Verdict:
    """What the agent said about its own work, as the contract asks it to."""
    status: str = ""
    handoff_to: str = ""
    reason: str = ""
    raw: str = ""

    @property
    def needs_review(self) -> bool:
        return self.status.strip().upper() in STATUS_REVIEW

    @property
    def succeeded(self) -> bool | None:
        """True, False, or None when the agent did not say readably."""
        upper = self.status.strip().upper()
        if upper in STATUS_OK:
            return True
        if upper in STATUS_BAD:
            return False
        return None


@dataclass
class StepResult:
    """What an executor reports back. Deliberately small.

    `changed` is what makes a step real: a run that finished having edited
    nothing is not progress, and teamwork.run_task already treats it that way.
    """
    ok: bool
    summary: str = ""
    changed: list[str] = field(default_factory=list)
    error: str = ""
    limited: bool = False


@dataclass
class StepRecord:
    task_id: str
    role: str
    attempt: int
    action: str
    ok: bool
    verdict: Verdict
    detail: str = ""


@dataclass
class ChainResult:
    project: str
    records: list[StepRecord] = field(default_factory=list)
    finished: bool = False
    stopped_because: str = ""
    #: Set when a Codex limit paused the chain. NOT a failure - calling
    #: run_chain again once the subscription resets picks up here.
    paused_at: str = ""
    #: Set when an agent asked for a person. Cleared by passing the id in
    #: `approved` on a later call, which is what the panel button does.
    awaiting_review: str = ""

    @property
    def paused(self) -> bool:
        return bool(self.paused_at)

    @property
    def ran(self) -> int:
        return len(self.records)


_SECTION = re.compile(
    r"^\s*(?:[-*#>\s]*)(STATUS|HANDOFF_TO|HANDOFF_REASON)\s*[:=]\s*(.+?)\s*$",
    re.IGNORECASE | re.MULTILINE)


def read_verdict(text: str) -> Verdict:
    """Pull the contract sections out of an agent's final message.

    Tolerant about decoration - agents write "STATUS: OK", "- STATUS: OK" and
    "**STATUS:** OK" - and strict about nothing else. A message with no STATUS
    yields an empty verdict whose `succeeded` is None, and the caller treats
    that as "could not tell", never as success.
    """
    verdict = Verdict(raw=text or "")
    for match in _SECTION.finditer(text or ""):
        key = match.group(1).upper()
        value = match.group(2).strip().strip("*_`").strip()
        if key == "STATUS" and not verdict.status:
            verdict.status = value
        elif key == "HANDOFF_TO" and not verdict.handoff_to:
            verdict.handoff_to = value
        elif key == "HANDOFF_REASON" and not verdict.reason:
            verdict.reason = value
    return verdict


def error_hash(text: str) -> str:
    """A short fingerprint of a failure, for stop_on_repeated_error_hash.

    Digits are flattened first: the same failure usually differs only by a
    line number or a timestamp, and a fingerprint that changed every run
    would never trigger the rule it exists for.
    """
    flattened = re.sub(r"\d+", "#", (text or "").strip().lower())
    return hashlib.sha256(flattened.encode("utf-8")).hexdigest()[:12]


def decide(step: StepResult, verdict: Verdict, *, attempt: int,
           max_retry: int, roles: set[str]) -> tuple[str, str]:
    """(action, why) for one finished step. The agent's verdict leads.

    Order matters. A subscription limit is not a failure of the work, so it
    is checked first and stops rather than burning retries. Then the agent's
    own STATUS. Only if the agent said nothing readable does the executor's
    exit code decide - and an unreadable verdict on a technically successful
    run is still a retry, because "it exited zero" is not the same claim as
    "it did the job".
    """
    if step.limited:
        # A pause, not a stop. The work is fine; the subscription is not, and
        # a subscription comes back. Marking this BLOCKED would turn "come
        # back later" into "somebody go and fix this".
        return PAUSE, "Codex 구독 한도. 한도가 돌아오면 이어서 진행합니다."

    if attempt > max_retry:
        return STOP, f"재시도 {max_retry}회를 넘겼습니다."

    # Checked before the handoff and before success: an agent saying it is
    # unsure is asking for a person, and retrying produces the same
    # uncertainty a second time.
    if verdict.needs_review:
        return REVIEW_WAIT, (verdict.reason
                             or f"에이전트가 검토를 요청했습니다: {verdict.status}")

    said = verdict.succeeded

    # A handoff to another role is the agent asking for someone else, which
    # is meaningful whether or not its own step succeeded: a gameplay
    # engineer that cannot finish without art says so by naming art_director.
    target = verdict.handoff_to.strip().lower()
    if target and target in roles and said is not True:
        return REROUTE, f"{verdict.handoff_to} 에게 넘깁니다: {verdict.reason or '이유 없음'}"

    if said is True:
        if not step.changed:
            # The agent says it succeeded and the tree disagrees. The tree
            # wins - this is section 8's first principle in miniature.
            return RETRY, "성공이라고 했지만 바뀐 파일이 없습니다."
        return CONTINUE, ""

    if said is False:
        return RETRY, f"에이전트가 실패를 보고했습니다: {verdict.status}"

    # Nothing readable in the final message.
    if not step.ok:
        return RETRY, step.error or "실행이 실패했습니다."
    return RETRY, "최종 메시지에 STATUS 가 없어 성공 여부를 확인할 수 없습니다."


def chain_tasks(board: TaskBoard, prefix: str) -> list[Task]:
    """The plan's tasks, in id order, skipping ones already finished."""
    tasks = [t for t in board.tasks if t.id.startswith(prefix)]
    return sorted(tasks, key=lambda t: t.id)


def run_chain(board: TaskBoard, prefix: str,
              execute: Callable[[Task, str], StepResult],
              *, roles: set[str] | None = None,
              max_retry: int = 5,
              stop_on_repeated_error: bool = True,
              approved: set[str] | None = None,
              on_event: Callable[[StepRecord], None] | None = None,
              ) -> ChainResult:
    """Run every step of one plan, routing on each agent's own verdict.

    `execute` is how a step actually happens - Codex for a writing role,
    Gemini for art, Unity for a test or a build. Passed in, so this file
    stays ignorant of all three and a new executor needs no change here.

    `approved` holds ids a person has cleared past a review pause - what the
    panel's 계속 진행 button sends. An approved task is stepped OVER, not run
    again: its work already happened, and the only thing missing was somebody
    saying it was good enough to build on.

    Safe to call repeatedly. Finished steps are skipped, so resuming after a
    Codex limit is the same call made again.
    """
    tasks = chain_tasks(board, prefix)
    if not tasks:
        return ChainResult(project=prefix, stopped_because=f"'{prefix}' 로 시작하는 작업이 없습니다.")

    known_roles = roles if roles is not None else {t.agent_role for t in tasks if t.agent_role}
    result = ChainResult(project=prefix)
    seen_errors: dict[str, int] = {}

    index = 0
    attempt = 1
    while index < len(tasks):
        task = tasks[index]
        cleared = approved or set()

        # REVIEW with no review_note is teamwork.run_task's "this step
        # succeeded" - a finished step, not a question for anybody.
        if task.status == DONE or (task.status == REVIEW and not task.review_note):
            index, attempt = index + 1, 1
            continue

        if task.status == REVIEW and task.review_note:
            if task.id in cleared:
                task.notes.append(
                    f"CONTINUE: 사람이 검토를 통과시켰습니다 ({task.review_note}).")
                task.review_note = ""
                task.status = DONE
                index, attempt = index + 1, 1
                continue
            # Already waiting. Returning without running anything matters:
            # re-invoking the chain while a review is outstanding must not
            # spend another Codex call to be told the same thing.
            result.awaiting_review = task.id
            result.stopped_because = f"{task.id} 이 검토를 기다리고 있습니다."
            return result

        role = task.agent_role or "?"
        step = execute(task, role)
        verdict = read_verdict(step.summary)
        action, why = decide(step, verdict, attempt=attempt,
                             max_retry=max_retry, roles=known_roles)

        if action in (RETRY, REROUTE) and stop_on_repeated_error:
            fingerprint = error_hash(step.error or verdict.status or step.summary)
            seen_errors[fingerprint] = seen_errors.get(fingerprint, 0) + 1
            if seen_errors[fingerprint] >= 2:
                action = STOP
                why = (f"같은 실패가 {seen_errors[fingerprint]}회 반복됐습니다 "
                       f"(hash {fingerprint}). 남은 재시도를 태우지 않고 멈춥니다.")

        record = StepRecord(task_id=task.id, role=role, attempt=attempt,
                            action=action, ok=step.ok, verdict=verdict, detail=why)
        result.records.append(record)
        if on_event is not None:
            on_event(record)

        if action == CONTINUE:
            # The agent that did the work said it was done and the tree
            # agrees, so the step is DONE - not REVIEW awaiting somebody.
            # This is the point of the chain: completion is determined by
            # whoever did the work, and the next step's depends_on check
            # requires DONE, so leaving it at REVIEW stalls the chain on its
            # own finished steps.
            #
            # Narrower than it looks: only tasks this chain ran reach here.
            # A CODEX-* task run through `team run` still lands at REVIEW for
            # a person, which is CLAUDE.md section 12's rule and is unchanged.
            task.status = DONE
            index, attempt = index + 1, 1
            continue
        if action == RETRY:
            attempt += 1
            continue
        if action == PAUSE:
            # Status untouched on purpose. The task is still todo; nothing
            # about it is wrong, and the next call retries it as normal.
            task.notes.append(f"PAUSED: {why}")
            result.paused_at = task.id
            result.stopped_because = why
            return result

        if action == REVIEW_WAIT:
            task.status = REVIEW
            task.review_note = why
            task.notes.append(f"REVIEW: {why}")
            result.awaiting_review = task.id
            result.stopped_because = why
            return result

        if action == REROUTE:
            # The board keeps the audit trail: who it went to and why. The
            # allowlist is NOT changed - a handoff moves the work, never the
            # permissions, which is the same line dispatch holds.
            task.agent_role = verdict.handoff_to.strip().lower()
            task.handoff_from = role
            task.notes.append(f"HANDOFF: {role} -> {task.agent_role} ({verdict.reason})")
            task.status = TODO
            attempt += 1
            continue

        task.status = BLOCKED
        task.notes.append(f"CHAIN STOPPED: {why}")
        result.stopped_because = why
        return result

    result.finished = True
    return result
