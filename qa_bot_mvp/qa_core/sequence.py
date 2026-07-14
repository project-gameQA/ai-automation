"""
sequence.py  (코어, 시계열 = 스텝 단위 이상탐지)
------------------------------------------------
집계 특징(features.py)은 한 판을 숫자 몇 개로 뭉개서 '순서'가 사라진다.
그래서 '맴돌기'처럼 순서로만 드러나는 이상, 그리고 '어느 스텝이 이상한지'를 못 짚는다.

이 모듈은 판을 '스텝 시퀀스' 그대로 보고, 스텝 단위로 이상 점수를 낸다.
방식: 정상 플레이에서 '직전 몇 스텝(window)을 보고 다음 스텝을 예측'하도록 학습.
      실제 스텝이 예측과 크게 다르면 그 스텝이 이상(예측 오차 = 스텝 이상 점수).
      -> 오차가 튀는 스텝의 '위치(스텝 번호)'가 이상 지점.

게임 무관: 스텝 특징은 이동 델타/리워드 등 보편값만 쓰고, 스케일은 meta에서 읽는다.
무거운 딥러닝 없이 sklearn MLPRegressor(윈도우 입력)로 구현 -> 어떤 하드웨어서도 학습.
(윈도우가 짧은 순서 패턴을 담으므로 맴돌기 같은 국소 순서 이상을 잡는다.
 완전한 장기 시퀀스는 LSTM이 필요하지만, 국소 이상 지역화에는 윈도우로 충분.)
"""
import numpy as np
from sklearn.neural_network import MLPRegressor
from sklearn.preprocessing import StandardScaler


def _game_keys(ep):
    """에피소드의 스텝별 게임 특징 키 목록(정렬). 없으면 빈 리스트."""
    for s in ep.steps:
        if s.game_features:
            return sorted(s.game_features.keys())
    return []


def step_features(ep, game_keys=None):
    """
    Episode -> (T, d) 스텝별 특징 시퀀스.
    공통 특징(이동 델타/리워드/행동) + 어댑터가 넣은 스텝별 game_features(있으면).
    game_keys: 게임 특징 열 순서(고정). None이면 이 에피소드에서 추론.
    코어는 game_features의 '이름'을 모른다 — 정해진 순서로 숫자만 붙일 뿐.
    """
    cell = ep.meta.get("cell_size", 1.0)
    move_eps = ep.meta.get("move_eps", 0.0)
    init = ep.meta.get("init_pos")
    pos = ([tuple(float(v) for v in init)] if init is not None else
           [tuple(float(v) for v in ep.steps[0].pos)])
    for s in ep.steps:
        pos.append(tuple(float(v) for v in s.pos))

    if game_keys is None:
        game_keys = _game_keys(ep)

    rows = []
    for i in range(len(pos) - 1):
        dx = (pos[i + 1][0] - pos[i][0]) / cell
        dy = (pos[i + 1][1] - pos[i][1]) / cell
        dist = abs(dx) + abs(dy)
        moved = 0.0 if dist <= move_eps / cell else 1.0
        st = ep.steps[i]
        common = [dx, dy, dist, moved, float(st.reward), float(st.action)]
        gf = [float(st.game_features.get(k, 0.0)) for k in game_keys]  # 어댑터 스텝 특징
        rows.append(common + gf)
    d = 6 + len(game_keys)
    return np.array(rows, dtype=float) if rows else np.zeros((0, d))


def _windows(seqs, k):
    """각 시퀀스에서 (직전 k스텝 평탄화) -> (다음 스텝) 쌍을 만든다."""
    Xs, ys = [], []
    for s in seqs:
        for t in range(k, len(s)):
            Xs.append(s[t - k:t].reshape(-1))
            ys.append(s[t])
    if not Xs:
        return np.zeros((0, 0)), np.zeros((0, 0))
    return np.array(Xs), np.array(ys)


class StepAnomalyModel:
    """정상 스텝 시퀀스로 '다음 스텝 예측'을 학습하고, 예측 오차로 스텝 이상 점수를 낸다."""

    def __init__(self, window=4, hidden=(64, 32), seed=0):
        self.k = window
        self.xscaler = StandardScaler()
        self.yscaler = StandardScaler()
        self.mlp = MLPRegressor(hidden_layer_sizes=hidden, max_iter=300,
                                random_state=seed, early_stopping=True)

    def fit(self, normal_eps):
        # 스텝별 게임 특징 키를 정상 데이터에서 확정(이후 모든 곳에서 이 순서 사용)
        self.game_keys = []
        for ep in normal_eps:
            gk = _game_keys(ep)
            if gk:
                self.game_keys = gk
                break
        seqs = [step_features(ep, self.game_keys) for ep in normal_eps]
        seqs = [s for s in seqs if len(s) > self.k]
        X, y = _windows(seqs, self.k)
        Xs = self.xscaler.fit_transform(X)
        ys = self.yscaler.fit_transform(y)
        self.mlp.fit(Xs, ys)
        per_step = self._errors_concat(seqs)
        self.step_threshold = float(np.quantile(per_step, 0.999)) if len(per_step) else 1.0
        ep_max = np.array([self.step_errors(s).max() for s in seqs if len(s) > self.k])
        self.episode_threshold = float(np.quantile(ep_max, 0.95)) if len(ep_max) else 1.0
        return self

    def _errors_concat(self, seqs):
        allE = []
        for s in seqs:
            allE.append(self.step_errors(s))
        return np.concatenate(allE) if allE else np.zeros(0)

    def step_errors(self, seq):
        """스텝 시퀀스 -> 스텝별 예측 오차 배열(길이 T). 앞 k스텝은 문맥이라 0."""
        T = len(seq)
        errs = np.zeros(T)
        if T <= self.k:
            return errs
        X = np.array([seq[t - self.k:t].reshape(-1) for t in range(self.k, T)])
        yt = seq[self.k:]
        pred = self.yscaler.inverse_transform(self.mlp.predict(self.xscaler.transform(X)))
        e = np.linalg.norm(pred - yt, axis=1)
        errs[self.k:] = e
        return errs

    def anomaly_steps(self, ep):
        """Episode -> 이상으로 판정된 스텝 번호 리스트 (스텝 오차 > 스텝 임계값)."""
        errs = self.step_errors(step_features(ep, self.game_keys))
        return [int(t) for t in np.where(errs > self.step_threshold)[0]]

    def episode_score(self, ep):
        """판 단위 점수(스텝 최대 오차)."""
        errs = self.step_errors(step_features(ep, self.game_keys))
        return float(errs.max()) if len(errs) else 0.0

    def episode_flagged(self, ep):
        """판 단위 이상 여부 (판 최대오차 > 판 임계값)."""
        return self.episode_score(ep) > self.episode_threshold
