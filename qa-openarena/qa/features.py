"""이상탐지 오라클의 입력이 될 윈도우 특징을 뽑는 모듈이다.

왜 프레임이 아니라 윈도우인가:
하드 인바리언트는 프레임 하나만 보고 "물리적으로 불가능한 상태"를 판정한다. 그 방식으로는
시간에 걸쳐 나타나는 이상을 볼 수 없다. 봇이 한 구역을 3분간 맴돌 때, 그 3분의 **모든
프레임은 개별적으로 완벽히 정상**이다. 좌표도 속도도 체력도 정상 범위다. 프레임 단위
특징으로는 이상 신호가 데이터에 존재하지 않으므로 어떤 모델을 써도 잡을 수 없다.
같은 구간을 5초 창으로 요약하면 "이동은 많은데 순이동거리가 0에 가깝다"가 되어 뚜렷이 드러난다.

무엇을 겨냥하는가:
아무것도 겨냥하지 않는다. 특정 이상(저중력 등)이 잘 잡히도록 특징을 다듬으면 시험지에
맞춰 공부하는 셈이 되어, 실제로 만날 예상 밖의 버그를 놓친다. 여기서 뽑는 값은
"봇 하나가 N초 동안 무엇을 했는가"를 일반적으로 기술할 뿐이다.

위치를 직접 쓰지 않는 이유:
좌표를 그대로 학습하면 모델은 "봇이 잘 안 가는 구석"을 이상으로 본다. 맵 끝 통로나 잘 안
쓰는 점프대에 봇이 가면 점수가 뜨는데 그것은 버그가 아니다. 체력 규칙을 폐기하며 얻은
"드물다 ≠ 잘못됐다"와 같은 함정이다. 순이동거리·활동 반경 같은 파생량은 봇이 맵 어디에
있든 같은 의미를 가지므로 이 문제가 없다.

일부러 뺀 것:
- 체력 변화: 정상 데스매치에서 체력은 교전 여부에 따라 극단적으로 흔들린다. 3초에 100이
  깎이는 창도, 5분간 무피해인 창도 모두 정상이다. 넣으면 모델이 "격전 중인 창"을 이상으로
  본다. 오탐률이 주 지표인데 여기서 갉아먹을 이유가 없다.
- 발사(attack): 텔레메트리에 명중·데미지 정보가 없어 발사 비율만으로는 해석할 수 없다.
  명중률을 계측에 추가하면 "쏘는데 아무 일도 안 일어남"이라는 좋은 신호가 되지만 QVM
  재빌드가 필요하다.

실시간 대비:
집계기와 같은 이유로 **상태 기계**로 만든다. 상태를 하나씩 받아 창이 완성될 때만 결과를
내놓는다. 지금은 파일 전체를 먹이고, 나중에 실시간으로 붙일 때는 새로 읽은 것만 먹이면
된다. 두 경우에 이 모듈의 코드는 동일하다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterator, Optional

from .telemetry import StateSample

# 창 하나의 길이(초). stuck_seconds(2초)보다 길어야 규칙이 못 보는 영역을 본다.
# 20Hz에서 5초는 100프레임이라 통계가 안정적이고, 10초짜리 이상이 평균에 묻히지도 않는다.
# 이 값은 아직 관측에서 역산한 것이 아니라 정한 상수다. 교란 세션에서 3·5·10초로 바꿔 가며
# 탐지가 어떻게 달라지는지 재면 근거가 생긴다.
DEFAULT_WINDOW_SECONDS = 5.0

# 창을 옮기는 간격(초). 창 길이보다 짧으면 창이 겹친다.
# 겹치면 이상 구간 하나가 여러 창에 걸쳐 나타나 시작 시점을 이 간격 단위로 짚을 수 있다.
# 대신 이웃 창끼리 비슷해져 학습 데이터의 실질 다양성은 늘지 않는다.
DEFAULT_STEP_SECONDS = 1.0

# 창 하나가 성립하기 위한 최소 표본 수. 기록이 끊긴 구간에서 두세 프레임짜리 창이 만들어져
# 통계가 요동치는 것을 막는다. 20Hz 5초면 100프레임이므로 절반 이하는 버린다.
MIN_SAMPLES_PER_WINDOW = 40

# 진행 방향을 계산할 때 이 속력(초당 거리) 이하는 무시한다.
# 거의 멈춘 상태에서는 속도 벡터의 방향이 노이즈라, 그대로 쓰면 헤딩 변화량이 폭증한다.
MIN_SPEED_FOR_HEADING = 10.0

# 방향 변화를 잴 때 이 간격(초)마다 한 번씩 표본을 뽑는다.
#
# 왜 연속 프레임끼리 비교하지 않는가: 프레임 간격이 0.05초인데, 봇이 50ms 만에 방향을 크게
# 꺾는 것은 물리적으로 거의 불가능하다. 연속 프레임으로 재면 (1) 90도 이상 꺾인 횟수는
# 사실상 항상 0이 되어 특징이 죽고, (2) 방향 변화량 총합은 실제 전환이 아니라 프레임 단위
# 미세 흔들림이 누적된 값이 된다. 직진하는 봇에서도 5초에 수십 도가 쌓인다.
#
# 0.25초(20Hz에서 5프레임)는 사람이 "방향을 바꿨다"고 인지할 만한 최소 단위에 가깝고,
# 흔들림은 그 안에서 상쇄된다.
HEADING_STRIDE_SECONDS = 0.25

# 수직 속도가 이 값을 넘으면 공중에 있는 것으로 본다. 지면에서의 미세한 진동을 걸러낸다.
AIRBORNE_VZ = 5.0

# 특징 이름 목록이다. 모델 학습·저장 시 열 순서를 고정하기 위해 한곳에 둔다.
# 순서가 바뀌면 저장해 둔 모델이 다른 의미의 값을 받게 되므로, 항목 추가는 반드시 끝에 한다.
FEATURE_NAMES = [
    "net_displacement",
    "path_length",
    "move_efficiency",
    "radius_gyration",
    "heading_change",
    "direction_reversals",
    "input_ratio",
    "moved_with_input",
    "moved_without_input",
    "airborne_ratio",
    "z_range",
]


@dataclass
class WindowFeatures:
    """한 봇의 한 시간 구간을 요약한 특징 벡터다."""

    entity_id: int      # 어느 봇인지
    start_time: float   # 창의 시작 게임 시간(초)
    end_time: float     # 창의 끝 게임 시간(초)
    n_samples: int      # 이 창을 이루는 프레임 수

    # A. 이동 결과
    net_displacement: float   # 창 시작에서 끝까지의 직선거리(수평). 실제로 어디까지 갔나.
    path_length: float        # 프레임별 이동거리의 합(수평). 얼마나 움직였나.
    move_efficiency: float    # net / path. 0에 가까우면 제자리·맴돌기, 1이면 직진.

    # B. 활동 범위와 방향
    radius_gyration: float    # 평균 위치로부터 떨어진 거리의 RMS. 활동 반경.
    heading_change: float     # 진행 방향 변화량의 총합(도). 크면 헤맴·진동.
    direction_reversals: int  # 0.25초 사이에 90도 이상 꺾인 횟수. 왔다갔다한 횟수.

    # C. 의도와 결과
    input_ratio: float          # 이동 명령이 있었던 프레임의 비율.
    moved_with_input: float     # 입력이 있을 때의 평균 이동 속력. 낮으면 끼임 성향.
    moved_without_input: float  # 입력이 없을 때의 평균 이동 속력. 높으면 외력에 밀림.

    # D. 수직
    airborne_ratio: float  # 공중에 있던 프레임의 비율.
    z_range: float         # 창 안에서의 고도 변화 폭.

    def to_vector(self) -> list[float]:
        """FEATURE_NAMES 순서대로 값을 늘어놓는다. 모델 입력용이다."""
        return [float(getattr(self, name)) for name in FEATURE_NAMES]

    def to_dict(self) -> dict:
        """식별 정보까지 포함한 딕셔너리다. CSV 출력이나 API 응답에 쓴다."""
        row = {
            "entity_id": self.entity_id,
            "start_time": round(self.start_time, 2),
            "end_time": round(self.end_time, 2),
            "n_samples": self.n_samples,
        }
        for name in FEATURE_NAMES:
            row[name] = round(float(getattr(self, name)), 4)
        return row


def _horizontal(a: StateSample, b: StateSample) -> float:
    """두 상태 사이의 수평 거리다.

    수직 성분을 빼는 이유: 낙하는 z가 수천 단위로 변해 수평 이동량을 압도한다. 수직 움직임은
    D 그룹(airborne_ratio, z_range)이 따로 담당하므로, 이동 관련 특징은 수평만 본다.
    """
    dx = b.x - a.x
    dy = b.y - a.y
    return math.hypot(dx, dy)


def _heading(s: StateSample) -> Optional[float]:
    """수평 속도 벡터의 진행 방향(라디안)이다. 거의 멈춰 있으면 None을 돌려준다."""
    speed = math.hypot(s.vx, s.vy)          # 수평 속력
    if speed < MIN_SPEED_FOR_HEADING:        # 너무 느리면 방향이 노이즈다.
        return None
    return math.atan2(s.vy, s.vx)


def compute_features(samples: list[StateSample]) -> Optional[WindowFeatures]:
    """한 창에 속한 상태 목록에서 특징을 계산한다.

    표본이 너무 적으면 None을 돌려준다. 통계가 요동치는 창을 학습에 넣지 않기 위해서다.
    """
    # 표본 수 검사는 WindowExtractor 가 먼저 하지만, 이 함수를 직접 부르는 경우를 위해 남긴다.
    if len(samples) < MIN_SAMPLES_PER_WINDOW:
        return None

    first, last = samples[0], samples[-1]
    duration = last.time - first.time
    if duration <= 0:  # 시간이 흐르지 않은 창은 속력을 계산할 수 없다.
        return None

    # ── A. 이동 결과 ────────────────────────────────────────────────────────
    net = _horizontal(first, last)  # 시작점에서 끝점까지의 직선거리

    path = 0.0                      # 프레임별 이동거리의 합
    with_input_dist = 0.0           # 입력이 있던 프레임의 이동거리 합
    with_input_n = 0                # 입력이 있던 프레임 수
    without_input_dist = 0.0        # 입력이 없던 프레임의 이동거리 합
    without_input_n = 0             # 입력이 없던 프레임 수

    for i in range(1, len(samples)):
        prev, cur = samples[i - 1], samples[i]
        step = _horizontal(prev, cur)  # 이 한 프레임 동안 움직인 거리
        path += step
        # 이동 의도는 '이번 프레임의 입력'으로 본다. 그 입력의 결과가 이 프레임의 위치 변화다.
        if cur.move_input > 0:
            with_input_dist += step
            with_input_n += 1
        else:
            without_input_dist += step
            without_input_n += 1

    # 경로가 0이면 효율은 정의되지 않는다. 아예 안 움직인 것이므로 0으로 둔다.
    efficiency = (net / path) if path > 0 else 0.0

    # ── B. 활동 범위와 방향 ─────────────────────────────────────────────────
    mean_x = sum(s.x for s in samples) / len(samples)
    mean_y = sum(s.y for s in samples) / len(samples)
    # 평균 위치에서 떨어진 거리의 제곱평균제곱근. 활동 반경을 하나의 수로 요약한다.
    gyration = math.sqrt(
        sum((s.x - mean_x) ** 2 + (s.y - mean_y) ** 2 for s in samples) / len(samples)
    )

    heading_change = 0.0   # 방향 변화량의 총합(도)
    reversals = 0          # 90도 이상 꺾인 횟수
    prev_heading = None
    last_heading_time = None
    for s in samples:
        # 프레임마다 재지 않고 일정 간격으로만 표본을 뽑는다. 이유는 HEADING_STRIDE_SECONDS
        # 주석 참조. 이 처리가 없으면 두 특징 모두 실질적으로 무의미해진다.
        if last_heading_time is not None and (s.time - last_heading_time) < HEADING_STRIDE_SECONDS:
            continue
        h = _heading(s)
        if h is None:      # 거의 멈춘 표본은 방향 계산에서 제외한다.
            continue
        if prev_heading is not None:
            # 두 각도의 차이를 -pi ~ pi 범위로 접는다. 이 처리를 빼면 179도와 -179도의
            # 차이가 358도로 계산되어 실제(2도)와 전혀 달라진다.
            delta = h - prev_heading
            delta = (delta + math.pi) % (2 * math.pi) - math.pi
            deg = abs(math.degrees(delta))
            heading_change += deg
            if deg > 90.0:
                reversals += 1
        prev_heading = h
        last_heading_time = s.time

    # ── C. 의도와 결과 ──────────────────────────────────────────────────────
    input_ratio = sum(1 for s in samples if s.move_input > 0) / len(samples)

    # 프레임당 거리를 프레임 간격으로 나눠 '초당 거리'로 만든다.
    # 이렇게 두면 서버 프레임률이 달라져도 값의 의미가 유지된다.
    dt = duration / (len(samples) - 1)
    moved_with_input = (with_input_dist / with_input_n / dt) if with_input_n else 0.0
    moved_without_input = (without_input_dist / without_input_n / dt) if without_input_n else 0.0
    # 주의: 해당 프레임이 하나도 없을 때도 0.0이 되어 '움직이지 않았음'과 구분되지 않는다.
    # input_ratio 를 함께 보면 어느 쪽인지 알 수 있으므로 별도 처리는 두지 않는다.

    # ── D. 수직 ─────────────────────────────────────────────────────────────
    airborne_ratio = sum(1 for s in samples if abs(s.vz) > AIRBORNE_VZ) / len(samples)
    z_values = [s.z for s in samples]
    z_range = max(z_values) - min(z_values)

    return WindowFeatures(
        entity_id=first.entity_id,
        start_time=first.time,
        end_time=last.time,
        n_samples=len(samples),
        net_displacement=net,
        path_length=path,
        move_efficiency=efficiency,
        radius_gyration=gyration,
        heading_change=heading_change,
        direction_reversals=reversals,
        input_ratio=input_ratio,
        moved_with_input=moved_with_input,
        moved_without_input=moved_without_input,
        airborne_ratio=airborne_ratio,
        z_range=z_range,
    )


class WindowExtractor:
    """상태를 하나씩 받아 창이 완성될 때마다 지정한 함수를 호출하는 범용 상태 기계다.

    창을 자르는 로직 자체는 어떤 특징을 뽑든 동일하므로 여기 한 곳에만 둔다. 이동 특징과
    활동 특징이 각자 이 로직을 복제하면, 미묘한 차이가 생겨도 한참 뒤에야 드러난다.
    실제로 이 프로젝트에서 같은 성격의 버그를 두 번 겪었다(끼임 중복 억제, 사본 경계).

    봇마다 별도의 버퍼를 둔다. 봇을 섞으면 서로 다른 개체의 이동이 한 창에 뒤엉킨다.

    compute_fn 은 한 창의 상태 목록을 받아 결과 객체 또는 None(버릴 창)을 반환한다.
    """

    def __init__(
        self,
        window_seconds: float,
        step_seconds: float,
        min_samples: int,
        compute_fn,
    ) -> None:
        self.window_seconds = window_seconds
        self.step_seconds = step_seconds
        self.min_samples = min_samples   # 이보다 표본이 적은 창은 통계가 요동쳐 버린다.
        self.compute_fn = compute_fn
        # 봇별 상태: {"samples": [...], "start": 창 시작 시각}
        self._buffers: dict[int, dict] = {}

    def feed(self, sample: StateSample) -> list:
        """상태 하나를 넣고, 이번에 완성된 창들의 결과를 반환한다.

        상태가 하나 들어왔을 때 창이 여러 개 완성될 수 있다. 기록이 한동안 끊겼다가
        재개되면 그 사이의 창들이 한꺼번에 마감되기 때문이다. 그래서 리스트를 반환한다.
        """
        buf = self._buffers.get(sample.entity_id)
        if buf is None:  # 처음 보는 봇이면 버퍼를 만든다.
            self._buffers[sample.entity_id] = {"samples": [sample], "start": sample.time}
            return []

        buf["samples"].append(sample)
        done: list = []

        # 창의 끝을 넘어섰으면 창을 마감하고 시작점을 한 칸 옮긴다.
        # while 인 이유는 위에 적은 대로 한 번에 여러 창이 마감될 수 있기 때문이다.
        while sample.time >= buf["start"] + self.window_seconds:
            window_end = buf["start"] + self.window_seconds
            in_window = [s for s in buf["samples"] if buf["start"] <= s.time < window_end]
            if len(in_window) >= self.min_samples:
                result = self.compute_fn(in_window)
                if result is not None:
                    done.append(result)
            buf["start"] += self.step_seconds
            # 새 창 시작보다 오래된 표본은 다시 쓰이지 않으므로 버린다. 메모리가 계속 늘지 않게 한다.
            buf["samples"] = [s for s in buf["samples"] if s.time >= buf["start"]]

        return done

    def finalize(self) -> list:
        """스트림이 끝난 뒤, 남은 표본으로 마지막 창을 만들 수 있으면 만든다.

        창 길이를 다 채우지 못한 꼬리는 버린다. 짧은 창은 같은 특징이라도 다른 의미를 갖기
        때문이다(예: 2초 동안의 이동거리와 5초 동안의 이동거리를 같은 값으로 볼 수 없다).
        """
        done: list = []
        for buf in self._buffers.values():
            samples = buf["samples"]
            if len(samples) < self.min_samples:
                continue
            if samples[-1].time - buf["start"] >= self.window_seconds * 0.99:
                result = self.compute_fn(samples)
                if result is not None:
                    done.append(result)
        done.sort(key=lambda w: (w.start_time, w.entity_id))
        return done


class FeatureExtractor(WindowExtractor):
    """이동 특징 전용 창 추출기다. 범용 WindowExtractor 에 이동 특징 계산을 끼운 것이다.

    주의(2026-07-23): 이 이동 특징들은 **현재 이상탐지 오라클에서 사용하지 않는다.**
    60초 척도에서 실측한 결과 정상 세션끼리도 18.8%를 이상으로 판정해, 신호가 아니라
    노이즈로 작동했다. 짧은 척도에서 쓸모가 있을 가능성은 남아 있으나 그것을 입증할
    시험 데이터를 아직 확보하지 못했다. 상세는 docs/process.md 참조.
    분석 도구(tools/extract_features.py)에서는 여전히 유용하므로 남겨 둔다.
    """

    def __init__(
        self,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        step_seconds: float = DEFAULT_STEP_SECONDS,
    ) -> None:
        super().__init__(
            window_seconds=window_seconds,
            step_seconds=step_seconds,
            min_samples=MIN_SAMPLES_PER_WINDOW,
            compute_fn=compute_features,
        )


def extract_all(samples: Iterator[StateSample], **kwargs) -> list[WindowFeatures]:
    """상태 스트림 전체에서 특징을 뽑아 시간 순으로 반환한다. 오프라인 처리용 편의 함수다."""
    extractor = FeatureExtractor(**kwargs)
    out: list[WindowFeatures] = []
    for sample in samples:
        out.extend(extractor.feed(sample))
    out.extend(extractor.finalize())
    out.sort(key=lambda w: (w.start_time, w.entity_id))
    return out
