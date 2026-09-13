"""One Korean line describing what a running job is actually doing.

The control panel used to show a raw English transcript and the word
"실행 중". That answers "is something happening" but not "what is it doing
now", and the honest answer to the second question is usually available: the
orchestrator prints recognisable milestone lines as it goes.

THE RULE THIS FILE EXISTS TO KEEP: every phrase below is anchored to a line
the orchestrator actually prints. Nothing here estimates, and nothing here
reports a percentage - there is no denominator anywhere in this pipeline, so
a percentage would be invented. When no marker has been seen yet the phase is
"확인 불가", which is this project's standing answer for absent evidence, and
not a guess dressed up as a status.

The markers are matched against the tail of the job's output and the LAST one
wins, because a run moves forward through them. `evidence` carries the actual
line that matched, so a reader can check the summary against the transcript
below it rather than trusting this translation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# A phase whose work happens inside a single silent subprocess. Naming them
# matters: without this the page looks frozen for the twenty minutes Codex is
# thinking, and someone kills a run that was fine.
SLOW = "slow"


@dataclass(frozen=True)
class Marker:
    """One recognisable line, and what it means in Korean."""
    pattern: re.Pattern[str]
    phase: str
    kind: str = ""


def _m(pattern: str, phase: str, kind: str = "") -> Marker:
    return Marker(re.compile(pattern, re.MULTILINE), phase, kind)


# Ordered only for readability - matching takes the last hit in the OUTPUT,
# not the last entry here.
MARKERS: dict[str, tuple[Marker, ...]] = {
    "team-run": (
        _m(r"^=== HANDING (\S+) TO CODEX ===", "작업 명세를 읽고 Codex에게 넘기는 중"),
        _m(r"^\s*allowlist:", "수정 허용 파일 목록을 확인하는 중"),
        _m(r"running codex exec", "Codex가 코드를 작성하는 중", SLOW),
        _m(r"^--- codex said ---", "Codex가 끝냈습니다 · 결과를 읽는 중"),
        _m(r"^--- files this run changed ---", "바뀐 파일을 허용 목록과 대조하는 중"),
        _m(r"^\s*status -> ", "작업판 상태를 갱신하는 중"),
        _m(r"^BLOCKED", "중단됨 · 허용 목록을 벗어난 변경이 있습니다"),
        _m(r"^CODEX_LIMITED", "중단됨 · Codex 사용 한도"),
        _m(r"^REFUSED", "거부됨 · 실행 조건을 만족하지 못했습니다"),
        _m(r"^(FAILED|ERROR)", "실패"),
    ),
    "build": (
        _m(r"^=== LOCAL BUILD", "Unity 빌드 준비 중"),
        _m(r"^\s*This takes a while", "Unity 실행 중 · 생성 → 검증 → 빌드", SLOW),
        _m(r"^\s*\[ OK \] generate", "Scene/Prefab 생성 완료"),
        _m(r"^\s*\[FAIL\] generate", "생성 단계에서 실패"),
        _m(r"^\s*\[ OK \] validate", "GameSpec 검증 완료"),
        _m(r"^\s*\[FAIL\] validate", "검증 단계에서 실패"),
        _m(r"^\s*\[ OK \] build", "APK 빌드 완료 · 파일을 확인하는 중"),
        _m(r"^\s*\[FAIL\] build", "빌드 단계에서 실패"),
        _m(r"^APK: ", "APK 생성 확인됨"),
        _m(r"^BUILD_FAILED", "실패 · 검증된 APK 없음"),
        _m(r"^ERROR", "실패"),
    ),
    # Every phrase here is keyed to a line cmd_test actually prints; the
    # wording was taken from a real run's output, not guessed at.
    "test": (
        _m(r"^=== LOCAL UNITY TESTS", "Unity 테스트 준비 중"),
        _m(r"^\s*Unity: ", "Unity 실행 파일을 찾았습니다", SLOW),
        _m(r"^\s*\[ OK \] generate", "Scene/Prefab 생성 완료 · 테스트를 실행하는 중", SLOW),
        _m(r"^\s*\[FAIL\] generate", "생성 단계에서 실패 · 테스트는 돌지 않았습니다"),
        _m(r"^\s*\[ OK \] test-", "테스트 완료 · 결과를 읽는 중"),
        _m(r"^\s*\[FAIL\] test-", "테스트 단계에서 실패"),
        _m(r"^\s*passed=\d+", "결과 집계됨"),
        _m(r"^TESTS_PASSED", "테스트 전부 통과"),
        _m(r"^TESTS_FAILED", "실패 · 테스트가 통과하지 못했습니다"),
        _m(r"^(REFUSED|ERROR)", "실패"),
    ),
    "codex-doctor": (
        _m(r"^=== CODEX ===", "Codex CLI 상태를 확인하는 중"),
        _m(r"^=== codex doctor", "codex doctor 원문을 읽는 중"),
    ),
    "git-status": (
        _m(r"\S", "작업 트리의 변경 파일을 세는 중"),
    ),
    "chain-run": (
        _m(r"^=== CHAIN ", "계획 단계를 순서대로 실행하는 중"),
        _m(r"^  \[일시정지\]", "Codex 한도로 일시정지 · 한도 복귀 후 이어짐"),
        _m(r"^  \[검토 대기\]", "검토 대기 · 계속 진행 버튼이 필요합니다"),
        _m(r"^  \[OK\]", "단계 통과 · 다음으로"),
        _m(r"^체인 완료", "체인 완료"),
    ),
    "chain-continue": (
        _m(r"^=== CHAIN ", "검토를 통과시키고 이어서 실행하는 중"),
    ),
    "ollama-list": (
        _m(r"\S", "설치된 모델과 라이선스·RAM 적합성을 확인하는 중"),
    ),
    "dashboard": (
        _m(r"\S", "대시보드를 다시 그리는 중"),
    ),
}

# What each action is, for the line above the phase. Kept here rather than
# reusing server.ACTIONS[...].label so this module stays importable without
# the server - the dashboard renders in environments that never serve.
ACTION_LABEL = {
    "team-run": "Codex 작업 실행",
    "build": "Unity 빌드",
    "test": "Unity 테스트",
    "codex-doctor": "Codex 진단",
    "git-status": "변경 파일 확인",
    "ollama-list": "설치된 모델 확인",
    "chain-run": "체인 진행",
    "chain-continue": "계속 진행",
    "dashboard": "대시보드 새로고침",
}

# Which AI each action drives. The office view uses this to show the right
# character working instead of contradicting the panel above it.
ACTION_AGENT_PREFIX = {
    "team-run": "Codex",
    "codex-doctor": "Codex",
    "build": "Unity",
    "test": "Unity",
}

STARTING = "시작하는 중"
UNKNOWN_PHASE = "확인 불가 · 아직 알아볼 수 있는 출력이 없습니다"

# Enough tail to hold the recent markers without rescanning a 200k build log
# on every one-second poll.
SCAN_TAIL = 8000


def elapsed_korean(seconds: float) -> str:
    """'3분 12초'. Seconds only under a minute, hours once there are any."""
    total = max(0, int(seconds))
    if total < 60:
        return f"{total}초"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}분 {secs}초"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}시간 {minutes}분"


def _last_line(text: str) -> str:
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


@dataclass
class Progress:
    """What to show while a job runs. Every field is derived, none guessed."""
    action: str
    action_label: str
    phase: str
    evidence: str = ""
    elapsed: str = ""
    slow: bool = False
    done: bool = False
    exit_code: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "action_label": self.action_label,
            "phase": self.phase,
            "evidence": self.evidence,
            "elapsed": self.elapsed,
            "slow": self.slow,
            "done": self.done,
            "exit_code": self.exit_code,
        }


def summarise(action: str, output: str, seconds: float = 0.0,
              done: bool = False, exit_code: int | None = None) -> Progress:
    """Describe a run in Korean from its own output.

    `output` may be empty (the process has not printed yet) and may be a tail
    with its head already trimmed - both are normal, and neither is allowed to
    produce a confident-sounding phase.
    """
    label = ACTION_LABEL.get(action, action)
    progress = Progress(action=action, action_label=label, phase=STARTING,
                        elapsed=elapsed_korean(seconds), done=done,
                        exit_code=exit_code)

    if done:
        progress.phase = ("완료" if exit_code == 0
                          else f"실패 · 종료 코드 {exit_code}")
        progress.evidence = _last_line(output[-SCAN_TAIL:])
        return progress

    if not output.strip():
        # Not "확인 불가": a process that has printed nothing one second in is
        # starting, and saying so is both true and less alarming.
        return progress

    tail = output[-SCAN_TAIL:]
    best: tuple[int, Marker] | None = None
    for marker in MARKERS.get(action, ()):
        found = None
        for match in marker.pattern.finditer(tail):
            found = match
        if found is not None and (best is None or found.start() > best[0]):
            best = (found.start(), marker)

    if best is None:
        progress.phase = UNKNOWN_PHASE
        progress.evidence = _last_line(tail)
        return progress

    start, marker = best
    progress.phase = marker.phase
    progress.slow = marker.kind == SLOW
    line_end = tail.find("\n", start)
    progress.evidence = tail[start:line_end if line_end != -1 else None].strip()
    return progress


def agent_prefix_for(action: str) -> str:
    """Which agent name prefix an action drives, '' when it drives none."""
    return ACTION_AGENT_PREFIX.get(action, "")
