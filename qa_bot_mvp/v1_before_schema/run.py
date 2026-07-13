"""
run.py
------
전체 파이프라인을 한 번에 실행:
  1) 정상 플레이 수집
  2) 버그 3종 주입해서 플레이 수집
  3) 궤적 -> 특징 벡터
  4) 정상만으로 이상탐지기 학습
  5) 정상+버그 테스트셋으로 성능 평가 (precision/recall/ROC-AUC)
  6) 버그 종류별로 얼마나 잡았는지 리포트

실행:  python run.py
"""
import numpy as np
from collect import collect
from features import featurize, FEATURE_NAMES
from detect import train, evaluate

N_NORMAL = 400          # 정상 플레이 판 수
N_PER_BUG = 60          # 버그 종류별 판 수
N_TRAIN = 300           # 정상 중 학습에 쓸 판 수 (나머지는 테스트)


def main():
    print(">> 1) 정상 플레이 수집 중...")
    normal = collect(N_NORMAL, defect=None, seed0=0)

    print(">> 2) 버그 플레이 수집 중 (softlock / teleport / reward_bug)...")
    bug_sets = {
        "softlock":   collect(N_PER_BUG, defect="softlock",   seed0=10_000),
        "teleport":   collect(N_PER_BUG, defect="teleport",   seed0=20_000),
        "reward_bug": collect(N_PER_BUG, defect="reward_bug", seed0=30_000),
    }

    print(">> 3) 특징 추출...")
    Xn = np.array([featurize(t) for t in normal])
    Xn_train, Xn_test = Xn[:N_TRAIN], Xn[N_TRAIN:]

    print(">> 4) 정상 데이터만으로 이상탐지기 학습...")
    clf, scaler = train(Xn_train)

    # 테스트셋 = 남은 정상 + 모든 버그
    Xb_all, y_bug_type = [], []
    for name, trajs in bug_sets.items():
        for t in trajs:
            Xb_all.append(featurize(t))
            y_bug_type.append(name)
    Xb_all = np.array(Xb_all)

    X_test = np.vstack([Xn_test, Xb_all])
    y_test = np.array([0] * len(Xn_test) + [1] * len(Xb_all))

    print(">> 5) 평가...\n")
    metrics, pred, scores = evaluate(clf, scaler, X_test, y_test)
    print("=== 전체 탐지 성능 ===")
    for k, v in metrics.items():
        print(f"  {k:>10}: {v:.3f}")

    # 버그 종류별 recall (얼마나 잡았나)
    print("\n=== 버그 종류별 탐지율 (recall) ===")
    offset = len(Xn_test)
    for i, name in enumerate(bug_sets):
        seg = pred[offset + i * N_PER_BUG: offset + (i + 1) * N_PER_BUG]
        print(f"  {name:>10}: {seg.mean():.3f}  ({int(seg.sum())}/{N_PER_BUG} 잡음)")

    # 정상을 버그로 잘못 판정한 비율 (false positive)
    fp = pred[:offset].mean()
    print(f"\n  정상 오탐율(false positive): {fp:.3f}")

    # 어떤 특징이 정상 vs 버그를 가르는지 (해석)
    print("\n=== 특징별 평균 (정상 vs 버그) ===")
    mn, mb = Xn_test.mean(0), Xb_all.mean(0)
    for i, f in enumerate(FEATURE_NAMES):
        print(f"  {f:>18}: 정상 {mn[i]:8.2f}  |  버그 {mb[i]:8.2f}")


if __name__ == "__main__":
    main()
