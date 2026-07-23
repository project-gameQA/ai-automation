"""검출 설정값을 한곳에 모은 모듈이다.

왜 필요한가:
경계값과 임계값이 `run_invariants.py`와 `dashboard/server/app.py` 두 곳에 각각 적혀 있었다.
값은 맞춰 두었지만 한쪽만 고치면 **같은 텔레메트리에 CLI와 서버가 다른 판정을 낸다.**
검출 결과가 도구에 따라 달라지면 그 결과를 신뢰할 근거가 사라지므로, 값을 한 곳에 모은다.

무엇을 모으는가:
값 자체뿐 아니라 **검출기와 집계기를 만드는 함수**까지 여기에 둔다. 값만 모으면 생성 코드가
여전히 두 곳에 남아, 인자 하나를 빠뜨리는 식으로 다시 어긋날 수 있다. 소비자는
`build_checker()`와 `build_aggregator()`만 부르면 된다.

의존 방향:
이 모듈은 `telemetry`와 `invariants`, `aggregate`를 가져다 쓰지만, 그 셋은 이 모듈을 모른다.
설정은 조립하는 쪽의 관심사이지 검출 로직의 관심사가 아니기 때문이다. 이 방향을 지켜야
검출기를 다른 설정으로 테스트할 수 있다.

맵 의존성(알려진 한계):
아래 경계값은 **특정 맵에서 보정한 값**이다. 다른 맵에서 쓰면 경계 이탈이 정상 플레이에도
뜬다. 경계·바닥·과속 규칙이 맵에 의존하고, 체력·끼임은 무관하다. 확장 방향은 맵 이름을
키로 하는 설정 표를 두고 텔레메트리에 맵 이름 필드를 추가하는 것이다. 지금은 한 맵 기준
단일 설정으로 두되, 나중에 표로 바꾸기 쉽도록 값들을 이 모듈 상단에 평평하게 늘어놓는다.
"""

from .telemetry import MapBounds
from .invariants import InvariantChecker
from .aggregate import BugAggregator
from .features import FeatureExtractor

# ── 맵 경계 ────────────────────────────────────────────────────────────────
# tools/calibrate_bounds.py 로 정상 플레이 텔레메트리에서 산출한 값이다.
# 주입을 끈 상태의 텔레메트리로 보정해야 한다. 위치 주입이 섞이면 경계가 부풀려진다.
BOUNDS = MapBounds(
    min_x=-852,
    max_x=1721,
    min_y=-483,
    max_y=2097,
    floor_z=-29,
    ceiling_z=622,
)

# ── 규칙 임계값 ────────────────────────────────────────────────────────────
# 물리적으로 가능한 최대 속력. 같은 보정에서 산출했다.
MAX_SPEED = 1214

# 이 시간(초) 이상 이동 입력이 있는데도 위치가 안 변하면 끼임으로 본다.
STUCK_SECONDS = 2.0

# 이 거리 이하의 이동은 '정지'로 간주하는 허용 오차다.
STUCK_EPSILON = 2.0

# 어떤 정당한 메커니즘으로도 도달할 수 없는 체력 절대 상한이다.
# OpenArena 는 체력이 max_health 를 넘는 것이 정상이므로(스폰 오버힐 max_health+25,
# 메가헬스·가드 파워업은 최대 2*handicap) max_health 를 기준으로 삼으면 오탐이 난다.
# 정당한 상한(약 200)보다 넉넉히 위, 주입 오버플로우(999)보다는 아래로 잡는다.
MAX_POSSIBLE_HEALTH = 250

# 바닥·경계 판정의 여유다. 관측 노이즈로 경계선을 살짝 넘는 것을 위반으로 보지 않기 위함이다.
FLOOR_MARGIN = 5.0
BOUNDS_MARGIN = 5.0

# ── 집계 ───────────────────────────────────────────────────────────────────
# 같은 (봇, 규칙) 탐지가 이 간격(초) 이내로 이어지면 하나의 사건으로 본다.
#
# 근거(2026-07-23 실측): tools/measure_interval.py 로 실제 게임 텔레메트리를 측정한 결과,
# 봇별 연속 기록 간격이 0.050초로 100% 균일했고 초당 20프레임이 역산됐다. 따라서 0.5초는
# 정확히 10프레임에 해당한다.
#
# 왜 10프레임인가: 봇이 경계선 위에서 오락가락하면 탐지가 몇 프레임 깜빡이는데, 그것을 서로
# 다른 사건으로 쪼개면 목록이 무의미해진다. 10프레임은 그 깜빡임을 흡수하면서도 실제로 따로
# 일어난 두 사건은 가를 수 있는 폭이다.
#
# 왜 실측 흔들림(0.050초) 바로 위로 낮추지 않는가: 측정한 세션에 깜빡이는 버그가 없었을 뿐,
# 그런 현상이 없는 것은 아니다. 0.1초는 2프레임 여유라 3프레임만 깜빡여도 사건이 쪼개진다.
# 실측이 확정한 것은 "0.5초가 곧 10프레임"이라는 환산이지, 10프레임이 과하다는 사실이 아니다.
#
# 주의: 텔레메트리의 time 은 실제 시간이 아니라 시뮬레이션 시간이다. 게임은 서버 프레임마다
# level.time 을 고정 간격으로 올리므로, 서버가 실제로 버벅여도 이 값은 흔들리지 않는다.
# 그래서 이 측정으로는 성능 문제를 알 수 없다. 워치독 오라클은 별도 계측이 필요하다.
GAP_SECONDS = 0.5

# ── 이상탐지 특징 ──────────────────────────────────────────────────────────
# 창 하나의 길이(초). stuck_seconds(2.0)보다 길어야 규칙이 못 보는 영역을 본다.
# 20Hz에서 5초는 100프레임이라 통계가 안정적이면서, 10초짜리 이상이 평균에 묻히지도 않는다.
# 아직 관측에서 역산한 값이 아니다. 교란 세션에서 3·5·10초로 바꿔 가며 탐지가 어떻게
# 달라지는지 재면 근거가 생긴다.
WINDOW_SECONDS = 5.0

# 창을 옮기는 간격(초). 창 길이보다 짧아 창이 겹친다. 겹치면 이상 구간 하나가 여러 창에
# 걸쳐 나타나 시작 시점을 이 간격 단위로 짚을 수 있다.
WINDOW_STEP_SECONDS = 1.0


def build_extractor() -> FeatureExtractor:
    """공용 설정으로 윈도우 특징 추출기를 만든다."""
    return FeatureExtractor(
        window_seconds=WINDOW_SECONDS,
        step_seconds=WINDOW_STEP_SECONDS,
    )


def build_checker() -> InvariantChecker:
    """공용 설정으로 하드 인바리언트 검출기를 만든다.

    CLI와 서버가 이 함수를 함께 쓰므로, 같은 텔레메트리에 같은 판정이 나오는 것이 보장된다.
    """
    return InvariantChecker(
        bounds=BOUNDS,
        max_speed=MAX_SPEED,
        stuck_seconds=STUCK_SECONDS,
        stuck_epsilon=STUCK_EPSILON,
        max_possible_health=MAX_POSSIBLE_HEALTH,
        floor_margin=FLOOR_MARGIN,
        bounds_margin=BOUNDS_MARGIN,
    )


def build_aggregator(on_close=None) -> BugAggregator:
    """공용 설정으로 집계기를 만든다.

    on_close 는 사건이 닫힐 때 호출할 콜백이다. 서버는 여기에 파일 기록기를 연결하고,
    CLI는 넘기지 않는다(파일로 남길 것이 없다).
    """
    return BugAggregator(gap_seconds=GAP_SECONDS, on_close=on_close)


def as_dict() -> dict:
    """현재 설정을 세션 요약에 남길 형태로 반환한다.

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
        "floor_margin": FLOOR_MARGIN,
        "bounds_margin": BOUNDS_MARGIN,
        "gap_seconds": GAP_SECONDS,
    }
