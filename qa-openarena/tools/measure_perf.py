"""정상 플레이의 서버 틱 기준선을 재는 도구다.

왜 필요한가:
워치독의 성능 저하 임계값(`WATCHDOG_TICK_RATIO_ALERT`)은 정한 상수로 시작했다. 이 환경에서
정상 플레이의 틱이 얼마나 흔들리는지 재 봐야 근거 있는 값을 정할 수 있다. 맵 경계를
tools/calibrate_bounds.py 로 보정했던 것과 같은 절차다.

무엇을 재는가:
서버가 돌고 있는 동안 /api/events 를 주기적으로 불러 워치독이 보고하는 틱을 모은다.

**워치독이 실시간 감시 상태(live)일 때만 표본으로 센다.** 게임을 켜지 않았거나 지난 기록을
따라잡는 중이면 시뮬레이션 시간이 흐르지 않아 틱이 0으로 보고되는데, 그것은 "느리다"가 아니라
"관측할 것이 없다"는 뜻이다. 이 구분을 하지 않으면 대기 시간이 길수록 통계가 0 쪽으로 끌려가
중앙값이 0이 되는 일이 실제로 있었다.

왜 파일로 남기는가:
틱은 **그 순간의 서버만 아는 값**이다. 실제 시각과 시뮬레이션 시간을 함께 아는 것은 서버뿐이고,
텔레메트리 파일에는 시뮬레이션 시간만 남는다. 지금 안 남기면 그 관측은 다시 만들 수 없다.
"재현 가능한 파생물은 버려도 되고, 재현 불가능한 관측은 남긴다"는 이 프로젝트의 기준을 그대로
적용해, 세션 폴더에 관측 기록과 요약을 남긴다.

실행 예:
    python tools/measure_perf.py --seconds 180
    python tools/measure_perf.py --seconds 600 --url http://127.0.0.1:8000
"""

import json
import statistics
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEFAULT_URL = "http://127.0.0.1:8000"
DEFAULT_SECONDS = 180
INTERVAL = 1.0  # 서버 폴링 간격과 맞춘다.

# 진행 표시를 한 줄에 덮어쓸지 여부를 자동으로 정한다.
# 파일로 리다이렉트하면 \r 이 그대로 들어가 한 줄이 끝없이 길어지므로, 그때는 줄바꿈을 쓴다.
_IS_TTY = sys.stdout.isatty()


def out(text: str, end: str = "\n") -> None:
    """즉시 출력한다. 리다이렉트해도 진행 상황이 바로 파일에 들어가도록 flush 한다."""
    print(text, end=end, flush=True)


def main(argv):
    url = DEFAULT_URL
    seconds = DEFAULT_SECONDS
    if "--url" in argv:
        url = argv[argv.index("--url") + 1]
    if "--seconds" in argv:
        seconds = int(argv[argv.index("--seconds") + 1])

    try:
        from qa import config
        session_dir = Path(config.__file__).resolve().parents[1] / "sessions"
    except Exception:
        session_dir = Path("sessions")

    out("")
    out("=" * 66)
    out("  서버 틱 기준선 측정")
    out("=" * 66)
    out(f"  {url} 를 {INTERVAL:.0f}초 간격으로 {seconds}초 동안 관측한다.")
    out("  게임을 켜고 봇 매치를 시작한 뒤 실행한다. 주입기는 끈다.")
    out("  워치독이 '감시 중'일 때만 표본으로 센다.")
    out("")

    ticks = []
    cpus = []
    mems = []
    records = []      # 파일로 남길 관측 기록
    target = None
    idle_polls = 0
    started = time.time()
    started_iso = datetime.now().isoformat(timespec="seconds")

    try:
        while time.time() - started < seconds:
            try:
                with urllib.request.urlopen(url + "/api/events", timeout=5) as r:
                    w = json.load(r).get("watchdog") or {}
            except Exception as e:
                out(f"\n  서버에 연결하지 못했다: {e}")
                return 1

            target = w.get("target_tick") or target
            state = w.get("state", "unknown")
            tick = w.get("tick_rate")

            # 실시간 감시 상태에서 실제로 측정된 값만 표본으로 센다.
            counted = state == "live" and tick is not None and tick > 0
            if counted:
                ticks.append(tick)
                if w.get("cpu_percent") is not None:
                    cpus.append(w["cpu_percent"])
                if w.get("memory_mb") is not None:
                    mems.append(w["memory_mb"])
            else:
                idle_polls += 1

            records.append({
                "t": round(time.time() - started, 1),
                "state": state,
                "tick": tick,
                "counted": counted,
                "cpu": w.get("cpu_percent"),
                "mem": w.get("memory_mb"),
            })

            elapsed = int(time.time() - started)
            label = {"live": "감시 중", "idle": "게임 대기", "catching_up": "따라잡는 중"}.get(state, state)
            line = (f"  {elapsed:>4}/{seconds}초   상태 {label:<8} 표본 {len(ticks):>4}   "
                    f"틱 {'—' if tick is None else round(tick, 1)}")
            out(line + ("      " if _IS_TTY else ""), end="\r" if _IS_TTY else "\n")
            time.sleep(INTERVAL)
    except KeyboardInterrupt:
        out("\n  중단됨. 지금까지 모은 표본으로 계산한다.")

    out("")
    out("")

    if idle_polls:
        out(f"  대기·따라잡기 상태였던 폴링 {idle_polls}회는 표본에서 제외했다.")
        out("  (게임을 켜지 않았거나 지난 기록을 읽는 동안은 잴 것이 없다.)")
        out("")

    if len(ticks) < 30:
        out(f"  실제 표본이 {len(ticks)}개뿐이라 통계를 낼 수 없다.")
        out("  게임을 켜고 봇 매치를 시작한 상태인지 확인한 뒤 다시 잰다.")
        out("  대시보드의 WATCHDOG 줄에서 '감시 중'이 떠 있어야 한다.")
        _save(session_dir, started_iso, records, None, target)
        return 1

    ticks_sorted = sorted(ticks)
    def pct(p):
        return ticks_sorted[max(0, int(len(ticks_sorted) * p))]

    lo, p5, med = pct(0.01), pct(0.05), statistics.median(ticks)

    out(f"  틱 표본 {len(ticks)}개   목표 {target}")
    out(f"    최소 {ticks_sorted[0]:.2f}   1% {lo:.2f}   5% {p5:.2f}   "
        f"중앙값 {med:.2f}   최대 {ticks_sorted[-1]:.2f}")
    out(f"    표준편차 {statistics.pstdev(ticks):.3f}")
    if cpus:
        # psutil 은 코어가 여러 개면 100%를 넘는 값을 돌려준다. 코어 수로 나눈 값도 함께 보여 준다.
        try:
            import psutil
            cores = psutil.cpu_count() or 1
        except Exception:
            cores = 1
        out(f"  CPU  중앙값 {statistics.median(cpus):.1f}%   최대 {max(cpus):.1f}%"
            + (f"   (코어 {cores}개 기준 전체의 {max(cpus)/cores:.0f}%)" if cores > 1 else ""))
    if mems:
        out(f"  메모리  처음 {mems[0]:.0f}MB   끝 {mems[-1]:.0f}MB   최대 {max(mems):.0f}MB")
        if mems[-1] - mems[0] > 100:
            out("    주의: 관측 중 메모리가 100MB 넘게 늘었다. 더 길게 재서 누수인지 확인한다.")
    out("")

    suggest = None
    if target:
        ratio_min = ticks_sorted[0] / target
        # 관측된 최저보다 아래에 여유를 두고 자른다. 정상 흔들림은 통과시키고 실제 저하만 잡기 위해서다.
        suggest = round(max(0.3, ratio_min - 0.08), 2)
        out("-" * 66)
        out("  임계값 판단 근거")
        out("-" * 66)
        out(f"    정상 플레이의 최저 비율   {ratio_min:.3f}")
        out(f"    권장 WATCHDOG_TICK_RATIO_ALERT ≈ {suggest}")
        out("")
        out("    참고: 틱은 5초 평균이라 짧은 멈칫이 희석된다. 실제 멈춤 시간과 비율의 관계는")
        out("          0.5초 → 0.90,  0.75초 → 0.85,  1.25초 → 0.75,  2초 → 0.60 이다.")
        out("          기준을 낮게 잡을수록 오탐은 줄지만 짧은 멈칫을 놓친다.")
        out("")
        out("    반영 위치: qa/config.py 의 WATCHDOG_TICK_RATIO_ALERT")
        out("    이 도구는 값을 제안할 뿐 결정하지 않는다. 위 분포를 보고 판단한다.")

    summary = {
        "started_at": started_iso,
        "duration_seconds": round(time.time() - started, 1),
        "target_tick": target,
        "samples": len(ticks),
        "idle_polls": idle_polls,
        "tick_min": ticks_sorted[0],
        "tick_p1": lo,
        "tick_p5": p5,
        "tick_median": med,
        "tick_max": ticks_sorted[-1],
        "tick_stdev": statistics.pstdev(ticks),
        "cpu_median": statistics.median(cpus) if cpus else None,
        "cpu_max": max(cpus) if cpus else None,
        "memory_start_mb": mems[0] if mems else None,
        "memory_end_mb": mems[-1] if mems else None,
        "suggested_ratio_alert": suggest,
    }
    _save(session_dir, started_iso, records, summary, target)
    out("")
    return 0


def _save(session_dir: Path, started_iso: str, records, summary, target) -> None:
    """관측 기록과 요약을 세션 폴더에 남긴다.

    틱은 그 순간의 서버만 아는 값이라 나중에 다시 만들 수 없다. 사람이 리다이렉트를 걸었는지에
    의존하지 않고 도구가 스스로 남긴다.
    """
    try:
        session_dir.mkdir(parents=True, exist_ok=True)
        stamp = started_iso.replace(":", "").replace("-", "").replace("T", "_")
        rec_path = session_dir / f"perf_{stamp}.jsonl"
        with open(rec_path, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        out(f"  관측 기록: {rec_path}")
        if summary is not None:
            sum_path = session_dir / f"perf_{stamp}.summary.json"
            sum_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            out(f"  요약:      {sum_path}")
    except Exception as e:
        out(f"  기록을 남기지 못했다: {e}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
