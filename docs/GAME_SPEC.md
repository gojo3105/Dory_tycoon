# GameSpec 레퍼런스

`GameSpecs/*.json` 하나가 게임 하나를 정의한다. Unity의 `JsonUtility`로 파싱하므로 **중첩 객체만
사용하고 Dictionary/다형성은 사용할 수 없다.** 필드 정의의 단일 소스는
`Assets/GameFactory/Core/GameSpec/GameSpecData.cs`이며, 구조적 검증은
`Assets/GameFactory/Core/GameSpec/GameSpecValidator.cs`가 담당한다. 새 필드를 추가할 때는
이 세 곳(`GameSpecData.cs`, `GameSpecValidator.cs`, 이 문서)을 함께 갱신한다.

## 전체 예시

```json
{
  "game": { "id": "game01", "title": "Factory Runner", "genre": "Runner" },
  "player": { "moveSpeed": 6, "jumpPower": 11, "gravityScale": 3.5 },
  "mechanics": {
    "jump": true, "doubleJump": true, "slide": true, "dash": false, "wallJump": false,
    "gravitySwitch": true, "teleport": false, "timeSlow": false
  },
  "level": { "levelCount": 1, "difficulty": "Medium", "procedural": true, "length": 120 },
  "enemy": { "enabled": false, "types": 0 },
  "special": { "mechanic": "GravitySwitch" },
  "theme": { "environment": "Factory", "character": "Slime" }
}
```

## 필드

### `game` (필수)

| 필드 | 타입 | 설명 | 검증 규칙 |
|---|---|---|---|
| `id` | string | 저장소 전체에서 유일한 식별자. 생성된 Scene/Prefab 경로, Resources 경로, Android bundle id 접미사에 그대로 쓰인다. | `^[a-z][a-z0-9_]*$` (소문자 snake_case), `GameSpecs/*.json` 전체에서 중복 불가 (`GameValidator`) |
| `title` | string | 화면에 노출되는 게임 이름. `PlayerSettings.productName`에도 사용된다. | 비어있으면 안 됨 |
| `genre` | string | `GameGenre` enum 값 중 하나. 현재 Scene 생성기가 구현된 것은 `Runner`뿐. | `Runner`/`Puzzle`/`Physics`/`Idle`/`Defense`/`Merge`/`Arcade`/`Shooter` 중 하나 |

### `player`

| 필드 | 타입 | 설명 | 검증 규칙 |
|---|---|---|---|
| `moveSpeed` | float | 자동 이동 속도 (world units/sec). | `> 0` |
| `jumpPower` | float | 점프 시 부여되는 수직 속도. | `mechanics.jump`가 true면 `> 0` |
| `gravityScale` | float | 플레이어에게 적용되는 중력 배율. 점프의 체공 시간과 도달 거리를 결정한다. 이 값이 없어서 Unity 기본값 1이 적용됐을 때 점프가 2초간 떠 있고 장애물 두세 개를 한 번에 넘어갔다. | `> 0` (0 이하면 0.1로 보정) |

### `mechanics`

각 필드는 해당 기믹의 활성화 여부다. 대응하는 구현이 없으면 활성화해도 아무 효과가 없다.
현재 실제로 동작하는 것은 `jump`, `doubleJump`, `slide`, `gravitySwitch` 네 개다
(`gravitySwitch`만 `Modules/GravitySwitch`의 독립 모듈이고, 나머지는 `RunnerPlayerController` 안에 있다 -
점프 횟수를 세는 일에 모듈 하나를 만들 이유가 없다).

| 필드 | 타입 | 비고 |
|---|---|---|
| `jump` | bool | Runner의 기본 조작. |
| `doubleJump` | bool | true면 공중에서 한 번 더 점프할 수 있다 (`RunnerPlayerController`). 두 번째 점프는 화면 밖으로 나가지 않도록 점프력의 85%만 쓴다. |
| `slide` | bool | true면 아래로 스와이프해 슬라이드(숙이기)할 수 있고, **머리 위에 걸린 장애물**이 생성된다 (`PrefabGenerator`/`ObstacleSpawner`). 슬라이드가 꺼져 있으면 그 장애물은 아예 생성되지 않는다 - 넘을 수단이 없는 장애물은 난이도가 아니라 벽이기 때문이다. |
| `dash` | bool | 모듈 미구현 (예약). |
| `wallJump` | bool | 모듈 미구현 (예약). |
| `gravitySwitch` | bool | true면 `LevelGenerator`가 `GravityZoneSpawner`를 배선한다. |
| `teleport` | bool | 모듈 미구현 (예약). |
| `timeSlow` | bool | 모듈 미구현 (예약). |

### `level`

| 필드 | 타입 | 설명 | 검증 규칙 |
|---|---|---|---|
| `levelCount` | int | 레벨/스테이지 수. Runner는 절차적 엔드리스 러너이므로 현재는 정보성 필드. | `>= 1` |
| `difficulty` | string | 장애물 간격 스케일에 영향 (`ObstacleSpawner.Configure`). | `Easy`/`Medium`/`Hard` |
| `procedural` | bool | 절차적 생성 여부 (정보성 필드, Runner는 항상 절차적). | - |
| `length` | float | 코인 간격, 중력 반전 구간 길이 등 절차적 생성의 기준 스케일. | `> 0` |

### `enemy`

| 필드 | 타입 | 설명 | 검증 규칙 |
|---|---|---|---|
| `enabled` | bool | 적 등장 여부. Runner MVP는 아직 적 모듈을 사용하지 않는다. | - |
| `types` | int | 적 종류 수. | `enabled`가 true면 `>= 1` |

### `special`

| 필드 | 타입 | 설명 | 검증 규칙 |
|---|---|---|---|
| `mechanic` | string | 이 게임의 대표 기믹 하나. `Assets/GameFactory/Modules/` 폴더명과 대응. | `""`(없음) 또는 `Jump`/`DoubleJump`/`Dash`/`WallJump`/`GravitySwitch`/`Teleport`/`TimeSlow`/`FallingFloor`/`MovingPlatform`/`Enemy`/`Boss`/`Weapon`/`Collectible`/`Checkpoint` 중 하나 |

### `theme`

| 필드 | 타입 | 설명 |
|---|---|---|
| `environment` | string | 배경 테마 (자유 텍스트). 현재는 지형 스프라이트 색상 시드로만 쓰인다. |
| `character` | string | 캐릭터 테마 (자유 텍스트). 플레이어는 이제 GameSpec별 색상 스프라이트가 아니라 10개 게임이 공유하는 `MainCharacter` 프리팹을 쓰므로, 현재 이 필드는 생성 로직에서 읽히지 않는다(향후 게임별 의상/액세서리 배리에이션 선택에 쓸 수 있도록 스키마에는 남겨둔다). |

## 게임 다양성 규칙

캐릭터/배경(`theme`)만 바꾼 리스킨은 새 게임으로 인정하지 않는다. `mechanics`/`level`/`enemy`/
`special`의 조합이 실제로 달라져야 한다 - `GameSpecValidator`는 이를 강제하지 않으므로 리뷰 시
사람이 확인한다.
