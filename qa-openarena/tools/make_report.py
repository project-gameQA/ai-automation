"""세션 파일에서 리포트를 만든다.

서버는 세션을 마감할 때 리포트를 자동으로 만든다. 이 도구는 그것을 **다시** 만드는 용도다.
쓰이는 경우가 셋이다.

- 리포트 기능이 생기기 전에 쌓인 세션에서 뒤늦게 만들 때
- 모델을 다시 학습한 뒤 지난 세션들을 새 기준으로 다시 채점할 때
- 여러 세션을 한 표로 비교할 때(`--compare`)

서버가 만드는 것과 렌더링 코드가 같다(`qa/report.py`). 두 경로가 다른 코드를 타면 같은
세션에서 다른 리포트가 나올 수 있다.

이상 점수는 세션 파일에 남지 않는다. 텔레메트리 사본과 모델만 있으면 다시 낼 수 있는
파생물이기 때문이다. 그래서 이 도구는 사본에서 창을 다시 잘라 채점한다. 사본이 없거나 모델이
없으면 그 구역만 비고 나머지는 나온다.

실행 예:
    python tools/make_report.py sessions/session_20260727_183012.summary.json
    python tools/make_report.py sessions/*.summary.json
    python tools/make_report.py sessions/*.summary.json --compare
    python tools/make_report.py sessions/*.summary.json --out reports
"""

import glob
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qa import config, report

USAGE = __doc__


def load_jsonl(path: Path) -> list:
    """JSONL 파일을 딕셔너리 목록으로 읽는다. 깨진 줄은 건너뛴다.

    한 줄이 깨졌다고 리포트 전체를 포기하면 안 된다. 세션 파일은 프로세스가 갑자기 죽는
    상황까지 견디도록 한 줄씩 덧붙이는 형식으로 만들어져 있고, 그런 경우 마지막 줄이
    잘려 있을 수 있다.
    """
    rows = []
    if not path.exists():
        return rows
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def score_session(telemetry: Path):
    """텔레메트리 사본에서 창을 잘라 다시 채점한다. 못 하면 (빈 목록, None) 을 돌려준다."""
    if not telemetry.exists():
        return [], None
    try:
        from qa.anomaly import AnomalyModel, extract_activity
        from qa.replay_source import iter_samples_from_jsonl
    except ImportError:
        return [], None                       # scikit-learn 이 없는 환경이다.

    model_path = Path(config.ANOMALY_MODEL_PATH)
    if not model_path.is_absolute():
        model_path = Path(__file__).resolve().parents[1] / model_path
    if not model_path.exists():
        return [], None                       # 아직 학습하지 않았다.

    try:
        model = AnomalyModel.load(str(model_path))
        windows = extract_activity(
            iter_samples_from_jsonl(str(telemetry)),
            window_seconds=config.ACTIVITY_WINDOW_SECONDS,
            step_seconds=config.ACTIVITY_STEP_SECONDS,
            min_samples=config.ACTIVITY_MIN_SAMPLES,
        )
        return [s.to_dict() for s in model.score(windows)], model.threshold
    except Exception as exc:
        print(f"    채점하지 못했다: {exc}")
        return [], None


def build_one(summary_path: Path, out_dir: Path) -> Path | None:
    """세션 하나의 리포트를 만들어 파일로 쓴다."""
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"  {summary_path.name}: 요약을 읽지 못했다 ({exc})")
        return None

    sid = summary.get("session_id") or summary_path.stem.replace(".summary", "")
    folder = summary_path.parent
    events = load_jsonl(folder / f"session_{sid}.jsonl")
    trail = load_jsonl(folder / f"session_{sid}.watchdog.jsonl")

    scores, threshold = score_session(folder / f"session_{sid}.telemetry.jsonl")
    if scores:
        # 다시 채점했으므로 요약의 옛 숫자 대신 이번 결과를 쓴다. 모델이 바뀌었으면 값이
        # 달라지는 것이 맞고, 그렇지 않으면 같은 값이 나온다.
        summary["anomaly_windows_scored"] = len(scores)
        summary["anomaly_count"] = sum(1 for s in scores if s.get("is_anomaly"))
        summary["anomaly_threshold"] = threshold

    watchdog = {
        "trail": trail,
        # 세션 파일에는 경보가 따로 없다. 관측 기록에서 경보 구간을 되살릴 수는 없으므로
        # 요약의 건수만 쓰고, 구간 표시는 서버가 만든 리포트 쪽에만 나온다.
        "alerts": [],
        "target_tick": config.TARGET_TICK,
        "tick_ratio_alert": config.WATCHDOG_TICK_RATIO_ALERT,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"report_{sid}.html"
    path.write_text(report.render(summary, events, scores, watchdog), encoding="utf-8")

    rate = ""
    if summary.get("anomaly_windows_scored"):
        rate = (f", 이상 {summary['anomaly_count'] / summary['anomaly_windows_scored'] * 100:.1f}%"
                f" (창 {summary['anomaly_windows_scored']}개)")
    print(f"  {path.name}  ← 사건 {len(events)}건{rate}, 워치독 관측 {len(trail)}개")
    return path


def expand(args) -> list:
    """인자에 들어 있는 * 와 ? 를 실제 파일 목록으로 바꾼다.

    Windows 의 cmd 는 이 일을 대신 해 주지 않는다. 리눅스나 PowerShell 에서는 셸이 미리 풀어
    주지만 cmd 는 `sessions/*.summary.json` 을 글자 그대로 넘긴다. 프로그램이 받는 것이
    환경마다 다르므로, 받는 쪽에서 한 번 더 풀어 두면 어느 환경에서든 같게 동작한다.
    """
    out = []
    for a in args:
        if "*" in a or "?" in a:
            hit = sorted(glob.glob(a))
            if not hit:
                print(f"  {a}: 맞는 파일이 없다")
            out.extend(Path(h) for h in hit)
        else:
            out.append(Path(a))
    return out


def main(argv):
    paths = expand([a for a in argv if not a.startswith("--")])
    if "-h" in argv or "--help" in argv or not paths:
        print(USAGE)
        return 0 if paths else 1

    out_dir = Path("reports")
    if "--out" in argv:
        out_dir = Path(argv[argv.index("--out") + 1])
        paths = [p for p in paths if str(p) != str(out_dir)]

    compare = "--compare" in argv

    print()
    print(f"리포트 생성 ({len(paths)}개 세션 → {out_dir})")
    summaries = []
    for p in sorted(paths):
        if build_one(p, out_dir):
            try:
                summaries.append(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass

    if compare and summaries:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"compare_{time.strftime('%Y%m%d_%H%M%S')}.html"
        path.write_text(report.render_compare(summaries), encoding="utf-8")
        print(f"\n  {path.name}  ← 세션 {len(summaries)}개 비교")

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
