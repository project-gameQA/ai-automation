"""
schema.py
---------
표준 궤적 포맷 = 어댑터와 코어 사이의 '계약'.

모든 게임 어댑터는 자기 게임 데이터를 이 형태로 번역하고,
탐지 코어는 오직 이 형태만 읽는다. 새 게임을 붙인다 = 이 포맷으로
뽑아내는 어댑터를 짠다.

구조:
  Episode (한 판)
    ├─ 메타데이터 (게임 id, seed, 결과, [평가용 정답])
    └─ steps: [Step, Step, ...]
         각 Step = 한 틱
           - 보편 필드 : t, action, reward, pos, done   (코어가 게임 몰라도 사용)
           - state     : 게임별 변수 (체력/탄약/속도 ...) -- 유연한 dict
           - events    : 이름 붙은 사건 ("door_opened", "level_loaded")
"""
from dataclasses import dataclass, field, asdict
from typing import Optional, Union
import json


@dataclass
class Step:
    t: int                                  # 스텝 번호 (0,1,2,...)
    action: Union[int, str]                 # 이번 스텝에 봇이 한 행동
    reward: float                           # 이번 스텝 보상/점수 변화
    pos: list                               # 위치 [x, y] 또는 [x, y, z]
    done: bool = False                      # 이 스텝에서 판이 끝났는가
    state: dict = field(default_factory=dict)    # 게임별 변수: {"health":.., "ammo":..}
    events: list = field(default_factory=list)   # 이름 붙은 사건: ["door_opened"]


@dataclass
class Episode:
    game_id: str                            # "minigrid-doorkey-8x8", "vizdoom-corridor"
    adapter_version: str                    # 어댑터 버전 (재현성)
    seed: int
    steps: list = field(default_factory=list)

    # --- 아래 2개는 '평가용 정답'. 실제 운영에선 None (버그인지 모르니까) ---
    injected_defect: Optional[str] = None   # "softlock" | "teleport" | ... | None(정상)
    outcome: Optional[str] = None           # "success" | "fail" | "crash" | "timeout"

    meta: dict = field(default_factory=dict)  # 자유 필드: 엔진, 맵 이름, 빌드 버전 등

    # ---------- 직렬화 ----------
    def to_json_line(self) -> str:
        """에피소드 하나 -> JSON 한 줄 (데이터셋은 .jsonl 한 줄 = 한 판)."""
        return json.dumps(asdict(self), ensure_ascii=False)

    @staticmethod
    def from_dict(d: dict) -> "Episode":
        steps = [Step(**s) for s in d.pop("steps", [])]
        return Episode(steps=steps, **d)


def save_dataset(episodes, path):
    """에피소드 리스트를 .jsonl 로 저장 (한 줄 = 한 판)."""
    with open(path, "w", encoding="utf-8") as f:
        for ep in episodes:
            f.write(ep.to_json_line() + "\n")


def load_dataset(path):
    """.jsonl 을 Episode 리스트로 로드."""
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                out.append(Episode.from_dict(json.loads(line)))
    return out
