# CLAUDE.md

Claude Code 작업 규칙. **현재 상태/진행 상황은 `HANDOVER.md`**, 규칙은 이 파일.

## 1. 프로젝트

| 항목 | 값 |
|---|---|
| 정체 | **Game Factory** (Godot "Dory Tycoon" 아님) |
| 목적 | GameSpec(JSON) 하나로 Android 게임 자동 생성 · 테스트 · 빌드 |
| 파이프라인 | GameSpec → Editor 생성기 → Scene/Prefab/Level/UI → Validate → Unity Test → APK/AAB |
| 핵심 원칙 | 게임마다 새 코드를 짜지 않는다. Core/Modules/Gameplay + GameSpec 조합으로 "생성"한다 |
| 목표 | 게임 10개 (`GAME_10_MASTER_PLAN.md`) |

## 2. 역할 분담 (2026-09-02 사용자 지시, 고정)

| # | 규칙 | 정책 키 |
|---|---|---|
| 1 | **모든 코딩은 Codex.** Claude는 `AI_GAME_COMPANY/config/TASKBOARD.json` 명세 작성 · diff 리뷰 · 설정/문서만 | `codex_writes_all_code` |
| 2 | **디자인은 Gemini 무료 등급.** 키는 `GEMINI_API_KEY` | `allow_gemini_design` |
| 3 | **관제 화면은 AI를 회사 부서별 캐릭터로 표시** (`docs/OFFICE_VIEW_SPEC.md`) | - |

### 규칙 1의 예외

- 사용자가 직접 지시한 건에 한해 Claude가 코드를 쓴다. 작업판 notes에 `DONE BY CLAUDE`로 기록.
- 기록된 예외: 진행상황 한글 요약 / 작업 대기열 (09-03), 개발시스템 전체 수정 (09-03),
  점프·애니메이션 수정 (09-05), 슬라이드·이단점프 (09-05), 캐릭터 리그 배선 (09-06 - 그림 자체는 Gemini 몫),
  제어판 명령창 (09-07 - §10.1. 이게 규칙 1을 **깨는** 게 아니라 **자동화**한다: 지시가 작업판 명세가 되고 Codex가 짠다).

### 알아둘 제약

- `codex` CLI는 **빌드 PC에만 있다.** Claude Code 컨테이너에는 없다.
- 따라서 Claude는 **작업을 큐에 넣는 것까지만** 가능. PC에서 `team run --task <id>`를 돌려야 코드가 생긴다.
- "Codex에게 맡겼다" 이후 저장소에 코드가 없으면 → 아직 실행 안 된 것. 버그 아님.

### Gemini 제약 (문서가 아니라 코드로 강제)

- 키는 `GEMINI_API_KEY` 전용. `GOOGLE_API_KEY`는 `blocked_env_keys`에 그대로 둔다.
- `gemini_free_tier_models`에 없는 모델 id는 어댑터가 거부.
- 429/쿼터 소진 시 승격 금지. `on_codex_limit`과 동일하게 degrade.
- 키 값은 커밋/로그/출력 금지. **존재 여부만** 기록. 로그인은 HUMAN_GATE `initial_gemini_login`.
- `ai.google.dev`가 이 환경 프록시에서 차단됨 → 무료 등급을 1차 출처로 확인 못 함. 가정하지 말고 실패시킬 것.

## 3. 개발 환경

| 항목 | 값 |
|---|---|
| 엔진 | Unity (버전은 `ProjectSettings/ProjectVersion.txt`, 임의 변경 금지) |
| 언어 | C# |
| 플랫폼 | Android portrait 우선, iOS 확장 가능 구조 |
| 물리 | 2D (Rigidbody2D / Collider2D) |
| UI | uGUI. 추가 패키지는 꼭 필요할 때만 |
| CI/CD | GitHub Actions + Windows self-hosted runner (Unity 배치 모드) |

## 4. 폴더 구조

```text
Assets/GameFactory/
  Core/            공용 시스템 (GameManager, SaveSystem, PoolSystem, InputSystem, GameSpec/)
  Gameplay/        장르별 게임플레이 (Runner, Puzzle, Physics, Idle, Defense)
  Modules/         재사용 기믹 모듈 (GravitySwitch, Dash, ...)
  UI/              게임 상태와 분리된 UI 컨트롤러
  LevelGeneration/ 절차적 레벨 생성
  Editor/          생성/검증/빌드 Editor 스크립트
  Tests/           EditMode / PlayMode
  Build/           빌드 자동화 보조
GameSpecs/         게임별 GameSpec 원본 JSON (+ .ci-trigger, JSON 아님)
GeneratedGames/    생성 산출물 메타데이터
Builds/ Logs/      git 미포함 (폴더만 유지)
.github/workflows/ validate / test / build-android / game-factory / pc-control
docs/              ARCHITECTURE, GAME_SPEC, AUTOMATION, BUILD, OFFICE_VIEW_SPEC
```

## 5. GameSpec

- 필드 정의의 **단일 소스**는 `Assets/GameFactory/Core/GameSpec/GameSpecData.cs`.
- 전체 필드 표와 예시는 `docs/GAME_SPEC.md`.
- JsonUtility 파싱 → **중첩 객체만.** Dictionary / 다형성 금지.
- `game.id`는 lowercase snake_case, 저장소 전체에서 유일.
- 새 필드 추가 시 `GameSpecData.cs` · `GameSpecValidator.cs` · `docs/GAME_SPEC.md` **함께** 갱신.
- 구조 검증 = `GameSpecValidator`(Core) / 파일 간 검증(중복 id, Bundle ID 충돌) = `Editor/GameValidator.cs`.

## 6. 장르 / 모듈

- 1차 지원: Runner, Puzzle, Physics. 구조는 Idle/Defense/Merge/Arcade/Shooter까지 확장 가능하게 유지.
- 기믹은 `Modules/<ModuleName>/`에 독립 모듈로 구현. 새 게임은 기존 모듈 조합 우선.
- **캐릭터/배경만 바꾼 리스킨 금지.** mechanics/level/enemy/special 조합이 실제로 달라져야 한다.

## 7. 코딩 규칙

- 최신 안정 API 사용 (`Rigidbody2D.linearVelocity`, deprecated `velocity` 금지).
- 명시적 타입 사용.
- 한 스크립트에 한 책임. UI / 게임 상태(`Core.GameManager`) / 캐릭터·기믹 상태를 분리.
- GameSpec 값 하드코딩 금지. **구조 배선 = `SetReferences`/`SetTargets`(에디터 타임), 수치 = `Configure`(런타임).**
- Singleton은 `GameManager`, `AudioManager` 등 최소한만.
- Instantiate/Destroy 대신 `Core/PoolSystem.cs` 사용.
- Update/FixedUpdate 호출 수를 늘리지 않는다. 입력은 `Core/InputSystem.cs` 하나로 통일.
- 저사양 Android 기준으로 Draw Call / Texture Size / 동시 오브젝트 수를 고려.

## 8. 금지 사항

- 정상 동작하는 코드를 이유 없이 재작성
- 대규모 파일 삭제 (필요하면 **먼저 사용자 확인**)
- Unity `.meta` 파일 임의 삭제
- `Library/`, `Temp/`, `Obj/` 커밋
- keystore · 서명 비밀번호 · API 키 커밋 (존재 여부만 기록)
- Unity 버전 임의 변경
- **APK가 생성되지 않았는데 "빌드 성공" 보고** — 제1원칙
- 사용자 허락 없는 커밋/푸시

## 9. Unity 빌드 / 테스트

### 9.1 이 컨테이너의 한계

Unity Editor 없음 → Editor 실행 · 컴파일 확인 · Play 모드 · Android 빌드 **불가**.
대신 C# 문법과 스크립트 간 참조(네임스페이스/시그니처/컴포넌트명)를 수동 검토하고,
**"실행하지 못했다"를 명시**한다. 실제 검증은 Unity 설치 환경 (`docs/BUILD.md`, `docs/AUTOMATION.md`).

### 9.2 PC 상태를 읽는 4개 파일

**"에러 붙여주세요" / "APK 확인해주세요"를 말하기 전에 반드시 먼저 읽는다.**

| 파일 | 내용 | 생성 |
|---|---|---|
| `Reports/errors/latest.txt` | 컴파일 에러, CS0618, 런타임 예외 | `scripts/dev/collect-errors.ps1` |
| `Reports/build-status/latest.txt` | 디스크에 실제로 있는 APK/AAB | `scripts/dev/report-build-status.ps1` |
| `Reports/runs/latest.txt` | 오케스트레이터 실행 (명령/종료코드/시간/출력/예외), 최근 50개 보관 | `company/orchestrator/runlog.py` |
| `Reports/sync-status/latest.txt` | 자동 동기화 OK/UP-TO-DATE/BLOCKED/FAILED + 이유 | `scripts/desktop/sync-and-run.ps1` |

- 자동 동기화는 15분 주기. `sync-status`가 **7시간 넘게 갱신 안 되면 예약 작업이 죽은 것.**
- 비밀 값은 기록 전에 `[REDACTED:<이름>]`으로 치환된다. 기록 실패는 명령의 종료 코드를 바꾸지 않는다.
- 대시보드 "PC 연결" 섹션이 같은 파일들을 보여준다.

### 9.3 반복해서 틀렸던 것

| 증상 | 원인 | 대응 |
|---|---|---|
| CI는 실패하는데 리포트가 비어 있음 | 작업 디렉터리가 둘 (`C:\Dory_tycoon` vs `C:\actions-runner\_work\Dory_tycoon\Dory_tycoon`) | 두 리포트 스크립트의 `-CiWorkspacePath` 확인. 정션으로 합치지 말 것 (`actions/checkout`의 `git clean`이 미커밋 작업을 날린다) |
| `CS1069: type name ... not found` | 새 Unity 내장 모듈 미등록 | `Packages/manifest.json`에 모듈 추가. `packages-lock.json`은 손대지 말고 Unity가 재생성 |
| 에러 목록이 짧아서 문제가 그것뿐인 줄 앎 | `GameFactory.Editor.asmdef` → `GameFactory.Runtime` 참조. Runtime이 깨지면 Editor는 컴파일조차 안 됨 | Runtime 에러부터 고치고 다시 확인 |
| "실패" 보고가 실제로는 오탐 | 작업 성과를 파일 변경으로만 판단 | **실패 보고가 나오면 정말 실패인지 먼저 의심.** 09-03 다섯 건 전부 오탐이었다 |

### 9.4 포크에서 최신 리포트 읽기

upstream에 아직 병합 전일 수 있으므로 사용자 포크에서 직접 읽는다.

```bash
git fetch https://github.com/gojo3105/dory_tycoon claude/delete-current-content-mgn4xm:fork-check
git show fork-check:Reports/errors/latest.txt
git show fork-check:Reports/build-status/latest.txt
git show fork-check:Reports/sync-status/latest.txt
git show fork-check:Reports/runs/latest.txt
git branch -D fork-check
```

## 10. 관제 화면

```bash
python -m company.orchestrator.main dashboard --open   # 읽기 전용 HTML
python -m company.orchestrator.main serve              # 제어판 (버튼 실제 동작)
python -m company.orchestrator.main character --status # 캐릭터 리그: 키 게이트 + 디스크 상태
```

- `AI_GAME_COMPANY/` 안에서 실행. 루트에서 치면 `No module named 'company'`.
- PC에서는 `scripts/desktop/open-control-panel.ps1` 하나로 (pull → cd → serve → 브라우저).
- `dashboard`는 커밋된 파일만 읽고 이미지를 data URI로 박아 넣는다 → 어디서 열어도 같은 결과.
- **정적 파일에는 버튼도 명령창도 나오지 않는다.** 누를 곳 없는 버튼은 없는 게 낫다.
  이건 텍스트 상자에도 똑같이 적용된다 — 지시를 받아먹고 아무것도 안 하는 입력창이 제일 나쁘다.

`server.py`가 코드로 강제하는 4가지:

| 항목 | 내용 |
|---|---|
| 바인딩 | 127.0.0.1 전용 (`0.0.0.0`이면 빌드 실행 엔드포인트가 랜에 열린다) |
| 명령 | 클라이언트는 동작 *이름*만 전송. argv는 서버가 생성. `shell=False`, id는 `^[A-Za-z0-9][\w-]{0,63}$` + 실제 존재 확인 |
| 인증 | 실행마다 새 랜덤 토큰 + Origin 검사 (localhost는 경계가 아니다) |
| 동시성 | 한 번에 한 작업 (Unity 빌드와 Codex가 같은 작업 트리를 쓴다) |

### 10.1 명령창 (`orders.py`)

사람이 문장을 타이핑하는 유일한 곳. "명령 = 동작 이름만" 규칙을 어기지 않는 이유:

**입력은 명령이 되지 않는다. 작업판의 데이터가 된다.**
글은 `TASKBOARD.json`의 `goal`로 들어가고, 실제로 도는 건 기존 `team run --task <id>` 그대로다.

요청이 정할 수 없는 3가지 (`orders.py`가 코드로 강제):

| 항목 | 어디서 오나 |
|---|---|
| 작업 id | `orders.py`가 `ORDER-날짜-번호`로 생성. SAFE_ID를 자동으로 만족 |
| 수정 허용 파일 | `DEPARTMENTS` 표. 요청은 **어느 팀인지만** 말한다. 파일 목록은 못 정한다 |
| owner | 팀의 담당자로 고정 (규칙 1 → 전부 codex) |

- 팀 = 담당 구역 = 허용 목록. 허용 목록 없는 팀은 접수 자체를 거부한다
  (`run_task`가 무조건 BLOCKED를 내므로, Codex 실행을 낭비하고 지시 탓으로 보고하게 된다).
- 지시에 다른 경로를 적어도 허용 목록은 안 넓어진다. 그건 요청일 뿐 권한이 아니다.
- **Codex 다음에 Unity 테스트가 이어서 돈다.** Codex는 컴파일을 못 하니까
  거기서 멈추면 검증 안 된 C#을 "완료"로 돌려주게 된다 (§8 제1원칙).
- 앞 단계가 실패하면 **뒤 단계는 안 돈다.** 안 써진 코드에 대해 테스트가 초록불을 내는 게
  이 화면이 낼 수 있는 가장 나쁜 출력이다.
- 접수됐지만 실행을 못 한 지시는 작업판에 `todo`로 남는다. 지운 적 없다.
- `DEPARTMENTS`의 파일 목록을 넓히는 건 정책 플래그를 켜는 것과 같은 무게의 행위다.

- 여기서도 커밋/푸시는 하지 않는다.
- **근거가 없으면 "확인 불가"로 표시한다. 절대 "정상"으로 올려 읽지 않는다.**
  각 AI 줄마다 그 상태의 출처 파일을 함께 표시한다. "설치됨"은 근거가 아니다.

## 11. PC 원격 제어 (`pc-control.yml`)

Claude가 PC에서 명령을 돌리는 **유일한 경로**. (2026-09-03 지시)

| 항목 | 내용 |
|---|---|
| 러너 | `C:\actions-runner-control`, 라벨 `pc-control`, **사용자 계정으로 실행** (`-WindowsLogonAccount`) |
| 이유 | NETWORK SERVICE는 `C:\Dory_tycoon` · 포크 git 자격증명 · Codex 로그인 중 아무것도 없다 |
| 동작 | `status` / `sync` / `dashboard` / `team-run` / `codex-doctor` / `test` / `build` 7개 고정. 입력이 명령이 되는 경로 없음 |
| Unity | `test` / `build`가 **PC의 Unity를 실제로 돌린다.** 결과(APK 유무·컴파일 에러)는 job 로그에 그대로 찍힌다 |
| 기본값 | `status` — 아무것도 건드리지 않음. `stash_dirty`는 opt-in, `status` 확인 후에만. 삭제는 없음 (stash는 복구 가능) |
| 결과 | `mcp__github__get_job_logs`로 직접 읽는다. 사용자가 붙여넣을 필요 없음 |

- 기존 4개 빌드 워크플로에는 `if: github.repository_owner != 'gojo3105-a11'`.
  **CI 빌드는 포크, upstream 러너는 제어 채널.** 섞지 않는다.

## 12. Codex 공동 개발

Codex는 리뷰어가 아니라 **공동 개발자**. 작업판은 `AI_GAME_COMPANY/config/TASKBOARD.json`
(각 작업에 `owner` = claude/codex, `files` = 수정 허용 목록).

```bash
python -m company.orchestrator.main team board
python -m company.orchestrator.main team run --task CODEX-XXX --dry-run
python -m company.orchestrator.main team run --task CODEX-XXX
```

코드로 강제되는 3가지 (`company/orchestrator/teamwork.py`):

| 항목 | 내용 |
|---|---|
| 게이트 | `allow_codex_write`가 true여야 쓰기 가능. `use_codex_subscription`만으로는 리뷰만 |
| 커밋 | **절대 하지 않는다.** 작업 트리에 변경만 남기고 사람이 검토 후 커밋 |
| 상태 | DONE이 아니라 **REVIEW**. DONE은 `orchestrator build` 통과 후 사람이 정한다 |

- 허용 목록을 벗어나면 BLOCKED. **아무것도 되돌리지 않는다** (다른 쪽 미커밋 작업이 있을 수 있다).
- Codex는 매번 기억 없이 시작하므로 CLAUDE.md 핵심 규칙이 프롬프트에 자동 첨부된다
  (`teamwork.HOUSE_RULES`). **규칙을 바꾸면 거기도 같이 고친다.**

## 13. PowerShell 규칙

- `scripts/**/*.ps1`는 **ASCII만. 한글 금지.**
- 이유: Windows PowerShell 5.1이 BOM 없는 UTF-8을 로컬 코드페이지로 읽어 문자열이 깨지고 파싱이 실패한다
  (`collect-errors.ps1` / `sync-and-run.ps1`이 실제로 이것 때문에 안 돌았다).
- 한국어 설명은 `.md`에 쓴다.

## 14. Git 규칙

- 작업 단위를 작게 나눠 커밋 (`feat: add game spec system`).
- 각 단계가 (문법적으로) 정상일 때 커밋.
- **사용자 허락 없이 커밋/푸시하지 않는다.**

## 15. 작업 순서

1. 현재 파일 구조 분석
2. 구현 계획 제시
3. 최소 범위 구현
4. 가능한 범위에서 오류 검사 (Unity 미설치 시 수동 검토로 대체하고 **명시**)
5. 변경 내용 요약 (§16 형식)
6. 다음 작업 제안

**질문이 필요한 경우는 5가지뿐:** 외부 계정/인증 정보, Unity 라이선스, Android 서명 키,
기존 파일 대량 삭제, 복구 어려운 구조 변경. 그 외에는 스스로 판단해 진행한다.

## 16. 결과 보고 형식

**2026-09-03 지시: "항상 결과 요약하고 쉽게 말해."**

- 결론 한 줄로 시작
- 전문 용어 대신 쉬운 말
- 명령은 복사해서 붙일 수 있게 한 덩어리로
- 긴 설명은 결론 뒤

```text
현재 단계 / 완료 / 생성·수정 파일 / 테스트 결과(PASS·FAIL·실행 불가+사유) / 다음 작업
```

## 17. 진행 상태

### 시스템 (Phase 1-8)

- [x] Phase 1-8 + 문서화 전부 완료 (저장소 분석 → 폴더 구조 → GameSpec → Runner MVP →
      Editor 생성기 → Validation/Test → Android 빌드 → GitHub Actions)

### 게임 10개

- [x] Phase 0-1: `PROJECT_ANALYSIS.md`, `GAME_10_MASTER_PLAN.md`
- [x] **Game01 (Factory Runner)** — 시나리오/구현계획/맵/핵심루프/상점/컴파일 0개/
      APK 빌드 확인/`Reports/Game01_Report.txt` 완료
- [ ] Game02 (Idle Factory Tycoon) — 미착수. `Game02_SCENARIO.md`부터
- [ ] Game03~10 — 미착수

### 알려진 한계

| 항목 | 상태 |
|---|---|
| Scene/PrefabGenerator | Runner만 구현. Game02(Idle)부터 새 장르용 생성기 필요 |
| Modules | GravitySwitch만 실제 모듈. DoubleJump/Dash/MovingPlatform 등은 폴더만 (슬라이드·이단점프는 `RunnerPlayerController` 안) |
| MainCharacter.prefab | Unity 프리미티브 placeholder. 실제 3D 모델 미착수 (`Assets/Common/Character/CHARACTER_DESIGN.md`) |
| 캐릭터 리그 | `CharacterPartSlicer`(Unity)가 `player.png`를 파츠로 자르고, `CharacterRigGenerator`가 Animator/AnimationClip을 만든다. 빌드 시 자동. Gemini 재생성은 선택 (`orchestrator character --force`, 키 필요) |
| Ollama | 설치 모델 0개. 사용 전 라이선스 확인 → `LICENSE_REGISTRY.json` 등록 |
| Gemini | 키 미발급, 무료 등급 미검증 |
