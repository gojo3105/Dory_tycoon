# 참고 게임 분석 — leereilly/games

출처: <https://github.com/leereilly/games> (2026-09-08 분석). 400개 항목 전수 파싱.

## 먼저: 이건 소스 모음이 아니다

사용자 요청은 "소스를 기반으로 만들어"였다. 실제로 열어보니 그게 불가능한 이유가 세 가지다.
셋 다 확인한 사실이고, 추측이 아니다.

### 1. 소스가 없다. 링크 목록이다

한 개의 README에 **400개 항목**이 링크로만 들어 있다. 각 항목은 **각자 다른 저장소**이고
**각자 다른 라이선스**다. "이 소스"라고 부를 하나의 대상이 존재하지 않는다.

목록 자체는 **CC BY-NC-SA 4.0** (비상업). 2025-09-14에 **아카이브**되어 읽기 전용이다.

### 2. 라이선스. 실제로 확인했다

주요 후보 11개의 LICENSE 파일을 직접 받아서 읽었다:

| 게임 | 라이선스 | APK에 코드를 넣을 수 있나 |
|---|---|---|
| 2048 | **MIT** | 가능 (고지 필요) |
| Particle Clicker | **MIT** | 가능 (고지 필요) |
| Matter.js (물리 엔진) | **MIT** | 가능 (고지 필요) |
| cube-composer | **MIT** | 가능 (고지 필요) |
| Hextris | **GPL-3.0** | **불가** |
| SuperTux | **GPL-3.0** | **불가** |
| Pixel Dungeon | **GPL-3.0** | **불가** |
| 0hh1 | **LICENSE 파일 없음** | **불가** (기본값은 전권 보유) |
| Mario-Level-1 | **LICENSE 파일 없음** | **불가** (게다가 닌텐도 IP) |
| OpenRCT2 / OpenTTD | 제가 확인한 경로에 없음 | 확인 전까지 불가 |

**GPL 코드를 APK에 넣으면 APK 전체가 GPL이 된다.** 소스 공개 의무가 생기고, 스토어 배포
조건과 충돌한다. 목록에서 가장 완성도 높은 게임들이 대부분 GPL이다 — 우연이 아니라,
오픈소스 게임이 오래 유지되는 방식이 GPL이기 때문이다.

라이선스 파일이 **없는** 것이 "자유롭게 써도 된다"는 뜻이 아니다. 기본값은 저작권자 전권
보유이고, 이 프로젝트 `LICENSE_REGISTRY.json`의 규칙이 바로 그것이다 (CLAUDE.md 8절).

### 3. 언어와 엔진이 다르다

이 프로젝트는 **Unity / C# / Android 세로화면**이다. 목록의 분포는:

| 구분 | 개수 | 기술 |
|---|---|---|
| Browser-Based | 151 | JavaScript / HTML5 / Canvas / Phaser |
| Native | 126 | C++ / Python / Lua / Godot |
| Frameworks/Engines | 66 | 대부분 JS 라이브러리 |
| Mobile | 24 | Android(Cocos2d, Java) / iOS(Cocos2D) |
| 기타 | 33 | 맵, 플러그인, 소스 덤프 |

계획된 장르와 겹치는 것은 139개인데, **Unity/C# 프로젝트는 사실상 없다.** JS를 C#으로
옮기는 것은 재사용이 아니라 재작성이다. 붙여넣어서 되는 코드가 아니다.

### 4. 가장 깊은 문제: Game Factory를 부순다

CLAUDE.md 1절: *"게임마다 새 코드를 짜지 않는다. Core/Modules/Gameplay + GameSpec 조합으로
생성한다."* 6절: *"캐릭터/배경만 바꾼 리스킨 금지."*

게임마다 다른 오픈소스를 가져와 고치는 방식은 **게임 10개가 아니라 서로 안 닮은 프로젝트
10개**를 만든다. 그러면 이 저장소가 존재하는 이유인 생성기·검증·빌드 파이프라인이 아무것도
재사용하지 못한다. 두 번째 게임부터 손해가 나기 시작한다.

## 그래서 이 목록을 어떻게 쓰는가

**코드를 가져오는 대상이 아니라, 무엇을 만들지 정하는 자료로 쓴다.**
검증된 메커닉이 이미 400개 모여 있다는 점이 이 목록의 진짜 가치다.

지금 진짜 병목은 코드 출처가 아니다. **모듈이 비어 있다는 것**이다:

| Modules 폴더 | C# 파일 |
|---|---|
| GravitySwitch | 3개 |
| Boss · Collectible · Dash · DoubleJump · Enemy · FallingPlatform · MovingPlatform · Weapon | **전부 0개** |

| Gameplay 폴더 | C# 파일 |
|---|---|
| Runner | 12개 |
| Idle · Puzzle · Physics · Defense | **전부 0개** |

Game02~10은 아트나 아이디어가 없어서 멈춴 게 아니다. **장르 코드와 모듈이 없어서** 멈춰 있다.
아래 표는 각 게임에 필요한 모듈을 참고 게임에서 역산한 것이다.

## Game02~10 매핑

라이선스 칸은 **읽어도 되는 근거**를 뜻한다. MIT는 코드를 봐도 되고 고지하면 인용도 되지만,
**아래 어느 경우에도 소스를 붙여넣지 않는다** — 메커닉을 읽고 C#으로 새로 쓴다.

| # | 게임 | 참고할 게임 | 라이선스 | 배울 것 | 새로 필요한 모듈 |
|---|---|---|---|---|---|
| 02 | Idle Factory Tycoon | **Particle Clicker** | MIT | 오프라인 누적 계산, 업그레이드 곡선, 탭 보너스 | `Gameplay/Idle`, `Modules/Producer`, `Modules/UpgradeTree` |
| 03 | Gravity Blocks | **cube-composer**, Infectors, Ned Et Les Maki | MIT / 미확인 | 소코반 되돌리기, 스테이지 클리어 판정, 이동 규칙 | `Gameplay/Puzzle`, `Modules/GridBoard`, `Modules/UndoStack` (`GravitySwitch` 재사용) |
| 04 | Roll & Balance | **Descensus 2**, Polly-B-Gone, Gish | 미확인 | 기울어지는 발판, 공 굴리기 감각, 물리 코스 설계 | `Gameplay/Physics`, `Modules/TiltInput`, `Modules/RollingBody` |
| 05 | Factory Defense | Tower Defense (Three.js), Grave Robbers | 미확인 | 경로 추종, 웨이브 편성, 사거리·쿨다운 | `Gameplay/Defense`, `Modules/PathFollower`, `Modules/Turret`, `Modules/WaveSpawner` |
| 06 | Merge Workshop | **2048**, Hex 2048, Couch 2048 | MIT / 미확인 | 그리드 병합 규칙, 이동 애니메이션, 막힘 판정 | `Modules/GridBoard` (03과 공유), `Modules/MergeRule` |
| 07 | Relic Collector | Color Quest, Green Wall | 미확인 | 소규모 탐색 맵, 제한시간 수집 | `Modules/Collectible` (폴더만 있음), `Modules/Timer` |
| 08 | Delivery Dash | Monkey Rally, OSGG | 미확인 | 경량 차량 조작, 순서 있는 목표 | `Modules/VehicleController`, `Modules/ObjectiveQueue` |
| 09 | Survive the Swarm | OpenNotrium, Ancient Beast | 미확인 | 다수 적 스폰, 자동 공격 사거리 | `Modules/Enemy` (폴더만), `Modules/AutoAttack`, `Core/PoolSystem` 재사용 |
| 10 | Sky Guardian | Survivor, Wannabe Tempest | 미확인 | 세로 스크롤 탄막, 적 패턴 | `Modules/Weapon` (폴더만), `Modules/Projectile`, `Modules/EnemyPattern` |

### 이 표에서 나온 결론 두 가지

**`Modules/GridBoard`를 Game03과 Game06이 공유한다.** 퍼즐과 머지는 둘 다 그리드다. 이게
Game Factory가 실제로 이득을 보는 지점이고, 서로 다른 오픈소스를 각각 가져왔다면 절대
공유되지 않았을 부분이다.

**`Modules/Collectible`, `Enemy`, `Weapon`은 이미 폴더가 있다.** 계획 단계에서 필요하다고
판단해서 만들어둔 자리다. Game07/09/10이 그걸 채운다.

## 실제로 쓸 수 있는 것 하나

**Matter.js (MIT)** — 참고 목록 중 유일하게 이 프로젝트에 직접 도움이 되는 항목인데,
게임이 아니라 2D 물리 엔진이다. 그런데 Unity에는 이미 Box2D 기반 Rigidbody2D가 들어 있고,
CLAUDE.md는 외부 패키지 추가를 최소화하라고 못박고 있다. **가져올 필요 없다** — Game04
설계에서 물리 파라미터를 잡을 때 문서를 읽는 정도가 적정하다.

## 하지 말 것

- **GPL 게임의 코드를 참고하며 C#을 쓰지 않는다.** 파생 저작물 판단은 붙여넣기 여부가 아니라
  실질적 유사성으로 갈린다. GPL 게임은 *플레이해보는* 것까지가 안전하다.
- **닌텐도·세가 등의 IP 클론(Mario-Level-1, Commander Genius 등)은 메커닉만 봐도
  캐릭터·레벨·음악은 절대 가져오지 않는다.**
- **라이선스 미확인 항목을 "아마 괜찮다"로 넘기지 않는다.** 8절이 금지한다. 쓸 일이 생기면
  그때 그 저장소의 LICENSE를 직접 읽고 `LICENSE_REGISTRY.json`에 항목을 만든다.

## 다음 작업

Game02가 가장 먼저다. 계획서상 **실패 조건이 없는 유일한 게임**이라 다른 9개와 확실히
구분되고, `Gameplay/Idle` + `Producer` + `UpgradeTree`만 있으면 성립한다. 참고 게임도
유일하게 MIT다 (Particle Clicker).
