"""텔레메트리에서 윈도우 특징을 뽑아 분포를 보여 주고, 필요하면 CSV로 저장하는 도구다.

모델을 만들기 전에 특징이 실제로 어떤 값을 갖는지 눈으로 확인하기 위한 것이다. 값이 전부
같거나(분산 0) 한쪽으로 극단적으로 몰려 있으면 그 특징은 학습에 기여하지 못하므로, 모델을
돌리기 전에 알아야 한다.

세션 두 개를 함께 넣으면 분포를 나란히 비교한다. 정상 세션과 교란 세션(g_spSkill 하향 등)을
비교해, 어떤 특징이 실제로 반응하는지 미리 볼 때 쓴다.

실행 예:
    python tools/extract_features.py sessions/session_A.telemetry.jsonl
    python tools/extract_features.py sessions/session_A.telemetry.jsonl --csv normal.csv
    python tools/extract_features.py sessions/normal.jsonl --compare sessions/lowgrav.jsonl
"""

import csv
import statistics
import sys
from pathlib import Path

# 프로젝트 루트를 모듈 검색 경로에 넣어야 qa 패키지를 가져올 수 있다.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qa.replay_source import iter_samples_from_jsonl, SkipCounter
from qa.features import FEATURE_NAMES, extract_all


def summarize(windows, name):
    """특징별 요약 통계를 계산한다."""
    stats = {}
    for feat in FEATURE_NAMES:
        values = [getattr(w, feat) for w in windows]
        stats[feat] = {
            "min": min(values),
            "median": statistics.median(values),
            "max": max(values),
            # 표준편차가 0이면 그 특징은 모든 창에서 같은 값이라는 뜻이고, 학습에 아무 기여도
            # 하지 못한다. 모델을 돌리기 전에 반드시 확인해야 하는 항목이다.
            "stdev": statistics.pstdev(values) if len(values) > 1 else 0.0,
        }
    return stats


def print_stats(stats, label):
    """요약 통계를 표로 출력한다."""
    print(f"  [{label}]")
    print(f"    {'특징':<22}{'최소':>12}{'중앙값':>12}{'최대':>12}{'표준편차':>12}")
    print("    " + "-" * 70)
    for feat in FEATURE_NAMES:
        s = stats[feat]
        flag = "   <- 분산 0" if s["stdev"] == 0 else ""
        print(f"    {feat:<22}{s['min']:>12.2f}{s['median']:>12.2f}"
              f"{s['max']:>12.2f}{s['stdev']:>12.2f}{flag}")
    print()


def print_compare(a_stats, b_stats, a_label, b_label):
    """두 세션의 중앙값을 나란히 놓고 변화율을 보여 준다."""
    print(f"  중앙값 비교: [{a_label}] vs [{b_label}]")
    print(f"    {'특징':<22}{a_label[:10]:>12}{b_label[:10]:>12}{'변화':>12}")
    print("    " + "-" * 58)
    for feat in FEATURE_NAMES:
        a = a_stats[feat]["median"]
        b = b_stats[feat]["median"]
        if a != 0:
            change = f"{(b - a) / abs(a) * 100:+.0f}%"
        else:
            change = "—" if b == 0 else "0에서 증가"
        print(f"    {feat:<22}{a:>12.2f}{b:>12.2f}{change:>12}")
    print()
    print("    변화가 큰 특징일수록 그 교란에 반응한다는 뜻이다. 다만 이 표는 참고일 뿐이며,")
    print("    특징을 특정 교란에 맞춰 다듬으면 시험지에 맞춰 공부하는 셈이 된다.")
    print()


def load(path):
    """파일 하나에서 윈도우 특징을 뽑는다. (창 목록, 건너뛴 줄 수)를 반환한다."""
    skipped = SkipCounter()
    windows = extract_all(iter_samples_from_jsonl(path, on_skip=skipped))
    return windows, skipped


def main(argv):
    if not argv:
        print("사용법: python tools/extract_features.py <텔레메트리> [--csv 출력.csv] [--compare 다른.jsonl]")
        return 1

    path = argv[0]
    csv_path = None
    compare_path = None
    if "--csv" in argv:
        csv_path = argv[argv.index("--csv") + 1]
    if "--compare" in argv:
        compare_path = argv[argv.index("--compare") + 1]

    windows, skipped = load(path)
    if not windows:
        print("창을 하나도 만들지 못했다. 텔레메트리가 너무 짧거나 형식이 다르다.")
        return 1

    bots = sorted({w.entity_id for w in windows})
    span = max(w.end_time for w in windows) - min(w.start_time for w in windows)

    print()
    print("=" * 74)
    print(f"  윈도우 특징 추출: {path}")
    print("=" * 74)
    print(f"  창 수    {len(windows):,}   봇 {len(bots)}마리 {bots}   구간 {span:.1f}초")
    if skipped.count:
        # 첫 줄이나 마지막 줄이 완결되지 않은 것은 정상이다. 수가 많으면 파일이 이상한 것이다.
        print(f"  건너뛴 줄 {skipped.count}개 (줄 번호 {skipped.first_line}~{skipped.last_line})"
              f"{'  <- 몇 줄 수준이면 정상' if skipped.count <= 5 else '  <- 수가 많다. 파일을 확인한다'}")
    print()

    stats = summarize(windows, path)
    print_stats(stats, Path(path).stem[:24])

    # 학습에 충분한 양인지 알려 준다. 특징 11개에 창 수백 개면 정상 범위를 제대로 배우기 어렵다.
    if len(windows) < 500:
        print(f"  주의: 창이 {len(windows)}개뿐이다. 특징 {len(FEATURE_NAMES)}개를 학습하기에는 얇다.")
        print("        봇 수를 늘리는 편이 시간을 늘리는 것보다 효율이 좋다(같은 시간에 표본이 배로 늘고")
        print("        봇마다 행동이 달라 다양성도 커진다). 봇 8마리로 10분이면 약 4,700개가 나온다.")
        print()

    if compare_path:
        other, _ = load(compare_path)
        if other:
            other_stats = summarize(other, compare_path)
            print_stats(other_stats, Path(compare_path).stem[:24])
            print_compare(stats, other_stats, Path(path).stem[:10], Path(compare_path).stem[:10])
        else:
            print(f"  비교 대상에서 창을 만들지 못했다: {compare_path}")
            print()

    if csv_path:
        rows = [w.to_dict() for w in windows]
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        print(f"  CSV로 저장했다: {csv_path}  ({len(rows)}행)")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
