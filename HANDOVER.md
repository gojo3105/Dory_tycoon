# 인수인계 - Game Factory 프로젝트

작성: 2026-09-05 11:25 (Claude). 담당이 ChatGPT로 넘어갈 때 이 파일 하나만 읽으면
지금 어디까지 왔는지, 다음에 뭘 해야 하는지 알 수 있게 쓴 문서다.

`CLAUDE.md`가 이 프로젝트의 규칙 원본이고, 이 파일은 **지금 이 순간의 상태**만 담는다.
규칙이 궁금하면 CLAUDE.md, 상황이 궁금하면 이 파일.

---

## 1. 이 프로젝트가 뭔가

`GameSpecs/game01.json` 같은 설정 파일 하나로 Android 게임을 자동 생성하는 시스템.
게임마다 코드를 새로 짜지 않고, 재사용 코드(Core/Modules/Gameplay) + 설정을 조합한다.

```
GameSpec(JSON) -> Unity 에디터 생성기 -> Scene/Prefab/Level/UI
              -> 검증 -> Unity 테스트 -> Android 빌드 -> APK
```

목표는 게임 10개. **현재 Game01(Factory Runner) 하나만 진행 중이고, Game02는 미착수.**

---

## 2. 누가 뭘 하는가 (사용자가 정한 규칙)

1. **코드는 Codex가 쓴다.** 사람/AI 조수는 명세를 `AI_GAME_COMPANY/config/TASKBOARD.json`에
   올리고, 나온 결과를 리뷰한다. Codex는 빌드 PC에서 `codex exec`로 돈다.
2. **디자인이 필요하면 Gemini 무료 등급.** 키는 `GEMINI_API_KEY`, 아직 발급 안 됨.
3. **관제 화면은 AI를 회사 부서 캐릭터로 보여준다.**

이 규칙은 그대로 유지하는 게 좋다. 오늘 Codex는 실제로 작업 5건을 끝냈다.

---

## 3. 지금 상태 (2026-09-05 11:25 기준, 전부 검증된 사실)

### 확인된 것

| 항목 | 값 | 근거 |
|---|---|---|
| APK | 17.32 MB, sha256:7F81E6F18E29EA94 | `Reports/build-status/latest.txt`, 빌드 09-02 20:25 |
| PlayMode 테스트 | **11개 통과, 실패 0** | `Reports/runs/20260905-111849-test.txt`, 09-05 11:18 |
| 컴파일 에러 | 0 | `Reports/errors/latest.txt` |
| Unity | 6000.5.9f1 | 임의로 바꾸지 말 것 |

**09-05 11:18에 PC에서 `orchestrator test --platform playmode`가 2분 53초 만에 통과했다.**
이게 중요한 이유: GitHub 러너 없이 Unity 테스트를 돌린 첫 사례이고, 동시에
그 시점에 PC에 있던 모든 C#(팔다리, 배경 포함)이 **컴파일된다는 증거**다.

### 작업판 (`AI_GAME_COMPANY/config/TASKBOARD.json`)

- **done 4개** — 경제 분리, PlayMode 테스트, 버튼 4색 아트, orchestrator test
- **review 11개** — 코드는 있고 컴파일도 되지만, 눈으로 확인 안 된 것들
  (09-05 추가: CLAUDE-VERBS1 슬라이드/이단점프)
- **blocked/in_progress 1개** — CODEX-AICTL1 (Ctrl+C로 중단됨, 파일 안 남김. 다시 돌리면 됨)
- **todo 2개** — CODEX-BGWIRE1 (배경을 씬에 연결), CODEX-ENERGY1 (에너지 게이지 + 코인 회복)

`review`가 많은 건 나쁜 뜻이 아니다. 이 프로젝트에서 `done`은 **빌드를 통과하고 사람이
눈으로 본 뒤**에만 붙인다. "컴파일된다"와 "화면에서 제대로 보인다"는 다른 얘기다.

### 아직 화면에서 안 보이는 것

- 배경 (`ParallaxBackground.cs`는 있는데 씬이 안 만들어줌 → BGWIRE1)
- 팔다리 움직임 (컴파일됨, 실제로 어떻게 보이는지 미확인)
- **슬라이드 / 이단 점프 / 머리 위 장애물** (09-05 추가, 아직 한 번도 안 돌려봄 → 다음 빌드에서 확인)
- 컬러 버튼 (아트는 오늘 들어왔고, **다음 빌드부터** 보임)
- 한국어 폰트 (안드로이드 기기에서 한글이 제대로 나오는지 미확인 — CLAUDE-UI1)

---

## 4. PC와 저장소가 어떻게 연결돼 있는가

여기가 이 프로젝트에서 제일 헷갈리는 부분이다.

- **저장소가 두 개다.** `gojo3105-a11/Dory_tycoon`(upstream)과 `gojo3105/dory_tycoon`(fork).
  PC의 `C:\Dory_tycoon`은 fork를 origin으로 쓰고, Unity CI 러너도 fork에 등록돼 있다.
- **자동 동기화가 15분마다 돈다.** `scripts/desktop/sync-and-run.ps1`이
  upstream을 당겨서 fork로 밀어준다. 예약 작업으로 등록돼 있다.
- **PC 상태를 밖에서 읽는 방법은 커밋된 파일뿐이다.** 네 개를 본다:

| 파일 | 내용 |
|---|---|
| `Reports/runs/latest.txt` | 마지막 오케스트레이터 실행 (명령/종료코드/시간/출력) |
| `Reports/sync-status/latest.txt` | 자동 동기화 성공/실패와 그 이유 |
| `Reports/errors/latest.txt` | Unity 컴파일 에러 |
| `Reports/build-status/latest.txt` | 디스크에 실제로 있는 APK |

**"에러 붙여주세요"라고 하기 전에 이 네 파일을 먼저 봐야 한다.** 이 파일들이 있는
이유가 그거다.

> ChatGPT로 넘어가면 이 파일들을 **자동으로 읽을 수 없다.** 사용자가 붙여넣어야 한다.
> 위 표의 경로를 알려주고, 필요할 때 `Get-Content` 결과를 요청하는 식으로 쓰면 된다.

---

## 5. 자주 쓰는 명령 (전부 PC에서)

```powershell
# 제어판 (동기화 + 브라우저까지 한 번에)
cd C:\Dory_tycoon; powershell -ExecutionPolicy Bypass -File scripts\desktop\open-control-panel.ps1

# Unity 테스트 - GitHub 러너 없이 돈다
cd C:\Dory_tycoon\AI_GAME_COMPANY; python -m company.orchestrator.main test --platform playmode

# 전체 빌드 (생성 -> 검증 -> APK)
cd C:\Dory_tycoon\AI_GAME_COMPANY; python -m company.orchestrator.main build --game game01

# Codex에게 작업 넘기기
cd C:\Dory_tycoon\AI_GAME_COMPANY; python -m company.orchestrator.main team run --task CODEX-BGWIRE1

# 작업판 보기
cd C:\Dory_tycoon\AI_GAME_COMPANY; python -m company.orchestrator.main team board
```

`AI_GAME_COMPANY` 안에서 실행해야 한다. 저장소 루트에서 치면
`No module named 'company'`로 죽는다.

---

## 6. 다음에 할 일 (순서대로)

1. **빌드해서 APK를 만든다.** 아트가 들어왔고 테스트가 통과했으니, 지금 빌드하면
   컬러 버튼이 처음으로 보인다.
   `python -m company.orchestrator.main build --game game01`
2. **APK를 폰에 설치해서 눈으로 본다.** 한글이 깨지지 않는지(CLAUDE-UI1),
   팔다리가 어색하지 않은지(CODEX-ANIM1), **아래로 밀면 숙여지는지 / 머리 위 장애물을
   지나갈 수 있는지 / 이단 점프가 화면 밖으로 안 나가는지**(CLAUDE-VERBS1).
   전부 **기기에서만 확인 가능**하다.
3. **CODEX-BGWIRE1을 Codex에게 넘긴다.** 배경이 화면에 나오게 하는 마지막 조각.
4. **CODEX-AICTL1을 다시 돌린다.** 중단됐던 작업. 파일은 안 남겼으니 그냥 다시 돌리면 된다.
5. **CODEX-ENERGY1을 Codex에게 넘긴다.** 에너지 게이지가 들어가면 "부딪히지만 않으면
   영원히 사는" 지금 구조가 실제 게임이 된다.
6. 그 뒤 Game01을 마무리하고 **Game02(Idle Factory Tycoon)** 로 넘어간다.
   `GAME_10_MASTER_PLAN.md`에 10개 기획이 다 있다.

---

## 7. 오늘 고친 버그들 (같은 실수를 반복하지 않으려면)

전부 "Codex는 일을 끝냈는데 시스템이 실패로 기록한" 경우였다. 패턴이 하나다:
**작업 성과를 파일 변경으로만 판단하면, 그 파일이 다른 이유로 움직였을 때 오판한다.**

| 증상 | 원인 | 고친 방법 |
|---|---|---|
| 실행이 마지막 줄에서 죽음 | 한국어 콘솔(cp949)이 `—` 문자를 못 찍음 | 콘솔 출력을 UTF-8로, 못 찍는 글자는 `?`로 대체 |
| 끝난 작업이 "막힘"으로 | Unity가 `packages-lock.json`을 다시 씀 | 도구가 쓰는 파일은 허용 목록 검사에서 제외 |
| 끝난 작업이 "막힘"으로 | 실행 기록 파일이 실행 중에 생김 | `Reports/*`도 도구 소유로 |
| 끝난 작업이 "아무것도 안 함"으로 | 실행 도중에 커밋이 일어남 | 커밋 위치도 기억해서 판단 |
| Ctrl+C가 "진행 중"으로 남음 | `KeyboardInterrupt`는 일반 예외가 아님 | `BaseException`으로 잡음 |
| 동기화가 조용히 멈춤 | 작업판 파일이 매번 충돌 | `tools/merge-taskboard.py`로 자동 병합 |
| 제어판 오류가 안 남음 | `serve`를 기록 대상에서 뺐음 | 전부 기록 |

**교훈:** 이 시스템에서 "실패했다"는 보고가 나오면, **정말 실패한 게 맞는지 먼저
의심할 것.** 오늘 다섯 번 중 다섯 번이 오탐이었다.

---

## 8. 절대 하면 안 되는 것

- `Assets/Common/Character/SourceImage/` 의 원본 이미지 삭제/교체 (사용자 자산)
- keystore, 서명 비밀번호, API 키를 커밋 — 존재 여부만 기록
- `~/.codex/auth.json` 읽기/복사
- `scripts/**/*.ps1`에 한글 쓰기 (PowerShell 5.1이 깨뜨린다. ASCII만)
- Unity 버전 임의 변경
- APK가 안 만들어졌는데 "빌드 성공"이라고 보고 — 이 프로젝트의 제1원칙
- 사용자 허락 없이 커밋/푸시

---

## 9. 미해결로 남기는 것

- **GitHub 러너 두 개 다 문제 있음.** fork용 CI 러너(`C:\actions-runner`)는 서비스가
  Stopped 상태고, 새로 등록한 제어용 러너(`C:\actions-runner-control`)는 비밀번호 문제로
  서비스 설치가 안 됐다. 둘 다 `.\run.cmd`로 직접 띄우면 동작한다.
  급하지 않다 — 로컬 빌드/테스트가 다 되기 때문.
- **기본 브랜치가 낡았다.** `claude/claude-md-docs-hjvt5z`는 8월 5일 Godot 시절 상태이고
  워크플로가 하나도 없다. 실제 작업은 `claude/delete-current-content-mgn4xm`에 있다.
  워크플로를 수동 실행하려면 기본 브랜치를 바꿔야 한다.
- **Ollama에 설치된 모델 0개.** `gemma4:26b`는 라이선스 미확인 + RAM 초과로 제거됨.
  7B급 모델을 쓰려면 그 모델 id의 라이선스를 먼저 확인하고
  `LICENSE_REGISTRY.json`에 등록해야 한다.
- **Gemini 키 미발급.** 무료 등급 여부도 1차 출처로 확인 못 했다
  (`ai.google.dev`가 프록시에서 차단).
- **Codex가 만든 AICTL1/LIVE1 버전이 `Reports/codex-attempts.diff`에 보관돼 있다.**
  LIVE1은 이미 다른 방식으로 구현됐고, AICTL1은 옛 `dashboard.py` 기준이라
  다시 얹는 작업이 필요하다.
