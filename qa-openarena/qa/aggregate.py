"""프레임 단위 탐지를 '버그 사건' 단위로 묶는 집계 층이다.

하드 인바리언트는 매 프레임 판정하므로, 봇이 5초간 바닥 아래로 떨어지면 텔레메트리
주기만큼(20Hz면 약 100건) 같은 탐지가 반복해서 나온다. 사람이 검토할 단위는 그 100건이
아니라 "봇 3이 12:04부터 5.1초간 바닥을 뚫고 떨어졌다"는 사건 하나다. 이 모듈이 그
변환을 맡는다.

층의 구분에 대해: 이 모듈은 qa 패키지 안에 있지만 검출기(InvariantChecker)의 일부가
아니다. 구분은 폴더가 아니라 호출 방향으로 정해진다. 검출기는 이 모듈을 부르지 않고,
소비자(서버·CLI)가 검출기 뒤에 이 모듈을 붙인다. 검출기는 계속 매 프레임 판정만 한다.

실시간 대비: 집계기는 탐지를 하나씩 받아 처리하는 상태 기계다. 지금은 파일 전체를 한 번에
먹이고, 서버가 파일 끝의 새 줄만 읽는 방식으로 바뀌면 새로 읽은 것만 먹이면 된다. 두 경우에
집계기 코드는 동일하다.
"""

from __future__ import annotations  # 메서드 시그니처에서 자기 클래스 타입을 문자열 없이 쓰기 위해 사용한다.

from dataclasses import dataclass, field  # 사건 레코드를 간결하게 정의하기 위해 사용한다.
from typing import Callable, Optional  # 선택적 값과 콜백을 타입으로 표현하기 위해 사용한다.

from .telemetry import StateSample, Bug  # 집계기가 받는 입력 데이터 구조다.

# 같은 (봇, 규칙)의 탐지가 이 시간(초) 이내로 이어지면 하나의 사건으로 본다.
# 근거: 텔레메트리는 서버 프레임마다 기록되므로 연속 구간의 간격은 프레임 주기(20Hz면 0.05초)다.
# 0.5초는 약 10프레임에 해당해, 봇이 경계선 위에서 들락날락하며 탐지가 한두 프레임 깜빡이는
# 경우를 하나로 묶으면서, 실제로 따로 일어난 두 사건은 분리한다.
# 한계: 이 값은 관측에서 역산한 값이 아니라 정한 상수다. 맵 경계를 보정했던 것처럼 실제
# 텔레메트리의 프레임 간격을 재서 그 배수로 잡는 편이 더 정확하다. 확장 항목으로 남긴다.
DEFAULT_GAP_SECONDS = 0.5

# 규칙별로 '가장 심했던 순간'을 무엇으로 판단할지 정의한 표다.
# 값은 (Bug.details 안의 키, 방향)이다. 방향 "max"는 클수록 심한 것, "min"은 작을수록 심한 것이다.
# 각 규칙이 이미 자기가 위반한 수치를 details에 넣고 있으므로, 집계기는 규칙의 내용을 몰라도
# 이 표만 보고 극값을 추적할 수 있다.
PEAK_SPEC: dict[str, tuple[str, str]] = {
    "fell_through_floor": ("z", "min"),            # 가장 깊이 내려간 z
    "out_of_bounds": ("overshoot", "max"),         # 경계를 가장 많이 벗어난 거리
    "health_out_of_range": ("health", "max"),      # 가장 높았던 체력
    "impossible_velocity": ("speed", "max"),       # 가장 빨랐던 속력
    "stuck": ("idle_seconds", "max"),              # 가장 오래 멈춰 있던 시간
}

# 극값을 화면에 표시할 때 쓸 사람이 읽는 이름이다.
PEAK_LABEL: dict[str, str] = {
    "fell_through_floor": "최저 z",
    "out_of_bounds": "최대 이탈 거리",
    "health_out_of_range": "최대 체력",
    "impossible_velocity": "최대 속력",
    "stuck": "최대 정지 시간",
}


def snapshot(s: StateSample) -> dict:
    """StateSample을 대시보드가 그대로 쓸 수 있는 딕셔너리로 변환한다.

    서버의 두 엔드포인트(원시 탐지·집계 사건)가 같은 형태를 써야 상세 패널을 공용으로
    만들 수 있으므로, 변환을 한 곳에 모아 둔다.
    """
    return {
        "tick": s.tick,                  # 프레임 번호
        "time": s.time,                  # 게임 시간(초)
        "x": s.x, "y": s.y, "z": s.z,    # 위치
        "vx": s.vx, "vy": s.vy, "vz": s.vz,  # 속도 성분
        "speed": round(s.speed, 1),      # 속력(성분에서 계산한 값)
        "health": s.health,              # 현재 체력
        "max_health": s.max_health,      # 최대 체력
        "move_input": s.move_input,      # 이동 입력 유무
    }


@dataclass
class BugEvent:
    """연속된 탐지들을 하나로 묶은 버그 사건이다."""

    event_id: int          # 사건 고유 번호(대시보드 목록의 키로 쓴다)
    entity_id: int         # 어느 봇인지
    rule: str              # 어떤 규칙을 어겼는지
    severity: str          # 심각도("HIGH"/"MEDIUM"). 규칙에서 정해지므로 사건 내내 동일하다.
    start_time: float      # 사건이 시작된 게임 시간(초)
    end_time: float        # 사건이 마지막으로 관측된 게임 시간(초)
    hits: int              # 이 사건을 이루는 프레임 단위 탐지 수
    message: str           # 가장 최근 탐지의 설명 문자열
    first_sample: dict     # 사건이 시작된 순간의 상태값(무엇이 잘못되기 시작했는지)
    peak_value: Optional[float] = None   # 규칙별 극값. 표에 없는 규칙이면 None이다.
    peak_sample: dict = field(default_factory=dict)  # 극값이 관측된 순간의 상태값
    ongoing: bool = False  # 아직 끝나지 않은 사건인지(스트림 끝에서 결정한다)

    @property
    def duration(self) -> float:
        """사건이 지속된 시간(초)이다. 한 프레임짜리 사건은 0이 된다."""
        return self.end_time - self.start_time

    def to_dict(self) -> dict:
        """대시보드로 보낼 JSON 친화적 형태로 변환한다."""
        return {
            "event_id": self.event_id,
            "entity_id": self.entity_id,
            "rule": self.rule,
            "severity": self.severity,
            "start_time": round(self.start_time, 2),
            "end_time": round(self.end_time, 2),
            "duration": round(self.duration, 2),
            "hits": self.hits,
            "message": self.message,
            "first_sample": self.first_sample,
            "peak_value": None if self.peak_value is None else round(self.peak_value, 2),
            "peak_label": PEAK_LABEL.get(self.rule, ""),
            "peak_sample": self.peak_sample,
            "ongoing": self.ongoing,
        }


class BugAggregator:
    """탐지를 시간 순서대로 받아 사건 단위로 묶는 상태 기계다.

    묶는 기준은 (봇, 규칙)이다. 위치는 기준에 넣지 않는데, 봇이 바닥을 뚫고 떨어지는
    동안 좌표는 계속 변하기 때문이다. 심각도는 규칙에서 파생되므로 따로 넣을 필요가 없다.

    사용법:
        agg = BugAggregator()
        for sample in samples:
            for bug in checker.check(sample):
                agg.feed(bug, sample)
        agg.finalize(last_sample_time)
        events = agg.events()
    """

    def __init__(
        self,
        gap_seconds: float = DEFAULT_GAP_SECONDS,
        on_close: Optional[Callable[[BugEvent], None]] = None,
    ) -> None:
        self.gap_seconds = gap_seconds                  # 사건을 끊는 시간 간격
        # 사건이 닫힐 때 호출할 콜백이다. 서버가 여기에 파일 기록기를 연결한다.
        # 집계기는 이 콜백이 무엇을 하는지 모른다. 파일에 쓰든 다른 곳으로 보내든 집계기의
        # 관심사가 아니며, 검출기가 집계기를 모르는 것과 같은 이유로 방향을 한쪽으로만 둔다.
        self.on_close = on_close
        self._open: dict[tuple[int, str], BugEvent] = {}  # 아직 진행 중인 사건들. 키는 (봇, 규칙)이다.
        self._closed: list[BugEvent] = []               # 이미 끝난 사건들
        self._next_id = 0                               # 사건에 매길 번호
        self._raw_count = 0                             # 지금까지 먹인 원시 탐지 수

    @property
    def raw_count(self) -> int:
        """집계 전 원시 탐지 수다. '3124건이 47개 사건으로 묶였다'를 보여줄 때 쓴다."""
        return self._raw_count

    def feed(self, bug: Bug, sample: StateSample) -> None:
        """탐지 하나를 집계에 넣는다. 탐지는 시간 순서대로 들어와야 한다."""
        self._raw_count += 1                      # 원시 탐지 수를 센다.
        key = (bug.entity_id, bug.rule)           # 같은 봇의 같은 규칙이면 같은 사건 후보다.
        event = self._open.get(key)               # 그 조합으로 열려 있는 사건이 있는지 본다.

        # 열린 사건이 있고 시간 간격이 허용 범위 안이면, 그 사건이 계속되는 중이다.
        if event is not None and (bug.time - event.end_time) <= self.gap_seconds:
            event.end_time = bug.time             # 사건의 끝을 현재로 늘린다.
            event.hits += 1                       # 이 사건을 이루는 탐지 수를 늘린다.
            event.message = bug.message           # 설명은 가장 최근 것으로 갱신한다(끼임의 누적 시간이 여기 담긴다).
            self._update_peak(event, bug, sample)  # 극값을 갱신한다.
            return

        # 여기까지 왔다면 새 사건이 시작된 것이다. 열린 사건이 있었다면 먼저 닫는다.
        if event is not None:
            self._close(key)

        self._next_id += 1                        # 새 사건 번호를 발급한다.
        snap = snapshot(sample)                   # 시작 순간의 상태를 찍어 둔다.
        event = BugEvent(
            event_id=self._next_id,
            entity_id=bug.entity_id,
            rule=bug.rule,
            severity=bug.severity.value.upper(),  # 검출기는 소문자, 화면은 대문자를 쓴다.
            start_time=bug.time,
            end_time=bug.time,
            hits=1,
            message=bug.message,
            first_sample=snap,
            peak_sample=snap,                     # 첫 탐지가 일단은 극값이기도 하다.
        )
        self._set_initial_peak(event, bug)        # 규칙별 극값의 초기값을 넣는다.
        self._open[key] = event                   # 진행 중 목록에 넣는다.

    def _set_initial_peak(self, event: BugEvent, bug: Bug) -> None:
        """사건의 첫 탐지에서 극값 초기값을 읽는다."""
        spec = PEAK_SPEC.get(bug.rule)   # 이 규칙의 극값 정의를 찾는다.
        if spec is None:                 # 표에 없는 규칙이면(향후 추가될 규칙 등)
            return                       # 극값 없이 둔다. 나머지 집계는 그대로 동작한다.
        value = bug.details.get(spec[0])  # details에서 해당 수치를 꺼낸다.
        if isinstance(value, (int, float)):  # 숫자인 경우에만 극값으로 쓴다.
            event.peak_value = float(value)

    def _update_peak(self, event: BugEvent, bug: Bug, sample: StateSample) -> None:
        """진행 중인 사건의 극값을 이번 탐지와 비교해 갱신한다."""
        spec = PEAK_SPEC.get(bug.rule)  # (details 키, 방향)
        if spec is None:
            return
        key, direction = spec
        value = bug.details.get(key)
        if not isinstance(value, (int, float)):  # 수치가 없으면 비교할 것이 없다.
            return
        value = float(value)
        if event.peak_value is None:             # 아직 극값이 없으면 이번 값이 극값이다.
            better = True
        elif direction == "max":                 # 클수록 심한 규칙이면 더 큰 값을 취한다.
            better = value > event.peak_value
        else:                                    # 작을수록 심한 규칙이면 더 작은 값을 취한다.
            better = value < event.peak_value
        if better:
            event.peak_value = value             # 극값을 갱신하고
            event.peak_sample = snapshot(sample)  # 그 순간의 상태도 함께 저장한다.

    def _close(self, key: tuple[int, str]) -> None:
        """진행 중인 사건 하나를 끝난 것으로 옮긴다."""
        event = self._open.pop(key)  # 진행 중 목록에서 빼고
        event.ongoing = False        # 끝난 사건으로 표시한 뒤
        self._closed.append(event)   # 완료 목록에 넣는다.
        if self.on_close is not None:  # 소비자가 콜백을 걸어 두었으면
            self.on_close(event)       # 사건이 확정됐음을 알린다(서버는 이때 파일에 기록한다).

    def open_events(self) -> list[BugEvent]:
        """아직 닫히지 않은 사건들을 반환한다. 세션을 끝낼 때 이들도 기록해야 한다."""
        return list(self._open.values())

    def finalize(self, stream_time: Optional[float] = None) -> None:
        """스트림이 끊긴 시점을 기준으로 남아 있는 사건들의 상태를 확정한다.

        stream_time은 텔레메트리에서 마지막으로 관측된 게임 시간이다. 이 값을 주면,
        마지막 관측보다 gap 이상 전에 끝난 사건은 '끝난 사건'으로, 마지막 관측 직전까지
        이어지던 사건은 '진행 중'으로 구분할 수 있다.

        정적 모드에서 '진행 중'은 "파일이 사건 도중에 끝났다"는 뜻이고, 실시간 모드에서는
        "지금 이 순간 벌어지고 있다"는 뜻이 된다. 같은 코드가 두 의미를 모두 표현한다.

        stream_time을 주지 않으면 남은 사건을 모두 진행 중으로 둔다.
        """
        for key in list(self._open.keys()):  # 순회 중에 딕셔너리를 바꾸므로 키 목록을 복사해 돈다.
            event = self._open[key]
            if stream_time is not None and (stream_time - event.end_time) > self.gap_seconds:
                self._close(key)   # 마지막 관측보다 한참 전에 끝난 사건이므로 닫는다.
            else:
                event.ongoing = True  # 스트림 끝까지 이어지던 사건이므로 진행 중으로 표시한다.

    def events(self) -> list[BugEvent]:
        """끝난 사건과 진행 중인 사건을 합쳐 시작 시간 순으로 반환한다."""
        all_events = self._closed + list(self._open.values())  # 두 목록을 합친다.
        all_events.sort(key=lambda e: e.start_time)            # 시간 순으로 정렬한다.
        return all_events
