"""정상 플레이 세션으로 이상탐지 모델을 학습해 파일로 저장한다.

학습 데이터는 **주입기를 끄고 정상 상태로 뽑은 텔레메트리**여야 한다. 이 오라클은 정상만
학습하고 거기서 벗어난 것을 잡으므로, 학습 데이터에 비정상이 섞이면 그것을 정상으로 배운다.

세션 파일을 여러 개 넘기면 모두 합쳐 학습한다.

실행 예:
    python tools/train_anomaly.py sessions/session_A.telemetry.jsonl
    python tools/train_anomaly.py sessions/A.jsonl sessions/B.jsonl --out models/anomaly.joblib
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qa import config
from qa.anomaly import ACTIVITY_FEATURES, ACTIVITY_LABEL, extract_activity
from qa.replay_source import iter_samples_from_jsonl, SkipCounter


def load_windows(paths):
    """여러 세션 파일에서 활동 창을 모아 반환한다."""
    all_windows = []
    for p in paths:
        skipped = SkipCounter()
        windows = extract_activity(
            iter_samples_from_jsonl(p, on_skip=skipped),
            window_seconds=config.ACTIVITY_WINDOW_SECONDS,
            step_seconds=config.ACTIVITY_STEP_SECONDS,
            min_samples=config.ACTIVITY_MIN_SAMPLES,
        )
        bots = sorted({w.entity_id for w in windows})
        note = f"  (건너뛴 줄 {skipped.count}개)" if skipped.count else ""
        print(f"  {Path(p).name}")
        print(f"     창 {len(windows):,}개, 봇 {len(bots)}마리{note}")
        if not windows:
            # 창이 하나도 안 나오면 대개 세션이 창 길이보다 짧은 경우다.
            print(f"     창을 만들지 못했다. 세션이 {config.ACTIVITY_WINDOW_SECONDS:.0f}초보다 짧을 수 있다.")
        all_windows.extend(windows)
    return all_windows


def main(argv):
    paths = [a for a in argv if not a.startswith("--")]
    out = config.ANOMALY_MODEL_PATH
    if "--out" in argv:
        out = argv[argv.index("--out") + 1]

    if not paths:
        print("사용법: python tools/train_anomaly.py <정상 세션...> [--out 모델경로]")
        print("주의: 주입기를 끄고 뽑은 정상 플레이 텔레메트리만 넣는다.")
        return 1

    print()
    print("=" * 70)
    print("  이상탐지 모델 학습")
    print("=" * 70)
    print(f"  창 {config.ACTIVITY_WINDOW_SECONDS:.0f}초, {config.ACTIVITY_STEP_SECONDS:.0f}초 간격")
    print()

    windows = load_windows(paths)
    print()
    if not windows:
        print("  학습할 창이 없다.")
        return 1

    # 학습 전에 특징 분포를 보여 준다. 값이 전부 같은 특징이 있으면 모델에 기여하지 못하므로
    # 학습을 돌리기 전에 알아야 한다.
    import statistics
    print(f"  특징 분포 (창 {len(windows):,}개)")
    print(f"    {'특징':<20}{'최소':>10}{'중앙값':>10}{'최대':>10}{'표준편차':>10}")
    print("    " + "-" * 60)
    dead = []
    for name in ACTIVITY_FEATURES:
        v = [getattr(w, name) for w in windows]
        sd = statistics.pstdev(v) if len(v) > 1 else 0.0
        if sd < 1e-9:
            dead.append(name)
        print(f"    {ACTIVITY_LABEL.get(name, name):<20}{min(v):>10.3f}"
              f"{statistics.median(v):>10.3f}{max(v):>10.3f}{sd:>10.3f}")
    print()
    if dead:
        print(f"  경고: {', '.join(dead)} 의 값이 모든 창에서 같다. 학습에 기여하지 못한다.")
        print()

    model = config.build_anomaly_model()
    try:
        model.fit(windows)
    except ValueError as e:
        print(f"  학습 실패: {e}")
        return 1

    Path(out).parent.mkdir(parents=True, exist_ok=True)
    model.save(out)

    print(f"  학습 완료")
    print(f"    창 {model.metadata['n_windows']:,}개, 봇 {model.metadata['n_bots']}마리")
    print(f"    임계값 {model.threshold:.4f} (학습 데이터의 {model.percentile:.0f}%가 이상으로 잡히는 선)")
    print(f"    저장: {out}")
    print()
    print("  다음: 학습에 쓰지 않은 정상 세션으로 오탐률을 확인한다.")
    print(f"    python tools/score_anomaly.py <다른 정상 세션>")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
