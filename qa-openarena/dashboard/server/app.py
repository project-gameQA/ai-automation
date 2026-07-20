"""대시보드용 FastAPI 서버다.

역할은 두 가지다. (1) 텔레메트리 파일(qa_telemetry.jsonl)을 읽어 하드 인바리언트
검출기를 돌리고, (2) 그 결과(버그 목록)를 리액트 대시보드가 가져갈 수 있는 API로
내보낸다. 지금은 정적 버전이라 파일 전체를 한 번에 검출해 돌려준다. 이후 실시간(새 줄만
계속 읽기)과 집계(중복 묶기)를 이 위에 얹는다.

실행: uvicorn app:app --reload  (dashboard/server 폴더에서)
필요 패키지: pip install fastapi "uvicorn[standard]"
"""

import os    # 환경변수로 텔레메트리 경로를 받기 위해 사용한다.
import sys   # 프로젝트 루트를 모듈 검색 경로에 추가하기 위해 사용한다.
from pathlib import Path  # 파일 경로를 다루기 위해 사용한다.

# 이 파일(dashboard/server/app.py)에서 두 단계 위가 프로젝트 루트(qa-openarena)다.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
# 프로젝트 루트를 검색 경로에 넣어야 qa 패키지(검출기)를 import 할 수 있다.
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import FastAPI  # 웹 API 서버를 만드는 프레임워크다.
from fastapi.middleware.cors import CORSMiddleware  # 브라우저의 교차 출처 요청을 허용하기 위해 사용한다.

from qa.telemetry import MapBounds  # 검출기에 넘길 맵 경계 값 객체다.
from qa.invariants import InvariantChecker  # 하드 인바리언트 검출기다.
from qa.replay_source import iter_samples_from_jsonl  # 텔레메트리 파일을 StateSample로 읽는 소스다.

# 검출할 텔레메트리 파일 경로. 환경변수 QA_TELEMETRY 로 덮어쓸 수 있고, 없으면 기본값을 쓴다.
TELEMETRY_PATH = os.environ.get("QA_TELEMETRY", str(PROJECT_ROOT / "tests" / "qa_telemetry.jsonl"))

# 검출기 설정값. run_invariants.py 와 동일하게 유지해야 한다(맵마다 다른 보정값).
# TODO: 나중에 qa/config.py 같은 공용 설정으로 빼서 중복을 없앤다.
BOUNDS = MapBounds(min_x=-852, max_x=1721, min_y=-483, max_y=2097, floor_z=-29, ceiling_z=622)
MAX_SPEED = 1214
STUCK_SECONDS = 2.0
STUCK_EPSILON = 2.0
MAX_POSSIBLE_HEALTH = 250


def run_detection(path: str) -> list[dict]:
    """텔레메트리 파일을 읽어 검출한 버그들을 대시보드용 딕셔너리 목록으로 반환한다."""
    if not Path(path).exists():  # 파일이 없으면
        return []                # 빈 목록을 반환한다(대시보드는 "0건"으로 표시된다).

    # 매 호출마다 새 검출기를 만든다(끼임 규칙의 내부 이력이 초기화되어야 하므로).
    checker = InvariantChecker(
        bounds=BOUNDS,
        max_speed=MAX_SPEED,
        stuck_seconds=STUCK_SECONDS,
        stuck_epsilon=STUCK_EPSILON,
        max_possible_health=MAX_POSSIBLE_HEALTH,
    )

    records: list[dict] = []  # 대시보드로 보낼 결과를 담을 목록이다.
    next_id = 0               # 각 탐지에 붙일 고유 번호다.
    for sample in iter_samples_from_jsonl(path):  # 텔레메트리를 한 틱씩 읽는다.
        bugs = checker.check(sample)              # 그 틱의 상태를 검출기에 넘겨 버그 목록을 받는다.
        for bug in bugs:                          # 이번 틱에서 나온 버그들을 순회한다.
            next_id += 1                          # 고유 번호를 매긴다.
            # 검출 결과(bug)와 그 순간의 상태(sample)를 합쳐 대시보드가 쓰기 좋은 형태로 만든다.
            records.append({
                "id": next_id,                       # 프론트엔드에서 항목을 구분할 키
                "session": bug.time,                 # 게임 시간(초). 대시보드 시각 표시에 쓴다.
                "entity_id": bug.entity_id,          # 어느 봇인지
                "rule": bug.rule,                    # 위반한 규칙 이름
                "severity": bug.severity.value.upper(),  # "HIGH"/"MEDIUM" (프론트엔드 표기에 맞춘다)
                "message": bug.message,              # 사람이 읽는 설명
                "kind": "hard",                      # 하드 인바리언트 출처(이상탐지와 구분하기 위한 표시)
                # 상세 패널이 쓰는, 그 순간의 텔레메트리 값들
                "x": sample.x, "y": sample.y, "z": sample.z,
                "vx": sample.vx, "vy": sample.vy, "vz": sample.vz,
                "speed": round(sample.speed, 1),
                "health": sample.health, "max_health": sample.max_health,
                "move_input": sample.move_input,
            })
    return records


app = FastAPI(title="OpenArena QA Monitor API")  # API 서버 객체를 만든다.

# 리액트 개발 서버(localhost:5173 등)에서 이 API를 요청할 수 있게 교차 출처를 허용한다.
# 로컬 개발용이라 넉넉히 허용한다. 외부 배포 시에는 출처를 좁혀야 한다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    """서버가 살아 있는지 확인하는 정보용 엔드포인트다."""
    return {
        "service": "OpenArena QA Monitor API",
        "telemetry": TELEMETRY_PATH,           # 지금 읽고 있는 파일 경로
        "telemetry_exists": Path(TELEMETRY_PATH).exists(),  # 파일이 실제로 있는지
    }


@app.get("/api/detections")
def detections():
    """텔레메트리를 검출해 버그 목록을 반환한다(정적 버전: 파일 전체를 한 번에)."""
    recs = run_detection(TELEMETRY_PATH)  # 파일을 읽어 검출을 수행한다.
    return {
        "count": len(recs),   # 총 탐지 수
        "source": TELEMETRY_PATH,  # 어느 파일에서 나왔는지
        "detections": recs,   # 실제 탐지 목록
    }


if __name__ == "__main__":  # 이 파일을 직접 실행하면 개발 서버를 띄운다.
    import uvicorn           # ASGI 서버. FastAPI 앱을 실제로 구동한다.
    uvicorn.run(app, host="127.0.0.1", port=8000)  # localhost:8000 에서 서버를 연다.
