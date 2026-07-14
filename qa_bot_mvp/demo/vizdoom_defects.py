"""
demo/vizdoom_defects.py  (시연/검증 전용 — 배포판에 포함되지 않음)
------------------------------------------------------------------
ViZDoom에 결함을 주입하는 봇. 실제 배포에는 쓰이지 않는다(그래서 demo/).

구성:
  - VizdoomDefect : 행동(버튼)을 결함에 맞게 변형 (구 env_defects.py)
  - VizdoomDefectHook : 깨끗한 어댑터 rollout(hook=...) 자리에 끼우는 어댑터
  - collect_defective : 결함 봇으로 n판 수집

주의: Doom은 좌표 설정이 불가(ACS 필요)해 teleport는 주입 못 함.
      stuck(완전 정지)은 행동으로 가능. under_explore는 위치를 읽어 이동 억제로 근사.
"""
import numpy as np

MOVE_FORWARD = 2
MOVE_BUTTONS = (2, 3, 4)
LOW_ENTROPY_BIAS = 0.60
UNDER_EXPLORE_RADIUS = 128.0
OSC_START, OSC_END = 60, 110


class VizdoomDefect:
    def __init__(self, defect=None, rng=None):
        self.defect = defect
        self.rng = rng or np.random.default_rng()
        self.n_buttons = 0
        self.init_pos = None

    def reset(self, n_buttons, init_pos=None):
        self.n_buttons = n_buttons
        self.init_pos = init_pos

    def modify_action(self, step_idx, action, cur_pos):
        if self.defect == "low_entropy":
            if self.rng.random() < LOW_ENTROPY_BIAS:
                a = [0] * self.n_buttons
                if self.n_buttons > MOVE_FORWARD:
                    a[MOVE_FORWARD] = 1
                return a
            return action

        if self.defect == "stuck":
            return [0] * self.n_buttons

        if self.defect == "oscillate_mid" and OSC_START <= step_idx < OSC_END:
            a = [0] * self.n_buttons
            if step_idx % 2 == 0:
                if self.n_buttons > MOVE_FORWARD:
                    a[MOVE_FORWARD] = 1
            else:
                a[0] = 1   # 좌회전
            return a

        if self.defect == "under_explore" and self.init_pos is not None and cur_pos is not None:
            dx = cur_pos[0] - self.init_pos[0]
            dy = cur_pos[1] - self.init_pos[1]
            if (dx * dx + dy * dy) ** 0.5 > UNDER_EXPLORE_RADIUS:
                a = list(action)
                for b in MOVE_BUTTONS:
                    if b < self.n_buttons:
                        a[b] = 0
                return a
            return action

        return action


class VizdoomDefectHook:
    """깨끗한 어댑터 rollout(hook=...) 에 끼우는 결함 훅."""
    def __init__(self, defect):
        self.label = defect
        self._dfx = None

    def reset(self, n_buttons, init_pos, rng):
        self._dfx = VizdoomDefect(defect=self.label, rng=rng)
        self._dfx.reset(n_buttons, init_pos=init_pos)

    def modify(self, t, action, cur_pos):
        return self._dfx.modify_action(t, action, cur_pos)


def collect_defective(n, defect, seed0=0):
    """결함 봇으로 n판 수집. (깨끗한 어댑터 collect에 결함 훅 팩토리를 넘김)"""
    from adapters.vizdoom.collect import collect
    return collect(n, seed0=seed0, hook_factory=lambda s: VizdoomDefectHook(defect))
