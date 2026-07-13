"""
run.py
------
전체 파이프라인을 한 번에 실행:
  1) 정상 플레이 수집        (Episode 리스트)
  2) 버그 3종 주입해서 수집  (Episode 리스트)
  3) Episode -> 특징 벡터
  4) 정상만으로 이상탐지기 학습
  5) 정상+버그 테스트셋으로 성능 평가 (precision/recall/ROC-AUC)
  6) 버그 종류별로 얼마나 잡았는지 리포트

실행:  python run.py
"""
import numpy as np
import os
from adapters.minigrid.collect import collect
from qa_core.features import build_matrix
from qa_core.detect import train, evaluate
from qa_core.report import build_report, save_report

# 리포트는 실행 위치와 무관하게 항상 프로젝트 루트 밑 reports/ 에 저장.
REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")

N_NORMAL = 400          # 정상 플레이 판 수
N_PER_BUG = 60          # 버그 종류별 판 수
N_TRAIN = 300           # 정상 중 학습에 쓸 판 수 (나머지는 테스트)


def main():
    print(">> 1) 정상 플레이 수집 중...")
    normal = collect(N_NORMAL, defect=None, seed0=0)

    print(">> 2) 미묘한 결함 플레이 수집 (under_explore / low_entropy)...")
    #   하드 오라클용 결함(softlock/teleport/reward)은 규칙으로 잡는 게 맞아 제외.
    #   여기선 규칙으론 경계를 못 긋는 '미묘한' 결함으로 이상탐지의 의의를 검증.
    bug_sets = {
        "under_explore": collect(N_PER_BUG, defect="under_explore", seed0=10_000),
        "low_entropy":   collect(N_PER_BUG, defect="low_entropy",   seed0=20_000),
    }

    print(">> 3) 특징 추출...")
    # 정상으로 열(특징) 집합을 확정. 이후 모든 행렬을 이 names 순서로 맞춘다.
    Xn, names = build_matrix(normal)
    Xn_train, Xn_test = Xn[:N_TRAIN], Xn[N_TRAIN:]

    print(">> 4) 정상 데이터만으로 이상탐지기 학습...")
    clf, scaler = train(Xn_train)

    # 테스트셋 = 남은 정상 + 모든 버그. 라벨은 각 Episode의 injected_defect에서 도출.
    # 학습셋과 동일한 열 순서(names)로 강제 정렬.
    bug_eps = [ep for eps in bug_sets.values() for ep in eps]
    Xb_all, _ = build_matrix(bug_eps, names=names)

    # 리포트용: X_test 행과 순서가 정확히 일치하는 에피소드 리스트.
    normal_test_eps = normal[N_TRAIN:]
    test_episodes = normal_test_eps + bug_eps

    X_test = np.vstack([Xn_test, Xb_all])
    y_test = np.array(
        [0] * len(Xn_test) +
        [0 if ep.injected_defect is None else 1
         for eps in bug_sets.values() for ep in eps]
    )

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
    for i, f in enumerate(names):
        print(f"  {f:>18}: 정상 {mn[i]:8.2f}  |  버그 {mb[i]:8.2f}")

    # --- 6) QA 리포트 저장 (운영형: 라벨 없음. 모델 출력 + 특징만) ---
    #     파이프라인은 평가 위해 버그를 심었지만, 리포트에는 종류/정답을 담지 않는다.
    #     실서비스에서 나올 형태 그대로.
    report = build_report(
        game_id="minigrid-doorkey-8x8",
        model={"type": "IsolationForest", "n_estimators": 300, "contamination": "auto"},
        episodes=test_episodes, X=X_test, feature_names=names,
        flagged=pred, anomaly_score=scores,
    )
    path = save_report(report, os.path.join(REPORTS_DIR, "report_minigrid.json"))
    print(f"\n>> 6) 리포트 저장: {path}  "
          f"(에피소드 {report['summary']['n_episodes']}개 중 "
          f"{report['summary']['n_flagged']}개 flagged, 이상 점수 내림차순)")


if __name__ == "__main__":
    main()
