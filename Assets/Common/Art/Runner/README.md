# Assets/Common/Art/Runner

Runner 장르가 쓰는 **실제 아트**를 놓는 곳이다. 여기 파일이 있으면
`PrefabGenerator`가 절차 생성 단색 placeholder 대신 이걸 쓴다.

## 생성기가 찾는 정확한 파일명

| 파일 | 쓰이는 곳 | 없으면 |
|---|---|---|
| `ground.png` | GroundTile (Tiled 드로우) | 테마 해시 기반 단색 |
| `obstacle.png` | Obstacle | 빨간 사각형 |
| `coin.png` | Coin | 노란 사각형 |
| `gravity_zone.png` | GravityZone (Tiled 드로우) | 보라 반투명 사각형 |

이름이 정확히 맞아야 한다. **하나만 넣어도 된다** — 넣은 것만 교체되고 나머지는
placeholder로 남는다. 폴더가 비어 있으면 동작은 지금과 완전히 동일하다.

## 넣으면 자동으로 되는 것

`Assets/GameFactory/Editor/SharedArtImporter.cs`(AssetPostprocessor)가
`Assets/Common/Art/` 아래 텍스처의 임포트 설정을 자동으로 잡는다:

- Texture Type = Sprite (Single)
- **Mesh Type = Full Rect** — 중요. `SpriteRenderer.drawMode = Tiled`(ground,
  gravity_zone이 사용)은 기본값인 Tight 메시로 임포트된 스프라이트를 타일링하지
  **못하고 조용히 실패한다**
- Pixels Per Unit = 64, Point 필터, mipmap 끔, alpha is transparency

이 설정은 **최초 임포트 때만** 찍힌다(`importSettingsMissing` 검사). 나중에
Inspector에서 개별 조정한 값을 재임포트 때마다 되돌리지 않기 위해서다.

콜라이더 크기는 스프라이트 실제 크기에서 계산한다(`SpriteWorldSize`). 그래서
아트 해상도가 64px가 아니어도 보이는 것과 판정이 어긋나지 않는다.

## 라이선스 (§8 - 반드시 지킬 것)

**여기 넣는 모든 파일은 `AI_GAME_COMPANY/config/LICENSE_REGISTRY.json`에
등록되어야 하고, status가 `APPROVED`가 아니면 출시 APK에 들어갈 수 없다.**

출처가 확실하지 않은 이미지를 그냥 복사해 넣지 말 것. 마스터 프롬프트 §38이
`License UNKNOWN Asset 출시`를 명시적으로 금지한다.

권장 절차:

1. 브라우저로 아트 팩 zip을 받아서 `AI_GAME_COMPANY/asset_staging/_incoming/`에 넣는다
2. `.\AI_GAME_COMPANY\tools\fetch-cc0-assets.ps1 -Commit` 실행 —
   zip 안에 동봉된 라이선스 파일 + 아카이브 SHA-256 + 이미지 목록을 증거로 커밋한다
   (`Assets/`는 건드리지 않는다)
3. 라이선스 텍스트 검토 후 레지스트리를 `APPROVED`로 올리고, 인벤토리에서 고른 파일을
   위 표의 이름으로 여기에 복사한다

## `rig/` — 움직이는 캐릭터

`player.png` 한 장은 통짜 그림이라 **아무것도 움직일 수 없다.** 팔다리가 실루엣
안에 그려져 있기 때문이다. `rig/`에는 같은 그림을 **분해한 것**이 들어간다.

**옆모습(`player_side.png`)이 있으면 그걸 우선 쓴다.** 옆으로 스크롤하는 게임이라
정면 그림은 제자리 뛰기밖에 안 되기 때문이다.

| 파일 | 내용 |
|---|---|
| `body.png` | 팔다리를 지우고 배를 메운 몸통 |
| `arm_near.png` (옆모습) | 보이는 쪽 앞발 하나. 반대쪽은 몸에 가려 아예 없다 |
| `foot_near.png` / `foot_far.png` (옆모습) | 앞발/뒷발. `_far`는 **몸 뒤에** 그린다 |
| `arm_l/r.png`, `foot_l/r.png` (정면) | 정면 그림일 때의 팔·발 |
| `rig.json` | 각 파츠가 **어디를 축으로 도는지**(`joint`), 그 축이 **파츠 그림 안 어디에 있는지**(`anchor`), 그리고 어떤 그림으로 잘랐는지(`reference` / `reference_hash`) |

- `joint` / `anchor`는 둘 다 비율이고, **y는 위에서부터** 잰다.
  `anchor`는 0~1을 벗어날 수 있다 — 엉덩이 관절은 발 그림보다 위에 있다.
- 숫자가 틀렸으면 **`rig.json`을 고치고 다시 생성한다. C#을 고치지 않는다.**
- `Assets/GameFactory/Editor/CharacterRigGenerator.cs`가 이걸 읽어서 관절 계층 +
  `AnimationClip`(Run/Air/Slide) + `AnimatorController`를 **에셋으로** 만든다.
- `rig.json`의 `view`가 `side`면 걸음(앞뒤 이동 + 들어올림), `front`면 제자리 뛰기.
- 흰 배경 위에 그려진 그림은 배경·그림자를 자동으로 따내고 크기를 맞춘 뒤 자른다.
- 이 폴더가 없으면 예전처럼 `player.png` 한 장짜리 캐릭터가 나온다. 추가 기능일 뿐이다.

### 누가 만드나

**Unity가 직접 만든다.** `Assets/GameFactory/Editor/CharacterPartSlicer.cs`가
`player.png`를 읽어서 파츠로 자르고 `rig.json`까지 쓴다. 키도, 외부 서비스도 필요 없다.

- **빌드하면 자동으로** 만들어진다 (`PrefabGenerator`가 없으면 부른다).
- 손으로 다시 만들려면: Unity 메뉴 **Game Factory > Character > Slice player.png into rig parts**

자르는 위치 숫자는 `CharacterPartSlicer.cs`의 표 하나에 모여 있다. 다른 그림으로
바꾸면 **그 표를 고치고 다시 자른다.**

**같은 파일 이름으로 그림만 바꿔도 다시 자른다.** `rig.json`의 `reference_hash`가
그때 자른 그림의 바이트 지문이라서, 지문이 다르면 다음 실행이 다시 자른다.
(파일 날짜는 못 쓴다 — checkout·sync가 픽셀 하나 안 바뀌어도 날짜를 바꾼다.)
`reference_hash`가 없는 옛 `rig.json`은 "무엇으로 잘랐는지 알 수 없음"이므로 다시 자른다.

어려운 건 팔다리를 떼는 게 아니라 **뗀 자리를 메우는 것**이다. 그냥 지우면 배에
사각형 구멍이 남고, 팔이 움직이는 순간 그게 보인다. 그래서 주변 털로 다시 칠한다
(알파 가중 pull-push). 앞발은 실루엣 **안**에 있어서 색만 칠하면 되지만, 발은
배 **아래**로 나와 있어서 윤곽선까지 다시 그려야 한다.

### Gemini로 다시 그리기 (선택)

더 나은 그림을 원하면 Gemini 무료 등급으로 다시 그릴 수 있다 (CLAUDE.md 규칙 2).
키가 있어야 한다:

```bash
cd AI_GAME_COMPANY
python -m company.orchestrator.main character --status    # 키 게이트 확인
python -m company.orchestrator.main character --force     # 다시 그리기
```

`rig.json`의 `source`가 누가 만든 건지 알려준다 (`unity-slicer` / `gemini`).
**Gemini나 사람이 만든 것은 자동으로 덮어쓰지 않는다.** 자동 재생성은
`unity-slicer`가 만든 것에만 적용된다.
