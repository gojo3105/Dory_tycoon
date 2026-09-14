"""Evidence-based QA, quality-review and Android release gates."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping


QA_RESULTS = {"PASS", "FAIL", "NOT_VERIFIED"}
SEVERITIES = {"BLOCKER", "CRITICAL", "MAJOR", "MINOR", "POLISH"}
QUALITY_WEIGHTS = {
    "gameplay": 20,
    "graphics": 20,
    "ui_ux": 15,
    "animation_vfx": 15,
    "audio": 10,
    "stability": 10,
    "mobile": 10,
}
VISUAL_DIMENSIONS = {"graphics", "ui_ux", "animation_vfx"}


class GateValidationError(ValueError):
    """Gate input is malformed or claims evidence that was not observed."""


@dataclass(frozen=True)
class Finding:
    result: str
    severity: str
    summary: str

    def __post_init__(self) -> None:
        if self.result not in QA_RESULTS:
            raise GateValidationError(f"invalid QA result: {self.result}")
        if self.severity not in SEVERITIES:
            raise GateValidationError(f"invalid severity: {self.severity}")


@dataclass(frozen=True)
class GateResult:
    passed: bool
    status: str
    reasons: tuple[str, ...] = ()


def qa_gate(findings: list[Finding]) -> GateResult:
    blockers = [finding.summary for finding in findings
                if finding.result == "FAIL" and
                finding.severity in {"BLOCKER", "CRITICAL"}]
    failures = [finding.summary for finding in findings if finding.result == "FAIL"]
    if blockers:
        return GateResult(False, "BLOCKED", tuple(blockers))
    if failures:
        return GateResult(False, "REWORK", tuple(failures))
    if any(finding.result == "NOT_VERIFIED" for finding in findings):
        return GateResult(False, "NOT_VERIFIED", tuple(
            finding.summary for finding in findings if finding.result == "NOT_VERIFIED"))
    return GateResult(True, "PASS")


def quality_gate(scores: Mapping[str, int | None], *,
                 visually_inspected: bool, target: int = 85) -> GateResult:
    missing = set(QUALITY_WEIGHTS) - set(scores)
    extra = set(scores) - set(QUALITY_WEIGHTS)
    if missing or extra:
        raise GateValidationError(
            f"quality dimensions mismatch; missing={sorted(missing)}, extra={sorted(extra)}")
    if not visually_inspected:
        invented = [name for name in VISUAL_DIMENSIONS if scores[name] is not None]
        if invented:
            raise GateValidationError(
                "visual scores require a device or screenshot inspection: " + ", ".join(invented))
    not_verified = [name for name, score in scores.items() if score is None]
    if not_verified:
        return GateResult(False, "NOT_VERIFIED", tuple(not_verified))
    for name, score in scores.items():
        if not isinstance(score, int) or score < 0 or score > QUALITY_WEIGHTS[name]:
            raise GateValidationError(
                f"{name} must be between 0 and {QUALITY_WEIGHTS[name]}")
    total = sum(int(score) for score in scores.values())
    if total < target:
        return GateResult(False, "CHANGES_REQUIRED", (f"quality score {total} < {target}",))
    return GateResult(True, "APPROVED", (f"quality score {total}",))


@dataclass(frozen=True)
class ReleaseEvidence:
    process_ok: bool
    unity_report_ok: bool
    apk_path: Path | None
    current_run: bool = True
    errors: tuple[str, ...] = field(default_factory=tuple)


def release_gate(evidence: ReleaseEvidence) -> GateResult:
    reasons: list[str] = []
    category = ""
    if evidence.errors:
        category = "COMPILE_FAILURE"
        reasons.extend(evidence.errors)
    elif not evidence.process_ok:
        category = "UNITY_BUILD_FAILURE"
        reasons.append("build process exit code failed")
    elif not evidence.unity_report_ok:
        category = "UNITY_BUILD_FAILURE"
        reasons.append("Unity Build Report did not report success")
    elif evidence.apk_path is None or not evidence.apk_path.is_file():
        category = "ARTIFACT_MISSING"
        reasons.append("current APK does not exist")
    elif not evidence.current_run:
        category = "ARTIFACT_MISSING"
        reasons.append("APK is not tied to the current run")
    if reasons:
        return GateResult(False, category, tuple(reasons))
    return GateResult(True, "RELEASE_CANDIDATE", (str(evidence.apk_path),))
