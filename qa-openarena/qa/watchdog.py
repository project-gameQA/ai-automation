"""성능·크래시 워치독(③) 오라클이다.

오라클 ①·②와 관측 대상이 다르다. 앞의 둘은 **봇의 상태**를 보지만, 이쪽은 **게임 프로세스
자체**를 본다. 봇이 정상으로 보여도 게임이 버벅이거나 멈춰 있으면 그것도 버그다.

────────────────────────────────────────────────────────────────────────────
문제: 텔레메트리의 time 은 성능을 담지 않는다
────────────────────────────────────────────────────────────────────────────
Quake3 계열은 `level.time` 을 서버 프레임마다 `1000/sv_fps` ms씩 **고정 증가**시킨다.
즉 텔레메트리의 `time` 은 실제 시간이 아니라 시뮬레이션 시간이다. 서버가 실제로 2초간
멈춰도 `level.time` 은 50ms만 흐른다. 실측에서 프레임 간격이 0.050초로 100% 균일하게
나온 이유가 이것이다.

따라서 텔레메트리만 들여다봐서는 성능 문제를 **원리적으로** 알 수 없다.

────────────────────────────────────────────────────────────────────────────
해법: 시뮬레이션 시간과 실제 시간을 비교한다
────────────────────────────────────────────────────────────────────────────
서버는 폴링할 때마다 **실제 시각**을 안다. 그리고 새로 읽은 텔레메트리에서 **시뮬레이션
시간이 얼마나 흘렀는지**도 안다. 게임이 실시간으로 도는 한 둘은 같은 속도로 흘러야 한다.

    실효 서버 틱 = (Δ시뮬레이션 시간 / 프레임 주기) / Δ실제 시간

정상이면 sv_fps(20)와 같다. 게임이 버벅이면 시뮬레이션 시간이 실제 시간보다 느리게 흐르므로
이 값이 떨어진다. **게임 소스를 고치지 않고 잴 수 있다는 것이 이 방식의 이점이다.**

주의: 이것은 **서버 틱**이지 클라이언트 렌더 FPS 가 아니다. 화면이 몇 프레임으로 그려지는지는
텔레메트리에 없고, 재려면 cgame 계측이 필요하다. 봇이 액터인 이 프로젝트에서는 봇 로직이
도는 서버 틱이 더 적절한 지표이지만, 사람이 체감하는 프레임과는 다른 값이다.

────────────────────────────────────────────────────────────────────────────
프로세스 관측: CPU·메모리·생존 여부
────────────────────────────────────────────────────────────────────────────
CPU 사용률과 메모리는 텔레메트리에 없으므로 프로세스를 직접 봐야 한다. psutil 로 게임
프로세스를 찾아 읽는다. psutil 이 없거나 프로세스를 못 찾으면 그 부분만 비활성화되고
틱 측정은 계속 동작한다.

프로세스 상태를 함께 보면 **행(hang)과 종료를 구분할 수 있다**. 텔레메트리만으로는 둘 다
"기록이 멈췄다"로 같아 보인다.

    텔레메트리 멈춤 + 프로세스 살아 있음  → 진행 없음. 행이거나 메뉴/점수판 구간이다.
    텔레메트리 멈춤 + 프로세스 없음      → 종료. 크래시인지 정상 종료인지는 사람이 판단한다.

한계: "진행 없음"은 진짜 행과 메뉴·점수판 구간을 구분하지 못한다. 셋 다 텔레메트리가 멈추고
프로세스는 살아 있는 상태로 보인다. 구분하려면 게임의 현재 상태(매치 중인지)를 텔레메트리에
실어 보내야 하고 그것은 QVM 재빌드 건이다. 그래서 경보 이름을 "행"이 아니라 "진행 없음"으로
두고 사실만 보고한다.

────────────────────────────────────────────────────────────────────────────
밀린 기록과 실시간의 구분
────────────────────────────────────────────────────────────────────────────
서버가 시작하면 기존 텔레메트리 파일을 처음부터 다 읽는다. 그것은 지난 매치의 기록이지 지금
관측한 것이 아니다. 이 구분을 하지 않으면 게임을 켜지도 않은 상태에서 성능 저하나 행으로
오판한다. 구분 방법은 데이터에 이미 있다. 밀린 기록을 읽을 때는 실제 시간 1초에 시뮬레이션
시간이 수백 초 흐르므로 비율이 1을 훨씬 넘는다.

또한 **실시간 관측이 한 번이라도 확인되기 전에는 어떤 판정도 하지 않는다.** 게임을 켜지
않았거나 메인 메뉴에 있는 상태는 이상이 아니라 대기 상태다.

────────────────────────────────────────────────────────────────────────────
임계값에 대해
────────────────────────────────────────────────────────────────────────────
아래 기본값은 **관측에서 역산한 값이 아니라 정한 상수다.** 이 환경에서 정상 플레이의 틱이
얼마나 흔들리는지 아직 재 보지 않았다. tools/measure_perf.py 로 기준선을 잡은 뒤 조정해야
한다. 맵 경계를 보정했던 것과 같은 절차다.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

# 게임 프로세스를 찾을 때 이름에 포함되어야 하는 문자열이다. 소문자로 비교한다.
DEFAULT_PROCESS_HINT = "openarena"

# 틱을 평균 낼 구간의 길이(초). 폴링 간격이 1초이고 파일 쓰기가 뭉쳐 도착할 수 있어,
# 한 번의 관측만으로는 값이 크게 흔들린다. 몇 초를 모아 평균한다.
DEFAULT_SMOOTH_SECONDS = 5.0

# 실효 틱이 목표 대비 이 비율 아래로 떨어지면 성능 저하로 본다.
# 0.75 는 20fps 목표에서 15fps 아래를 뜻한다. 근거 없는 기본값이므로 실측 후 조정한다.
DEFAULT_TICK_RATIO_ALERT = 0.75

# 텔레메트리가 이 시간(초) 이상 들어오지 않으면 멈춘 것으로 본다.
# 맵 로딩에도 잠깐 멈추므로 그보다는 넉넉해야 한다.
DEFAULT_STALL_SECONDS = 5.0

# 실효 틱이 목표의 이 배를 넘으면 **밀린 기록을 따라잡는 중**으로 본다.
#
# 왜 필요한가: 서버가 시작하면 기존 텔레메트리 파일을 처음부터 다 읽는다. 그것은 지난
# 매치의 기록이지 지금 관측한 것이 아니다. 이 구분을 하지 않으면, 게임을 아직 켜지도 않은
# 상태에서 "직전에 데이터가 들어왔는데 지금은 안 들어온다"로 보여 성능 저하나 행으로
# 오판한다. 실제로 그렇게 오탐이 났다.
#
# 구분 방법은 데이터에 이미 있다. 밀린 기록을 읽을 때는 실제 시간 1초에 시뮬레이션 시간이
# 수백 초 흐르므로 비율이 1을 훨씬 넘는다. 실시간이면 1 근처다.
BACKLOG_RATIO = 1.5


@dataclass
class PerfSample:
    """한 번의 관측 결과다."""

    wall_time: float          # 관측한 실제 시각(epoch 초)
    sim_advance: float        # 직전 관측 이후 흐른 시뮬레이션 시간(초)
    wall_advance: float       # 직전 관측 이후 흐른 실제 시간(초)
    tick_rate: Optional[float]  # 실효 서버 틱(초당 프레임). 계산 불가면 None
    cpu_percent: Optional[float]
    memory_mb: Optional[float]
    process_alive: Optional[bool]  # psutil 을 못 쓰면 None


@dataclass
class WatchdogAlert:
    """워치독이 낸 경보 하나다."""

    kind: str          # "low_tick" | "no_progress" | "process_gone"
    started_at: float  # 실제 시각(epoch 초)
    ended_at: float
    detail: dict = field(default_factory=dict)
    ongoing: bool = True

    @property
    def duration(self) -> float:
        return self.ended_at - self.started_at

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration": round(self.duration, 1),
            "ongoing": self.ongoing,
            **{k: (round(v, 2) if isinstance(v, float) else v) for k, v in self.detail.items()},
        }


class ProcessMonitor:
    """게임 프로세스를 찾아 CPU·메모리·생존 여부를 읽는다.

    psutil 이 없거나 프로세스를 못 찾아도 예외를 내지 않는다. 그 경우 해당 값만 None 이 되고
    틱 측정은 계속 동작해야 한다. 워치독 전체가 선택적 의존성 하나 때문에 멈추면 안 된다.
    """

    def __init__(self, process_hint: str = DEFAULT_PROCESS_HINT) -> None:
        self.process_hint = process_hint.lower()
        self.available = False   # psutil 을 쓸 수 있는지
        self.reason = ""         # 못 쓰는 이유(화면에 노출한다)
        self._psutil = None
        self._proc = None        # 찾은 프로세스 핸들
        try:
            import psutil
            self._psutil = psutil
            self.available = True
        except ImportError:
            self.reason = "psutil 이 설치돼 있지 않다. pip install psutil"

    def _find(self):
        """이름에 힌트가 들어가는 프로세스를 찾는다."""
        if self._psutil is None:
            return None
        for p in self._psutil.process_iter(["name"]):
            try:
                name = (p.info.get("name") or "").lower()
                if self.process_hint in name:
                    # cpu_percent 는 첫 호출이 항상 0.0 을 돌려준다(직전 호출과의 차이를 재기
                    # 때문이다). 여기서 한 번 불러 기준점을 만들어 둔다.
                    p.cpu_percent(None)
                    return p
                    
            except (self._psutil.NoSuchProcess, self._psutil.AccessDenied):
                continue
        return None

    def sample(self) -> tuple:
        """(cpu_percent, memory_mb, alive) 를 반환한다. 못 읽으면 각각 None 이다."""
        if not self.available:
            return (None, None, None)

        # 들고 있던 핸들이 죽었으면 버린다. 게임을 껐다 켜면 PID 가 바뀐다.
        if self._proc is not None:
            try:
                if not self._proc.is_running():
                    self._proc = None
            except Exception:
                self._proc = None

        if self._proc is None:
            self._proc = self._find()
            if self._proc is None:
                return (None, None, False)  # 프로세스가 없다는 것도 정보다.

        try:
            cpu = self._proc.cpu_percent(None)
            mem = self._proc.memory_info().rss / (1024 * 1024)
            return (cpu, mem, True)
        except Exception:
            # 권한 문제나 종료 직후 접근 등이다. 다음 관측에서 다시 찾는다.
            self._proc = None
            return (None, None, False)


class Watchdog:
    """폴링마다 관측을 받아 성능 지표와 경보를 내는 상태 기계다.

    사용법:
        wd = Watchdog(frame_period=0.05, target_tick=20.0)
        # 폴링마다
        wd.observe(sim_time=최신_게임시간, got_samples=이번에_읽은_줄이_있는지, map_restarted=...)
    """

    def __init__(
        self,
        frame_period: float,
        target_tick: float,
        smooth_seconds: float = DEFAULT_SMOOTH_SECONDS,
        tick_ratio_alert: float = DEFAULT_TICK_RATIO_ALERT,
        stall_seconds: float = DEFAULT_STALL_SECONDS,
        process_hint: str = DEFAULT_PROCESS_HINT,
    ) -> None:
        self.frame_period = frame_period      # 서버 프레임 주기(초). 20Hz면 0.05
        self.target_tick = target_tick        # 목표 틱(초당 프레임)
        self.smooth_seconds = smooth_seconds
        self.tick_ratio_alert = tick_ratio_alert
        self.stall_seconds = stall_seconds

        self.monitor = ProcessMonitor(process_hint)
        self._history: deque = deque()        # 최근 (wall_time, sim_time) 쌍
        self._last_sim: Optional[float] = None
        self._last_wall: Optional[float] = None
        self._last_data_wall: Optional[float] = None   # 마지막으로 새 줄이 들어온 실제 시각
        self._suppress_until: float = 0.0     # 맵 로딩 직후 경보를 억제할 시각

        self.latest: Optional[PerfSample] = None
        self._open: dict = {}                 # 진행 중인 경보. 키는 kind
        self.alerts: list = []                # 닫힌 경보 + 진행 중인 경보
        # 실시간 관측이 한 번이라도 확인됐는지. 확인되기 전에는 어떤 판정도 하지 않는다.
        # 게임을 켜지 않은 상태나 밀린 기록을 읽는 동안을 성능 문제로 보고하면 안 된다.
        self._live_confirmed = False
        self.state = "idle"  # "idle" | "catching_up" | "live" 

    def observe(
        self,
        sim_time: Optional[float],
        got_samples: bool,
        map_restarted: bool = False,
        now: Optional[float] = None,
    ) -> None:
        """폴링 한 번의 관측을 넣는다.

        sim_time      : 텔레메트리에서 본 최신 게임 시간. 아직 없으면 None.
        got_samples   : 이번 폴링에서 새 줄을 읽었는지.
        map_restarted : 맵이 새로 로드됐는지. 로딩 중 멈춤은 정상이므로 경보를 잠시 억제한다.
        """
        now = now if now is not None else time.time()

        if map_restarted:
            # 맵 로딩은 실제로 몇 초 멈춘다. 그것을 행으로 보고하면 매 맵 전환마다 오탐이 난다.
            self._suppress_until = now + self.stall_seconds * 2
            self._history.clear()
            self._last_sim = None
            self._last_wall = None
            self._close_all(now)

        if got_samples:
            self._last_data_wall = now

        cpu, mem, alive = self.monitor.sample()

        # ── 틱 계산 ────────────────────────────────────────────────────────
        tick = None
        sim_adv = 0.0
        wall_adv = 0.0
        if sim_time is not None:
            self._history.append((now, sim_time))
            # 평활 구간보다 오래된 관측은 버린다.
            while len(self._history) > 2 and now - self._history[0][0] > self.smooth_seconds:
                self._history.popleft()
            if len(self._history) >= 2:
                w0, s0 = self._history[0]
                w1, s1 = self._history[-1]
                wall_adv = w1 - w0
                sim_adv = s1 - s0
                if wall_adv > 0.5:  # 너무 짧은 구간은 폴링 지터에 휘둘린다.
                    tick = (sim_adv / self.frame_period) / wall_adv
            self._last_sim = sim_time
            self._last_wall = now

        self.latest = PerfSample(
            wall_time=now,
            sim_advance=sim_adv,
            wall_advance=wall_adv,
            tick_rate=tick,
            cpu_percent=cpu,
            memory_mb=mem,
            process_alive=alive,
        )

        if now < self._suppress_until:
            self.state = "catching_up"
            return  # 맵 로딩 직후에는 판정하지 않는다.

        # ── 밀린 기록을 따라잡는 중인지 판단 ────────────────────────────────
        # 이 판단이 판정보다 먼저 와야 한다. 따라잡는 동안의 값으로 판정하면 오탐이 난다.
        if tick is not None and self.target_tick and (tick / self.target_tick) > BACKLOG_RATIO:
            self.state = "catching_up"
            self._history.clear()        # 따라잡은 구간이 이후 평균에 섞이지 않게 버린다.
            self._last_data_wall = now   # 멈춤 판정의 기준 시각을 지금으로 미룬다.
            self._close_all(now)
            return

        # 새 줄이 실시간 속도로 들어오고 있으면 그때부터 실시간 관측으로 본다.
        if got_samples and tick is not None:
            if self.state != "live":
                # 대기·따라잡기에서 실시간으로 막 넘어온 순간이다. 평활 구간에 남아 있는
                # 대기 시절 관측(시뮬레이션 시간이 멈춰 있던 구간)을 버린다. 그대로 두면
                # 게임을 켠 직후 몇 초 동안 틱이 낮게 나와 성능 저하로 오판한다.
                self._history.clear()
                self._history.append((now, sim_time))
                tick = None  # 이번 폴링은 판단 근거가 없다. 다음 폴링부터 정상적으로 잰다.
                self.latest.tick_rate = None
            self._live_confirmed = True
            self.state = "live"

        if not self._live_confirmed:
            # 아직 게임이 텔레메트리를 쓰고 있는 것을 확인하지 못했다. 메인 메뉴에 있거나
            # 게임을 켜지 않은 상태다. 이것은 이상이 아니라 대기 상태이므로 판정하지 않는다.
            self.state = "idle"
            self._close_all(now)
            return

        # ── 판정 ──────────────────────────────────────────────────────────
        stalled = (
            self._last_data_wall is not None
            and (now - self._last_data_wall) >= self.stall_seconds
        )

        if stalled and alive is True:
            # 게임은 떠 있는데 아무것도 진행되지 않는다.
            #
            # 정직하게 말하면 이 신호는 세 가지를 구분하지 못한다. (1) 진짜 행,
            # (2) 매치가 끝나 메인 메뉴로 나간 상태, (3) 점수판(intermission) 구간이다.
            # 셋 다 "텔레메트리가 멈췄고 프로세스는 살아 있다"로 같아 보인다. 구분하려면
            # 게임 쪽에서 현재 상태를 텔레메트리에 실어 보내야 하며, 그것은 QVM 재빌드 건이다.
            # 그래서 경보 이름을 "행"이 아니라 "진행 없음"으로 두고 사실만 보고한다.
            self._raise("no_progress", now, {"stalled_seconds": now - self._last_data_wall})
        else:
            self._clear("no_progress", now)

        if stalled and alive is False:
            # 크래시인지 정상 종료인지는 여기서 구분할 수 없다. 사실만 보고하고 판단은 사람이 한다.
            self._raise("process_gone", now, {"stalled_seconds": now - self._last_data_wall})
        else:
            self._clear("process_gone", now)

        if stalled:
            # 텔레메트리가 안 들어오는 동안에는 틱을 잴 근거가 없다. 계산상 0에 수렴하지만
            # 그것은 "느리다"가 아니라 "데이터가 없다"는 뜻이다. 이 상태를 성능 저하로
            # 보고하면 행 경보와 중복되고, 게다가 열린 채로 남아 계속 표시된다.
            # 멈춤은 hang/process_gone 이 담당하므로 여기서는 성능 경보를 닫는다.
            self._clear("low_tick", now)
        elif tick is not None:
            ratio = tick / self.target_tick if self.target_tick else 1.0
            if ratio < self.tick_ratio_alert:
                self._raise("low_tick", now, {"tick_rate": tick, "ratio": ratio})
            else:
                self._clear("low_tick", now)

    def _raise(self, kind: str, now: float, detail: dict) -> None:
        """경보를 새로 열거나, 이미 열려 있으면 갱신한다."""
        alert = self._open.get(kind)
        if alert is None:
            alert = WatchdogAlert(kind=kind, started_at=now, ended_at=now, detail=detail)
            self._open[kind] = alert
            self.alerts.append(alert)
        else:
            alert.ended_at = now
            alert.detail.update(detail)

    def _clear(self, kind: str, now: float) -> None:
        """열려 있던 경보를 닫는다."""
        alert = self._open.pop(kind, None)
        if alert is not None:
            alert.ended_at = now
            alert.ongoing = False

    def _close_all(self, now: float) -> None:
        for kind in list(self._open.keys()):
            self._clear(kind, now)

    def status(self) -> dict:
        """대시보드가 쓸 현재 상태를 반환한다."""
        s = self.latest
        return {
            "state": self.state,
            "live_confirmed": self._live_confirmed,
            "process_monitor": self.monitor.available,
            "process_monitor_reason": self.monitor.reason,
            "target_tick": self.target_tick,
            "tick_rate": round(s.tick_rate, 2) if (s and s.tick_rate is not None) else None,
            "tick_ratio": (round(s.tick_rate / self.target_tick, 3)
                           if (s and s.tick_rate is not None and self.target_tick) else None),
            "cpu_percent": round(s.cpu_percent, 1) if (s and s.cpu_percent is not None) else None,
            "memory_mb": round(s.memory_mb, 1) if (s and s.memory_mb is not None) else None,
            "process_alive": s.process_alive if s else None,
            "tick_ratio_alert": self.tick_ratio_alert,
            "stall_seconds": self.stall_seconds,
            # 최근 것이 위로 오도록 뒤집는다.
            "alerts": [a.to_dict() for a in self.alerts[-30:]][::-1],
            "alert_count": len(self.alerts),
        }
