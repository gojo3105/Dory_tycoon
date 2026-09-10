"""Create safe Runner GameSpecs from a short game idea.

The browser may describe a game, but it never chooses a filename, command, or
Unity method.  This module turns a small allowlisted set of choices into the
next available ``gameNN`` spec.  It deliberately keeps the shared Dori
character fixed and only enables mechanics that the current Runner generator
actually implements.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MAX_IDEA_CHARS = 800
MAX_TITLE_CHARS = 60
MAX_GAMES = 10
SHARED_CHARACTER = "Dori_Default"

STYLE_LABELS = {
    "adventure": "도리 어드벤처",
    "treasure": "보물 코인 러시",
    "gravity": "중력 반전 챌린지",
    "speed": "스피드 탈출",
    "endurance": "끝없는 공장",
}
THEMES = ("Factory", "Candy", "Sky", "Forest", "Neon", "Lava")
DIFFICULTIES = ("Easy", "Medium", "Hard")

_GAME_FILE = re.compile(r"^game(?P<number>\d{2})\.json$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class GameCreationError(ValueError):
    """A request cannot be represented by the current game generator."""


@dataclass(frozen=True)
class GamePlan:
    game_id: str
    title: str
    style: str
    style_label: str
    pitch: str
    features: tuple[str, ...]
    spec: dict[str, Any]
    planner: str = "로컬 자동 설계 엔진"

    def as_dict(self) -> dict[str, Any]:
        return {
            "game_id": self.game_id,
            "title": self.title,
            "style": self.style,
            "style_label": self.style_label,
            "pitch": self.pitch,
            "features": list(self.features),
            "character": SHARED_CHARACTER,
            "planner": self.planner,
            "spec": self.spec,
        }


def _text(payload: dict[str, Any], key: str, *, required: bool = False,
          limit: int) -> str:
    value = payload.get(key, "")
    if not isinstance(value, str):
        raise GameCreationError(f"{key} 값은 문자열이어야 합니다.")
    value = " ".join(value.strip().split())
    if _CONTROL.search(value):
        raise GameCreationError(f"{key} 값에 제어 문자를 사용할 수 없습니다.")
    if required and not value:
        raise GameCreationError("만들 게임의 아이디어를 입력하세요.")
    if len(value) > limit:
        raise GameCreationError(f"{key} 값은 {limit}자 이하여야 합니다.")
    return value


def _choice(payload: dict[str, Any], key: str, allowed: tuple[str, ...],
            default: str = "auto") -> str:
    value = payload.get(key, default)
    if not isinstance(value, str) or value not in (default, *allowed):
        raise GameCreationError(f"지원하지 않는 {key} 값입니다.")
    return value


def next_game_id(repo_root: Path) -> str:
    used: set[int] = set()
    specs = repo_root / "GameSpecs"
    if specs.is_dir():
        for path in specs.iterdir():
            match = _GAME_FILE.match(path.name)
            if match:
                used.add(int(match.group("number")))
    for number in range(1, MAX_GAMES + 1):
        if number not in used:
            return f"game{number:02d}"
    raise GameCreationError(
        f"자동 생성 슬롯 {MAX_GAMES}개가 모두 사용 중입니다. 기존 GameSpec을 정리한 뒤 다시 시도하세요."
    )


def _base_spec(repo_root: Path) -> dict[str, Any]:
    path = repo_root / "GameSpecs" / "game01.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GameCreationError("기준 GameSpecs/game01.json을 읽을 수 없습니다.") from exc
    if not isinstance(data, dict):
        raise GameCreationError("기준 GameSpec 형식이 올바르지 않습니다.")
    required = ("game", "player", "mechanics", "level", "energy",
                "runnerProgression", "enemy", "special", "theme")
    if any(not isinstance(data.get(section), dict) for section in required):
        raise GameCreationError("기준 GameSpec에 필요한 설정 구역이 없습니다.")
    return copy.deepcopy(data)


def _contains(idea: str, *needles: str) -> bool:
    lowered = idea.casefold()
    return any(needle.casefold() in lowered for needle in needles)


def _auto_style(idea: str) -> str:
    scores = {
        "treasure": sum(_contains(idea, word) for word in
                        ("코인", "보물", "수집", "gold", "coin", "treasure")),
        "gravity": sum(_contains(idea, word) for word in
                       ("중력", "우주", "반전", "gravity", "space", "flip")),
        "speed": sum(_contains(idea, word) for word in
                     ("속도", "빠른", "추격", "탈출", "speed", "fast", "escape", "chase")),
        "endurance": sum(_contains(idea, word) for word in
                         ("무한", "생존", "오래", "endless", "survival", "long")),
    }
    best = max(scores, key=scores.get)
    return best if scores[best] else "adventure"


def _auto_theme(idea: str, digest: bytes) -> str:
    matches = (
        ("Candy", ("사탕", "디저트", "쿠키", "candy", "dessert", "cookie")),
        ("Sky", ("하늘", "구름", "비행", "sky", "cloud", "flight")),
        ("Forest", ("숲", "정글", "나무", "forest", "jungle", "tree")),
        ("Neon", ("네온", "도시", "사이버", "neon", "city", "cyber")),
        ("Lava", ("불", "용암", "화산", "fire", "lava", "volcano")),
        ("Factory", ("공장", "기계", "factory", "machine")),
    )
    for theme, words in matches:
        if _contains(idea, *words):
            return theme
    return THEMES[digest[1] % len(THEMES)]


def _default_title(style: str, theme: str) -> str:
    theme_ko = {
        "Factory": "팩토리", "Candy": "캔디", "Sky": "스카이",
        "Forest": "포레스트", "Neon": "네온", "Lava": "라바",
    }[theme]
    suffix = {
        "adventure": "런", "treasure": "코인 러시", "gravity": "플립",
        "speed": "대시", "endurance": "서바이벌",
    }[style]
    return f"도리 {theme_ko} {suffix}"


def _tune(spec: dict[str, Any], style: str, difficulty: str,
          digest: bytes) -> tuple[str, ...]:
    player = spec["player"]
    mechanics = spec["mechanics"]
    level = spec["level"]
    energy = spec["energy"]
    progression = spec["runnerProgression"]

    # Small deterministic variation keeps repeated creations distinct while
    # remaining inside values already exercised by Game01.
    variation = (digest[0] / 255.0 - 0.5) * 0.3
    presets: dict[str, dict[str, Any]] = {
        "adventure": dict(speed=6.0, jump=11.0, gravity=3.5, length=150,
                          drain=.032, refill=.05, combo=2.5, fever=20,
                          duration=6.5, multiplier=5, gain=.08, cap=1.45,
                          flip=True, special="GravitySwitch"),
        "treasure": dict(speed=5.8, jump=10.8, gravity=3.3, length=145,
                         drain=.027, refill=.065, combo=3.1, fever=14,
                         duration=7.5, multiplier=6, gain=.06, cap=1.35,
                         flip=False, special="Collectible"),
        "gravity": dict(speed=5.7, jump=11.2, gravity=3.7, length=135,
                        drain=.031, refill=.05, combo=2.6, fever=22,
                        duration=6.0, multiplier=5, gain=.07, cap=1.40,
                        flip=True, special="GravitySwitch"),
        "speed": dict(speed=6.8, jump=10.6, gravity=4.0, length=125,
                      drain=.041, refill=.048, combo=2.0, fever=18,
                      duration=5.5, multiplier=5, gain=.11, cap=1.60,
                      flip=False, special="DoubleJump"),
        "endurance": dict(speed=5.6, jump=11.0, gravity=3.5, length=190,
                          drain=.021, refill=.042, combo=2.8, fever=26,
                          duration=7.0, multiplier=5, gain=.055, cap=1.32,
                          flip=True, special="Checkpoint"),
    }
    chosen = presets[style]
    player.update(moveSpeed=round(chosen["speed"] + variation, 2),
                  jumpPower=chosen["jump"], gravityScale=chosen["gravity"])
    mechanics.update(jump=True, doubleJump=True, slide=True, dash=False,
                     wallJump=False, gravitySwitch=chosen["flip"],
                     teleport=False, timeSlow=False)
    level.update(levelCount=10, difficulty=difficulty, procedural=True,
                 length=chosen["length"])
    energy.update(enabled=True, drainPerSecond=chosen["drain"],
                  refillPerPickup=chosen["refill"])
    progression.update(comboWindow=chosen["combo"],
                       feverPickups=chosen["fever"],
                       feverDuration=chosen["duration"],
                       maxCoinMultiplier=chosen["multiplier"],
                       speedGainPer100m=chosen["gain"],
                       maxSpeedMultiplier=chosen["cap"])
    spec["enemy"].update(enabled=False, types=0)
    spec["special"]["mechanic"] = chosen["special"]

    difficulty_delta = {"Easy": -.35, "Medium": 0.0, "Hard": .45}[difficulty]
    player["moveSpeed"] = round(max(4.8, player["moveSpeed"] + difficulty_delta), 2)
    return (
        "자동 달리기 · 점프 · 이단 점프 · 슬라이드",
        "코인 콤보와 피버 보너스",
        "거리별 속도 상승",
        "10개 스테이지 · 미션 · 성장 · 저장",
        "보상형 광고 · 결제 연결",
        "중력 반전 구간" if chosen["flip"] else "정통 장애물 회피 구간",
    )


def plan_game(repo_root: Path, payload: dict[str, Any], *, game_id: str | None = None) -> GamePlan:
    if not isinstance(payload, dict):
        raise GameCreationError("요청 형식이 올바르지 않습니다.")
    idea = _text(payload, "idea", required=True, limit=MAX_IDEA_CHARS)
    title = _text(payload, "title", limit=MAX_TITLE_CHARS)
    style = _choice(payload, "style", tuple(STYLE_LABELS))
    difficulty = _choice(payload, "difficulty", DIFFICULTIES)
    theme = _choice(payload, "theme", THEMES)
    digest = hashlib.sha256(idea.casefold().encode("utf-8")).digest()

    if style == "auto":
        style = _auto_style(idea)
    if difficulty == "auto":
        difficulty = ("Easy", "Medium", "Hard")[digest[2] % 3]
    if theme == "auto":
        theme = _auto_theme(idea, digest)

    chosen_id = game_id or next_game_id(repo_root)
    spec = _base_spec(repo_root)
    title = title or _default_title(style, theme)
    spec["game"].update(id=chosen_id, title=title, genre="Runner")
    spec["theme"].update(environment=theme, character=SHARED_CHARACTER)
    features = _tune(spec, style, difficulty, digest)
    pitch = (
        f"도리가 {theme} 테마를 달리며 {STYLE_LABELS[style]} 규칙으로 기록과 보상을 쌓는 "
        f"{difficulty} 난이도의 캐주얼 러너입니다."
    )
    return GamePlan(chosen_id, title, style, STYLE_LABELS[style], pitch, features, spec)


def create_game(repo_root: Path, payload: dict[str, Any]) -> GamePlan:
    """Write one new GameSpec atomically and return the exact saved plan."""
    plan = plan_game(repo_root, payload)
    specs = repo_root / "GameSpecs"
    specs.mkdir(parents=True, exist_ok=True)
    target = specs / f"{plan.game_id}.json"
    if target.exists():
        raise GameCreationError(f"{plan.game_id} GameSpec이 이미 존재합니다.")
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(plan.spec, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)
    return plan
