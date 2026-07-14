"""
collect.py  (MiniGrid 어댑터 — 배포용, 결함 코드 없음)
------------------------------------------------------
봇에게 게임을 시키고 한 판(에피소드)을 표준 Episode/Step 포맷으로 반환한다.
이 파일은 '정상 플레이'만 안다. 결함 주입 코드는 포함하지 않는다(배포판에 안 들어감).

시연/검증용 결함 주입은 demo/minigrid_defects.py 가 담당하며, 아래 rollout의
optional hook 자리를 통해 끼운다. hook 이 없으면(기본) 순수 정상 봇이다.

봇은 무작위로 행동하는 단순 봇. (강화학습은 나중 확장)
"""
import numpy as np
import gymnasium as gym
import minigrid  # noqa: F401  (import 만으로 MiniGrid 환경들이 등록됨)
from qa_core.schema import Episode, Step

ENV_ID = "MiniGrid-DoorKey-8x8-v0"
GAME_ID = "minigrid-doorkey-8x8"
ADAPTER_VERSION = "1.0"
MAX_STEPS = 200


def rollout(seed=0, hook=None):
    """
    게임 한 판을 플레이하고 Episode 하나를 반환.
    hook: 시연용 결함 주입 훅(선택). None이면 순수 정상 봇.
          훅 규약 - wrap(env, rng)->env, choose(rng)->action, label(str/None)
    """
    rng = np.random.default_rng(seed)
    env = gym.make(ENV_ID)
    if hook is not None:
        env = hook.wrap(env, rng)          # 결함 모듈이 env를 감쌈(배포판엔 없음)
    obs, info = env.reset(seed=seed)

    init_pos = [int(v) for v in env.unwrapped.agent_pos]

    steps = []
    terminated = truncated = False
    for t in range(MAX_STEPS):
        # 정상 봇: 무작위. 결함 훅이 있으면 훅이 행동을 고른다.
        action = hook.choose(rng) if hook is not None else int(rng.integers(0, 6))
        obs, reward, terminated, truncated, info = env.step(action)
        steps.append(Step(
            t=t,
            action=action,
            reward=float(reward),
            pos=[int(v) for v in env.unwrapped.agent_pos],
            done=bool(terminated or truncated),
            state={"dir": int(obs["direction"])},
        ))
        if terminated or truncated:
            break

    env.close()
    return Episode(
        game_id=GAME_ID,
        adapter_version=ADAPTER_VERSION,
        seed=seed,
        steps=steps,
        injected_defect=(hook.label if hook is not None else None),  # 정상은 None
        outcome="success" if terminated else "timeout",
        meta={"init_pos": init_pos,
              # 하드 오라클(oracle.py)용 물리 파라미터. 배포 시에도 규칙은 도니 여기 둔다.
              "max_step_dist": 1,
              "map_bounds": (1, 6, 1, 6),
              "freeze_limit": 180},
    )


def collect(n, seed0=0):
    """정상 봇으로 n판 플레이 -> Episode 리스트. (결함 없음, 배포용)"""
    return [rollout(seed=seed0 + i) for i in range(n)]
