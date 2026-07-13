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
import os
from adapters.vizdoom.collect import collect        # ← 어댑터만 교체
from qa_core.features import build_matrix              # ← 코어 그대로
from qa_core.detect import train, evaluate            # ← 코어 그대로
from qa_core.report import build_report, save_report  # ← 코어 그대로 (리포트도 게임 무관)

# 리포트는 실행 위치와 무관하게 항상 프로젝트 루트 밑 reports/ 에 저장.
REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")

N_NORMAL = 150
N_PER_BUG = 40
N_TRAIN = 110


def main():
    print(">> 1) 정상 플레이 수집 (실제 Doom, 헤드리스)...")
    normal = collect(N_NORMAL, defect=None, seed0=0)

    print(">> 2) 미묘한 결함 플레이 수집 (under_explore / low_entropy)...")
    bug_sets = {
        "under_explore": collect(N_PER_BUG, defect="under_explore", seed0=10_000),
        "low_entropy":   collect(N_PER_BUG, defect="low_entropy",   seed0=20_000),
    }

    print(">> 3) 특징 추출 (qa_core.features 그대로)...")
    Xn, names = build_matrix(normal)
    Xn_train, Xn_test = Xn[:N_TRAIN], Xn[N_TRAIN:]

    print(">> 4) 정상만으로 이상탐지기 학습 (qa_core.detect 그대로)...")
    clf, scaler = train(Xn_train)

    bug_eps = [ep for eps in bug_sets.values() for ep in eps]
    Xb_all, _ = build_matrix(bug_eps, names=names)

    # 리포트용: X_test 행과 순서가 일치하는 에피소드 리스트.
    test_episodes = normal[N_TRAIN:] + bug_eps

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
    for i, f in enumerate(names):
        print(f"  {f:>18}: 정상 {mn[i]:9.2f}  |  버그 {mb[i]:9.2f}")

    # --- 6) QA 리포트 저장 (운영형: 라벨 없음. MiniGrid와 같은 코어 함수) ---
    report = build_report(
        game_id="vizdoom-my-way-home",
        model={"type": "IsolationForest", "n_estimators": 300, "contamination": "auto"},
        episodes=test_episodes, X=X_test, feature_names=names,
        flagged=pred, anomaly_score=scores,
    )
    path = save_report(report, os.path.join(REPORTS_DIR, "report_vizdoom.json"))
    print(f"\n>> 6) 리포트 저장: {path}  "
          f"(에피소드 {report['summary']['n_episodes']}개 중 "
          f"{report['summary']['n_flagged']}개 flagged, 이상 점수 내림차순)")


if __name__ == "__main__":
    main()
