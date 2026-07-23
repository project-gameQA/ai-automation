"""대시보드용 FastAPI 서버다.

역할은 네 가지다.
 (1) 텔레메트리 파일을 감시하며 새로 붙은 줄만 읽고,
 (2) 하드 인바리언트 검출기를 돌려 프레임 단위 탐지를 만들고,
 (3) 그 탐지를 집계 층에 넘겨 '버그 사건' 단위로 묶고,
 (4) 결과를 리액트 대시보드가 가져갈 수 있는 API로 내보낸다.
동시에 확정된 사건을 세션 파일(JSONL)에 남기고, 읽은 텔레메트리 원본도 세션 폴더에 복사한다.
게임이 맵 전환마다 텔레메트리를 지우기 때문에, 사본이 없으면 그 매치를 다시 분석할 수 없다.

정적 버전과의 차이: 이전에는 요청마다 파일을 처음부터 다시 읽고 검출기를 새로 만들었다.
지금은 서버가 요청 사이에 상태(읽던 위치, 끼임 이력, 진행 중인 사건)를 들고 있으며,
요청 한 번의 비용이 '파일 전체'가 아니라 '새로 생긴 줄'로 줄었다. 이것이 1초 간격 폴링을
감당할 수 있게 만드는 핵심이다.

무엇을 보관하고 무엇을 버리는가:
- 사건은 전부 보관하고 파일로도 남긴다. 시스템이 내린 결론이므로 잃으면 안 된다.
- 원시 탐지는 최근 것만 링 버퍼에 들고, 총 개수는 세기만 한다. 텔레메트리 파일만 있으면
  언제든 다시 만들 수 있는 파생물이기 때문이다(run_invariants.py 가 하는 일이 그것이다).
  봇 하나가 끼여 있으면 초당 20건씩 나오므로, 전부 들고 있으면 여기가 먼저 문제가 된다.

엔드포인트:
- GET  /api/events      : 집계된 사건 목록(대시보드가 사용). 호출할 때마다 새 텔레메트리를 읽는다.
- GET  /api/detections  : 최근 원시 탐지(집계 대조·검증용)
- GET  /api/session     : 현재 세션 정보(파일 경로, 기록된 사건 수 등)
- POST /api/export      : 진행 중인 사건까지 파일에 남기고 요약을 쓴다.
- POST /api/reset       : 현재 세션을 마감하고 새 세션을 시작한다(새 매치 시작 시 사용).

실행: uvicorn app:app --reload  (dashboard/server 폴더에서)
필요 패키지: pip install fastapi "uvicorn[standard]"
"""

import os         # 환경변수로 경로와 동작을 바꾸기 위해 사용한다.
import sys        # 프로젝트 루트를 모듈 검색 경로에 추가하기 위해 사용한다.
import threading  # 동시에 들어온 요청이 같은 상태를 건드리지 않도록 잠금을 걸기 위해 사용한다.
from collections import deque  # 최근 원시 탐지만 유지하는 링 버퍼로 사용한다.
from pathlib import Path

# 이 파일(dashboard/server/app.py)에서 두 단계 위가 프로젝트 루트(qa-openarena)다.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
# 프로젝트 루트를 검색 경로에 넣어야 qa 패키지(검출기)를 import 할 수 있다.
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware  # 브라우저의 교차 출처 요청을 허용하기 위해 사용한다.

from qa.telemetry import MapBounds
from qa.invariants import InvariantChecker
from qa.tail_source import TailSource        # 파일 끝을 따라가며 새 줄만 읽는 소스다.
from qa.aggregate import BugAggregator, snapshot
from qa.session_log import SessionLog        # 확정된 사건을 파일에 남기는 기록기다.

# ── 설정 ────────────────────────────────────────────────────────────────────
# 감시할 텔레메트리 파일. 환경변수 QA_TELEMETRY 로 덮어쓸 수 있다.
TELEMETRY_PATH = os.environ.get("QA_TELEMETRY", str(PROJECT_ROOT / "tests" / "qa_telemetry.jsonl"))
# 세션 파일을 남길 폴더. 환경변수 QA_SESSION_DIR 로 바꿀 수 있다.
SESSION_DIR = os.environ.get("QA_SESSION_DIR", str(PROJECT_ROOT / "sessions"))
# 파일 기록을 끄고 싶으면 QA_SESSION_LOG=0 으로 실행한다.
SESSION_LOG_ENABLED = os.environ.get("QA_SESSION_LOG", "1") != "0"
# 텔레메트리 원본 사본을 세션 폴더에 남길지 여부. QA_ARCHIVE_TELEMETRY=0 으로 끌 수 있다.
# 기본으로 켜 두는 이유: 게임이 맵 전환마다 텔레메트리를 지우므로, 사본이 없으면 그 매치를
# 다시 분석할 방법이 영영 사라진다. 대신 세션 폴더가 시간당 수십 MB씩 늘어난다.
ARCHIVE_TELEMETRY = os.environ.get("QA_ARCHIVE_TELEMETRY", "1") != "0"

# 검출기 설정값. run_invariants.py 와 동일하게 유지해야 한다(맵마다 다른 보정값).
# TODO: qa/config.py 같은 공용 설정으로 빼서 중복을 없앤다. 현재 run_invariants.py 의 값과
#       불일치가 있어, 같은 텔레메트리에 CLI 와 서버가 다른 판정을 낼 수 있다.
BOUNDS = MapBounds(min_x=-852, max_x=1721, min_y=-483, max_y=2097, floor_z=-29, ceiling_z=622)
MAX_SPEED = 1214
STUCK_SECONDS = 2.0
STUCK_EPSILON = 2.0
MAX_POSSIBLE_HEALTH = 250
# 같은 (봇, 규칙) 탐지를 하나의 사건으로 묶을 시간 간격(초). 근거는 qa/aggregate.py 주석 참조.
GAP_SECONDS = 0.5
# 메모리에 유지할 최근 원시 탐지 수. 대조·검증이 목적이므로 최근 것만 있으면 충분하다.
RAW_KEEP = 500
# 응답 하나에 실어 보낼 기본 사건 수. 대시보드가 그리는 양과 맞춘다.
DEFAULT_LIMIT = 300


def detector_config() -> dict:
    """세션 요약에 남길 검출 설정값이다.

    "이 결과가 어떤 경계값과 임계값으로 나왔는가"를 기록에 함께 남기지 않으면, 나중에
    세션 파일만 봤을 때 판정의 근거를 재구성할 수 없다.
    """
    return {
        "bounds": {
            "min_x": BOUNDS.min_x, "max_x": BOUNDS.max_x,
            "min_y": BOUNDS.min_y, "max_y": BOUNDS.max_y,
            "floor_z": BOUNDS.floor_z, "ceiling_z": BOUNDS.ceiling_z,
        },
        "max_speed": MAX_SPEED,
        "stuck_seconds": STUCK_SECONDS,
        "stuck_epsilon": STUCK_EPSILON,
        "max_possible_health": MAX_POSSIBLE_HEALTH,
        "gap_seconds": GAP_SECONDS,
    }


class LiveSession:
    """한 세션 동안의 감시 상태를 한데 묶어 들고 있는 객체다.

    검출기(끼임 이력)와 집계기(진행 중인 사건)는 모두 내부 상태를 가진다. 실시간에서는
    이 상태가 요청 사이에 유지되어야 하므로, 함께 만들고 함께 버릴 수 있도록 한 객체에 모았다.
    게임이 재시작되면 이 객체를 통째로 새로 만든다.
    """

    def __init__(self) -> None:
        # 사건을 파일에 남길 기록기를 먼저 만든다(집계기의 콜백으로 연결해야 하기 때문이다).
        self.log = SessionLog(SESSION_DIR, archive_telemetry=ARCHIVE_TELEMETRY) if SESSION_LOG_ENABLED else None
        self.checker = InvariantChecker(
            bounds=BOUNDS,
            max_speed=MAX_SPEED,
            stuck_seconds=STUCK_SECONDS,
            stuck_epsilon=STUCK_EPSILON,
            max_possible_health=MAX_POSSIBLE_HEALTH,
        )
        # 사건이 닫힐 때마다 파일에 한 줄씩 덧붙이도록 콜백을 건다.
        self.aggregator = BugAggregator(
            gap_seconds=GAP_SECONDS,
            on_close=(self.log.append_event if self.log else None),
        )
        # 최근 원시 탐지만 유지하는 링 버퍼다. maxlen 을 넘으면 오래된 것부터 자동으로 밀려난다.
        self.recent_raw: deque = deque(maxlen=RAW_KEEP)
        self.raw_id = 0          # 원시 탐지에 붙일 일련번호
        self.last_time = None    # 텔레메트리에서 마지막으로 본 게임 시간
        self.sample_count = 0    # 지금까지 처리한 텔레메트리 줄 수

    def feed(self, samples: list) -> None:
        """새로 읽은 상태들을 검출기와 집계기에 통과시킨다."""
        for sample in samples:
            self.sample_count += 1
            self.last_time = sample.time
            for bug in self.checker.check(sample):
                self.raw_id += 1
                self.recent_raw.append({
                    "id": self.raw_id,
                    "session": bug.time,
                    "entity_id": bug.entity_id,
                    "rule": bug.rule,
                    "severity": bug.severity.value.upper(),
                    "message": bug.message,
                    "kind": "hard",
                    **snapshot(sample),
                })
                self.aggregator.feed(bug, sample)
        # 마지막 관측 시각을 기준으로, 간격이 벌어진 사건은 닫고 이어지던 사건은 진행 중으로 둔다.
        # 이 호출이 사건을 닫으면 그 순간 콜백이 돌아 파일에도 기록된다.
        self.aggregator.finalize(self.last_time)

    def finish(self, telemetry_path: str, reason: str) -> dict:
        """세션을 마감한다. 진행 중인 사건까지 파일에 남기고 요약을 쓴다."""
        result = {"session_id": None, "events_file": None, "summary_file": None,
                  "telemetry_file": None, "telemetry_bytes": 0, "flushed": 0}
        if self.log is None:  # 파일 기록이 꺼져 있으면 남길 것이 없다.
            return result
        flushed = self.log.flush_open_events(self.aggregator.open_events())
        events = self.aggregator.events()
        # 규칙별 사건 수를 요약에 넣는다. 세션 파일을 열지 않고도 대략을 볼 수 있어야 한다.
        by_rule: dict[str, int] = {}
        for e in events:
            by_rule[e.rule] = by_rule.get(e.rule, 0) + 1
        self.log.write_summary({
            "reason": reason,                       # 왜 마감했는지(수동 내보내기·리셋·게임 재시작)
            "telemetry": telemetry_path,            # 어느 텔레메트리를 봤는지
            "samples_processed": self.sample_count,  # 몇 줄을 처리했는지
            "raw_detections": self.aggregator.raw_count,  # 집계 전 원시 탐지 수
            "events_total": len(events),            # 사건 수
            "events_by_rule": by_rule,              # 규칙별 사건 수
            "last_game_time": self.last_time,       # 텔레메트리가 어디까지 기록됐는지
            "config": detector_config(),            # 어떤 설정으로 판정했는지(재현성)
        })
        result.update({
            "session_id": self.log.session_id,
            "events_file": str(self.log.events_path),
            "summary_file": str(self.log.summary_path),
            # 사본이 실제로 만들어진 경우에만 경로를 돌려준다.
            "telemetry_file": str(self.log.telemetry_path) if self.log.archived_bytes else None,
            "telemetry_bytes": self.log.archived_bytes,
            "flushed": flushed,
        })
        self.log.close()
        return result


# ── 서버 전역 상태 ──────────────────────────────────────────────────────────
# 파일 읽기 위치는 세션이 바뀌어도 이어져야 하는 경우(수동 리셋)와 처음으로 돌아가야 하는
# 경우(게임 재시작)가 다르므로, 소스는 세션 객체 바깥에 둔다.
_source = TailSource(TELEMETRY_PATH)
_session = LiveSession()
# FastAPI 의 동기 엔드포인트는 스레드풀에서 실행되므로, 폴링이 잦아지면 앞 요청이 끝나기 전에
# 다음 요청이 들어와 같은 상태를 동시에 건드릴 수 있다. 그러면 읽기 위치가 어긋나거나 같은 줄을
# 두 번 먹는다. 상태를 만지는 구간 전체를 잠금으로 감싼다.
_lock = threading.Lock()


def _poll() -> None:
    """새 텔레메트리를 읽어 현재 세션에 반영한다. 잠금 안에서만 호출한다."""
    global _session
    result = _source.poll()
    if result.restarted:
        # 파일이 잘렸다는 것은 새 매치가 시작됐다는 뜻이다. 이전 세션을 마감해 파일에 남기고,
        # 검출기·집계기·기록기를 전부 새로 만든다. 옛 끼임 이력이 새 매치로 넘어가면 안 된다.
        _session.finish(TELEMETRY_PATH, reason="telemetry_restart")
        _session = LiveSession()
    # 사본 쓰기는 세션 교체 이후에 한다. 순서가 중요하다. 파일이 초기화된 폴링에서 읽은
    # 바이트는 이미 '새 매치'의 것이므로, 먼저 쓰면 이전 매치의 사본에 섞여 들어간다.
    if result.raw:
        if _session.log is not None:
            _session.log.archive(result.raw)
    if result.samples:
        _session.feed(result.samples)


app = FastAPI(title="OpenArena QA Monitor API")

# 리액트 개발 서버(localhost:5173 등)에서 이 API를 요청할 수 있게 교차 출처를 허용한다.
# 로컬 개발용이라 넉넉히 허용한다. 외부 배포 시에는 출처를 좁혀야 한다.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/")
def root():
    """서버가 살아 있는지 확인하는 정보용 엔드포인트다."""
    return {
        "service": "OpenArena QA Monitor API",
        "mode": "live",
        "telemetry": TELEMETRY_PATH,
        "telemetry_exists": Path(TELEMETRY_PATH).exists(),
        "session_log": SESSION_LOG_ENABLED,
        "session_dir": SESSION_DIR if SESSION_LOG_ENABLED else None,
        "archive_telemetry": ARCHIVE_TELEMETRY,
        "endpoints": ["/api/events", "/api/detections", "/api/session", "/api/export", "/api/reset"],
    }


@app.get("/api/events")
def events(limit: int = DEFAULT_LIMIT):
    """새 텔레메트리를 읽어 반영한 뒤, 집계된 사건 목록을 반환한다.

    limit 은 응답에 실어 보낼 사건 수다. 사건 자체는 전부 서버에 남아 있고 파일에도 기록되며,
    여기서 자르는 것은 매초 오가는 양뿐이다. 대시보드가 그리는 양과 맞춰 두었다.
    """
    with _lock:
        _poll()
        all_events = _session.aggregator.events()
        recent = all_events[-limit:] if limit > 0 else all_events
        return {
            "count": len(all_events),                # 전체 사건 수
            "returned": len(recent),                 # 이번 응답에 담긴 수
            "raw_count": _session.aggregator.raw_count,  # 집계 전 원시 탐지 수(누적)
            "gap_seconds": GAP_SECONDS,              # 어떤 기준으로 묶었는지 함께 밝힌다.
            "last_time": _session.last_time,         # 텔레메트리가 어디까지 기록됐는지
            "samples": _session.sample_count,        # 지금까지 처리한 텔레메트리 줄 수
            "skipped_lines": _source.skipped,        # 파싱 실패로 건너뛴 줄 수(조용히 묻히지 않게 노출)
            "source": TELEMETRY_PATH,
            "session_id": _session.log.session_id if _session.log else None,
            "telemetry_bytes": _session.log.archived_bytes if _session.log else 0,
            "events": [e.to_dict() for e in recent],
        }


@app.get("/api/detections")
def detections():
    """최근 원시 탐지를 반환한다(집계 대조·검증용).

    전체가 아니라 최근 RAW_KEEP 건만 유지한다. 원시 탐지는 텔레메트리에서 언제든 다시
    만들 수 있는 파생물이므로, 결론에 해당하는 사건과 달리 전부 들고 있을 필요가 없다.
    """
    with _lock:
        _poll()
        return {
            "count": len(_session.recent_raw),        # 지금 들고 있는 수
            "total": _session.aggregator.raw_count,   # 누적 발생 수
            "kept": RAW_KEEP,                         # 유지 한도
            "source": TELEMETRY_PATH,
            "detections": list(_session.recent_raw),
        }


@app.get("/api/session")
def session_info():
    """현재 세션의 기록 상태를 반환한다."""
    with _lock:
        log = _session.log
        return {
            "session_id": log.session_id if log else None,
            "logging": SESSION_LOG_ENABLED,
            "events_file": str(log.events_path) if log else None,
            "summary_file": str(log.summary_path) if log else None,
            "events_written": log.written if log else 0,   # 파일에 이미 확정 기록된 사건 수
            "events_open": len(_session.aggregator.open_events()),  # 아직 진행 중이라 미기록인 수
            "samples": _session.sample_count,
            "archiving": ARCHIVE_TELEMETRY,                 # 텔레메트리 사본을 남기는 중인지
            "telemetry_file": str(log.telemetry_path) if (log and log.archived_bytes) else None,
            "telemetry_bytes": log.archived_bytes if log else 0,  # 지금까지 사본에 쓴 양
        }


@app.post("/api/export")
def export():
    """진행 중인 사건까지 파일에 남기고 요약을 쓴 뒤, 같은 세션을 이어서 계속한다.

    세션을 끊지 않고 지금까지의 결과를 확정하고 싶을 때 쓴다. 요약 파일은 다시 쓰이고,
    진행 중이던 사건은 파일에 한 줄 추가된다.
    """
    global _session
    with _lock:
        _poll()
        info = _session.finish(TELEMETRY_PATH, reason="manual_export")
        # 기록기를 닫았으므로 같은 세션을 이어가려면 새 세션 객체가 필요하다. 다만 텔레메트리
        # 읽기 위치는 유지하므로, 이미 읽은 줄을 다시 읽지는 않는다.
        _session = LiveSession()
        return {"exported": True, **info}


@app.post("/api/reset")
def reset():
    """현재 세션을 마감하고, 텔레메트리를 처음부터 다시 읽는 새 세션을 시작한다.

    새 매치를 시작했는데 텔레메트리 파일이 잘리지 않고 이어 쓰이는 경우처럼, 자동 감지가
    동작하지 않는 상황에서 수동으로 상태를 비울 때 쓴다.
    """
    global _session
    with _lock:
        info = _session.finish(TELEMETRY_PATH, reason="manual_reset")
        _source.reset()          # 읽기 위치를 파일 처음으로 되돌린다.
        _session = LiveSession()  # 검출기·집계기·기록기를 모두 새로 만든다.
        return {"reset": True, "previous": info}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
