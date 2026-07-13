"""
report.py  (코어)
----------------
파이프라인 산출물 -> QA 리포트 dict(-> report.json).

[운영 리포트 원칙]
이 리포트는 '실서비스에 들어갔을 때' 나오는 형태로 만든다.
즉 정답 라벨을 절대 담지 않는다. 모델이 실제로 낼 수 있는 것만 담는다:
  - anomaly_score : 얼마나 이상한가 (모델 출력)
  - flagged       : 이상으로 판정했는가 (모델 출력)
  - 그리고 관측 가능한 사실: seed, outcome, features
버그 '종류'는 담지 않는다 — 이상탐지 모델은 종류 개념 자체가 없다.
정답이 있어야만 나오는 것(무슨 버그였나 / 맞췄나 / 종류별 recall / f1)은
운영에선 알 수 없으므로 리포트에 존재하지 않는다.

리포트 철학 = '트리아지': 에피소드를 이상 점수 내림차순으로 준다.
사람(QA 담당)이 제일 수상한 판부터 열어보고, features를 근거로 판단한다.
리포트는 종류를 단정하지 않는다. 재료를 정렬해 줄 뿐.

게임 무관: Episode의 보편 필드(seed, outcome)와 모델 출력만 읽는다.
run.py / run_vizdoom.py가 game_id만 바꿔 같은 함수를 호출한다.
"""
from datetime import datetime, timezone
import json
import os
import numpy as np


def build_report(*, game_id, model, episodes, X, feature_names,
                 flagged, anomaly_score):
    """
    파이프라인 결과를 '운영형' 리포트 dict로 조립.

    episodes      : 이번 배치에서 봇이 플레이한 에피소드 리스트.
                    X / flagged / anomaly_score의 행과 순서가 일치해야 한다.
    X             : (n, d) 특징 행렬 (열 순서 = feature_names)
    flagged       : 1=모델이 이상으로 플래그, 0=정상 판정
    anomaly_score : 값이 클수록 더 이상함

    주의: 정답 라벨(injected_defect 등)은 받지도, 담지도 않는다.
    """
    flagged = np.asarray(flagged).astype(int)
    scores = np.asarray(anomaly_score, dtype=float)
    Xt = np.asarray(X, dtype=float)
    n = len(episodes)

    # --- 특징 평균: flagged vs unflagged (라벨 없이 계산 가능. 모델 출력으로만 나눔) ---
    fmask = (flagged == 1)
    def _means(mask):
        if mask.any():
            m = Xt[mask].mean(0)
            return {nm: round(float(m[i]), 6) for i, nm in enumerate(feature_names)}
        return {nm: None for nm in feature_names}
    feature_means = {"flagged": _means(fmask), "unflagged": _means(~fmask)}

    # --- 에피소드별 상세 (이상 점수 내림차순 = 트리아지 순서) ---
    rows = []
    for i, ep in enumerate(episodes):
        rows.append({
            "index": i,
            "seed": ep.seed,
            "anomaly_score": round(float(scores[i]), 6),
            "flagged": bool(flagged[i] == 1),   # 모델이 수상하다고 봤나 (유일한 판정)
            "outcome": ep.outcome,              # 관측 사실 (success/timeout 등)
            "features": {nm: round(float(Xt[i, j]), 6)
                         for j, nm in enumerate(feature_names)},
        })
    rows.sort(key=lambda e: e["anomaly_score"], reverse=True)

    n_flagged = int(fmask.sum())
    return {
        "run": {
            "game_id": game_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "n_episodes": n,
            "model": model,
        },
        "summary": {
            "n_episodes": n,
            "n_flagged": n_flagged,                       # 사람이 봐야 할 판 수
            "flag_rate": round(n_flagged / n, 6) if n else 0.0,
        },
        "feature_names": list(feature_names),
        "feature_means": feature_means,   # flagged vs unflagged (라벨 아님, 모델 출력 기준)
        "episodes": rows,                 # anomaly_score 내림차순
    }


def save_report(report, path):
    """리포트 dict를 report.json 으로 저장. (상위 폴더 없으면 자동 생성)"""
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return path
