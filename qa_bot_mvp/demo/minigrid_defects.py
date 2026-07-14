"""
demo/minigrid_defects.py  (시연/검증 전용 — 배포판에 포함되지 않음)
------------------------------------------------------------------
MiniGrid에 '결함'을 주입해 봇이 비정상 플레이를 하도록 만든다.
이상탐지/규칙 층이 이 결함들을 잡는지 검증하는 용도이며, 실제 배포에는
쓰이지 않는다(그래서 adapters/ 가 아니라 demo/ 에 둔다).

구성:
  - DefectWrapper : env를 감싸 결함 행동/환경 조작을 주입 (구 env_defects.py)
  - MiniGridDefectHook : 깨끗한 어댑터(collect.rollout)의 hook 자리에 끼우는 어댑터
  - collect_defective : 결함 봇으로 n판 수집

주입 결함:
  under_explore : 좁은 영역에 갇힘 (공간 커버리지↓, 행동 다양성 정상)
  low_entropy   : 전진에 치우침 (행동 다양성↓, 공간 정상)
  oscillate_mid : 판 중간 구간에서 고정 행동 사이클 (순서 이상, 시계열용)
  teleport      : 물리적으로 불가능한 순간이동 (규칙=하드오라클용)
  stuck         : 완전 정지 (규칙=하드오라클용)
"""
import numpy as np
import gymnasium as gym

FORWARD = 2
LOW_ENTROPY_BIAS = 0.60
OSC_START, OSC_END = 60, 110
OSC_CELLS = [(3, 3), (4, 3)]


class DefectWrapper(gym.Wrapper):
    def __init__(self, env, defect=None, rng=None):
        super().__init__(env)
        self.defect = defect
        self.rng = rng or np.random.default_rng()
        self._region = None

    def reset(self, **kwargs):
        out = self.env.reset(**kwargs)
        self._t = 0
        if self.defect == "under_explore":
            self._region = (1, 3, 1, 2)   # 좁은 상자로 이동 제한
        return out

    def choose_action(self, rng):
        self._ta = getattr(self, "_ta", 0) + 1
        if self.defect == "low_entropy":
            if rng.random() < LOW_ENTROPY_BIAS:
                return FORWARD
            return int(rng.integers(0, 6))
        if self.defect == "oscillate_mid" and OSC_START <= self._ta < OSC_END:
            return FORWARD if (self._ta % 2 == 0) else 0   # 전진/좌회전 사이클
        return int(rng.integers(0, 6))

    def step(self, action):
        self._t = getattr(self, "_t", 0) + 1
        prev_pos = tuple(int(v) for v in self.env.unwrapped.agent_pos)
        obs, reward, terminated, truncated, info = self.env.step(action)

        if self.defect == "under_explore" and self._region is not None:
            x, y = (int(v) for v in self.env.unwrapped.agent_pos)
            xmin, xmax, ymin, ymax = self._region
            if not (xmin <= x <= xmax and ymin <= y <= ymax):
                self.env.unwrapped.agent_pos = prev_pos
        # oscillate_mid는 choose_action에서 처리
        elif self.defect == "teleport" and self.rng.random() < 0.15:
            self.env.unwrapped.agent_pos = (int(self.rng.integers(1, 7)),
                                            int(self.rng.integers(1, 7)))
        elif self.defect == "stuck":
            self.env.unwrapped.agent_pos = prev_pos

        return obs, reward, terminated, truncated, info


class MiniGridDefectHook:
    """깨끗한 어댑터 rollout(hook=...) 에 끼우는 결함 훅."""
    def __init__(self, defect):
        self.label = defect
        self._w = None

    def wrap(self, env, rng):
        self._w = DefectWrapper(env, defect=self.label, rng=rng)
        return self._w

    def choose(self, rng):
        return self._w.choose_action(rng)


def collect_defective(n, defect, seed0=0):
    """결함 봇으로 n판 수집. (깨끗한 어댑터의 rollout에 결함 훅을 끼움)"""
    from adapters.minigrid.collect import rollout
    return [rollout(seed=seed0 + i, hook=MiniGridDefectHook(defect)) for i in range(n)]
