"""Create a measurable Google Play growth package for every generated game.

The package is a production brief, not a promise of chart position. It makes
the Art Director's work testable: every creative has a hypothesis, every
release has quality and policy gates, and post-launch data decides the next
iteration.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


OFFICIAL_REFERENCES = {
    "android_quality": "https://developer.android.com/quality",
    "core_value": "https://developer.android.com/quality/core-value",
    "android_vitals": "https://developer.android.com/topic/performance/vitals",
    "store_experiments": (
        "https://support.google.com/googleplay/android-developer/answer/12053285"
    ),
    "pre_launch": (
        "https://support.google.com/googleplay/android-developer/answer/9844487"
    ),
    "store_listing": (
        "https://support.google.com/googleplay/android-developer/answer/13393723"
    ),
}

# Google Play's current visibility-related stability thresholds. They are
# deliberately separate from product targets, which are internal stretch goals.
TECHNICAL_GATES = {
    "user_perceived_crash_rate_pct_max": 1.09,
    "user_perceived_anr_rate_pct_max": 0.47,
    "cold_start_seconds_max": 3.0,
    "gameplay_fps_min": 30.0,
}

# Ambitious launch targets for a casual mobile game. These do not guarantee a
# rank; they are evidence thresholds for continuing investment and experiments.
PRODUCT_TARGETS = {
    "tutorial_completion_pct_min": 85.0,
    "day1_retention_pct_min": 40.0,
    "day7_retention_pct_min": 15.0,
    "day30_retention_pct_min": 7.0,
    "store_install_conversion_pct_min": 30.0,
    "rating_min": 4.5,
}


@dataclass(frozen=True)
class GrowthReadiness:
    ready: bool
    score: int
    passed: tuple[str, ...]
    gaps: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "score": self.score,
            "passed": list(self.passed),
            "gaps": list(self.gaps),
        }


def growth_root(game_id: str) -> str:
    return f"Growth/{game_id}"


def expected_paths(game_id: str) -> tuple[str, ...]:
    root = growth_root(game_id)
    return (
        f"{root}/PLAY_STORE_STRATEGY.md",
        f"{root}/STORE_LISTING.json",
        f"{root}/KPI_GATES.json",
        f"{root}/CREATIVE_EXPERIMENTS.json",
        f"{root}/RELEASE_CHECKLIST.md",
        f"{root}/METRICS_TEMPLATE.json",
    )


def _store_listing(plan: Any) -> dict[str, Any]:
    hook = f"도리와 함께 즐기는 {plan.style_label} {plan.genre_label}"
    return {
        "game_id": plan.game_id,
        "default_language": "ko-KR",
        "app_name": plan.title[:30],
        "short_description": hook[:80],
        "full_description_draft": (
            f"{plan.pitch}\n\n"
            f"핵심 재미\n- " + "\n- ".join(plan.features) +
            "\n\n게임 화면과 실제 기능을 확인한 뒤 최종 문구를 확정하세요."
        ),
        "claims_policy": [
            "실제 게임에서 확인되지 않은 기능, 순위, 수상, 성능을 쓰지 않는다.",
            "검색어를 반복하거나 다른 앱·브랜드와 혼동되는 표현을 쓰지 않는다.",
            "광고·결제·데이터 수집 설명은 실제 빌드와 일치해야 한다.",
        ],
        "localization_priority": ["ko-KR", "en-US", "ja-JP"],
    }


def _experiments(plan: Any) -> dict[str, Any]:
    return {
        "rule": "한 번에 한 요소만 바꾸고 충분한 표본과 Play Console 판정을 기다린다.",
        "target_metrics": ["unique_user_install_clicks", "unique_user_open_clicks"],
        "experiments": [
            {
                "id": "ICON-01",
                "asset": "app_icon",
                "hypothesis": "도리 얼굴을 크게 보여주면 작은 화면에서도 인지가 빨라진다.",
                "control": "게임 로고 중심",
                "variant_a": "도리 얼굴 + 장르 행동",
                "variant_b": "도리 얼굴 + 핵심 보상",
                "art_prompt": (
                    f"{plan.title}, cute Dori pixel mascot, {plan.genre_label}, "
                    f"{plan.spec['theme']['environment']} theme, bold silhouette, no text"
                ),
            },
            {
                "id": "FEATURE-01",
                "asset": "feature_graphic",
                "hypothesis": "핵심 행동과 보상을 한 장면에 보여주면 설치 의도가 높아진다.",
                "control": "세계관 전경",
                "variant_a": "도리의 핵심 행동 + 큰 보상",
                "variant_b": "위기 장면 + 성장 결과",
            },
            {
                "id": "SCREENSHOT-01",
                "asset": "screenshot_order",
                "hypothesis": "첫 3장에 조작·성장·보상을 순서대로 보여주면 이해가 빨라진다.",
                "control": "게임 진행 순서",
                "variant_a": "핵심 행동 → 성장 → 보상",
                "variant_b": "보상 → 핵심 행동 → 콘텐츠 규모",
            },
        ],
    }


def _strategy_markdown(plan: Any) -> str:
    features = "\n".join(f"- {item}" for item in plan.features)
    references = "\n".join(f"- {name}: {url}" for name, url in OFFICIAL_REFERENCES.items())
    return f"""# {plan.title} Google Play 1위 도전 전략

## 원칙

순위 1위를 보장하지 않는다. 재미, 안정성, 스토어 전환, 유지율을 실제 데이터로 검증하고 통과한 버전만 확장한다.

## 제품 약속

- 대상 게임: `{plan.game_id}`
- 장르/스타일: {plan.genre_label} / {plan.style_label}
- 핵심 한 문장: {plan.pitch}
- 첫 10초: 조작과 즉시 보상이 보여야 한다.
- 첫 세션: 튜토리얼 → 핵심 행동 → 성장 선택 → 보상 수령까지 완결한다.

## 핵심 기능

{features}

## Art Director 필수 산출물

- 앱 아이콘 3안: 로고 중심, 행동 중심, 보상 중심
- 피처 그래픽 3안: 세계관, 위기, 성장 결과
- 세로 스크린샷 최소 6장: 핵심 행동, 보상, 성장, 콘텐츠, 캐릭터, 이벤트
- 모든 이미지에 실제 게임 화면과 일치하는 근거를 남긴다.
- Gemini 무료 등급 또는 승인된 로컬 도구만 사용하고 라이선스를 기록한다.

## 출시 전 검증

1. 내부 테스트에서 튜토리얼과 첫 세션 이탈 구간을 고친다.
2. Pre-launch report의 안정성, 성능, 접근성 오류를 막는다.
3. Android vitals의 crash/ANR 기준을 지킨다.
4. Data safety, 광고, 결제, 개인정보처리방침이 실제 SDK 동작과 일치하는지 확인한다.
5. 스토어 등록정보는 한 요소씩 A/B 테스트한다.

## 출시 후 성장 루프

주간 단위로 유입 → 첫 실행 → 튜토리얼 → D1/D7/D30 → 결제/광고 → 이탈을 확인한다. 목표 미달 단계 하나만 골라 제품 또는 크리에이티브를 수정하고 다시 실험한다.

## 공식 기준

{references}
"""


def _release_checklist(plan: Any) -> str:
    return f"""# {plan.title} 출시 체크리스트

## 제품

- [ ] 첫 10초 안에 핵심 조작과 보상이 이해된다.
- [ ] 첫 세션이 튜토리얼·플레이·성장·보상까지 끝난다.
- [ ] D1/D7/D30, 튜토리얼 완료, 세션 길이, 이탈 지점 이벤트가 정의됐다.
- [ ] 반복 플레이 동기와 주간 콘텐츠 계획이 있다.

## 품질

- [ ] Unity EditMode와 PlayMode 테스트가 통과했다.
- [ ] 실제 AAB/APK와 현재 빌드의 해시가 확인됐다.
- [ ] Pre-launch report의 오류가 0개다.
- [ ] 사용자 인지 crash rate가 {TECHNICAL_GATES['user_perceived_crash_rate_pct_max']}% 미만이다.
- [ ] 사용자 인지 ANR rate가 {TECHNICAL_GATES['user_perceived_anr_rate_pct_max']}% 미만이다.
- [ ] 저사양 기기에서 핵심 플레이가 {TECHNICAL_GATES['gameplay_fps_min']:.0f} FPS 이상이다.

## 스토어·정책

- [ ] 아이콘·피처 그래픽·스크린샷이 실제 게임과 일치한다.
- [ ] 앱 설명에 검증되지 않은 표현이나 타사 브랜드가 없다.
- [ ] Data safety와 개인정보처리방침이 포함 SDK의 실제 동작과 일치한다.
- [ ] 광고·결제·연령 등급·대상 연령이 검토됐다.
- [ ] 내부/비공개 테스트와 단계적 출시 계획이 있다.

## 성장

- [ ] 아이콘, 피처 그래픽, 스크린샷 순서의 실험 가설이 있다.
- [ ] 한 실험에서 한 요소만 바꾼다.
- [ ] 사용자 설치 및 오픈 지표로 승자를 판단한다.
- [ ] 리뷰와 이탈 원인을 다음 업데이트 작업판에 연결한다.
"""


def write_growth_package(repo_root: Path, plan: Any) -> tuple[str, ...]:
    paths = expected_paths(plan.game_id)
    payloads: dict[str, str] = {
        paths[0]: _strategy_markdown(plan),
        paths[1]: json.dumps(_store_listing(plan), ensure_ascii=False, indent=2) + "\n",
        paths[2]: json.dumps({
            "game_id": plan.game_id,
            "technical_gates": TECHNICAL_GATES,
            "product_targets": PRODUCT_TARGETS,
            "note": "Product targets are stretch goals, not a chart-rank guarantee.",
            "official_references": OFFICIAL_REFERENCES,
        }, ensure_ascii=False, indent=2) + "\n",
        paths[3]: json.dumps(_experiments(plan), ensure_ascii=False, indent=2) + "\n",
        paths[4]: _release_checklist(plan),
        paths[5]: json.dumps({
            "game_id": plan.game_id,
            "period": "",
            "metrics": {key: None for key in (*TECHNICAL_GATES, *PRODUCT_TARGETS)},
            "source": "Play Console / product analytics / pre-launch report",
        }, ensure_ascii=False, indent=2) + "\n",
    }
    for relative, content in payloads.items():
        target = repo_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(target)
    return paths


def evaluate_metrics(metrics: Mapping[str, Any]) -> GrowthReadiness:
    checks: list[tuple[str, bool]] = []
    for name, limit in TECHNICAL_GATES.items():
        value = metrics.get(name)
        if name.endswith("_max"):
            checks.append((name, isinstance(value, (int, float)) and value <= limit))
        else:
            checks.append((name, isinstance(value, (int, float)) and value >= limit))
    for name, limit in PRODUCT_TARGETS.items():
        value = metrics.get(name)
        checks.append((name, isinstance(value, (int, float)) and value >= limit))
    passed = tuple(name for name, ok in checks if ok)
    gaps = tuple(name for name, ok in checks if not ok)
    score = round(len(passed) / len(checks) * 100) if checks else 0
    return GrowthReadiness(not gaps, score, passed, gaps)
