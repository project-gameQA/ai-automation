"""
features.py  (코어)
------------------
Episode 하나를 -> 특징 dict {이름: 스칼라} 로 변환.

[이번 리팩터링의 핵심]
예전: featurize가 '고정 8차원 배열'을 리턴했다. → 어댑터가 게임별 특징을
      아무리 넣어도 코어가 받을 자리가 없었다(8차원에 하드코딩).
지금: featurize가 dict를 리턴한다.
      - 공통 특징(움직임/보상): 코어가 항상 계산. 게임과 무관.
      - 게임별 특징: 어댑터가 ep.game_features에 넣어준 것을 그대로 병합.
        코어는 그 '이름과 의미'를 모른다. 그냥 숫자로 취급해 벡터에 실을 뿐.
      → 어댑터가 특징을 0개 넣든 100개 넣든 코어 수정 없이 처리된다.

계약(개방-폐쇄 원칙):
  - 새 게임/새 스칼라 특징 추가  = 어댑터만 수정. 코어 라인 변경 0.
  - 한 번의 파이프라인 실행(=한 게임) 안에서는 모든 에피소드가 같은 특징 키를
    가진다(build_matrix가 이걸 강제). 게임이 다르면 키/차원이 달라도 됨.

게임 스케일 파라미터(move_eps, cell_size)는 Episode.meta에서 읽어 자기 조정.
  - move_eps  : 이 거리 이하 이동은 '안 움직임'. 격자=0, 연속=작은 양수.
  - cell_size : 위치를 이 크기로 양자화해 '고유 칸'을 센다. 격자=1, 연속=크게.
기본값(eps=0, cell_size=1)은 MiniGrid 동작과 완전히 동일.
"""
import numpy as np

# 코어가 항상 계산하는 공통 특징(게임 무관). 게임별 특징은 여기 없음.
COMMON_FEATURE_NAMES = [
    "length", "total_reward", "success", "unique_cells",
    "frac_no_move", "max_freeze_streak", "max_jump", "action_entropy",
]

# 하위호환용 별칭(예전 import 깨지지 않게). 공통 특징 이름과 동일.
FEATURE_NAMES = COMMON_FEATURE_NAMES


def _manhattan(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def common_features(ep) -> dict:
    """Episode -> 공통 특징 dict. (예전 featurize의 8개와 값이 완전히 동일)"""
    move_eps = ep.meta.get("move_eps", 0.0)     # 격자=0, 연속=작은 양수
    cell_size = ep.meta.get("cell_size", 1.0)   # 격자=1, 연속=크게

    init = ep.meta.get("init_pos")
    pos = ([tuple(init)] if init is not None else []) + [tuple(s.pos) for s in ep.steps]
    actions = np.array([s.action for s in ep.steps] or [0], dtype=int)
    rewards = np.array([s.reward for s in ep.steps] or [0.0], dtype=float)

    jumps = np.array([_manhattan(pos[i], pos[i + 1]) for i in range(len(pos) - 1)] or [0.0])
    no_move = (jumps <= move_eps).astype(int)

    max_streak = cur = 0
    for nm in no_move:
        cur = cur + 1 if nm else 0
        max_streak = max(max_streak, cur)

    cells = {(round(p[0] / cell_size), round(p[1] / cell_size)) for p in pos}

    counts = np.bincount(actions, minlength=7).astype(float)
    p = counts / counts.sum()
    p = p[p > 0]
    entropy = float(-(p * np.log(p)).sum())

    return {
        "length":            float(len(actions)),
        "total_reward":      float(rewards.sum()),
        "success":           float(ep.outcome == "success"),
        "unique_cells":      float(len(cells)),
        "frac_no_move":      float(no_move.mean()),
        "max_freeze_streak": float(max_streak),
        "max_jump":          float(jumps.max()),
        "action_entropy":    entropy,
    }


def featurize(ep) -> dict:
    """
    Episode -> 특징 dict {이름: 스칼라}.
    = 공통 특징(코어) + 게임별 특징(어댑터가 ep.game_features에 넣은 것).
    코어는 game_features의 키 이름을 해석하지 않는다. 그냥 float로 병합할 뿐.
    """
    feats = common_features(ep)
    for name, value in ep.game_features.items():
        feats[name] = float(value)     # 이름 안 봄. 숫자로만 취급.
    return feats


def build_matrix(episodes, names=None):
    """
    Episode 리스트 -> (X: 2D float 배열, names: 열 이름 리스트).

    - names=None : 첫 에피소드의 키 순서로 열을 정한다(공통 특징 먼저, 그다음 어댑터 특징).
    - names 지정 : 그 순서/집합을 강제(학습셋과 테스트셋의 열을 정렬시킬 때 사용).

    같은 실행 안의 모든 에피소드는 같은 특징 키를 가져야 한다(안 그러면 오류).
    이렇게 하면 어댑터가 특징을 N개 넣어도 X의 열 수가 자동으로 N만큼 늘어난다.
    (열 개수를 코어가 하드코딩하지 않음 = '8차원 제한' 제거)
    """
    dicts = [featurize(ep) for ep in episodes]
    if not dicts:
        return np.empty((0, 0)), (names or [])

    if names is None:
        names = list(dicts[0].keys())     # dict는 삽입 순서 보존 → 결정적
    name_set = set(names)

    for i, d in enumerate(dicts):
        if set(d.keys()) != name_set:
            missing = name_set - set(d.keys())
            extra = set(d.keys()) - name_set
            raise ValueError(
                f"에피소드 {i}의 특징 키가 불일치합니다. "
                f"빠짐={sorted(missing)} 초과={sorted(extra)}. "
                f"같은 실행의 모든 에피소드는 동일한 특징 집합을 가져야 합니다."
            )

    X = np.array([[d[n] for n in names] for d in dicts], dtype=float)
    return X, names
