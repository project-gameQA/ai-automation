"""텔레메트리 및 버그 관련 데이터 구조를 정의하는 모듈이다.

한 프레임(틱)에서 관측된 봇의 상태(StateSample), 맵의 물리적 경계(MapBounds),
그리고 오라클이 버그를 판정했을 때 생성하는 결과(Bug)와 그 심각도(Severity)를
정의한다. 검출 로직(invariants.py)과 데이터 소스(replay_source.py)는 모두 이
구조들을 공유하며, 이 파일이 시스템의 공용 어휘 역할을 한다.
"""

from __future__ import annotations  # 클래스 정의 안에서 자기 타입을 문자열 없이 참조할 수 있도록 타입 힌트를 지연 평가한다.

from dataclasses import dataclass, field  # 반복적인 생성자/표현 메서드 작성을 줄이기 위해 dataclass를 사용한다.
from enum import Enum  # 심각도처럼 값의 집합이 정해진 항목을 표현하기 위해 열거형을 사용한다.


class Severity(Enum):
    """버그의 심각도를 나타내는 열거형이다."""

    LOW = "low"        # 경미한 이상. 진행에 큰 지장이 없다.
    MEDIUM = "medium"  # 중간 수준. 경험을 해치지만 치명적이지는 않다.
    HIGH = "high"      # 심각. 진행 불가나 명백한 규칙 위반에 해당한다.


@dataclass
class MapBounds:
    """맵의 물리적 경계를 표현하는 값 객체다.

    하드 인바리언트가 '맵 밖으로 나감'이나 '바닥을 뚫고 떨어짐'을 판정할 때
    기준으로 사용한다. 값은 맵마다 다르므로 외부에서 주입한다.
    """

    min_x: float      # 맵의 X축 최소 좌표
    max_x: float      # 맵의 X축 최대 좌표
    min_y: float      # 맵의 Y축 최소 좌표
    max_y: float      # 맵의 Y축 최대 좌표
    floor_z: float    # 맵 바닥의 Z좌표. 이보다 아래로 내려가면 낙하로 본다.
    ceiling_z: float  # 맵 천장의 Z좌표. 이보다 위로 올라가면 이상으로 본다.


@dataclass
class StateSample:
    """한 틱에서 관측된 한 엔티티(봇)의 상태를 표현한다.

    향후 게임 계측 코드(OpenArena 소스)나 현재의 리플레이 소스가 이 형태로 상태를
    만들어 검출기에 넘긴다. 필드는 하드 인바리언트가 필요로 하는 최소 집합이다.
    """

    tick: int          # 프레임 번호. 시간 순서를 식별한다.
    time: float        # 게임 시작 이후 경과 시간(초).
    entity_id: int     # 상태의 주인인 엔티티(봇) 식별자.
    x: float           # 위치 X 좌표
    y: float           # 위치 Y 좌표
    z: float           # 위치 Z 좌표(높이)
    vx: float          # 속도 X 성분
    vy: float          # 속도 Y 성분
    vz: float          # 속도 Z 성분
    health: float      # 현재 체력
    max_health: float  # 최대 체력. 체력 범위 검사의 상한으로 쓴다.
    move_input: int = 0  # 이번 틱에 이동 명령이 있었는지(1) 없었는지(0). 끼임 판정의 입력 게이트로 쓴다.
    attack: int = 0      # 이번 틱에 발사 버튼을 눌렀는지(1) 아닌지(0). 향후 발사 관련 판정에 쓴다.

    @property
    def speed(self) -> float:
        """3차원 속도 벡터의 크기(속력)를 반환한다."""
        return (self.vx ** 2 + self.vy ** 2 + self.vz ** 2) ** 0.5  # 각 축 성분의 제곱합의 제곱근이 속력이다.


@dataclass
class Bug:
    """오라클이 판정한 하나의 버그 사건을 표현한다.

    검출기는 규칙 위반을 발견할 때마다 이 객체를 만들어 반환한다. 집계 층과
    대시보드는 이 객체를 그대로 받아 목록에 누적하고 화면에 표시한다.
    """

    tick: int           # 버그가 관측된 프레임 번호
    time: float         # 버그가 관측된 게임 시간(초)
    entity_id: int      # 버그를 일으킨 엔티티 식별자
    rule: str           # 위반한 규칙의 이름(예: "fell_through_floor")
    severity: Severity  # 버그의 심각도
    message: str        # 사람이 읽을 수 있는 설명 문자열
    details: dict = field(default_factory=dict)  # 관측값 등 디버깅용 부가 정보. 기본값은 빈 딕셔너리다.
