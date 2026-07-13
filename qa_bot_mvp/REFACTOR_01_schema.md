# 리팩터링 기록 #1 — 표준 궤적 포맷(schema) 적용

> 목적: MiniGrid 전용 즉석 dict 형식으로 흐르던 궤적 데이터를
> `schema.py`의 `Episode`/`Step` 표준 포맷으로 바꾼다.
> 이렇게 하면 `collect.py`가 정식 "MiniGrid 어댑터"가 되고,
> 나중에 ViZDoom 등 다른 게임을 어댑터만 추가해 붙일 수 있다.

## 0. 원칙 & 보존

- **동작 불변**: 리팩터링 전후 탐지 수치가 같아야 한다. 달라지면 버그.
- **원본 보존**: 리팩터링 직전 상태를 `v1_before_schema/` 폴더에 원본 그대로 스냅샷함. 아무것도 삭제하지 않음.

### 리팩터링 직전 기준 성능 (baseline)

```
=== 전체 탐지 성능 ===
   precision: 0.910
      recall: 0.678
          f1: 0.777
     roc_auc: 0.797

=== 버그 종류별 탐지율 (recall) ===
    softlock: 1.000   (60/60)
    teleport: 0.833   (50/60)
  reward_bug: 0.200   (12/60)

  정상 오탐율(false positive): 0.120
```

## 1. 변경 요약 (어떤 파일이 왜 바뀌나)

| 파일 | 역할 | 이번 변경 |
|------|------|-----------|
| `schema.py`     | 표준 포맷(계약) | 변경 없음 (이미 존재, 이번 리팩터링의 목표 형식) |
| `collect.py`    | MiniGrid 어댑터 | **변경**: dict 대신 `Episode`/`Step` 반환 |
| `features.py`   | 코어 (특징) | **변경**: dict 대신 `Episode` 입력. 계산 로직은 불변 |
| `run.py`        | 오케스트레이터 | **변경**: 라벨 조립을 `injected_defect` 기반으로 단순화 |
| `detect.py`     | 코어 (모델) | 변경 없음 |
| `env_defects.py`| 결함 주입 | 변경 없음 |

---

## 2. `collect.py` — before / after

### 왜 바꾸나
현재 `traj`는 `{"pos":[], "dir":[], "action":[], "reward":[], "terminated":bool}` 형태의
MiniGrid 전용 dict다. `dir`은 MiniGrid에만 있는 필드이고, Doom이라면 대신 `health`/`ammo`가 온다.
이 임시 형식을 표준 `Episode`/`Step`으로 바꿔서, 게임별 값은 `state` dict로 분리한다.
정답 라벨(`injected_defect`)도 에피소드 안에 담는다.

### BEFORE (현재)
```python
import numpy as np
import gymnasium as gym
import minigrid  # noqa: F401
from env_defects import DefectWrapper

ENV_ID = "MiniGrid-DoorKey-8x8-v0"
MAX_STEPS = 200


def rollout(defect=None, seed=0):
    """게임 한 판을 플레이하고 궤적 하나를 반환."""
    rng = np.random.default_rng(seed)
    env = DefectWrapper(gym.make(ENV_ID), defect=defect, rng=rng)
    obs, info = env.reset(seed=seed)

    traj = {"pos": [], "dir": [], "action": [], "reward": []}
    traj["pos"].append(tuple(int(v) for v in env.unwrapped.agent_pos))
    traj["dir"].append(int(obs["direction"]))

    terminated = truncated = False
    for _ in range(MAX_STEPS):
        action = int(rng.integers(0, 6))
        obs, reward, terminated, truncated, info = env.step(action)
        traj["action"].append(action)
        traj["reward"].append(float(reward))
        traj["pos"].append(tuple(int(v) for v in env.unwrapped.agent_pos))
        traj["dir"].append(int(obs["direction"]))
        if terminated or truncated:
            break

    env.close()
    traj["terminated"] = bool(terminated)
    return traj


def collect(n, defect=None, seed0=0):
    """n판을 플레이해서 궤적 리스트를 반환."""
    return [rollout(defect=defect, seed=seed0 + i) for i in range(n)]
```

### AFTER (리팩터링 후)
```python
import numpy as np
import gymnasium as gym
import minigrid  # noqa: F401
from env_defects import DefectWrapper
from schema import Episode, Step               # ← 추가

ENV_ID = "MiniGrid-DoorKey-8x8-v0"
GAME_ID = "minigrid-doorkey-8x8"               # ← 추가 (에피소드 식별자)
ADAPTER_VERSION = "1.0"                          # ← 추가
MAX_STEPS = 200


def rollout(defect=None, seed=0):
    """게임 한 판을 플레이하고 Episode 하나를 반환."""
    rng = np.random.default_rng(seed)
    env = DefectWrapper(gym.make(ENV_ID), defect=defect, rng=rng)
    obs, info = env.reset(seed=seed)

    steps = []
    terminated = truncated = False
    for t in range(MAX_STEPS):
        action = int(rng.integers(0, 6))
        obs, reward, terminated, truncated, info = env.step(action)
        steps.append(Step(
            t=t,
            action=action,
            reward=float(reward),
            pos=[int(v) for v in env.unwrapped.agent_pos],   # 보편 필드
            done=bool(terminated or truncated),
            state={"dir": int(obs["direction"])},            # MiniGrid 전용 → state
        ))
        if terminated or truncated:
            break

    env.close()
    return Episode(
        game_id=GAME_ID,
        adapter_version=ADAPTER_VERSION,
        seed=seed,
        steps=steps,
        injected_defect=defect,                              # 정답 라벨을 판 안에
        outcome="success" if terminated else "timeout",
    )


def collect(n, defect=None, seed0=0):
    """n판을 플레이해서 Episode 리스트를 반환."""
    return [rollout(defect=defect, seed=seed0 + i) for i in range(n)]
```

### 주의 (동작 차이 점검 포인트)
- BEFORE는 초기 위치를 `pos[0]`으로 미리 넣고 시작했다(스텝 실행 전 위치 1개).
  AFTER는 각 스텝의 "실행 후 위치"만 담는다. → `pos` 리스트 길이가 1 줄어든다.
  특징 계산(연속 위치 간 점프)에는 영향이 미미하지만, 수치가 미세하게 달라질 수 있으므로
  검증 단계에서 반드시 확인한다. (필요하면 초기 위치를 첫 Step에 포함해 맞춘다.)

---

## 3. `features.py` — before / after

### 왜 바꾸나
dict(`traj["pos"]`)를 읽던 것을 `Episode`의 `steps`를 순회하도록 바꾼다.
**거리/streak/엔트로피 계산 로직은 한 줄도 바꾸지 않는다.** 데이터를 꺼내는 부분만 교체.

### BEFORE (데이터 꺼내는 부분만 발췌)
```python
def featurize(traj):
    pos = traj["pos"]
    actions = np.array(traj["action"] or [0], dtype=int)
    rewards = np.array(traj["reward"] or [0.0], dtype=float)
    ...
    return np.array([
        len(actions),
        rewards.sum(),
        float(traj["terminated"]),      # ← dict 키
        len(set(pos)),
        ...
    ], dtype=float)
```

### AFTER
```python
def featurize(ep):                       # ep = Episode 객체
    pos     = [tuple(s.pos) for s in ep.steps]
    actions = np.array([s.action for s in ep.steps] or [0], dtype=int)
    rewards = np.array([s.reward for s in ep.steps] or [0.0], dtype=float)
    ...
    return np.array([
        len(actions),
        rewards.sum(),
        float(ep.outcome == "success"),  # ← Episode 필드
        len(set(pos)),
        ...
    ], dtype=float)
```
※ 중간의 `jumps`, `no_move`, `max_streak`, `entropy` 계산 블록은 **그대로 유지**.
   `pos`를 tuple로 감싸는 이유: `set(pos)`로 고유 칸을 세려면 list가 아니라 tuple이어야 함.

---

## 4. `run.py` — before / after

### 왜 바꾸나
라벨(`y_test`)을 손으로 `[0]*정상 + [1]*버그`로 조립하던 것을,
이제 각 Episode 안 `injected_defect`에서 뽑도록 바꾼다. 더 안전하고 실전 코드에 가깝다.
(선택) 수집한 데이터를 `.jsonl`로 저장/로드하는 단계를 추가할 수 있다.

### BEFORE (라벨 조립 부분)
```python
    Xb_all, y_bug_type = [], []
    for name, trajs in bug_sets.items():
        for t in trajs:
            Xb_all.append(featurize(t))
            y_bug_type.append(name)
    Xb_all = np.array(Xb_all)

    X_test = np.vstack([Xn_test, Xb_all])
    y_test = np.array([0] * len(Xn_test) + [1] * len(Xb_all))
```

### AFTER
```python
    Xb_all, y_bug_type = [], []
    for name, eps in bug_sets.items():
        for ep in eps:
            Xb_all.append(featurize(ep))
            y_bug_type.append(name)            # 종류별 recall용
    Xb_all = np.array(Xb_all)

    X_test = np.vstack([Xn_test, Xb_all])
    # 라벨을 에피소드 안에서 직접 도출 (정상=None → 0, 버그=이름 → 1)
    y_test = np.array(
        [0] * len(Xn_test) +
        [0 if ep.injected_defect is None else 1
         for eps in bug_sets.values() for ep in eps]
    )
```
※ `normal`/`bug_sets`가 이제 Episode 리스트라는 점만 바뀌고,
   `collect(...)` 호출부와 학습/평가 흐름은 그대로다.
   `Xn = np.array([featurize(t) for t in normal])`의 `t`는 이제 Episode.

---

## 5. 변경 없는 파일 (참고)

- `schema.py`: 이번 리팩터링의 **목표 형식**. 그대로 사용.
- `detect.py`: 특징 벡터 배열만 받으므로 데이터 출처가 바뀌어도 무관.
- `env_defects.py`: 결함 주입 로직. 그대로.

---

## 6. 검증 절차 (리팩터링이 올바른지 확인)

1. `v1_before_schema/`의 원본으로 `run.py` 실행 → 기준 수치 재확인.
2. 리팩터링본으로 `run.py` 실행.
3. 두 결과의 precision/recall/ROC-AUC, 버그 종류별 recall 비교.
4. 2절의 "초기 위치 1개 차이" 때문에 수치가 미세하게 달라지면,
   원인을 기록하고 필요 시 초기 위치를 첫 Step에 포함해 정렬.
5. (추가) `save_dataset`/`load_dataset` 왕복 후에도 동일 결과인지 확인.

## 7. 이 리팩터링으로 얻는 것

- `collect.py`가 표준 포맷을 뱉는 **정식 MiniGrid 어댑터**가 됨
  → 이후 `vizdoom_collect.py`만 추가하면 `features.py`/`detect.py` 무수정 재사용.
- 데이터를 `.jsonl`로 저장/로드 가능 → 수집과 실험 분리, 재현성 확보.
- 정답 라벨이 데이터에 동봉 → 실전에선 `injected_defect=None`으로 그대로 사용 가능.

---

## 8. 실제 검증 결과 (리팩터링 적용 후 기록)

리팩터링 적용 완료. 아래는 실제 실행으로 확인한 결과다.

### 초기 위치 차이 처리
문서 2절에서 우려한 "초기 위치 1개 차이"는 **`meta["init_pos"]`에 리셋 직후 위치를
담고, `featurize`에서 이를 앞에 붙여 복원**하는 방식으로 해결했다.
그 결과 특징 벡터가 리팩터링 전과 비트 단위로 동일해졌다.

### 전/후 수치 비교 (완전 일치)

| 지표 | BEFORE (원본) | AFTER (리팩터링) |
|------|:---:|:---:|
| precision | 0.910 | 0.910 |
| recall | 0.678 | 0.678 |
| f1 | 0.777 | 0.777 |
| roc_auc | 0.797 | 0.797 |
| softlock recall | 1.000 (60/60) | 1.000 (60/60) |
| teleport recall | 0.833 (50/60) | 0.833 (50/60) |
| reward_bug recall | 0.200 (12/60) | 0.200 (12/60) |
| 정상 오탐율 | 0.120 | 0.120 |

특징별 평균값도 8개 전부 동일하게 재현됨. → **동작 불변 원칙 충족, 올바른 리팩터링.**

### 저장/로드 왕복 검증
`save_dataset` → `load_dataset` 왕복 후에도 특징 벡터 완전 동일,
`injected_defect` 라벨 보존 확인. `.jsonl` 영속화가 정상 작동.

### 최종 상태
- `collect.py` = 표준 포맷을 뱉는 정식 MiniGrid 어댑터
- `features.py` / `detect.py` = 게임 무관 코어 (이후 게임에서 무수정 재사용 가능)
- `schema.py` = 어댑터/코어를 잇는 계약
- 다음 작업: ViZDoom 어댑터(`vizdoom_collect.py`) 추가 시 코어 그대로 재사용
