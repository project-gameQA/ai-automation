"""
adapters/vizdoom/collect.py  (ViZDoom 어댑터)
--------------------------------------------
실제 Doom(ViZDoom)을 무작위 봇이 플레이하고, 한 판을
표준 Episode/Step 포맷으로 반환한다. qa_core는 손대지 않는다.

MiniGrid 어댑터와 '표준 포맷을 뱉는다'는 계약이 동일하므로,
qa_core/features.py, qa_core/detect.py를 그대로 재사용할 수 있다.

시나리오: my_way_home (방들을 이동해 목표를 찾는 내비게이션 과제.
          MiniGrid-DoorKey의 '이동+목표' 구조에 대응).
"""
import os
import numpy as np
import vizdoom as vzd
from qa_core.schema import Episode, Step
from adapters.vizdoom.env_defects import VizdoomDefect

SCENARIO = "my_way_home"
GAME_ID = "vizdoom-my-way-home"
ADAPTER_VERSION = "1.0"
MAX_STEPS = 150
FRAME_SKIP = 4

# 연속 좌표계라 격자 게임과 다른 스케일 파라미터를 쓴다.
MOVE_EPS = 2.0      # 2 유닛 이하 이동은 '정지'로 간주 (softlock 탐지용)
CELL_SIZE = 32.0    # 위치를 32유닛 격자로 양자화해 '고유 칸' 계산


def _make_game():
    game = vzd.DoomGame()
    game.load_config(os.path.join(vzd.scenarios_path, f"{SCENARIO}.cfg"))
    game.set_window_visible(False)          # 헤드리스
    game.set_mode(vzd.Mode.PLAYER)
    game.clear_available_game_variables()   # 변수 순서를 우리가 지정
    for gv in [vzd.GameVariable.POSITION_X, vzd.GameVariable.POSITION_Y,
               vzd.GameVariable.POSITION_Z, vzd.GameVariable.HEALTH]:
        game.add_available_game_variable(gv)
    game.set_console_enabled(False)
    game.init()
    return game


def rollout(game, defect=None, seed=0):
    """게임 한 판을 플레이하고 Episode 하나를 반환. (game 객체는 재사용)"""
    rng = np.random.default_rng(seed)
    game.set_seed(seed)
    game.new_episode()

    n_buttons = game.get_available_buttons_size()
    dfx = VizdoomDefect(defect=defect, rng=rng)
    dfx.reset(n_buttons)

    gv0 = game.get_state().game_variables
    init_pos = [float(gv0[0]), float(gv0[1]), float(gv0[2])]

    steps = []
    reached_goal = False
    for t in range(MAX_STEPS):
        if game.is_episode_finished():
            break
        # 무작위 행동 (MOVE_FORWARD 쪽에 약간 가중해 실제 이동 유도)
        action = [int(rng.integers(0, 2)) for _ in range(n_buttons)]
        action = dfx.apply_action(t, action)                 # softlock 주입
        reward = game.make_action(action, FRAME_SKIP)
        reward = dfx.corrupt_reward(float(reward))           # reward_bug 주입

        finished = game.is_episode_finished()
        if finished:
            # 종료 시 상태를 못 읽으므로 마지막 위치를 재사용
            pos = steps[-1].pos if steps else init_pos
            health = steps[-1].state["health"] if steps else float(gv0[3])
            reached_goal = game.get_total_reward() > 0.5     # 목표 도달 시 +1 보상
        else:
            gv = game.get_state().game_variables
            pos = [float(gv[0]), float(gv[1]), float(gv[2])]
            health = float(gv[3])

        steps.append(Step(
            t=t, action=action_to_int(action), reward=reward,
            pos=pos, done=bool(finished), state={"health": health},
        ))
        if finished:
            break

    return Episode(
        game_id=GAME_ID, adapter_version=ADAPTER_VERSION, seed=seed, steps=steps,
        injected_defect=defect,
        outcome="success" if reached_goal else "timeout",
        meta={"init_pos": init_pos, "move_eps": MOVE_EPS, "cell_size": CELL_SIZE,
              "scenario": SCENARIO},
    )


def action_to_int(action):
    """버튼 조합(list of 0/1)을 정수 하나로 인코딩 (엔트로피 특징용)."""
    v = 0
    for b in action:
        v = (v << 1) | int(b)
    return v % 7    # bincount(minlength=7) 범위에 맞춤


def collect(n, defect=None, seed0=0):
    """n판을 플레이해서 Episode 리스트를 반환. (game 객체 1개 재사용)"""
    game = _make_game()
    try:
        return [rollout(game, defect=defect, seed=seed0 + i) for i in range(n)]
    finally:
        game.close()
