"""Create safe mobile GameSpecs from a short game idea.

The browser may describe a game, but it never chooses a filename, command, or
Unity method.  This module turns a small allowlisted set of choices into the
next available ``gameNN`` spec.  It deliberately keeps the shared Dori
character fixed while allowing the factory to plan several mobile genres.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import subprocess
from dataclasses import dataclass, replace
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
    "combat": "액션 전투",
    "collection": "수집 성장",
    "management": "경영 자동화",
    "merge": "머지 강화",
    "roguelite": "로그라이트 도전",
    "rhythm": "리듬 콤보",
}
GENRE_LABELS = {
    "runner": "Runner",
    "rpg": "RPG",
    "fps": "FPS",
    "idle": "Idle",
    "puzzle": "Puzzle",
    "defense": "Defense",
    "arcade": "Arcade",
    "simulation": "Simulation",
}
THEMES = (
    "Factory", "Candy", "Sky", "Forest", "Neon", "Lava", "Ocean", "Space",
    "Desert", "Ice", "Office", "Dungeon", "Kingdom",
)
DIFFICULTIES = ("Relaxed", "Easy", "Medium", "Hard", "Expert")

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
    genre: str
    genre_label: str
    pitch: str
    features: tuple[str, ...]
    spec: dict[str, Any]
    idea: str
    git_branch: str
    git_status: str
    requirements_path: str
    planner: str = "로컬 자동 설계 엔진"

    def as_dict(self) -> dict[str, Any]:
        return {
            "game_id": self.game_id,
            "title": self.title,
            "style": self.style,
            "style_label": self.style_label,
            "genre": self.genre,
            "genre_label": self.genre_label,
            "pitch": self.pitch,
            "features": list(self.features),
            "character": SHARED_CHARACTER,
            "git_branch": self.git_branch,
            "git_status": self.git_status,
            "requirements_path": self.requirements_path,
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


def _auto_style(idea: str, digest: bytes) -> str:
    scores = {
        "treasure": sum(_contains(idea, word) for word in
                        ("코인", "보물", "수집", "gold", "coin", "treasure")),
        "gravity": sum(_contains(idea, word) for word in
                       ("중력", "우주", "반전", "gravity", "space", "flip")),
        "speed": sum(_contains(idea, word) for word in
                     ("속도", "빠른", "추격", "탈출", "speed", "fast", "escape", "chase")),
        "endurance": sum(_contains(idea, word) for word in
                         ("무한", "생존", "오래", "endless", "survival", "long")),
        "combat": sum(_contains(idea, word) for word in
                      ("전투", "액션", "타격", "검", "battle", "combat", "action")),
        "collection": sum(_contains(idea, word) for word in
                          ("도감", "펫", "캐릭터", "카드", "collection", "pet")),
        "management": sum(_contains(idea, word) for word in
                          ("경영", "공장", "회사", "운영", "management", "tycoon")),
        "merge": sum(_contains(idea, word) for word in
                     ("머지", "합성", "강화", "merge", "combine")),
        "roguelite": sum(_contains(idea, word) for word in
                         ("로그", "랜덤", "던전", "rogue", "random", "dungeon")),
        "rhythm": sum(_contains(idea, word) for word in
                      ("리듬", "음악", "박자", "rhythm", "music", "beat")),
    }
    best = max(scores, key=scores.get)
    if scores[best]:
        return best
    choices = tuple(STYLE_LABELS)
    return choices[digest[0] % len(choices)]


def _auto_genre(idea: str, digest: bytes) -> str:
    scores = {
        "rpg": sum(_contains(idea, word) for word in
                   ("rpg", "역할", "퀘스트", "장비", "레벨업", "스킬", "모험")),
        "fps": sum(_contains(idea, word) for word in
                   ("fps", "총", "슈팅", "사격", "shooter", "gun", "shoot")),
        "idle": sum(_contains(idea, word) for word in
                    ("idle", "방치", "자동", "키우기", "타이쿤", "수익")),
        "puzzle": sum(_contains(idea, word) for word in
                      ("퍼즐", "매치", "두뇌", "블록", "puzzle", "match")),
        "defense": sum(_contains(idea, word) for word in
                       ("디펜스", "방어", "타워", "wave", "defense", "tower")),
        "simulation": sum(_contains(idea, word) for word in
                          ("시뮬", "경영", "농장", "도시", "simulation", "farm")),
        "arcade": sum(_contains(idea, word) for word in
                      ("아케이드", "짧은", "미니게임", "arcade")),
        "runner": sum(_contains(idea, word) for word in
                      ("러너", "달리", "점프", "쿠키런", "runner", "run")),
    }
    best = max(scores, key=scores.get)
    if scores[best]:
        return best
    choices = tuple(GENRE_LABELS)
    return choices[digest[1] % len(choices)]


def _auto_theme(idea: str, digest: bytes) -> str:
    matches = (
        ("Candy", ("사탕", "디저트", "쿠키", "candy", "dessert", "cookie")),
        ("Sky", ("하늘", "구름", "비행", "sky", "cloud", "flight")),
        ("Forest", ("숲", "정글", "나무", "forest", "jungle", "tree")),
        ("Neon", ("네온", "도시", "사이버", "neon", "city", "cyber")),
        ("Lava", ("불", "용암", "화산", "fire", "lava", "volcano")),
        ("Ocean", ("바다", "해적", "물", "ocean", "sea", "pirate")),
        ("Space", ("우주", "은하", "행성", "space", "galaxy", "planet")),
        ("Desert", ("사막", "모래", "desert", "sand")),
        ("Ice", ("얼음", "눈", "겨울", "ice", "snow", "winter")),
        ("Office", ("사무실", "회사", "office", "company")),
        ("Dungeon", ("던전", "몬스터", "dungeon", "monster")),
        ("Kingdom", ("왕국", "기사", "성", "kingdom", "knight", "castle")),
        ("Factory", ("공장", "기계", "factory", "machine")),
    )
    for theme, words in matches:
        if _contains(idea, *words):
            return theme
    return THEMES[digest[1] % len(THEMES)]


def _default_title(style: str, theme: str, genre: str) -> str:
    theme_ko = {
        "Factory": "팩토리", "Candy": "캔디", "Sky": "스카이",
        "Forest": "포레스트", "Neon": "네온", "Lava": "라바",
        "Ocean": "오션", "Space": "스페이스", "Desert": "데저트",
        "Ice": "아이스", "Office": "오피스", "Dungeon": "던전",
        "Kingdom": "킹덤",
    }[theme]
    genre_suffix = {
        "rpg": "퀘스트",
        "fps": "슈터",
        "idle": "방치 타이쿤",
        "puzzle": "퍼즐",
        "defense": "디펜스",
        "arcade": "아케이드",
        "simulation": "시뮬",
    }
    if genre in genre_suffix:
        return f"도리 {theme_ko} {genre_suffix[genre]}"
    suffix = {
        "adventure": "런", "treasure": "코인 러시", "gravity": "플립",
        "speed": "대시", "endurance": "서바이벌", "combat": "배틀",
        "collection": "컬렉션", "management": "타이쿤", "merge": "머지",
        "roguelite": "던전", "rhythm": "비트",
    }[style]
    return f"도리 {theme_ko} {suffix}"


def _branch_name(game_id: str) -> str:
    return f"ai-game/{game_id}"


def _requirements_rel(game_id: str) -> str:
    return f"GameSpecs/{game_id}_REQUIREMENTS.md"


def _run_git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=30,
        check=False,
    )


def _prepare_git_branch(repo_root: Path, branch: str) -> str:
    """Create and switch to the per-game branch when this is a Git checkout."""
    if not (repo_root / ".git").exists():
        return "not_git"

    current = _run_git(repo_root, "rev-parse", "--abbrev-ref", "HEAD")
    if current.returncode != 0:
        raise GameCreationError("Git 현재 브랜치를 확인할 수 없습니다.")
    if current.stdout.strip() == branch:
        return "already_on_branch"

    exists = _run_git(repo_root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
    if exists.returncode == 0:
        switched = _run_git(repo_root, "switch", branch)
        if switched.returncode != 0:
            raise GameCreationError(f"Git 브랜치 전환 실패: {switched.stderr.strip()}")
        return "switched_existing_branch"

    created = _run_git(repo_root, "switch", "-c", branch)
    if created.returncode != 0:
        raise GameCreationError(f"Git 새 브랜치 생성 실패: {created.stderr.strip()}")
    return "created_and_switched"


def _requirements_markdown(plan: GamePlan) -> str:
    features = "\n".join(f"- {feature}" for feature in plan.features)
    return f"""# {plan.title} 필수 제작 지침

- Game ID: `{plan.game_id}`
- Git Branch: `{plan.git_branch}`
- GameSpec: `GameSpecs/{plan.game_id}.json`
- 장르: {plan.genre_label}
- 플레이 스타일: {plan.style_label}
- 난이도: {plan.spec['level']['difficulty']}
- 세계관: {plan.spec['theme']['environment']}
- 공용 캐릭터: `{SHARED_CHARACTER}`

## 제작 요청

{plan.idea}

## 꼭 지켜야 하는 필수 부분

- 모바일 Portrait 기준으로 한 손 터치 UI를 우선한다.
- 도리 캐릭터 정체성은 유지한다. 캐릭터 변경은 Gemini 캐릭터 에셋 단계로만 한다.
- 장르별 핵심 루프가 첫 10초 안에 이해되어야 한다.
- 저장, 튜토리얼, 설정, 미션/보상, 성장 요소를 GameSpec과 UI에 반영한다.
- 광고/결제는 무료 정책을 넘지 않는 연결 지점만 만든다.
- 승인되지 않은 외부 에셋, 유료 API, 유료 모델을 자동으로 사용하지 않는다.
- Unity 테스트와 APK 빌드 결과 없이는 출시 가능으로 표시하지 않는다.

## 자동 설계 핵심 기능

{features}
"""


def _write_requirements(repo_root: Path, plan: GamePlan) -> None:
    target = repo_root / plan.requirements_path
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(_requirements_markdown(plan), encoding="utf-8")
    temporary.replace(target)


def _tune(spec: dict[str, Any], style: str, difficulty: str,
          digest: bytes, genre: str) -> tuple[str, ...]:
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
        "combat": dict(speed=5.9, jump=10.4, gravity=3.6, length=145,
                       drain=.036, refill=.052, combo=2.2, fever=18,
                       duration=6.0, multiplier=5, gain=.08, cap=1.38,
                       flip=False, special="SkillCombo"),
        "collection": dict(speed=5.5, jump=10.5, gravity=3.2, length=160,
                           drain=.025, refill=.07, combo=3.4, fever=13,
                           duration=8.0, multiplier=7, gain=.05, cap=1.25,
                           flip=False, special="CollectionBonus"),
        "management": dict(speed=5.2, jump=10.0, gravity=3.1, length=175,
                           drain=.02, refill=.06, combo=3.0, fever=24,
                           duration=7.0, multiplier=5, gain=.045, cap=1.22,
                           flip=False, special="AutomationBoost"),
        "merge": dict(speed=5.4, jump=10.3, gravity=3.3, length=150,
                      drain=.026, refill=.058, combo=2.9, fever=16,
                      duration=7.0, multiplier=6, gain=.055, cap=1.30,
                      flip=False, special="MergeUpgrade"),
        "roguelite": dict(speed=6.1, jump=11.1, gravity=3.8, length=130,
                          drain=.038, refill=.045, combo=2.1, fever=20,
                          duration=5.8, multiplier=5, gain=.095, cap=1.48,
                          flip=True, special="RandomRelic"),
        "rhythm": dict(speed=6.3, jump=10.7, gravity=3.4, length=140,
                       drain=.03, refill=.06, combo=1.6, fever=12,
                       duration=8.5, multiplier=7, gain=.075, cap=1.42,
                       flip=False, special="RhythmCombo"),
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

    difficulty_delta = {
        "Relaxed": -.6, "Easy": -.35, "Medium": 0.0, "Hard": .45, "Expert": .75,
    }[difficulty]
    player["moveSpeed"] = round(max(4.8, player["moveSpeed"] + difficulty_delta), 2)
    if genre == "runner":
        return (
            "자동 달리기 · 점프 · 이단 점프 · 슬라이드",
            "코인 콤보와 피버 보너스",
            "거리별 속도 상승",
            "10개 스테이지 · 미션 · 성장 · 저장",
            "보상형 광고 · 결제 연결",
            "중력 반전 구간" if chosen["flip"] else "정통 장애물 회피 구간",
        )

    genre_tuning: dict[str, tuple[dict[str, Any], tuple[str, ...], str]] = {
        "rpg": (
            dict(jump=False, doubleJump=False, slide=False, dash=True,
                 wallJump=False, gravitySwitch=False, teleport=False, timeSlow=False),
            ("퀘스트 챕터", "장비 성장", "스킬 전투", "보스 스테이지", "일일 보상"),
            "SkillCombo",
        ),
        "fps": (
            dict(jump=True, doubleJump=False, slide=True, dash=True,
                 wallJump=False, gravitySwitch=False, teleport=False, timeSlow=True),
            ("터치 조준", "엄폐와 슬라이드", "웨이브 슈팅", "무기 강화", "짧은 세션 PvE"),
            "AimAssist",
        ),
        "idle": (
            dict(jump=False, doubleJump=False, slide=False, dash=False,
                 wallJump=False, gravitySwitch=False, teleport=False, timeSlow=False),
            ("오프라인 보상", "자동 생산 라인", "업그레이드 루프", "미션 보상", "광고 부스터"),
            "OfflineEarnings",
        ),
        "puzzle": (
            dict(jump=False, doubleJump=False, slide=False, dash=False,
                 wallJump=False, gravitySwitch=False, teleport=False, timeSlow=True),
            ("스테이지 퍼즐", "연쇄 콤보", "힌트 아이템", "별점 보상", "일일 챌린지"),
            "ComboPuzzle",
        ),
        "defense": (
            dict(jump=False, doubleJump=False, slide=False, dash=True,
                 wallJump=False, gravitySwitch=False, teleport=False, timeSlow=False),
            ("웨이브 방어", "타워 배치", "영웅 스킬", "보스 웨이브", "자원 회수"),
            "TowerDefense",
        ),
        "arcade": (
            dict(jump=True, doubleJump=True, slide=True, dash=True,
                 wallJump=False, gravitySwitch=chosen["flip"], teleport=False, timeSlow=False),
            ("한 판 60초", "콤보 점수", "랭킹 경쟁", "스킨 보상", "즉시 재도전"),
            "ScoreAttack",
        ),
        "simulation": (
            dict(jump=False, doubleJump=False, slide=False, dash=False,
                 wallJump=False, gravitySwitch=False, teleport=False, timeSlow=False),
            ("공간 확장", "직원 배치", "생산 자동화", "수익 성장", "컬렉션 보상"),
            "ManagementLoop",
        ),
    }
    mechanics.update(genre_tuning[genre][0])
    spec["enemy"].update(enabled=genre in {"rpg", "fps", "defense"}, types=3)
    spec["special"]["mechanic"] = genre_tuning[genre][2]
    progression.update(comboWindow=chosen["combo"], feverPickups=chosen["fever"],
                       feverDuration=chosen["duration"], maxCoinMultiplier=chosen["multiplier"])
    return (
        *genre_tuning[genre][1],
        "모바일 세션 · 저장 · 튜토리얼 · 설정",
        "보상형 광고 · 결제 연결",
    )


def plan_game(repo_root: Path, payload: dict[str, Any], *, game_id: str | None = None) -> GamePlan:
    if not isinstance(payload, dict):
        raise GameCreationError("요청 형식이 올바르지 않습니다.")
    idea = _text(payload, "idea", required=True, limit=MAX_IDEA_CHARS)
    title = _text(payload, "title", limit=MAX_TITLE_CHARS)
    style = _choice(payload, "style", tuple(STYLE_LABELS))
    genre = _choice(payload, "genre", tuple(GENRE_LABELS))
    difficulty = _choice(payload, "difficulty", DIFFICULTIES)
    theme = _choice(payload, "theme", THEMES)
    digest = hashlib.sha256(idea.casefold().encode("utf-8")).digest()

    if style == "auto":
        style = _auto_style(idea, digest)
    if genre == "auto":
        genre = _auto_genre(idea, digest)
    if difficulty == "auto":
        difficulty = DIFFICULTIES[digest[2] % len(DIFFICULTIES)]
    if theme == "auto":
        theme = _auto_theme(idea, digest)

    chosen_id = game_id or next_game_id(repo_root)
    spec = _base_spec(repo_root)
    title = title or _default_title(style, theme, genre)
    branch = _branch_name(chosen_id)
    requirements_path = _requirements_rel(chosen_id)
    spec["game"].update(
        id=chosen_id,
        title=title,
        genre=GENRE_LABELS[genre],
        gitBranch=branch,
        requirementsDoc=requirements_path,
    )
    spec["theme"].update(environment=theme, character=SHARED_CHARACTER)
    features = _tune(spec, style, difficulty, digest, genre)
    pitch = (
        f"도리가 {theme} 테마에서 {STYLE_LABELS[style]} 감성을 가진 "
        f"{GENRE_LABELS[genre]} 모바일 게임으로 성장과 보상을 쌓습니다. "
        f"난이도는 {difficulty}입니다."
    )
    return GamePlan(chosen_id, title, style, STYLE_LABELS[style],
                    genre, GENRE_LABELS[genre], pitch, features, spec,
                    idea, branch, "planned", requirements_path)


def create_game(repo_root: Path, payload: dict[str, Any]) -> GamePlan:
    """Write one new GameSpec atomically and return the exact saved plan."""
    plan = plan_game(repo_root, payload)
    git_status = _prepare_git_branch(repo_root, plan.git_branch)
    plan = replace(plan, git_status=git_status)
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
    _write_requirements(repo_root, plan)
    return plan
