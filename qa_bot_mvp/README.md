# Game QA 자동화 — 봇 플레이 기반 이상탐지

봇이 게임을 플레이하며 데이터를 생성하고, 정상 플레이만으로 학습한
이상탐지 모델이 주입된 버그를 잡아낸다. 게임을 직접 만들지 않고,
미리 만들어진 게임 환경(MiniGrid, ViZDoom)을 어댑터로 붙인다.

## 아키텍처: 어댑터 + 게임 무관 코어

```
[게임별 어댑터] ──표준 Episode/Step──> [게임 무관 코어]
 MiniGrid                               스키마 / 특징 / 이상탐지
 ViZDoom(Doom)                          (어떤 게임이든 재사용)
```
새 게임을 붙인다 = 표준 포맷을 뱉는 어댑터 하나를 추가한다. 코어는 무수정.

## 폴더 구조

```
game-qa-mvp/
├── run.py                  # MiniGrid 파이프라인 실행
├── run_vizdoom.py          # 실제 Doom 파이프라인 실행
│
├── qa_core/                # 재사용 코어 (게임 무관)
│   ├── schema.py           #   표준 궤적 포맷 (어댑터↔코어 계약)
│   ├── features.py         #   궤적 → 특징 벡터 (스케일은 meta로 자기조정)
│   └── detect.py           #   IsolationForest 이상탐지 학습/평가
│
├── adapters/
│   ├── minigrid/           # 격자 게임 어댑터 (collect, env_defects)
│   └── vizdoom/            # 실제 Doom 어댑터 (collect, env_defects)
│
├── REFACTOR_01_schema.md       # 표준 포맷 도입 기록
├── REFACTOR_02_restructure.md  # 패키지 구조 재구성 기록
├── REFACTOR_03_vizdoom.md      # 실제 게임 전이 기록
└── v1_/v2_/v3_ ...             # 각 단계 직전 스냅샷 (문서화용)
```

## 실행

```bash
pip install gymnasium minigrid scikit-learn numpy vizdoom
python run.py            # MiniGrid (격자 세계)
python run_vizdoom.py    # 실제 Doom (연속 좌표, 헤드리스)
```

## 파이프라인 6단계

1. 봇이 게임을 플레이 → 궤적(Episode) 수집
2. 결함 주입(softlock/teleport/reward_bug)으로 정답 라벨 확보
3. 궤적 → 고정 길이 특징 벡터
4. 정상 데이터만으로 이상탐지기 학습 (비지도)
5. 정상+버그 테스트셋으로 평가 (precision/recall/ROC-AUC)
6. 버그 종류별 탐지율 리포트

## 결과 요약

| | MiniGrid (격자) | Doom (실제 게임) |
|---|:---:|:---:|
| softlock recall | 1.000 | 1.000 |
| reward_bug recall | 0.200 | 0.650 |
| 코어 재사용 | — | 무수정 |

핵심 발견: 스키마와 탐지 모델은 게임 무관하게 재사용되지만,
특징은 격자 vs 연속 좌표 차이 때문에 게임별 스케일 파라미터가 필요하다
(`move_eps`, `cell_size`를 어댑터가 meta로 선언, 코어는 한 벌 유지).
자세한 내용은 `REFACTOR_03_vizdoom.md` 참고.
