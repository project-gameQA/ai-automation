# Game QA 자동화 MVP — 봇 플레이 기반 이상탐지

미리 만들어진 게임 환경(MiniGrid)을 봇이 플레이하며 데이터를 생성하고,
정상 플레이만으로 학습한 이상탐지 모델이 주입된 버그를 잡아내는 파이프라인.


## 파이프라인

```
게임 환경 (MiniGrid-DoorKey)
        │
        ├─ 무작위 봇이 플레이 ──> 정상 궤적 (state·action·reward 시퀀스)
        │
        └─ 결함 주입 wrapper ──> 버그 궤적 (softlock / teleport / reward_bug)
                                        │
                궤적 → 고정 길이 특징 벡터 (features.py)
                                        │
                정상만으로 IsolationForest 학습 (detect.py)
                                        │
                정상+버그 테스트셋으로 평가 → QA 리포트
```

## 파일 구조

| 파일 | 역할 |
|------|------|
| `env_defects.py` | 게임에 고장을 주입하는 wrapper (합성 버그 = 정답 라벨 확보) |
| `collect.py`     | 봇이 게임을 플레이하며 궤적을 기록 |
| `features.py`    | 궤적 하나 → 특징 벡터 하나 |
| `detect.py`      | 정상만으로 이상탐지기 학습 + 평가 |
| `run.py`         | 위 전부를 한 번에 실행 |
| `schema.py`         | 표준 궤적 포맷 |

## 실행

```bash
pip install gymnasium minigrid scikit-learn numpy
python run.py
```

## 예시 결과

```
=== 버그 종류별 탐지율 (recall) ===
    softlock: 1.000   # 얼어붙음 → max_freeze_streak로 완벽 탐지
    teleport: 0.833   # 순간이동 → max_jump로 탐지 (정상은 항상 1.00)
  reward_bug: 0.200   # 미묘한 보상 오염 → 특징 개선 필요
```

teleport는 "max_jump > 1이면 무조건 버그"라는 **하드 오라클**로
잡는 게 맞고, reward_bug처럼 미묘한 건 **특징 설계/ML**이 필요하다.
버그 종류에 따라 탐지 방식이 다르다..

## 다음 단계 (확장)

1. **오라클 층 추가** — max_jump>1 같은 하드 규칙을 ML과 분리해서 이중 탐지
2. **특징 강화** — reward_bug 탐지를 위한 보상 시퀀스 특징 추가
3. **오토인코더** — IsolationForest 대신 신경망 이상탐지 (재구성 오차)
4. //////// 이 부분은 생각좀 해봐야 함 /////////**RL 봇** — 무작위 봇을 Stable-Baselines3 PPO로 격상 (더 현실적인 정상 플레이)
5. **버그 분류기** — 이상 탐지 후 "어떤 종류 버그인지"까지 supervised 분류
6. **비전 확장** — 상태값 대신 화면 프레임 기반 (VizDoom/Atari)

