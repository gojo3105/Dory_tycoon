# Google Play 1위 도전 프로세스

순위는 보장할 수 없다. 이 프로세스는 높은 순위에 필요한 제품 가치, 품질, 스토어 전환, 유지율을 실제 데이터로 검증하고 반복 개선하기 위한 것이다.

## 새 게임 생성 결과

`새 게임 자동 생성`은 GameSpec과 함께 `Growth/<game>/`에 다음 6개 파일을 만든다.

1. `PLAY_STORE_STRATEGY.md`: 핵심 약속, Art Director 산출물, 출시 전·후 루프
2. `STORE_LISTING.json`: 앱 이름, 짧은/긴 설명 초안, 현지화 우선순위
3. `KPI_GATES.json`: 안정성·성능 게이트와 유지율·전환율 목표
4. `CREATIVE_EXPERIMENTS.json`: 아이콘, 피처 그래픽, 스크린샷 A/B 가설
5. `RELEASE_CHECKLIST.md`: 제품·품질·정책·성장 확인표
6. `METRICS_TEMPLATE.json`: Play Console과 제품 분석 결과 입력 형식

## 회사 실행 순서

```text
Game Director 제품 약속
→ Art Director 시장 비주얼·스토어 크리에이티브
→ Technical Director 구조 설계
→ Systems Engineer 게임·KPI 이벤트
→ UI/UX Engineer 첫 세션·전환 UX
→ QA Engineer 실제 테스트
→ Quality Reviewer 85점 게이트
→ Release Engineer AAB/APK·Play 품질 증거
→ 출시 후 데이터에 따라 다음 실험
```

Art Director는 실제 게임과 다른 이미지를 만들거나 “1위”, “최고”, 미구현 기능을 표현할 수 없다. 앱 아이콘, 피처 그래픽, 스크린샷은 각각 가설이 있는 3안을 만들고 Play Console에서 한 번에 한 요소만 실험한다.

## 투자 지속 기준

기술 기준은 Android vitals보다 엄격하거나 같아야 한다. 제품 목표는 장르와 국가의 Play Console 동종 앱 비교를 보고 조정한다. 값이 없으면 통과가 아니라 `NOT_VERIFIED`다.

- 사용자 인지 crash rate: 1.09% 미만
- 사용자 인지 ANR rate: 0.47% 미만
- 핵심 플레이: 30 FPS 이상
- 튜토리얼 완료: 85% 이상 목표
- D1/D7/D30 유지율: 40% / 15% / 7% 이상 목표
- 스토어 설치 전환: 30% 이상 목표
- 평점: 4.5 이상 목표

## 공식 기준

- Android 앱 품질: https://developer.android.com/quality
- Core value 및 사용자 지표: https://developer.android.com/quality/core-value
- Android vitals: https://developer.android.com/topic/performance/vitals
- 스토어 등록정보 실험: https://support.google.com/googleplay/android-developer/answer/12053285
- Pre-launch report: https://support.google.com/googleplay/android-developer/answer/9844487
- 스토어 등록정보 권장사항: https://support.google.com/googleplay/android-developer/answer/13393723

## 사람 또는 외부 계정이 필요한 단계

Play Console 앱 생성, 서명 키, Data safety 제출, 대상 연령과 등급 설문, 결제 상품, 광고 계정, 내부/비공개 테스트 사용자, 실험 시작, 프로덕션 출시는 계정 권한과 법적 판단이 필요하다. 자동화는 필요한 산출물과 미완료 항목을 준비하고, 해당 단계는 Human Gate로 남긴다.
