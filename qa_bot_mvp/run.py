"""
run.py  (MiniGrid, 규칙 + 이상탐지 2층 파이프라인)
-------------------------------------------------
1) 정상 플레이 수집 -> 2) 결함 주입 수집 -> 3) 특징 -> 4) 정상만으로 이상탐지 학습
5) 각 판을 두 층으로 검사:
     - 규칙 층(oracle): 명확한 불변식 위반(teleport, stuck 등) 확정. 정상 오탐 0.
     - 이상탐지 층: 규칙이 못 잡는 미묘한 이상(under_explore, low_entropy).
6) 두 결과를 한 리포트로 합침. 규칙 위반 먼저 -> 이상 점수 순.

실행: python run.py
"""
import numpy as np
import os
from adapters.minigrid.collect import collect          # 정상 봇(배포 어댑터)
from demo.minigrid_defects import collect_defective    # 결함 봇(시연 전용)
from qa_core.features import build_matrix
from qa_core.detect import train, evaluate
from qa_core.oracle import check_invariants
from qa_core.sequence import StepAnomalyModel
from qa_core.report import build_report, save_report

REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")

N_NORMAL = 400
N_PER_BUG = 60
N_TRAIN = 300

# 미묘한 결함(집계 이상탐지 층) / 명확한 결함(규칙 층) / 시간 국소 결함(시계열 층)
SUBTLE   = ["under_explore", "low_entropy"]
CLEAR    = ["teleport", "stuck"]
TEMPORAL = ["oscillate_mid"]
SEEDS = {"under_explore": 10_000, "low_entropy": 20_000,
         "teleport": 30_000, "stuck": 40_000, "oscillate_mid": 50_000}
ALL_DEFECTS = SUBTLE + CLEAR + TEMPORAL


def main():
    print(">> 1) 정상 플레이 수집...")
    normal = collect(N_NORMAL, seed0=0)

    print(">> 2) 결함 수집 (미묘/명확/시간국소)...")
    defect_sets = {name: collect_defective(N_PER_BUG, defect=name, seed0=SEEDS[name])
                   for name in ALL_DEFECTS}

    print(">> 3) 특징 추출...")
    Xn, names = build_matrix(normal)
    Xn_train, Xn_test = Xn[:N_TRAIN], Xn[N_TRAIN:]

    print(">> 4) 정상 데이터만으로 학습 (집계 이상탐지 + 시계열 스텝 모델)...")
    clf, scaler = train(Xn_train)
    seqm = StepAnomalyModel(window=5).fit(normal[:N_TRAIN])

    # 테스트셋 = 남은 정상 + 모든 결함
    bug_eps = [ep for name in ALL_DEFECTS for ep in defect_sets[name]]
    Xb_all, _ = build_matrix(bug_eps, names=names)
    normal_test_eps = normal[N_TRAIN:]
    test_episodes = normal_test_eps + bug_eps
    X_test = np.vstack([Xn_test, Xb_all])
    y_test = np.array([0] * len(Xn_test) + [1] * len(bug_eps))

    print(">> 5) 평가 (규칙 층 + 이상탐지 층)...\n")

    # --- 이상탐지 층 ---
    metrics, pred, scores = evaluate(clf, scaler, X_test, y_test)
    # --- 규칙 층: 모든 테스트 에피소드에 oracle 적용 ---
    violations = [check_invariants(ep) for ep in test_episodes]
    rule_caught = np.array([len(v) > 0 for v in violations])
    anom_caught = pred.astype(bool)

    n_norm = len(normal_test_eps)
    seg = {}
    off = n_norm
    for name in ALL_DEFECTS:
        seg[name] = (off, off + N_PER_BUG)
        off += N_PER_BUG

    # 시계열 층: 모든 테스트 에피소드의 이상 스텝(위치) 계산
    anomaly_steps = [seqm.anomaly_steps(ep) for ep in test_episodes]

    print("=== 규칙 층 (하드 오라클) — 명확한 위반을 확정 ===")
    for name in CLEAR + SUBTLE + TEMPORAL:
        a, b = seg[name]
        r = int(rule_caught[a:b].sum())
        tag = "(명확)" if name in CLEAR else "(규칙엔 안 걸려야 정상)"
        print(f"  {name:>13}: {r}/{N_PER_BUG} {tag}")
    print(f"  정상 오탐: {int(rule_caught[:n_norm].sum())}/{n_norm}")

    print("\n=== 이상탐지(집계) 층 — 규칙이 놓친 판 중 미묘한 이상 ===")
    for name in SUBTLE:
        a, b = seg[name]
        missed = ~rule_caught[a:b]
        if missed.sum() > 0:
            print(f"  {name:>13}: 규칙이 놓친 {int(missed.sum())}개 중 "
                  f"{int(anom_caught[a:b][missed].sum())}개 잡음 "
                  f"({anom_caught[a:b][missed].mean():.0%})")
    print(f"  정상 오탐(집계): {anom_caught[:n_norm].mean():.0%}")

    print("\n=== 시계열 층 — 이상의 '위치(스텝)'를 짚음 ===")
    a, b = seg["oscillate_mid"]
    # oscillate_mid 주입 구간은 스텝 60~110. 짚은 스텝이 그 안에 드는 비율.
    tot = sum(len(anomaly_steps[i]) for i in range(a, b))
    ins = sum(1 for i in range(a, b) for s in anomaly_steps[i] if 55 <= s <= 115)
    localized = ins / tot if tot else 0.0
    print(f"  oscillate_mid: 짚은 이상스텝 {tot}개 중 실제 주입구간(60~110) 내 {ins}개 ({localized:.0%})")
    norm_steps = sum(len(anomaly_steps[i]) for i in range(n_norm))
    print(f"  정상 판이 짚힌 이상스텝: 총 {norm_steps}개 (판당 {norm_steps/n_norm:.2f}개, 낮아야 정상)")
    print("  -> 집계 모델은 '이 판 이상'까지만, 시계열은 '몇 번째 스텝'까지 짚음")

    print("\n=== 종합 (규칙 OR 집계 이상탐지) ===")
    caught = rule_caught | anom_caught
    print(f"  전체 결함 탐지율: {caught[n_norm:].mean():.3f}  |  정상 오탐율: {caught[:n_norm].mean():.3f}")

    # --- 6) 리포트 (규칙 + 이상탐지 합침) ---
    report = build_report(
        game_id="minigrid-doorkey-8x8",
        model={"type": "IsolationForest + StepAnomalyModel",
               "n_estimators": 300, "seq_window": 5},
        episodes=test_episodes, X=X_test, feature_names=names,
        flagged=pred, anomaly_score=scores,
        hard_violations=violations, anomaly_steps=anomaly_steps,
    )
    path = save_report(report, os.path.join(REPORTS_DIR, "report_minigrid.json"))
    s = report["summary"]
    print(f"\n>> 6) 리포트 저장: {path}")
    print(f"     규칙 위반 {s['n_hard_violations']}판 + 이상탐지 flagged {s['n_flagged']}판 "
          f"(규칙 위반 먼저 -> 이상 점수 순)")


if __name__ == "__main__":
    main()
