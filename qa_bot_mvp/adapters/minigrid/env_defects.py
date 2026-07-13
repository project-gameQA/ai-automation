"""
env_defects.py  (MiniGrid, 미묘한 결함)
--------------------------------------
[방향 전환] 예전엔 명백한 결함 3종(softlock/teleport/reward_bug)을 주입했음.
그러나 그것들은 규칙(하드 오라클)으로 잡는 게 맞는 종류라, 비지도 이상탐지의
'의의'를 증명하는 데는 부적합했음(NOTE_oracle_vs_anomaly.md 참고).

그래서 이제는 '규칙으로는 경계를 못 긋되 정상은 아닌' 미묘한 결함을 주입함.
이상탐지가 정상 플레이 패턴을 학습해서 '어딘가 벗어남'을 잡는 걸 보이는 것이 목적.

주입하는 미묘한 결함 2종 (서로 다른 특징 축을 건드리도록 설계):
  - under_explore : 행동은 정상적으로 다양하지만, 좁은 영역에만 갇혀 돌아다님.
                    -> unique_cells 낮아짐 / action_entropy 는 정상.
  - low_entropy   : 넓게 돌아다니긴 하지만, 특정 행동(전진)에 치우침.
                    -> action_entropy 낮아짐 / unique_cells 는 정상.

두 결함이 다른 축에 나타나므로, 리포트에서 '무엇이 어느 특징으로 잡혔는지'가
분리되어 보임. (겹치면 사실상 같은 결함을 두 번 세는 꼴이라 일부러 축을 갈랐음)
"""
import numpy as np
import gymnasium as gym

FORWARD = 2                 # MiniGrid에서 '전진' 행동
LOW_ENTROPY_BIAS = 0.60     # low_entropy: 이 확률로 전진을 강제(나머지는 무작위)


class DefectWrapper(gym.Wrapper):
    def __init__(self, env, defect=None, rng=None):
        super().__init__(env)
        self.defect = defect                      # None이면 정상 플레이
        self.rng = rng or np.random.default_rng()
        self._region = None                        # under_explore 제한 영역

    def reset(self, **kwargs):
        out = self.env.reset(**kwargs)
        if self.defect == "under_explore":
            # 시작 위치 주변의 좁은 상자로 이동을 제한(규칙으론 경계 못 긋는 미묘한 강도).
            self._region = (1, 3, 1, 2)   # (xmin, xmax, ymin, ymax) 포함
        return out

    def choose_action(self, rng):
        """봇의 행동을 고른다. low_entropy면 전진에 치우치게(엔트로피↓)."""
        if self.defect == "low_entropy":
            if rng.random() < LOW_ENTROPY_BIAS:
                return FORWARD
            return int(rng.integers(0, 6))
        # 정상 / under_explore : 균일 무작위 (행동 다양성은 정상)
        return int(rng.integers(0, 6))

    def step(self, action):
        prev_pos = tuple(int(v) for v in self.env.unwrapped.agent_pos)
        obs, reward, terminated, truncated, info = self.env.step(action)

        # under_explore: 제한 영역을 벗어나면 이동을 무효화(이전 위치로 되돌림).
        # 행동 자체는 다양하게 유지되므로 entropy는 정상, 공간 커버리지만 낮아짐.
        if self.defect == "under_explore" and self._region is not None:
            x, y = (int(v) for v in self.env.unwrapped.agent_pos)
            xmin, xmax, ymin, ymax = self._region
            if not (xmin <= x <= xmax and ymin <= y <= ymax):
                self.env.unwrapped.agent_pos = prev_pos

        return obs, reward, terminated, truncated, info
