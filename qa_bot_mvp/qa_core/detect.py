"""
detect.py
---------
비지도 이상탐지 모델.

핵심: '정상 플레이'만 보고 학습한다. 버그 데이터는 학습에 안 쓴다.
학습된 모델은 "정상에서 벗어난 것"을 이상치로 플래그한다.
이게 modl.ai 같은 서비스가 미지의 버그까지 잡는 원리와 같다.
(여기선 IsolationForest 사용. 오토인코더로 교체해도 됨)
"""
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import precision_recall_fscore_support, roc_auc_score
import numpy as np


def train(X_normal):
    """정상 궤적 특징만으로 이상탐지기를 학습."""
    scaler = StandardScaler().fit(X_normal)
    clf = IsolationForest(n_estimators=300, contamination="auto", random_state=0)
    clf.fit(scaler.transform(X_normal))
    return clf, scaler


def evaluate(clf, scaler, X_test, y_test):
    """정상+버그 섞인 테스트셋으로 성능 측정."""
    Xs = scaler.transform(X_test)
    anomaly_score = -clf.score_samples(Xs)          # 값이 클수록 더 이상함
    pred = (clf.predict(Xs) == -1).astype(int)       # 1 = 버그로 판정
    prec, rec, f1, _ = precision_recall_fscore_support(
        y_test, pred, average="binary", zero_division=0)
    auc = roc_auc_score(y_test, anomaly_score)
    return {"precision": prec, "recall": rec, "f1": f1, "roc_auc": auc}, pred, anomaly_score
