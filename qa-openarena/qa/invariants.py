"""하드 인바리언트(결정적 규칙) 기반 오라클을 정의하는 모듈이다.

이 오라클은 학습이 필요 없다. 물리적으로 불가능하거나 명백히 규칙에 어긋나는
상태를 고정된 규칙으로 판정한다. 입력은 StateSample 하나이고, 출력은 그 틱에서
발견된 Bug의 리스트다. 상태가 필요한 규칙(끼임 판정)을 위해 엔티티별 이력을
내부에 보관한다.
"""

from typing import Optional  # 규칙 메서드가 Bug 또는 None을 반환함을 타입으로 명시하기 위해 사용한다.

from .telemetry import StateSample, Bug, Severity, MapBounds  # 공용 데이터 구조를 같은 패키지에서 가져온다.


class InvariantChecker:
    """여러 하드 인바리언트 규칙을 한데 모아 순차 적용하는 검출기다."""

    def __init__(
        self,
        bounds: MapBounds,
        max_speed: float,
        stuck_seconds: float,
        stuck_epsilon: float,
        max_possible_health: float = 250.0,
        floor_margin: float = 5.0,
        bounds_margin: float = 5.0,
    ) -> None:
        self.bounds = bounds                # 맵 경계 기준값을 보관한다. 바닥/경계 판정에 사용한다.
        self.max_speed = max_speed          # 물리적으로 가능한 최대 속력. 이를 넘으면 이상으로 본다.
        self.max_possible_health = max_possible_health  # 어떤 정당한 메커니즘으로도 도달 불가능한 체력 절대 상한이다.
        self.stuck_seconds = stuck_seconds  # 이 시간(초) 이상 사실상 정지하면 끼임으로 본다.
        self.stuck_epsilon = stuck_epsilon  # 이 거리 이하의 이동은 '정지'로 간주하는 허용 오차다.
        self.floor_margin = floor_margin    # 바닥 판정 여유. floor_z보다 이만큼 더 내려가야 낙하로 본다(노이즈 방지).
        self.bounds_margin = bounds_margin  # 수평 경계/천장 판정 여유.
        # 끼임 판정을 위한 엔티티별 이력을 저장하는 딕셔너리다.
        # 키는 entity_id, 값은 {"x","y","z","last_move_time","reported"} 형태다.
        self._history: dict[int, dict] = {}

    def check(self, sample: StateSample) -> list[Bug]:
        """한 StateSample을 받아 위반한 모든 규칙의 Bug 리스트를 반환한다."""
        bugs: list[Bug] = []  # 이번 틱에서 발견한 버그를 담을 리스트를 만든다.
        # 과거 상태가 필요 없는 무상태 규칙들을 차례로 적용한다.
        for rule in (self._check_floor, self._check_bounds, self._check_health, self._check_speed):
            bug = rule(sample)      # 각 규칙을 호출한다. 결과는 Bug 또는 None이다.
            if bug is not None:     # 규칙이 위반을 발견했으면
                bugs.append(bug)    # 결과 리스트에 추가한다.
        stuck_bug = self._check_stuck(sample)  # 과거 상태가 필요한 끼임 규칙을 별도로 호출한다.
        if stuck_bug is not None:   # 끼임이 판정되었으면
            bugs.append(stuck_bug)  # 결과 리스트에 추가한다.
        return bugs                 # 이번 틱의 버그 리스트를 반환한다. 위반이 없으면 빈 리스트다.

    def _check_floor(self, s: StateSample) -> Optional[Bug]:
        """엔티티가 맵 바닥 아래로 내려갔는지 판정한다."""
        threshold = self.bounds.floor_z - self.floor_margin  # 낙하로 볼 z 임계값을 계산한다.
        if s.z < threshold:  # 현재 z가 임계값보다 낮으면 바닥을 뚫고 떨어진 것으로 본다.
            return Bug(
                tick=s.tick,
                time=s.time,
                entity_id=s.entity_id,
                rule="fell_through_floor",
                severity=Severity.HIGH,
                message=f"z={s.z:.1f} 이(가) 바닥(z={self.bounds.floor_z:.1f}) 아래로 내려갔다.",
                details={"z": s.z, "floor_z": self.bounds.floor_z},
            )
        return None  # 위반이 없으면 None을 반환한다.

    def _check_bounds(self, s: StateSample) -> Optional[Bug]:
        """엔티티가 맵의 수평 경계를 벗어나거나 천장을 넘었는지 판정한다."""
        m = self.bounds_margin  # 경계 여유를 짧은 이름으로 참조한다.
        out_of_bounds = (        # 하나라도 참이면 경계를 벗어난 것이다.
            s.x < self.bounds.min_x - m
            or s.x > self.bounds.max_x + m
            or s.y < self.bounds.min_y - m
            or s.y > self.bounds.max_y + m
            or s.z > self.bounds.ceiling_z + m
        )
        if out_of_bounds:  # 경계를 벗어났으면
            return Bug(
                tick=s.tick,
                time=s.time,
                entity_id=s.entity_id,
                rule="out_of_bounds",
                severity=Severity.HIGH,
                message=f"위치({s.x:.1f}, {s.y:.1f}, {s.z:.1f}) 이(가) 맵 경계를 벗어났다.",
                details={"x": s.x, "y": s.y, "z": s.z},
            )
        return None

    def _check_health(self, s: StateSample) -> Optional[Bug]:
        """체력이 물리적으로 불가능한 절대 상한을 초과했는지 판정한다.

        주의: OpenArena에서는 체력이 max_health를 넘는 것이 정상이다(스폰 오버힐은
        max_health+25, 메가헬스/가드 파워업은 최대 2*handicap까지 올라간다). 따라서
        'health > max_health'는 하드 인바리언트로 부적합하다. 대신 어떤 정당한
        메커니즘으로도 도달할 수 없는 절대 상한(max_possible_health)만 위반으로 본다.
        '최대치를 약간 넘는' 애매한 영역은 이상탐지(ML)가 맡을 자리다.
        """
        if s.health > self.max_possible_health:  # 정당한 상한을 훨씬 넘는, 도달 불가능한 값이면 이상으로 본다.
            return Bug(
                tick=s.tick,
                time=s.time,
                entity_id=s.entity_id,
                rule="health_out_of_range",
                severity=Severity.HIGH,
                message=f"체력 {s.health:.0f} 이(가) 절대 상한 {self.max_possible_health:.0f} 을(를) 초과했다.",
                details={"health": s.health, "max_possible_health": self.max_possible_health},
            )
        return None

    def _check_speed(self, s: StateSample) -> Optional[Bug]:
        """속력이 물리적 상한을 초과했는지 판정한다."""
        if s.speed > self.max_speed:  # 계산된 속력이 상한보다 크면 물리적으로 불가능한 이동으로 본다.
            return Bug(
                tick=s.tick,
                time=s.time,
                entity_id=s.entity_id,
                rule="impossible_velocity",
                severity=Severity.MEDIUM,
                message=f"속력 {s.speed:.0f} 이(가) 상한 {self.max_speed:.0f} 을(를) 초과했다.",
                details={"speed": s.speed, "max_speed": self.max_speed},
            )
        return None

    def _check_stuck(self, s: StateSample) -> Optional[Bug]:
        """이동 입력이 있는데도 위치가 변하지 않는지(진짜 끼임)를 판정한다.

        단순히 '오래 정지'를 보는 것이 아니라, 봇이 움직이려는 의도(이동 명령)가
        있었는지를 함께 본다. 입력이 없는 정지는 정상 대기(조준·매복)이므로 판정하지
        않고, 입력이 있는데도 위치가 안 변할 때만 끼임으로 본다. 이렇게 의도와 결과의
        불일치를 보므로, 시간 기반 규칙의 오탐(정상 대기)이 사라진다.

        과거 위치와 마지막으로 유의미하게 움직인 시각을 기억해야 하므로 내부
        이력(self._history)을 갱신하며 동작한다.
        """
        rec = self._history.get(s.entity_id)  # 이 엔티티의 이전 이력을 조회한다.
        if rec is None:  # 처음 보는 엔티티라면 비교할 과거가 없다.
            self._history[s.entity_id] = {  # 현재 상태로 이력을 초기화한다.
                "x": s.x,
                "y": s.y,
                "z": s.z,
                "last_move_time": s.time,
                "reported": False,
            }
            return None  # 초기화만 하고 판정하지 않는다.
        dx = s.x - rec["x"]  # 이전 유의미 위치로부터의 X 변위를 구한다.
        dy = s.y - rec["y"]  # Y 변위를 구한다.
        dz = s.z - rec["z"]  # Z 변위를 구한다.
        moved = (dx * dx + dy * dy + dz * dz) ** 0.5  # 변위 벡터의 크기(이동 거리)를 구한다.
        if moved > self.stuck_epsilon:  # 허용 오차보다 많이 움직였으면 정지 상태가 아니다.
            rec["x"], rec["y"], rec["z"] = s.x, s.y, s.z  # 유의미 위치를 현재로 갱신한다.
            rec["last_move_time"] = s.time                # 마지막 이동 시각을 현재로 갱신한다.
            rec["reported"] = False                       # 다음 끼임을 다시 보고할 수 있도록 플래그를 해제한다.
            return None                                   # 움직였으므로 끼임이 아니다.
        if s.move_input <= 0:  # 이동 명령이 없으면(정상 대기) 끼임으로 보지 않는다.
            rec["last_move_time"] = s.time  # 정지 시간이 누적되지 않도록 기준 시각을 현재로 미룬다.
            rec["reported"] = False         # 보고 플래그도 초기화한다.
            return None                     # 입력이 없으므로 판정하지 않는다.
        # 여기까지 왔다면: 이동 명령이 있었는데도 위치가 (허용 오차 이내로) 변하지 않았다.
        idle = s.time - rec["last_move_time"]  # 입력이 있는데 못 움직인 상태가 지속된 시간을 구한다.
        if idle >= self.stuck_seconds and not rec["reported"]:  # 임계 시간을 넘었고 아직 보고 전이면
            rec["reported"] = True  # 같은 끼임을 매 틱 중복 보고하지 않도록 플래그를 세운다.
            return Bug(
                tick=s.tick,
                time=s.time,
                entity_id=s.entity_id,
                rule="stuck",
                severity=Severity.MEDIUM,
                message=f"이동 입력이 있는데 {idle:.1f}초간 위치가 변하지 않아 끼임으로 판정한다.",
                details={"idle_seconds": idle, "position": [s.x, s.y, s.z]},
            )
        return None  # 아직 임계에 못 미쳤거나 이미 보고했으면 판정하지 않는다.
