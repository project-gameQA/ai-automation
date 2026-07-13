"""
features.py
-----------
길이가 제각각인 궤적 하나를 -> 고정 길이 특징 벡터 하나로 변환.

각 특징은 특정 버그를 잡아내도록 설계됨:
  - max_freeze_streak : 오래 멈춰있으면 softlock 신호
  - max_jump          : 한 스텝에 순간이동하면 teleport 신호 (정상은 최대 1칸)
  - total_reward      : 보상이 이상하면 reward_bug 신호
나머지는 '정상 플레이의 통계적 모양'을 표현한다.
"""
import numpy as np

FEATURE_NAMES = [
    "length", "total_reward", "success", "unique_cells",
    "frac_no_move", "max_freeze_streak", "max_jump", "action_entropy",
]


def _manhattan(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def featurize(traj):
    pos = traj["pos"]
    actions = np.array(traj["action"] or [0], dtype=int)
    rewards = np.array(traj["reward"] or [0.0], dtype=float)

    jumps = np.array([_manhattan(pos[i], pos[i + 1]) for i in range(len(pos) - 1)] or [0])
    no_move = (jumps == 0).astype(int)

    # 연속으로 안 움직인 최장 구간 (softlock 탐지)
    max_streak = cur = 0
    for nm in no_move:
        cur = cur + 1 if nm else 0
        max_streak = max(max_streak, cur)

    # 행동 다양성 (엔트로피)
    counts = np.bincount(actions, minlength=7).astype(float)
    p = counts / counts.sum()
    p = p[p > 0]
    entropy = float(-(p * np.log(p)).sum())    # 섀넌 엔트로피공식... 근데 log밑이 2...인가?? -> 자연로그임

    return np.array([
        len(actions),               # 에피소드 길이
        rewards.sum(),              # 총 보상
        float(traj["terminated"]),  # 성공 여부
        len(set(pos)),              # 방문한 고유 칸 수 (탐험량)
        no_move.mean(),             # 안 움직인 스텝 비율
        max_streak,                 # 최장 정지 구간
        jumps.max(),                # 한 스텝 최대 이동거리
        entropy,                    # 행동 다양성
    ], dtype=float)
