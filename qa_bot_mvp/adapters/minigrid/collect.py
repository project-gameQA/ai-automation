"""
collect.py  (MiniGrid 어댑터)
----------------------------
봇에게 게임을 시키고, 한 판(에피소드)을 표준 Episode/Step 포맷으로 반환한다.
schema.py의 표준 포맷으로 뽑으므로, 이 파일이 곧 'MiniGrid 어댑터'다.
나중에 ViZDoom을 붙일 땐 이 파일과 같은 형태로 Episode를 반환하는
새 어댑터(vizdoom_collect.py)만 추가하면 된다.

봇은 여전히 '무작위로 행동하는' 단순 봇. (강화학습은 나중 확장)
"""
import numpy as np
import gymnasium as gym
import minigrid  # noqa: F401  (import 만으로 MiniGrid 환경들이 등록됨)
from adapters.minigrid.env_defects import DefectWrapper
from qa_core.schema import Episode, Step

ENV_ID = "MiniGrid-DoorKey-8x8-v0"
GAME_ID = "minigrid-doorkey-8x8"
ADAPTER_VERSION = "1.0"
MAX_STEPS = 200


def rollout(defect=None, seed=0):
    """게임 한 판을 플레이하고 Episode 하나를 반환."""
    rng = np.random.default_rng(seed)
    env = DefectWrapper(gym.make(ENV_ID), defect=defect, rng=rng)
    obs, info = env.reset(seed=seed)

    # 리셋 직후 위치 (스텝 실행 전). BEFORE 형식과 동일한 pos 시퀀스를
    # 특징 계산에서 복원하기 위해 meta에 담아둔다.
    init_pos = [int(v) for v in env.unwrapped.agent_pos]

    steps = []
    terminated = truncated = False
    for t in range(MAX_STEPS):
        action = int(rng.integers(0, 6))  # left,right,forward,pickup,drop,toggle 중 무작위
        obs, reward, terminated, truncated, info = env.step(action)
        steps.append(Step(
            t=t,
            action=action,
            reward=float(reward),
            pos=[int(v) for v in env.unwrapped.agent_pos],   # 보편 필드
            done=bool(terminated or truncated),
            state={"dir": int(obs["direction"])},            # MiniGrid 전용 → state
        ))
        if terminated or truncated:
            break

    env.close()
    return Episode(
        game_id=GAME_ID,
        adapter_version=ADAPTER_VERSION,
        seed=seed,
        steps=steps,
        injected_defect=defect,                              # 정답 라벨(운영 시 None)
        outcome="success" if terminated else "timeout",
        meta={"init_pos": init_pos},
    )


def collect(n, defect=None, seed0=0):
    """n판을 플레이해서 Episode 리스트를 반환."""
    return [rollout(defect=defect, seed=seed0 + i) for i in range(n)]
