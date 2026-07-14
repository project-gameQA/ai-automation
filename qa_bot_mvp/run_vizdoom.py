"""
run_vizdoom.py  (ViZDoom / 실제 Doom, 규칙 + 이상탐지 2층 파이프라인)
--------------------------------------------------------------------
run.py(MiniGrid)와 동일한 2층 구조. 어댑터만 adapters.vizdoom로 바뀌고
qa_core(features/detect/oracle/report)는 100% 동일하게 재사용된다.

Doom 특이점: 좌표를 '설정'할 수 없어(엔진 ACS 필요) teleport 결함은 주입 불가.
             그래서 명확 결함은 stuck만 주입한다(정지는 행동으로 가능).
             단 oracle의 teleport 규칙 자체는 그대로 동작한다(위반이 있으면 잡음).

실행:  python run_vizdoom.py
"""
import numpy as np
import os
from adapters.vizdoom.collect import collect        # 정상 봇(배포 어댑터)
from demo.vizdoom_defects import collect_defective  # 결함 봇(시연 전용)
from qa_core.features import build_matrix              # ← 코어 그대로
from qa_core.detect import train, evaluate            # ← 코어 그대로
from qa_core.oracle import check_invariants           # ← 코어 그대로
from qa_core.sequence import StepAnomalyModel         # ← 코어 그대로 (시계열도 게임 무관)
from qa_core.report import build_report, save_report  # ← 코어 그대로

REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")

N_NORMAL = 150
N_PER_BUG = 40
N_TRAIN = 110

SUBTLE   = ["under_explore", "low_entropy"]
CLEAR    = ["stuck"]              # Doom은 teleport 주입 불가
TEMPORAL = ["oscillate_mid"]
SEEDS = {"under_explore": 10_000, "low_entropy": 20_000,
         "stuck": 30_000, "oscillate_mid": 50_000}
ALL_DEFECTS = SUBTLE + CLEAR + TEMPORAL


def main():
    print(">> 1) 정상 플레이 수집 (실제 Doom, 헤드리스)...")
    normal = collect(N_NORMAL, seed0=0)

    print(">> 2) 결함 수집 (미묘/명확/시간국소)...")
    defect_sets = {name: collect_defective(N_PER_BUG, defect=name, seed0=SEEDS[name])
                   for name in ALL_DEFECTS}

    print(">> 3) 특징 추출 (qa_core.features 그대로)...")
    Xn, names = build_matrix(normal)
    Xn_train, Xn_test = Xn[:N_TRAIN], Xn[N_TRAIN:]

    print(">> 4) 정상만으로 학습 (집계 이상탐지 + 시계열 스텝 모델)...")
    clf, scaler = train(Xn_train)
    seqm = StepAnomalyModel(window=5).fit(normal[:N_TRAIN])

    bug_eps = [ep for name in ALL_DEFECTS for ep in defect_sets[name]]
    Xb_all, _ = build_matrix(bug_eps, names=names)
    normal_test_eps = normal[N_TRAIN:]
    test_episodes = normal_test_eps + bug_eps
    X_test = np.vstack([Xn_test, Xb_all])
    y_test = np.array([0] * len(Xn_test) + [1] * len(bug_eps))

    print(">> 5) 평가 (규칙 + 집계 이상탐지 + 시계열)...\n")
    metrics, pred, scores = evaluate(clf, scaler, X_test, y_test)
    violations = [check_invariants(ep) for ep in test_episodes]
    rule_caught = np.array([len(v) > 0 for v in violations])
    anom_caught = pred.astype(bool)
    anomaly_steps = [seqm.anomaly_steps(ep) for ep in test_episodes]

    n_norm = len(normal_test_eps)
    seg = {}
    off = n_norm
    for name in ALL_DEFECTS:
        seg[name] = (off, off + N_PER_BUG)
        off += N_PER_BUG

    print("=== 규칙 층 (하드 오라클) ===")
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
    tot = sum(len(anomaly_steps[i]) for i in range(a, b))
    ins = sum(1 for i in range(a, b) for s in anomaly_steps[i] if 55 <= s <= 115)
    print(f"  oscillate_mid: 짚은 이상스텝 {tot}개 중 주입구간(60~110) 내 {ins}개 "
          f"({ins/tot if tot else 0:.0%})")
    norm_steps = sum(len(anomaly_steps[i]) for i in range(n_norm))
    print(f"  정상 판 이상스텝: 총 {norm_steps}개 (판당 {norm_steps/n_norm:.2f}개)")

    print("\n=== 종합 (규칙 OR 집계 이상탐지) ===")
    caught = rule_caught | anom_caught
    print(f"  전체 결함 탐지율: {caught[n_norm:].mean():.3f}  |  정상 오탐율: {caught[:n_norm].mean():.3f}")

    report = build_report(
        game_id="vizdoom-my-way-home",
        model={"type": "IsolationForest + StepAnomalyModel",
               "n_estimators": 300, "seq_window": 5},
        episodes=test_episodes, X=X_test, feature_names=names,
        flagged=pred, anomaly_score=scores,
        hard_violations=violations, anomaly_steps=anomaly_steps,
    )
    path = save_report(report, os.path.join(REPORTS_DIR, "report_vizdoom.json"))
    s = report["summary"]
    print(f"\n>> 6) 리포트 저장: {path}")
    print(f"     규칙 위반 {s['n_hard_violations']}판 + 이상탐지 flagged {s['n_flagged']}판")


if __name__ == "__main__":
    main()
