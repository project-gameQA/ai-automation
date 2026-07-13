"""
run_vizdoom.py
--------------
실제 게임(ViZDoom / Doom)에 대한 전체 파이프라인.

핵심: 어댑터만 adapters.vizdoom로 바뀌고,
      qa_core.features / qa_core.detect 는 MiniGrid와 100% 동일하게 재사용된다.
      (run.py와 비교하면 import 두 줄만 다르다)

실행:  python run_vizdoom.py
"""
import numpy as np
from adapters.vizdoom.collect import collect        # ← 어댑터만 교체
from qa_core.features import featurize, FEATURE_NAMES  # ← 코어 그대로
from qa_core.detect import train, evaluate            # ← 코어 그대로

N_NORMAL = 150
N_PER_BUG = 40
N_TRAIN = 110


def main():
    print(">> 1) 정상 플레이 수집 (실제 Doom, 헤드리스)...")
    normal = collect(N_NORMAL, defect=None, seed0=0)

    print(">> 2) 버그 플레이 수집 (softlock / reward_bug)...")
    bug_sets = {
        "softlock":   collect(N_PER_BUG, defect="softlock",   seed0=10_000),
        "reward_bug": collect(N_PER_BUG, defect="reward_bug", seed0=20_000),
    }

    print(">> 3) 특징 추출 (qa_core.features 그대로)...")
    Xn = np.array([featurize(ep) for ep in normal])
    Xn_train, Xn_test = Xn[:N_TRAIN], Xn[N_TRAIN:]

    print(">> 4) 정상만으로 이상탐지기 학습 (qa_core.detect 그대로)...")
    clf, scaler = train(Xn_train)

    Xb_all = np.array([featurize(ep) for eps in bug_sets.values() for ep in eps])
    X_test = np.vstack([Xn_test, Xb_all])
    y_test = np.array(
        [0] * len(Xn_test) +
        [0 if ep.injected_defect is None else 1
         for eps in bug_sets.values() for ep in eps]
    )

    print(">> 5) 평가...\n")
    metrics, pred, scores = evaluate(clf, scaler, X_test, y_test)
    print("=== 전체 탐지 성능 (실제 Doom) ===")
    for k, v in metrics.items():
        print(f"  {k:>10}: {v:.3f}")

    print("\n=== 버그 종류별 탐지율 (recall) ===")
    offset = len(Xn_test)
    for i, name in enumerate(bug_sets):
        seg = pred[offset + i * N_PER_BUG: offset + (i + 1) * N_PER_BUG]
        print(f"  {name:>10}: {seg.mean():.3f}  ({int(seg.sum())}/{N_PER_BUG} 잡음)")

    fp = pred[:offset].mean()
    print(f"\n  정상 오탐율(false positive): {fp:.3f}")

    print("\n=== 특징별 평균 (정상 vs 버그) ===")
    mn, mb = Xn_test.mean(0), Xb_all.mean(0)
    for i, f in enumerate(FEATURE_NAMES):
        print(f"  {f:>18}: 정상 {mn[i]:9.2f}  |  버그 {mb[i]:9.2f}")


if __name__ == "__main__":
    main()
