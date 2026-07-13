"""
adapters/vizdoom/env_defects.py
-------------------------------
ViZDoom(실제 Doom)용 결함 주입.

MiniGrid는 Gym wrapper로 감쌌지만, ViZDoom은 DoomGame을 직접 다루므로
결함 로직을 수집 루프에서 호출하는 헬퍼 형태로 둔다.

주입 결함:
  - softlock   : 특정 틱부터 입력을 무시(무행동) -> 캐릭터 정지 (진행 불가)
  - reward_bug : 보상에 가짜 값을 흘림

주의: teleport는 ViZDoom에서 파이썬으로 플레이어 좌표를 직접 세팅할 수 없어
      (엔진 ACS 스크립팅 필요) 이번 어댑터에서는 제외한다. -> 향후 확장.
"""
import numpy as np


class VizdoomDefect:
    def __init__(self, defect=None, rng=None):
        self.defect = defect
        self.rng = rng or np.random.default_rng()
        self.n_buttons = 0
        self._frozen = False
        self._softlock_at = None

    def reset(self, n_buttons):
        self.n_buttons = n_buttons
        self._frozen = False
        if self.defect == "softlock":
            self._softlock_at = int(self.rng.integers(5, 25))

    def apply_action(self, step_idx, action):
        """softlock이면 트리거 이후 무행동(정지)을 강제."""
        if self.defect == "softlock":
            if step_idx >= self._softlock_at:
                self._frozen = True
            if self._frozen:
                return [0] * self.n_buttons     # 아무 버튼도 안 누름 -> 정지
        return action

    def corrupt_reward(self, reward):
        """reward_bug이면 10% 확률로 가짜 보상 주입."""
        if self.defect == "reward_bug" and self.rng.random() < 0.10:
            return reward + 0.3
        return reward
