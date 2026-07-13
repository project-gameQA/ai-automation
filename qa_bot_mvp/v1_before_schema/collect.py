"""
collect.py
----------
봇(에이전트)에게 게임을 시키고, 한 판(에피소드)마다
(위치, 방향, 행동, 보상) 시퀀스 = 궤적(trajectory)을 기록.

여기서 봇은 그냥 '무작위로 행동하는' 단순.
데이터 생성이 목적.
(강화학습 에이전트는 나중 확장으로 남겨둠...)
"""
import numpy as np
import gymnasium as gym
import minigrid  # noqa: F401  (import 만으로 MiniGrid 환경들이 등록됨)
from env_defects import DefectWrapper

ENV_ID = "MiniGrid-DoorKey-8x8-v0"
MAX_STEPS = 200


def rollout(defect=None, seed=0):
    """게임 한 판을 플레이하고 궤적 하나를 반환."""
    rng = np.random.default_rng(seed)
    env = DefectWrapper(gym.make(ENV_ID), defect=defect, rng=rng)
    obs, info = env.reset(seed=seed)

    traj = {"pos": [], "dir": [], "action": [], "reward": []}
    traj["pos"].append(tuple(int(v) for v in env.unwrapped.agent_pos))
    traj["dir"].append(int(obs["direction"]))

    terminated = truncated = False
    for _ in range(MAX_STEPS):
        action = int(rng.integers(0, 6))  # left,right,forward,pickup,drop,toggle 중 무작위
        obs, reward, terminated, truncated, info = env.step(action)
        traj["action"].append(action)
        traj["reward"].append(float(reward))
        traj["pos"].append(tuple(int(v) for v in env.unwrapped.agent_pos))
        traj["dir"].append(int(obs["direction"]))
        if terminated or truncated:
            break

    env.close()
    traj["terminated"] = bool(terminated)  # 목표 도달 성공 여부
    return traj


def collect(n, defect=None, seed0=0):
    """n판을 플레이해서 궤적 리스트를 반환."""
    return [rollout(defect=defect, seed=seed0 + i) for i in range(n)]
