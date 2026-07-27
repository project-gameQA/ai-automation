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

import json       # 적용된 시나리오(JSON)를 읽어 세션 요약에 싣기 위해 사용한다.
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

from qa import config                      # 검출 설정값과 생성 함수를 한곳에서 가져온다.
from qa.tail_source import TailSource      # 파일 끝을 따라가며 새 줄만 읽는 소스다.
from qa.aggregate import snapshot
from qa.session_log import SessionLog      # 확정된 사건을 파일에 남기는 기록기다.
from qa.anomaly import AnomalyModel        # 이상탐지(②) 오라클이다.
from qa import report                      # 세션 결과를 사람이 읽는 리포트로 만든다.

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

# 한 세션의 길이(분). 이 시간이 지나면 서버가 스스로 세션을 마감하고 새로 연다. 0이면 끈다.
#
# 왜 필요한가: 시나리오는 `fraglimit 0` `timelimit 0` 으로 매치가 끝나지 않게 만든다. 매치가
# 끝나면 게임이 텔레메트리를 새로 열어 기록이 날아가고 점수판 구간이 이상탐지 오탐이 되기
# 때문이다. 그래서 게임 쪽에는 세션 경계가 없다. 밤새 켜 두면 세션 하나가 무한히 이어져
# 텔레메트리 한 파일이 1GB 를 넘고, 리포트는 사람이 멈출 때까지 한 장도 나오지 않는다.
#
# 왜 게임을 재시작시키지 않고 서버가 자르는가: 매치를 끝내면 점수판 구간에서 봇이 얼어붙어
# 그 구간의 발사 비율이 0이 되고, 그것은 실력 최저 봇과 같은 신호라 이상탐지가 오탐한다.
# 서버 쪽에서 자르면 게임은 아무것도 모른 채 계속 돌고, 읽던 위치도 그대로 이어진다.
# 즉 **기록의 경계와 매치의 경계를 분리한다.**
#
# 왜 15분인가: 창 하나가 60초라 세션마다 앞 60초가 창이 되지 못한다. 이 손실이 5분이면 20%,
# 15분이면 6.7%, 30분이면 3.3%다. 반대로 세션이 길어지면 리포트 타임라인의 가로 한 점이 담는
# 시간이 늘어 짧은 사건의 위치를 짚을 수 없게 된다(15분이면 1픽셀이 1초, 60분이면 3.9초).
# 그리고 메모리에 유지하는 이상 판정 창 상한(3000개)이 봇 8마리 기준 약 64분이라 그보다
# 길면 타임라인 앞부분이 빈다. 10~20분이 합리적인 범위이고 그 가운데를 기본값으로 둔다.
SESSION_MINUTES = float(os.environ.get("QA_SESSION_MINUTES", "15"))

# 리포트를 놓을 폴더. 세션 폴더와 나누는 이유는, 세션 폴더에는 기계가 읽는 원자료가 쌓이고
# 리포트는 사람이 여는 파일이라 섞여 있으면 원하는 것을 고르기 번거롭기 때문이다.
REPORT_DIR = os.environ.get("QA_REPORT_DIR", str(PROJECT_ROOT / "reports"))
# 리포트 생성을 끄고 싶으면 QA_REPORT=0 으로 실행한다.
REPORT_ENABLED = os.environ.get("QA_REPORT", "1") != "0"

# 이번 실행에 적용한 테스트 시나리오(확정본 JSON)의 경로다. run_qa.bat 이
# tools/apply_scenario.py 로 만들어 이 환경변수로 넘긴다. 없으면 시나리오 없이 도는 것이다.
SCENARIO_PATH = os.environ.get("QA_SCENARIO", "")

# 게임 프로세스를 찾을 때 쓸 이름 조각. 실행 파일 이름이 환경마다 다를 수 있어 환경변수로 받는다
# (openarena.exe, ioquake3.x86_64 등). CPU·메모리 관측과 행/종료 구분에 쓴다.
PROCESS_HINT = os.environ.get("QA_PROCESS_HINT", config.WATCHDOG_PROCESS_HINT)

# 검출 경계값·임계값과 집계 간격은 qa/config.py 에 있다. run_invariants.py 도 같은 곳을
# 보므로, 같은 텔레메트리를 CLI 와 서버에 넣으면 같은 판정이 나온다. 예전에는 두 파일에
# 값이 각각 적혀 있어 한쪽만 고치면 결과가 어긋날 수 있었다.

# 아래 둘은 검출 규칙이 아니라 이 서버의 운영 값이라 여기에 둔다.
# 메모리에 유지할 최근 원시 탐지 수. 대조·검증이 목적이므로 최근 것만 있으면 충분하다.
RAW_KEEP = 500
# 응답 하나에 실어 보낼 기본 사건 수. 대시보드가 그리는 양과 맞춘다.
DEFAULT_LIMIT = 300

# 메모리에 유지할 이상 판정 창의 최대 수.
#
# 원래 500이었다. 근거는 "이상 점수는 텔레메트리 사본과 모델만 있으면 언제든 다시 낼 수 있는
# 파생물이라 전부 들고 있을 필요가 없다" 였고, 그때는 이 목록을 쓰는 곳이 대시보드 응답뿐이라
# 맞는 판단이었다. 리포트가 생기면서 사정이 달라졌다. 리포트의 타임라인은 세션 **전체**를
# 그리는데 목록이 잘려 있으면 앞부분이 통째로 빈 그림이 나온다. 그림이 조용히 틀리는 것은
# 없는 것보다 나쁘다.
#
# 3000이면 봇 8마리 기준 약 60분 세션까지 담는다. 항목 하나가 작은 딕셔너리라 메모리 부담은
# 사실상 없다. 그보다 긴 세션에서는 여전히 잘리며, 그때는 오프라인 도구로 다시 만들면 된다.
ANOMALY_KEEP = 3000

# ── 이상탐지 모델 ──────────────────────────────────────────────────────────
# 모델은 무상태이므로 세션이 바뀌어도 다시 만들 필요가 없다. 서버 시작 시 한 번 불러 둔다.
# 모델이 없어도 서버는 정상 동작해야 한다. 학습을 아직 안 했더라도 하드 인바리언트(①)는
# 돌아야 하기 때문이다. 이상탐지만 꺼진다.
_anomaly_model = None
_anomaly_error = None


def load_anomaly_model() -> None:
    """이상탐지 모델을 불러온다. 실패하면 이상탐지만 비활성화하고 서버는 계속 동작한다."""
    global _anomaly_model, _anomaly_error
    path = Path(config.ANOMALY_MODEL_PATH)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    if not path.exists():
        _anomaly_model = None
        _anomaly_error = f"모델 파일이 없다: {path}. tools/train_anomaly.py 로 학습한다."
        return
    try:
        _anomaly_model = AnomalyModel.load(str(path))
        _anomaly_error = None
    except Exception as exc:
        # 특징 목록이 바뀐 뒤 옛 모델을 불러오면 여기서 걸린다. 조용히 틀린 점수를 내는
        # 것보다 이상탐지를 끄고 이유를 노출하는 편이 낫다.
        _anomaly_model = None
        _anomaly_error = f"모델을 불러오지 못했다: {exc}"


def load_scenario():
    """이번 매치의 시나리오를 읽어 딕셔너리로 돌려준다. 없거나 깨졌으면 None 이다.

    세션이 시작될 때마다 파일에서 다시 읽는다. 게임을 켜 둔 채 콘솔에서 다른 시나리오를
    실행할 수 있고(`\\exec qa_match.cfg`), 그때 텔레메트리가 잘리면서 새 세션이 시작되는데,
    서버 시작 시점에 한 번만 읽어 두면 그 세션에 옛 시나리오가 붙는다.

    읽기에 실패해도 예외를 올리지 않는다. 시나리오는 기록에 함께 남기는 부가 정보이지
    검출에 쓰는 값이 아니므로, 이것 때문에 감시가 멈추면 안 된다.
    """
    if not SCENARIO_PATH:
        return None
    try:
        with open(SCENARIO_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


class LiveSession:
    """한 세션 동안의 감시 상태를 한데 묶어 들고 있는 객체다.

    검출기(끼임 이력)와 집계기(진행 중인 사건)는 모두 내부 상태를 가진다. 실시간에서는
    이 상태가 요청 사이에 유지되어야 하므로, 함께 만들고 함께 버릴 수 있도록 한 객체에 모았다.
    게임이 재시작되면 이 객체를 통째로 새로 만든다.
    """

    def __init__(self) -> None:
        # 사건을 파일에 남길 기록기를 먼저 만든다(집계기의 콜백으로 연결해야 하기 때문이다).
        self.log = SessionLog(
            SESSION_DIR,
            archive_telemetry=ARCHIVE_TELEMETRY,
            report_dir=REPORT_DIR if REPORT_ENABLED else None,
        ) if SESSION_LOG_ENABLED else None
        # 이 세션이 어떤 매치 조건에서 돌았는지를 시작 시점에 붙잡아 둔다. 마감할 때 읽으면
        # 그사이 다음 시나리오가 적용됐을 수 있어, 세션과 조건이 한 칸 어긋난다.
        self.scenario = load_scenario()
        self.checker = config.build_checker()  # 공용 설정으로 검출기를 만든다.
        # 사건이 닫힐 때마다 파일에 한 줄씩 덧붙이도록 콜백을 건다.
        self.aggregator = config.build_aggregator(
            on_close=(self.log.append_event if self.log else None)
        )
        # 최근 원시 탐지만 유지하는 링 버퍼다. maxlen 을 넘으면 오래된 것부터 자동으로 밀려난다.
        self.recent_raw: deque = deque(maxlen=RAW_KEEP)
        self.raw_id = 0          # 원시 탐지에 붙일 일련번호
        self.last_time = None    # 텔레메트리에서 마지막으로 본 게임 시간
        # 이 세션의 첫 표본이 가진 게임 시간. 세션 길이를 재는 기준이다.
        # 실제 시각이 아니라 게임 시간으로 재는 이유: 게임이 멈춰 있으면 기록할 것이 없는데도
        # 실제 시각은 계속 흐른다. 그때 세션을 잘라 봐야 빈 세션과 빈 리포트만 쌓인다.
        self.first_time = None
        self.sample_count = 0    # 지금까지 처리한 텔레메트리 줄 수

        # 이상탐지(②)용 창 추출기다. 검출기·집계기와 마찬가지로 상태를 가지므로 세션마다 새로 만든다.
        self.activity = config.build_activity_extractor()
        self.windows_scored = 0                             # 지금까지 채점한 창 수
        self.anomaly_count = 0                              # 그중 이상으로 판정된 수
        self.anomaly_id = 0                                 # 이상 항목에 붙일 일련번호
        self.recent_anomalies: deque = deque(maxlen=ANOMALY_KEEP)

        # 워치독 관측을 세션 단위로 모은다. 워치독 자체는 게임 프로세스를 보는 것이라 서버
        # 전체에서 하나만 두지만, **관측 기록은 세션의 것**이다. 리포트가 세션 구간의 틱 추이를
        # 그리려면 그 세션 동안의 관측만 있어야 한다.
        self.watchdog_trail: list = []
        self.watchdog_last_wall = None   # 같은 관측을 두 번 담지 않기 위한 표시

    def elapsed(self):
        """이 세션이 담은 게임 시간(초)이다. 표본이 없으면 None 이다."""
        if self.first_time is None or self.last_time is None:
            return None
        return self.last_time - self.first_time

    def note_watchdog(self, wd) -> None:
        """워치독의 이번 관측을 세션 기록에 담는다.

        게임 시간과 실제 시각을 **함께** 남기는 것이 요점이다. 리포트의 타임라인 축은 게임
        시간인데 워치독 경보는 실제 시각으로 기록되므로, 둘을 잇는 다리가 없으면 경보를
        타임라인 위에 놓을 수 없다.

        실시간 관측이 확인되기 전(게임을 안 켰거나 밀린 기록을 읽는 중)에는 담지 않는다.
        그 구간의 틱 값은 성능이 아니라 파일을 읽는 속도를 잰 것이라 그림에 넣으면 거짓말이 된다.
        """
        s = getattr(wd, "latest", None)
        if s is None or wd.state != "live" or self.last_time is None:
            return
        if s.wall_time == self.watchdog_last_wall:
            return                                  # 새 줄이 없어 관측이 갱신되지 않은 경우다.
        self.watchdog_last_wall = s.wall_time
        record = {
            "t": round(self.last_time, 2),          # 게임 시간(타임라인의 축)
            "wall": round(s.wall_time, 3),          # 실제 시각(경보와 맞추는 열쇠)
            "tick": None if s.tick_rate is None else round(s.tick_rate, 2),
            "cpu": s.cpu_percent,
            "mem": None if s.memory_mb is None else round(s.memory_mb, 1),
        }
        self.watchdog_trail.append(record)
        if self.log is not None:
            self.log.append_watchdog(record)

    def feed(self, samples: list) -> None:
        """새로 읽은 상태들을 검출기·집계기·이상탐지에 통과시킨다."""
        for sample in samples:
            self.sample_count += 1
            if self.first_time is None:
                self.first_time = sample.time
            self.last_time = sample.time
            # 이상탐지: 창이 완성될 때만 결과가 나온다. 창이 60초이므로 세션 시작 직후에는
            # 아무것도 나오지 않는 것이 정상이다.
            for window in self.activity.feed(sample):
                self._score_window(window)
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

    def _score_window(self, window) -> None:
        """완성된 활동 창 하나를 이상탐지 모델로 채점한다."""
        if _anomaly_model is None:  # 모델이 없으면 이상탐지만 건너뛴다.
            return
        result = _anomaly_model.score([window])
        if not result:
            return
        item = result[0]
        self.windows_scored += 1
        if not item.is_anomaly:
            return
        self.anomaly_count += 1
        self.anomaly_id += 1
        row = item.to_dict()
        row["id"] = self.anomaly_id
        row["kind"] = "anomaly"  # 하드 인바리언트 사건과 구분하기 위한 표시다.
        self.recent_anomalies.append(row)

    def _write_report(self, summary: dict):
        """세션 리포트를 만들어 파일로 쓴다. 실패해도 세션 마감을 막지 않는다.

        리포트는 요약과 사건 파일에서 다시 만들 수 있는 파생물이다. 렌더링 중에 예외가 났다고
        해서 세션 기록 전체를 잃으면 안 되므로, 여기서만 막고 이유를 남긴다.
        """
        if self.log is None or not REPORT_ENABLED:
            return None
        try:
            # 세션 구간에 걸친 워치독 경보만 고른다. 워치독은 서버 전체에서 하나라 앞 세션의
            # 경보까지 들고 있다. 실제 시각으로 자르는 이유는 경보가 그 시각으로 기록되기 때문이다.
            lo = summary.get("started_at", 0)
            hi = summary.get("ended_at", 0) or float("inf")
            alerts = [a.to_dict() for a in _watchdog.alerts
                      if lo <= a.started_at <= hi]
            wd = {
                "trail": self.watchdog_trail,
                "alerts": alerts,
                "target_tick": _watchdog.target_tick,
                "tick_ratio_alert": _watchdog.tick_ratio_alert,
            }
            html = report.render(summary, [e.to_dict() for e in self.aggregator.events()],
                                 list(self.recent_anomalies), wd)
            return self.log.write_report(html)
        except Exception as exc:
            print(f"[리포트] 만들지 못했다: {exc}")
            return None

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
        summary = {
            "reason": reason,                       # 왜 마감했는지(수동 내보내기·리셋·게임 재시작)
            "telemetry": telemetry_path,            # 어느 텔레메트리를 봤는지
            "samples_processed": self.sample_count,  # 몇 줄을 처리했는지
            "raw_detections": self.aggregator.raw_count,  # 집계 전 원시 탐지 수
            "events_total": len(events),            # 사건 수
            # 이상탐지 결과는 파일로 남기지 않는다. 텔레메트리 사본과 모델만 있으면 언제든
            # 다시 낼 수 있는 파생물이기 때문이다. 대신 건수만 요약에 적어 둔다.
            "anomaly_windows_scored": self.windows_scored,
            "anomaly_count": self.anomaly_count,
            # 워치독 경보는 게임 프로세스 상태에 대한 관측이라 세션 요약에 함께 남긴다.
            "watchdog_alerts": len(_watchdog.alerts),
            "events_by_rule": by_rule,              # 규칙별 사건 수
            "last_game_time": self.last_time,       # 텔레메트리가 어디까지 기록됐는지
            "config": config.as_dict(),             # 어떤 설정으로 판정했는지(재현성)
            # 어떤 매치 조건에서 나왔는지(재현성). 판정 설정만 남기면 재현에 필요한 절반이 빈다.
            # 맵·봇 수·봇별 실력·실험 cvar·주입 상태가 전부 여기에 들어 있다.
            "scenario": self.scenario,
            # 리포트가 이상 점수 곡선에 임계선을 그리려면 필요하다.
            "anomaly_threshold": _anomaly_model.threshold if _anomaly_model else None,
        }
        self.log.write_summary(summary)

        # 리포트는 요약을 쓴 **바로 그 자리**에서 만든다. 두 산출물이 같은 순간의 같은 데이터를
        # 담아야 서로 어긋나지 않는다. 별도의 버튼이나 시점을 두면 그사이에 사건이 하나 더 닫혀
        # 리포트와 요약의 숫자가 달라질 수 있다.
        report_path = self._write_report(summary)
        result.update({
            "session_id": self.log.session_id,
            "report_file": report_path,
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
# 워치독은 게임 프로세스를 보는 것이라 세션과 수명이 다르다. 맵이 바뀌어도 같은 게임
# 프로세스가 계속 돌고 있으므로, 세션마다 새로 만들지 않고 서버 전체에서 하나만 둔다.
_watchdog = config.build_watchdog(process_hint=PROCESS_HINT)
# FastAPI 의 동기 엔드포인트는 스레드풀에서 실행되므로, 폴링이 잦아지면 앞 요청이 끝나기 전에
# 다음 요청이 들어와 같은 상태를 동시에 건드릴 수 있다. 그러면 읽기 위치가 어긋나거나 같은 줄을
# 두 번 먹는다. 상태를 만지는 구간 전체를 잠금으로 감싼다.
_lock = threading.Lock()


def _start_session(seed_pending: bool) -> None:
    """새 세션을 시작한다. 잠금 안에서만 호출한다.

    seed_pending 은 "아직 완결되지 않은 줄 조각을 새 사본의 맨 앞에 넣을지"를 뜻하며,
    세션이 바뀌는 세 경우에 값이 다르다. 이 차이를 놓치면 사본이 조각난 줄로 시작하거나
    같은 바이트가 두 번 기록된다.

    - 수동 내보내기(/api/export): **True.** 읽기 위치와 버퍼가 그대로 유지된다. 보류 중인
      조각은 이전 세션 사본에 이미 들어가 있고 다음 읽기는 그 줄 중간부터 시작하므로,
      새 사본을 이 조각으로 시작해야 첫 줄이 온전해진다.
    - 수동 리셋(/api/reset): **False.** 소스를 reset() 하므로 버퍼가 비고 파일을 처음부터
      다시 읽는다. 넣을 조각이 없다.
    - 게임 재시작 감지(_poll): **False.** poll() 안에서 이미 버퍼를 비우고 새 파일을 읽었다.
      이 시점의 보류 조각은 방금 읽은 raw 안에 이미 들어 있으므로, 넣으면 중복된다.
    """
    global _session
    _session = LiveSession()
    if seed_pending and _session.log is not None:
        _session.log.archive(_source.pending)


def _poll() -> None:
    """새 텔레메트리를 읽어 현재 세션에 반영한다. 잠금 안에서만 호출한다."""
    result = _source.poll()
    if result.restarted:
        # 파일이 잘렸다는 것은 새 매치가 시작됐다는 뜻이다. 이전 세션을 마감해 파일에 남기고,
        # 검출기·집계기·기록기를 전부 새로 만든다. 옛 끼임 이력이 새 매치로 넘어가면 안 된다.
        _session.finish(TELEMETRY_PATH, reason="telemetry_restart")
        _start_session(seed_pending=False)  # 사유는 _start_session 설명 참조.
    # 사본 쓰기는 세션 교체 이후에 한다. 순서가 중요하다. 파일이 초기화된 폴링에서 읽은
    # 바이트는 이미 '새 매치'의 것이므로, 먼저 쓰면 이전 매치의 사본에 섞여 들어간다.
    if result.raw:
        if _session.log is not None:
            _session.log.archive(result.raw)
    if result.samples:
        _session.feed(result.samples)
    # 워치독은 새 줄이 없어도 매번 관측해야 한다. "아무것도 안 들어온다"가 바로 신호이기 때문이다.
    _watchdog.observe(
        sim_time=_session.last_time,
        got_samples=bool(result.samples),
        map_restarted=result.restarted,
    )
    # 관측을 세션 기록에 담는다. 이 값은 실제 시각과 비교해서만 얻을 수 있어 나중에 다시
    # 만들 수 없다. 텔레메트리 사본과 같은 이유로 그때그때 남긴다.
    _session.note_watchdog(_watchdog)

    # 세션이 정해진 길이를 넘으면 스스로 마감하고 새로 연다. 게임은 이 일을 모른다.
    # seed_pending=True 인 이유는 수동 내보내기와 사정이 같기 때문이다. 읽기 위치와 버퍼가
    # 그대로 유지되므로, 보류 중인 줄 조각을 새 사본의 맨 앞에 넣어야 첫 줄이 온전해진다.
    elapsed = _session.elapsed()
    if SESSION_MINUTES > 0 and elapsed is not None and elapsed >= SESSION_MINUTES * 60:
        _session.finish(TELEMETRY_PATH, reason="rotate")
        _start_session(seed_pending=True)


load_anomaly_model()  # 서버 시작 시 한 번 불러 둔다. 없으면 이상탐지만 꺼진다.

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
        "anomaly_enabled": _anomaly_model is not None,
        "anomaly_error": _anomaly_error,
        "process_monitor": _watchdog.monitor.available,
        "process_monitor_reason": _watchdog.monitor.reason,
        "scenario": (_session.scenario or {}).get("name"),
        "scenario_file": SCENARIO_PATH or None,
        "report_dir": REPORT_DIR if REPORT_ENABLED else None,
        "session_minutes": SESSION_MINUTES or None,
        "endpoints": ["/api/events", "/api/detections", "/api/session",
                      "/api/export", "/api/reset", "/api/reload_model"],
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
            "gap_seconds": config.GAP_SECONDS,       # 어떤 기준으로 묶었는지 함께 밝힌다.
            "last_time": _session.last_time,         # 텔레메트리가 어디까지 기록됐는지
            "samples": _session.sample_count,        # 지금까지 처리한 텔레메트리 줄 수
            "skipped_lines": _source.skipped,        # 파싱 실패로 건너뛴 줄 수(조용히 묻히지 않게 노출)
            "source": TELEMETRY_PATH,
            "session_id": _session.log.session_id if _session.log else None,
            "telemetry_bytes": _session.log.archived_bytes if _session.log else 0,
            "events": [e.to_dict() for e in recent],
            # 이상탐지 결과는 사건 목록과 섞지 않고 별도로 보낸다.
            # 하드 인바리언트는 "규칙을 어겼다"는 확정이고, 이상탐지는 "검사하라"는 단서다.
            # 한 목록에 섞으면 단서가 확정처럼 보인다.
            "anomaly": {
                "enabled": _anomaly_model is not None,
                "error": _anomaly_error,
                "window_seconds": config.ACTIVITY_WINDOW_SECONDS,
                "percentile": config.ANOMALY_PERCENTILE,
                "threshold": _anomaly_model.threshold if _anomaly_model else None,
                "windows_scored": _session.windows_scored,
                "count": _session.anomaly_count,
                # 최근 것이 위로 오도록 뒤집어 보낸다.
                "items": list(_session.recent_anomalies)[::-1],
            },
            # 워치독은 봇이 아니라 게임 프로세스를 본다. 관측 대상이 다르므로 따로 보낸다.
            "watchdog": _watchdog.status(),
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
            "anomaly_enabled": _anomaly_model is not None,
            "anomaly_error": _anomaly_error,
            "anomaly_model": str(config.ANOMALY_MODEL_PATH),
            "anomaly_windows_scored": _session.windows_scored,
            "anomaly_count": _session.anomaly_count,
            # 이 세션이 어떤 매치 조건에서 돌고 있는지. 시나리오를 쓰지 않으면 None 이다.
            "scenario": _session.scenario,
            "report_dir": REPORT_DIR if REPORT_ENABLED else None,
            "watchdog_samples": len(_session.watchdog_trail),
            # 세션이 얼마나 담겼고 언제 잘리는지. 대시보드가 표시할 수 있게 함께 낸다.
            "elapsed": _session.elapsed(),
            "rotate_seconds": SESSION_MINUTES * 60 if SESSION_MINUTES > 0 else None,
        }


@app.post("/api/export")
def export():
    """진행 중인 사건까지 파일에 남기고 요약을 쓴 뒤, 같은 세션을 이어서 계속한다.

    세션을 끊지 않고 지금까지의 결과를 확정하고 싶을 때 쓴다. 요약 파일은 다시 쓰이고,
    진행 중이던 사건은 파일에 한 줄 추가된다.
    """
    with _lock:
        _poll()
        info = _session.finish(TELEMETRY_PATH, reason="manual_export")
        # 기록기를 닫았으므로 같은 세션을 이어가려면 새 세션 객체가 필요하다. 다만 텔레메트리
        # 읽기 위치는 유지하므로, 이미 읽은 줄을 다시 읽지는 않는다.
        # 보류 중인 줄 조각을 새 사본 앞에 넣어야 첫 줄이 온전해진다(_start_session 설명 참조).
        _start_session(seed_pending=True)
        return {"exported": True, **info}


@app.post("/api/reset")
def reset():
    """현재 세션을 마감하고, 텔레메트리를 처음부터 다시 읽는 새 세션을 시작한다.

    새 매치를 시작했는데 텔레메트리 파일이 잘리지 않고 이어 쓰이는 경우처럼, 자동 감지가
    동작하지 않는 상황에서 수동으로 상태를 비울 때 쓴다.
    """
    with _lock:
        info = _session.finish(TELEMETRY_PATH, reason="manual_reset")
        _source.reset()                      # 읽기 위치를 파일 처음으로 되돌린다(버퍼도 비워진다).
        _start_session(seed_pending=False)   # 넣을 조각이 없다. 사유는 _start_session 설명 참조.
        return {"reset": True, "previous": info}


@app.post("/api/reload_model")
def reload_model():
    """이상탐지 모델을 디스크에서 다시 읽는다.

    서버를 켜 둔 채 tools/train_anomaly.py 로 모델을 새로 학습했을 때 쓴다. 서버를 재시작하면
    실시간 감시 상태(읽던 위치, 끼임 이력, 진행 중인 사건)가 통째로 날아가므로, 모델만
    바꾸려고 재시작하는 것은 손해가 크다.

    이미 채점한 창을 다시 채점하지는 않는다. 기존 결과는 옛 모델의 판정으로 남으므로,
    기준을 통일하려면 /api/reset 으로 세션을 새로 시작한다.
    """
    with _lock:
        load_anomaly_model()
        return {
            "enabled": _anomaly_model is not None,
            "error": _anomaly_error,
            "threshold": _anomaly_model.threshold if _anomaly_model else None,
            "metadata": _anomaly_model.metadata if _anomaly_model else None,
        }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
