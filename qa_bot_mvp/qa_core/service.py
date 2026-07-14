"""
service.py  (코어 — 서비스 레벨 기능: 학습/저장/로드/실행)
----------------------------------------------------------
UI(또는 스크립트)가 호출하는 상위 함수들. 게임 무관(에피소드 리스트만 받음).

버튼과의 대응:
  - train_baseline : [기준 모델 학습]  정상 에피소드로 학습 + 모델 저장
  - run_qa         : [QA 실행]         저장된 모델 로드 -> 규칙+이상탐지+시계열 -> 리포트

모델 번들에 담기는 것: 집계 모델(IsolationForest+scaler), 시계열 모델, 특징 열 순서,
game_id, 학습 메타. joblib 한 파일로 저장/로드.
"""
import os
from datetime import datetime, timezone
import numpy as np
import joblib

from qa_core.features import build_matrix
from qa_core.detect import train, predict
from qa_core.oracle import check_invariants
from qa_core.sequence import StepAnomalyModel
from qa_core.report import build_report


# ---------- 모델 저장/로드 ----------
def save_model(path, bundle):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    joblib.dump(bundle, path)
    return path

def load_model(path):
    return joblib.load(path)


# ---------- [기준 모델 학습] ----------
def train_baseline(normal_eps, game_id, model_path, use_sequence=True,
                   seq_window=5, progress=None):
    """
    정상 에피소드로 기준 모델을 학습하고 저장한다.
    progress: 콜백 함수(str) - UI 로그용. None이면 무시.
    """
    def log(m):
        if progress: progress(m)

    log(f"정상 데이터 {len(normal_eps)}건으로 특징 추출 중...")
    X, names = build_matrix(normal_eps)

    log("집계 이상탐지기 학습 중 (IsolationForest)...")
    clf, scaler = train(X)

    seqm = None
    if use_sequence:
        log("시계열 스텝 모델 학습 중...")
        seqm = StepAnomalyModel(window=seq_window).fit(normal_eps)

    bundle = {
        "game_id": game_id,
        "clf": clf,
        "scaler": scaler,
        "feature_names": list(names),
        "seqm": seqm,
        "meta": {"n_train": len(normal_eps), "seq_window": seq_window},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    save_model(model_path, bundle)
    log(f"기준 모델 저장 완료: {model_path}")
    return bundle


# ---------- [QA 실행] ----------
def run_qa(episodes, model_path, report_path=None, progress=None):
    """
    저장된 기준 모델로 에피소드들을 검사해 리포트(dict)를 만든다(선택적으로 저장).
    episodes: 이번에 봇이 플레이한 판들(정상만=배포, 정상+결함=시연).
    """
    def log(m):
        if progress: progress(m)

    bundle = load_model(model_path)
    names = bundle["feature_names"]
    clf, scaler, seqm = bundle["clf"], bundle["scaler"], bundle.get("seqm")

    log(f"{len(episodes)}건 특징 추출 중...")
    X, _ = build_matrix(episodes, names=names)

    log("집계 이상탐지 판정 중...")
    flagged, scores = predict(clf, scaler, X)

    log("규칙(하드 오라클) 검사 중...")
    violations = [check_invariants(ep) for ep in episodes]

    anomaly_steps = None
    if seqm is not None:
        log("시계열 스텝 위치 분석 중...")
        anomaly_steps = [seqm.anomaly_steps(ep) for ep in episodes]

    report = build_report(
        game_id=bundle["game_id"],
        model={"type": "IsolationForest + StepAnomalyModel",
               "loaded_from": os.path.basename(model_path)},
        episodes=episodes, X=X, feature_names=names,
        flagged=flagged, anomaly_score=scores,
        hard_violations=violations, anomaly_steps=anomaly_steps,
    )
    if report_path:
        from qa_core.report import save_report
        save_report(report, report_path)
        log(f"리포트 저장: {report_path}")
    return report
