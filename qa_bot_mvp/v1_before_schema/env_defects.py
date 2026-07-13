"""
env_defects.py
--------------
게임 환경을 감싸서 일부러 고장을 주입하는 wrapper.

핵심 아이디어: 우리가 직접 버그를 심었으니 "이 에피소드는 버그다"라는
정답(ground truth)을 안다. 그래서 탐지 모델 성능을 정확히 평가할 수 있....


주입하는 결함 3종:
  - softlock   : 특정 시점부터 봇 입력을 무시 -> 캐릭터가 얼어버림(끼임)
  - teleport   : 낮은 확률로 캐릭터를 임의 칸으로 순간이동 (물리 글리치)
  - reward_bug : 엉뚱한 보상을 흘림 (게임 로직 버그)
"""
import numpy as np
import gymnasium as gym


class DefectWrapper(gym.Wrapper):
    def __init__(self, env, defect=None, rng=None):
        super().__init__(env)
        self.defect = defect                      # None이면 정상 플레이
        self.rng = rng or np.random.default_rng()
        self._frozen = False
        self._step_count = 0
        self._softlock_at = None

    def reset(self, **kwargs):
        self._frozen = False
        self._step_count = 0
        if self.defect == "softlock":
            # 이번 판에서 몇 번째 스텝부터 얼어붙을지 무작위로 정함
            self._softlock_at = int(self.rng.integers(3, 12))
        return self.env.reset(**kwargs)

    def step(self, action):
        self._step_count += 1

        # --- SOFTLOCK: 트리거 이후 모든 행동 무시, 위치 고정 ---
        if self.defect == "softlock":
            if self._step_count >= self._softlock_at:
                self._frozen = True
            if self._frozen:
                pos = self.env.unwrapped.agent_pos
                obs, _, terminated, truncated, info = self.env.step(6)  # 'done' = 위치 변화 없음
                self.env.unwrapped.agent_pos = pos                      # 혹시 몰라 위치 강제 고정
                return obs, 0.0, False, truncated, info

        obs, reward, terminated, truncated, info = self.env.step(action)

        # --- TELEPORT: 5% 확률로 임의 칸으로 점프 ---
        if self.defect == "teleport" and self.rng.random() < 0.05:
            w, h = self.env.unwrapped.width, self.env.unwrapped.height
            x = int(self.rng.integers(1, w - 1))
            y = int(self.rng.integers(1, h - 1))
            self.env.unwrapped.agent_pos = (x, y)

        # --- REWARD BUG: 10% 확률로 가짜 보상 주입 ---
        if self.defect == "reward_bug" and self.rng.random() < 0.10:
            reward += 0.3

        return obs, reward, terminated, truncated, info
