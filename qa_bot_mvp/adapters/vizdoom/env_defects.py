"""
adapters/vizdoom/env_defects.py  (ViZDoom, 미묘한 결함)
------------------------------------------------------
[방향 전환] 예전엔 softlock/reward_bug(하드 오라클용)를 주입했음. 이제는 규칙으론
경계를 못 긋는 '미묘한' 결함으로 교체해 이상탐지의 의의를 검증함
(MiniGrid 어댑터와 동일한 결함 개념: under_explore / low_entropy).

주의: ViZDoom은 파이썬으로 플레이어 좌표를 '설정'할 수 없음(엔진 ACS 필요).
      하지만 좌표를 '읽는' 것은 가능하므로, under_explore는 위치를 읽어서
      시작점에서 멀어지면 이동 버튼을 억제하는 방식으로 구현함.
      (MiniGrid는 위치를 되돌렸지만, Doom은 이동을 막는 것으로 같은 효과를 냄)

주입 결함 2종:
  - under_explore : 시작점에서 일정 거리 이상 멀어지면 이동 버튼(전진/좌우이동)을
                    억제 -> 좁은 영역에 갇힘. 회전은 허용해 행동 다양성은 유지.
                    -> unique_cells 낮아짐 / action_entropy 는 비교적 정상.
  - low_entropy   : 특정 확률로 '전진만' 하도록 강제 -> 행동이 전진에 치우침.
                    -> action_entropy 낮아짐 / 공간 커버리지는 비교적 정상.
"""
import numpy as np

MOVE_FORWARD = 2                 # my_way_home 버튼 인덱스: 전진
MOVE_BUTTONS = (2, 3, 4)         # 전진 / 좌이동 / 우이동 (회전 0,1은 제외)
LOW_ENTROPY_BIAS = 0.60          # low_entropy: 이 확률로 '전진만' 강제
UNDER_EXPLORE_RADIUS = 128.0     # under_explore: 시작점에서 이 거리 넘으면 이동 억제


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
        """
        결함에 따라 이번 스텝의 행동(버튼 리스트)을 변형한다.
        여기서 반환하는 값이 실제 실행되고 로깅되므로, 특징(entropy 등)에 반영됨.
        cur_pos: 이번 스텝 '이전'의 현재 위치 [x, y, z] (under_explore 판단용).
        """
        if self.defect == "low_entropy":
            # 특정 확률로 전진만 누름(나머지 버튼 0) -> 행동이 전진에 치우침
            if self.rng.random() < LOW_ENTROPY_BIAS:
                a = [0] * self.n_buttons
                if self.n_buttons > MOVE_FORWARD:
                    a[MOVE_FORWARD] = 1
                return a
            return action

        if self.defect == "under_explore" and self.init_pos is not None and cur_pos is not None:
            # 시작점에서 반경을 벗어나면 이동 버튼을 눌러도 무시(0). 회전은 그대로.
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
