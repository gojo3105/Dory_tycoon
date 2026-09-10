# Build

## 컴파일만 빠르게 확인하기

CI 파이프라인 전체(생성→검증→테스트→빌드)를 돌리지 않고 "일단 컴파일은 되는지"만 보고 싶을 때
두 가지 방법이 있다.

**1. Unity Editor에서 (가장 빠름)**: 프로젝트를 열면 Unity가 자동으로 전체 스크립트를 컴파일한다.
컴파일 에러가 있으면 Console 창(`Ctrl+Shift+C`)에 빨간 줄로 나오고, 에디터 우하단에도 에러 개수가
표시된다. 에러가 있는 동안에는 `Game Factory` 메뉴 자체가 동작하지 않는다(에디터 스크립트도 같이
컴파일에 실패하므로) - 그것도 컴파일 실패의 신호다.

에러가 없으면 그 자리에서 `Game Factory` 메뉴로 파이프라인 각 단계를 직접 실행할 수 있다
(`Generate > Factory Runner Sample` → `Validate > All GameSpecs` → `Build > Factory Runner (APK)`).

**2. Editor를 열지 않고 커맨드라인에서**:

```powershell
cd C:\Dory_tycoon
.\scripts\dev\compile-check.ps1
```

Unity를 배치 모드로 띄워 전체 컴파일만 하고 종료한 뒤, `Logs/unity-compile.log`에서 `error CS####`를
찾아 PASS/FAIL을 알려준다. **Unity Editor가 같은 프로젝트를 열고 있으면 실패한다** (Unity는 같은
프로젝트를 두 번 열지 못한다) - 먼저 Editor를 닫아야 한다.

이 스크립트는 일부러 `-executeMethod` 센티넬을 쓰지 않는다: 컴파일이 깨진 상태에서는 호출할
어셈블리 자체가 없으므로, 정작 이 스크립트가 잡아내야 할 상황에서 센티넬이 절대 기록될 수 없다.

## 로컬에서 빌드하기 (Unity Editor)

1. `Game Factory > Generate > Factory Runner Sample` (또는 원하는 GameSpec에 대해
   `GameFactoryGenerator.Generate("GameSpecs/<id>.json")`)로 Scene을 먼저 생성한다.
2. `Game Factory > Build > Factory Runner (APK)`를 실행한다.
3. 결과물은 `Builds/<game_id>/APK/<GameXX>_<제목>_v<버전>.apk` (또는 AAB 빌드 시
   `Builds/<game_id>/AAB/`)에 생성된다 - 예: `game01`/제목 "Factory Runner"/버전 `0.1.0`이면
   `Builds/game01/APK/Game01_FactoryRunner_v0.1.0.apk` (`BundleIdUtility`처럼 폴더 경로는 항상
   `game.id` 그대로 쓰지만, 파일명만 사람이 읽기 좋은 `GameXX_제목_v버전` 형식이다). Unity 콘솔
   원본 로그는 `Logs/unity-build.log`, BuildAndroid가 정리한 단계별 요약은
   `Logs/unity-build-report.log` (일부러 다른 파일 - 같은 파일에 쓰면 Unity의 `-logFile`이 이미
   그 경로를 열고 있어서 Windows에서 `IOException: Sharing violation`이 난다).

## CLI로 빌드하기

CI에서는 `scripts/ci/wait-for-unity.ps1`을 통해 실행한다 (`docs/AUTOMATION.md` 참고). Unity
Editor에서 직접 실행할 때는:

```powershell
& "$env:UNITY_PATH" -batchmode -nographics -projectPath . `
  -executeMethod GameFactory.Editor.BuildAndroid.BuildFromCommandLine `
  -gameId game01 -buildType apk `
  -logFile Logs/unity-build.log -quit
```

`-buildType`을 `aab`로 바꾸면 Google Play 제출용 App Bundle이 생성된다. 빌드 대상 Scene
(`Assets/GeneratedGames/<id>/Scenes/<id>.unity`)이 없으면 즉시 예외를 던지고 실패한다 - 먼저
`GameFactoryGenerator`를 실행해야 한다.

## AI 관제 대시보드 APK

Unity 메뉴에서 `Game Factory > Generate > AI Dashboard Scene`을 실행한 뒤
`Game Factory > Build > AI Dashboard (APK)`를 선택하면
`Builds/dashboard/APK/DoryAIDashboard.apk`가 생성된다. 배치 빌드는 다음과 같다.

```powershell
& "$env:UNITY_PATH" -batchmode -nographics -projectPath . `
  -executeMethod GameFactory.Editor.BuildDashboard.BuildFromCommandLine `
  -logFile Logs/dashboard-build.log -quit
```

APK는 Android WebView로 실행 중인 관제 서버를 연다. Android 에뮬레이터에서는
기본 주소가 `http://10.0.2.2:8765/`이고, 실제 기기에서는 `DashboardWebViewController`
의 주소를 PC의 같은 Wi-Fi LAN 주소(예: `http://192.168.0.10:8765/`)로 바꾼 뒤
다시 빌드해야 한다. PC 서버는 휴대폰에서 접근할 수 있도록 LAN 바인딩과 방화벽 규칙을
별도로 허용해야 하며, 인터넷에 공개하지 않는다.

## Bundle ID (Android Application Id)

`BuildAndroid`는 게임마다 `com.gamefactory.<game_id>` 형태의 bundle id를 자동으로 만든다
(`Assets/GameFactory/Editor/BundleIdUtility.cs`). 한 번 만들어진 bundle id는
`GeneratedGames/<game_id>.json`(저장소 루트, Unity 에셋 아님)에 기록되고, 이후 재빌드에서는
그 값을 그대로 재사용한다 - **이미 배포된 게임의 bundle id가 재생성/재빌드로 바뀌는 일은 없다.**

`GameValidator`는 `GameSpecs/*.json` 전체에 대해 bundle id 충돌 여부를 미리 검사한다.

## 서명 (Signing)

Keystore 경로/비밀번호는 절대 저장소에 커밋하지 않는다. `BuildAndroid`는 다음 환경 변수를
읽어서 서명을 구성한다:

| 환경 변수 | 용도 |
|---|---|
| `ANDROID_KEYSTORE_PATH` | 이 self-hosted runner 위에 이미 존재하는 keystore 파일의 경로 |
| `ANDROID_KEYSTORE_PASS` | keystore 비밀번호 |
| `ANDROID_KEYALIAS_NAME` | key alias 이름 |
| `ANDROID_KEYALIAS_PASS` | key alias 비밀번호 |

`ANDROID_KEYSTORE_PATH`가 설정되어 있지 않으면 Unity의 기본 디버그 서명을 사용한다 - 기기에
설치해서 테스트하는 데는 문제가 없지만, Play Store에 제출할 수는 없다.

GitHub Actions에서 이 값들을 넘기려면 저장소 Settings → Secrets and variables → Actions에
`ANDROID_KEYSTORE_PATH`/`ANDROID_KEYSTORE_PASS`/`ANDROID_KEYALIAS_NAME`/`ANDROID_KEYALIAS_PASS`를
등록한다. **`ANDROID_KEYSTORE_PATH`는 파일 내용이 아니라, self-hosted runner 로컬 디스크에
이미 올려둔 keystore 파일의 경로 문자열이어야 한다** - keystore 파일 자체를 어떻게 그 runner에
배치할지(수동 복사, 별도 보안 채널 등)는 실제 서명 키를 발급받은 뒤 직접 결정해서 진행한다.

## 기타 Player Settings

`BuildAndroid.Build`가 빌드 직전에 다음을 설정한다:

- `PlayerSettings.productName` = GameSpec의 `game.title`
- `PlayerSettings.applicationIdentifier` = 위 bundle id
- `PlayerSettings.defaultInterfaceOrientation` = `Portrait`
- `PlayerSettings.bundleVersion` - 비어 있을 때만 `"1.0.0"`으로 초기화 (이미 값이 있으면 유지)
- `PlayerSettings.Android.bundleVersionCode` - `0` 이하일 때만 `1`로 초기화 (이미 값이 있으면 유지)

버전 코드/버전 문자열을 올리는 것은 이 자동화의 책임이 아니다 - 실제 배포 버전 관리 정책이
정해지면 그에 맞춰 별도로 갱신한다.
