"""학습된 이상탐지 모델로 세션을 채점한다.

용도가 둘이다.
- **오탐률 측정**: 학습에 쓰지 않은 정상 세션을 채점한다. 이 비율이 이 모델의 실질적인
  성능 지표다. 학습에 쓴 세션으로 재면 외운 것을 다시 보는 셈이라 의미가 없다.
- **탐지율 측정**: 교란 세션(봇 실력 하향 등)을 채점한다. 높을수록 좋다.

두 지표는 짝을 이룬다. 오탐률만 보면 아무것도 잡지 않는 모델이 최고가 되고, 탐지율만 보면
전부 이상이라고 답하는 모델이 최고가 된다.

실행 예:
    python tools/score_anomaly.py sessions/normal_B.telemetry.jsonl
    python tools/score_anomaly.py sessions/lowskill.telemetry.jsonl --top 10
    python tools/score_anomaly.py sessions/X.jsonl --model models/anomaly.joblib
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qa import config
from qa.anomaly import ACTIVITY_FEATURES, ACTIVITY_LABEL, AnomalyModel, extract_activity
from qa.replay_source import iter_samples_from_jsonl, SkipCounter


def main(argv):
    paths = [a for a in argv if not a.startswith("--") and not a.replace(".", "").isdigit()]
    model_path = config.ANOMALY_MODEL_PATH
    top_n = 8
    if "--model" in argv:
        model_path = argv[argv.index("--model") + 1]
        paths = [p for p in paths if p != model_path]
    if "--top" in argv:
        top_n = int(argv[argv.index("--top") + 1])
        paths = [p for p in paths if p != argv[argv.index("--top") + 1]]

    if not paths:
        print("사용법: python tools/score_anomaly.py <세션> [--model 경로] [--top N]")
        return 1

    if not Path(model_path).exists():
        print(f"모델이 없다: {model_path}")
        print("먼저 학습한다: python tools/train_anomaly.py <정상 세션>")
        return 1

    model = AnomalyModel.load(model_path)

    print()
    print("=" * 74)
    print(f"  이상탐지 채점  (모델: {model_path})")
    print("=" * 74)
    print(f"  학습: 창 {model.metadata.get('n_windows', '?')}개, "
          f"임계값 {model.threshold:.4f} (학습 데이터 {model.percentile:.0f}% 기준선)")
    print()

    for p in paths:
        skipped = SkipCounter()
        windows = extract_activity(
            iter_samples_from_jsonl(p, on_skip=skipped),
            window_seconds=config.ACTIVITY_WINDOW_SECONDS,
            step_seconds=config.ACTIVITY_STEP_SECONDS,
            min_samples=config.ACTIVITY_MIN_SAMPLES,
        )
        if not windows:
            print(f"  {Path(p).name}: 창을 만들지 못했다 "
                  f"(세션이 {config.ACTIVITY_WINDOW_SECONDS:.0f}초보다 짧을 수 있다)")
            print()
            continue

        scores = model.score(windows)
        flagged = [s for s in scores if s.is_anomaly]
        rate = len(flagged) / len(scores) * 100

        print(f"  {Path(p).name}")
        print(f"    창 {len(scores):,}개 중 {len(flagged)}개 이상 판정  ({rate:.1f}%)")
        print(f"    기준선은 {model.percentile:.0f}% 다. 정상 세션이면 이 근처, 교란 세션이면 높아야 한다.")

        if flagged:
            # 어떤 특징 때문에 걸렸는지 집계한다. 점수만으로는 무엇을 검사할지 알 수 없다.
            from collections import Counter
            by_feature = Counter(s.top_contributor()[0] for s in flagged)
            summary = ", ".join(
                f"{ACTIVITY_LABEL.get(k, k)} {v}건" for k, v in by_feature.most_common()
            )
            print(f"    주 원인: {summary}")
            print()
            print(f"    가장 이상한 창 {min(top_n, len(flagged))}개")
            print(f"      {'봇':>3}{'구간(초)':>16}{'점수':>9}   원인")
            print("      " + "-" * 62)
            for s in sorted(flagged, key=lambda z: z.score)[:top_n]:
                name, z = s.top_contributor()
                val = s.values.get(name, 0.0)
                print(f"      {s.entity_id:>3}{s.start_time:>8.0f}~{s.end_time:<7.0f}{s.score:>9.4f}"
                      f"   {ACTIVITY_LABEL.get(name, name)} {val:.3f} ({z:+.1f}σ)")
        if skipped.count:
            print(f"    건너뛴 줄 {skipped.count}개")
        print()

    print("  이 결과는 결론이 아니라 단서다. 이상 판정된 구간을 사람이 확인해야 한다.")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
