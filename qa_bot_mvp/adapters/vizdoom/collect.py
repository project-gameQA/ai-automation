"""
adapters/vizdoom/collect.py  (ViZDoom 어댑터 — 배포용, 결함 코드 없음)
--------------------------------------------------------------------
실제 Doom(ViZDoom)을 무작위 봇이 플레이하고 한 판을 표준 Episode/Step 으로 반환.
이 파일은 '정상 플레이'만 안다. 결함 주입은 demo/vizdoom_defects.py 가 담당하며,
collect(hook_factory=...) / rollout(hook=...) 의 optional 자리로 끼운다.

시나리오: my_way_home (내비게이션 과제).
"""
import os
import shutil
import tempfile
import numpy as np
import vizdoom as vzd
from qa_core.schema import Episode, Step

SCENARIO = "my_way_home"
GAME_ID = "vizdoom-my-way-home"
ADAPTER_VERSION = "1.0"
MAX_STEPS = 150
FRAME_SKIP = 4

MOVE_EPS = 2.0
CELL_SIZE = 32.0


class _EngineWorkdir:
    """with 블록 동안 쓰기 가능한 임시 폴더로 이동하고, 끝나면 복귀·정리.
    (ViZDoom이 _vizdoom.ini / _vizdoom/ 를 CWD에 쓰는데, 쓰기 불가 폴더면 멈추는 문제 회피)"""
    def __enter__(self):
        self.saved_cwd = os.getcwd()
        self.path = tempfile.mkdtemp(prefix="vizdoom_work_")
        os.chdir(self.path)
        return self

    @property
    def config_path(self):
        return os.path.join(self.path, "_vizdoom.ini")

    def __exit__(self, *exc):
        os.chdir(self.saved_cwd)
        shutil.rmtree(self.path, ignore_errors=True)
        return False


def _make_game(config_path):
    game = vzd.DoomGame()
    game.set_doom_config_path(config_path)
    game.load_config(os.path.join(vzd.scenarios_path, f"{SCENARIO}.cfg"))
    game.set_window_visible(False)
    game.set_mode(vzd.Mode.PLAYER)
    game.clear_available_game_variables()
    for gv in [vzd.GameVariable.POSITION_X, vzd.GameVariable.POSITION_Y,
               vzd.GameVariable.POSITION_Z, vzd.GameVariable.HEALTH,
               vzd.GameVariable.ANGLE]:
        game.add_available_game_variable(gv)
    game.set_console_enabled(False)
    game.init()
    return game


def action_to_int(action):
    """버튼 조합(list of 0/1)을 정수 하나로 인코딩 (엔트로피 특징용)."""
    v = 0
    for b in action:
        v = (v << 1) | int(b)
    return v % 7


def rollout(game, seed=0, hook=None):
    """
    한 판 플레이 -> Episode. hook 없으면 순수 정상 봇.
    hook 규약 - reset(n_buttons, init_pos, rng), modify(t, action, cur_pos)->action, label
    """
    rng = np.random.default_rng(seed)
    game.set_seed(seed)
    game.new_episode()

    n_buttons = game.get_available_buttons_size()
    gv0 = game.get_state().game_variables
    init_pos = [float(gv0[0]), float(gv0[1]), float(gv0[2])]
    prev_angle = float(gv0[4])
    if hook is not None:
        hook.reset(n_buttons, init_pos, rng)

    steps = []
    reached_goal = False
    for t in range(MAX_STEPS):
        if game.is_episode_finished():
            break
        gv_now = game.get_state().game_variables
        cur_pos = [float(gv_now[0]), float(gv_now[1]), float(gv_now[2])]

        action = [int(rng.integers(0, 2)) for _ in range(n_buttons)]
        if hook is not None:
            action = hook.modify(t, action, cur_pos)     # 결함 주입(배포판엔 없음)
        reward = game.make_action(action, FRAME_SKIP)

        finished = game.is_episode_finished()
        if finished:
            pos = steps[-1].pos if steps else init_pos
            health = steps[-1].state["health"] if steps else float(gv0[3])
            angle = prev_angle
            reached_goal = game.get_total_reward() > 0.5
        else:
            gv = game.get_state().game_variables
            pos = [float(gv[0]), float(gv[1]), float(gv[2])]
            health = float(gv[3])
            angle = float(gv[4])

        d = abs(angle - prev_angle)
        turn = min(d, 360.0 - d)
        prev_angle = angle

        steps.append(Step(
            t=t, action=action_to_int(action), reward=float(reward),
            pos=pos, done=bool(finished), state={"health": health},
            game_features={"turn": turn},
        ))
        if finished:
            break

    return Episode(
        game_id=GAME_ID, adapter_version=ADAPTER_VERSION, seed=seed, steps=steps,
        injected_defect=(hook.label if hook is not None else None),
        outcome="success" if reached_goal else "timeout",
        meta={"init_pos": init_pos, "move_eps": MOVE_EPS, "cell_size": CELL_SIZE,
              "scenario": SCENARIO,
              "max_step_dist": 100,
              "freeze_limit": 60},
    )


def collect(n, seed0=0, hook_factory=None):
    """정상 봇으로 n판 수집(배포용). hook_factory(seed)->hook 를 주면 결함 주입(시연용).
    엔진 파일을 쓰기 가능한 임시 폴더로 몰아넣어 'write protected' 멈춤을 피한다."""
    with _EngineWorkdir() as wd:
        game = _make_game(wd.config_path)
        try:
            out = []
            for i in range(n):
                hook = hook_factory(seed0 + i) if hook_factory is not None else None
                out.append(rollout(game, seed=seed0 + i, hook=hook))
            return out
        finally:
            game.close()
