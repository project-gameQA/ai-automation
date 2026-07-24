"""이상탐지(②) 오라클이다.

하드 인바리언트(①)는 물리적으로 불가능한 상태를 규칙으로 잡는다. 이 오라클은 규칙은 하나도
어기지 않지만 정상 플레이에서 벗어난 구간을 잡는다. 정상 플레이만 학습하고, 거기서 벗어난
정도를 점수로 낸다. 무엇이 이상인지 미리 알려 주지 않으므로 비지도 학습이다.

결론이 아니라 단서다. 점수가 높다고 버그가 확정되는 것이 아니라 "이 구간을 검사하라"는
뜻이다. 그래서 점수와 함께 어떤 특징이 얼마나 벗어났는지를 같이 내보낸다.

무엇을 잡는가(검증된 범위):
**활동·전투 패턴 이상**이다. "봇들이 갑자기 싸우지 않는다"가 대표적이며, 무기 파손, 데미지
계산 오류, AI 타겟팅 실패, 스폰 로직 문제가 모두 이 형태로 나타난다. 어느 것도 좌표나 체력이
불가능한 값이 되지 않으므로 하드 규칙에는 걸리지 않는다.

무엇을 못 잡는가(알려진 한계):
**짧은 이동 이상**이다. 끼임, 진동, 좁은 구역 맴돌기는 이 오라클의 범위가 아니다. 그것을
잡으려면 훨씬 짧은 창이 필요한데, 그 척도가 실제로 동작한다는 근거를 확보하지 못해 넣지
않았다. 근거 없는 구조를 미리 넣는 것보다 비어 있음을 밝히는 편이 낫다.

────────────────────────────────────────────────────────────────────────────
왜 60초 창인가 (2026-07-23 실측)
────────────────────────────────────────────────────────────────────────────
처음에는 5초 창으로 시작했다. 그런데 봇 실력을 최저로 낮춘 시험 데이터에서 탐지율이 4.4%로,
정상 대조군(8.6%)보다도 낮았다. 원인은 **차이가 창 하나가 아니라 분포에 있기 때문**이다.

실력이 낮은 봇은 거의 싸우지 않는다(발사 -82%, 받은 피해 -90%). 그런데 정상 플레이에도 5초
동안 한 발도 안 쏘는 창은 흔하다. 그래서 실력 낮은 봇의 조용한 창이 정상 데이터의 조용한
영역에 그대로 묻힌다. "3분 내내 안 쏜다"는 이상하지만 "5초 동안 안 쏜다"는 정상이다.

창 길이를 늘리며 실측한 결과다(정상 대조군 / 실력 최저 판의 이상 판정 비율).

    5초    8.6% /   4.4%
    15초   9.5% /  22.4%
    30초  12.1% /  64.6%
    60초   9.1% /  95.2%

60초를 택한다. 더 늘리면 시험 데이터에서 창 수가 부족해 판단할 수 없었다.

────────────────────────────────────────────────────────────────────────────
왜 활동 특징만 쓰는가 (2026-07-23 실측)
────────────────────────────────────────────────────────────────────────────
이동 특징(qa/features.py) 11개를 60초 창에서 함께 써 보았으나 결과가 나빴다.

    이동 11개   정상 대조군 18.8% / 실력 최저  46.8%
    활동 3개    정상 대조군  8.7% / 실력 최저 100.0%
    전부 14개   정상 대조군 16.8% / 실력 최저  90.3%

이동 특징을 넣으면 **오탐이 두 배가 되고 탐지율은 오히려 떨어진다.** 정상 세션끼리도 18.8%를
이상으로 판정한다는 것은 신호가 아니라 세션 간 흔들림을 잡고 있다는 뜻이다. 60초 척도에서
이동 특징은 노이즈로 작동한다.

체력 관련 값을 특징에서 뺐던 초기 판단은 철회했다. "정상 데스매치에서 체력이 교전에 따라
극단적으로 흔들려 격전 창을 이상으로 볼 것"이라 우려했으나, 실측한 정상 두 판의 발사 비율이
0.219 / 0.216 으로 안정적이었다. 60초로 평균을 내면 교전의 들쭉날쭉함이 가라앉는다.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Iterator, Optional

from .telemetry import StateSample
from .features import WindowExtractor  # 창을 자르는 로직은 이동 특징 쪽과 공유한다.

# 활동 특징 이름이다. 모델 저장 시 열 순서를 고정하기 위해 한곳에 둔다.
# 순서가 바뀌면 저장해 둔 모델이 다른 의미의 값을 받게 되므로, 항목 추가는 반드시 끝에 한다.
ACTIVITY_FEATURES = [
    "attack_ratio",       # 발사 버튼이 눌린 프레임의 비율
    "health_lost_rate",   # 초당 잃은 체력
    "health_ratio",       # 최대 체력 대비 현재 체력의 평균
]

# 사람이 읽는 이름이다. 도구 출력과 대시보드에 쓴다.
ACTIVITY_LABEL = {
    "attack_ratio": "발사 비율",
    "health_lost_rate": "체력 손실률",
    "health_ratio": "체력 유지율",
}


@dataclass
class ActivityWindow:
    """한 봇의 한 시간 구간을 활동 관점에서 요약한 것이다."""

    entity_id: int
    start_time: float
    end_time: float
    n_samples: int

    attack_ratio: float      # 0에 가까우면 싸우지 않는다는 뜻이다.
    health_lost_rate: float  # 교전 강도를 나타낸다.
    health_ratio: float      # 1에 가까우면 피해를 거의 안 받았다는 뜻이다.

    def to_vector(self) -> list[float]:
        """ACTIVITY_FEATURES 순서대로 값을 늘어놓는다. 모델 입력용이다."""
        return [float(getattr(self, name)) for name in ACTIVITY_FEATURES]

    def to_dict(self) -> dict:
        row = {
            "entity_id": self.entity_id,
            "start_time": round(self.start_time, 2),
            "end_time": round(self.end_time, 2),
            "n_samples": self.n_samples,
        }
        for name in ACTIVITY_FEATURES:
            row[name] = round(float(getattr(self, name)), 4)
        return row


def compute_activity(samples: list[StateSample]) -> Optional[ActivityWindow]:
    """한 창에 속한 상태 목록에서 활동 특징을 계산한다."""
    first, last = samples[0], samples[-1]
    duration = last.time - first.time
    if duration <= 0:  # 시간이 흐르지 않은 창은 비율을 계산할 수 없다.
        return None

    lost = 0.0
    for i in range(1, len(samples)):
        delta = samples[i].health - samples[i - 1].health
        if delta < 0:
            lost += -delta  # 체력이 줄었다. 피해를 입은 것이다.
        # 체력이 늘어난 경우는 세지 않는다. 아이템 회복과 리스폰이 섞여 있어 그 자체로는
        # 활동의 강도를 나타내지 않기 때문이다.

    return ActivityWindow(
        entity_id=first.entity_id,
        start_time=first.time,
        end_time=last.time,
        n_samples=len(samples),
        attack_ratio=sum(1 for s in samples if s.attack > 0) / len(samples),
        health_lost_rate=lost / duration,
        # max_health 가 0인 비정상 기록에 대비해 나눗셈을 보호한다.
        health_ratio=statistics.mean([s.health / max(s.max_health, 1) for s in samples]),
    )


class ActivityExtractor(WindowExtractor):
    """활동 특징 전용 창 추출기다. 창을 자르는 로직은 WindowExtractor 와 공유한다.

    이 로직을 복제하지 않는 이유가 있다. 창 경계 처리는 겉보기보다 미묘해서, 두 벌을 두면
    한쪽만 고쳐지는 일이 생긴다. 이 프로젝트에서 같은 성격의 버그를 이미 두 번 겪었다
    (끼임 중복 억제가 검출기와 집계기 두 곳에 존재한 일, 세션 사본의 경계 처리).
    """

    def __init__(self, window_seconds: float, step_seconds: float, min_samples: int) -> None:
        super().__init__(
            window_seconds=window_seconds,
            step_seconds=step_seconds,
            min_samples=min_samples,
            compute_fn=compute_activity,
        )


def extract_activity(
    samples: Iterator[StateSample],
    window_seconds: float,
    step_seconds: float,
    min_samples: int,
) -> list[ActivityWindow]:
    """상태 스트림 전체에서 활동 창을 뽑아 시간 순으로 반환한다. 오프라인 처리용이다."""
    ex = ActivityExtractor(window_seconds, step_seconds, min_samples)
    out: list[ActivityWindow] = []
    for s in samples:
        out.extend(ex.feed(s))
    out.extend(ex.finalize())
    out.sort(key=lambda w: (w.start_time, w.entity_id))
    return out


@dataclass
class AnomalyScore:
    """한 창에 대한 채점 결과다."""

    entity_id: int
    start_time: float
    end_time: float
    score: float        # 이상 점수. 값이 작을수록 이상하다(sklearn 관례를 따른다).
    is_anomaly: bool    # 임계값을 넘었는지
    contributions: dict  # 특징 이름 → 학습 데이터 기준 z 점수. 무엇이 벗어났는지 보여 준다.
    values: dict         # 그 창의 실제 특징 값

    def top_contributor(self) -> tuple:
        """가장 크게 벗어난 특징과 그 z 점수를 돌려준다."""
        if not self.contributions:
            return ("", 0.0)
        name = max(self.contributions, key=lambda k: abs(self.contributions[k]))
        return (name, self.contributions[name])

    def to_dict(self) -> dict:
        name, z = self.top_contributor()
        return {
            "entity_id": self.entity_id,
            "start_time": round(self.start_time, 2),
            "end_time": round(self.end_time, 2),
            "score": round(self.score, 4),
            "is_anomaly": self.is_anomaly,
            "top_feature": name,
            "top_feature_label": ACTIVITY_LABEL.get(name, name),
            "top_feature_z": round(z, 2),
            "contributions": {k: round(v, 2) for k, v in self.contributions.items()},
            "values": {k: round(v, 4) for k, v in self.values.items()},
        }


class AnomalyModel:
    """정상 플레이만 학습해 이상 점수를 내는 모델이다.

    알고리즘은 IsolationForest 다. 무작위로 특징과 분할점을 골라 트리를 만들고, 어떤 점이
    얼마나 적은 분할로 고립되는지를 본다. 밀집한 정상 영역의 점은 고립시키는 데 여러 번 잘라야
    하고, 동떨어진 점은 몇 번 만에 고립된다. 분포 가정이 없고 특징 수가 적을 때 안정적이라
    이 데이터에 맞는다.

    임계값 결정(중요):
    sklearn 의 contamination='auto' 는 데이터에 맞춰 눈금을 매기는 것이 아니라 고정된 오프셋을
    쓴다. 그러면 "정상 데이터의 몇 %가 걸리는가"를 통제할 수 없다. 대신 **학습 데이터 점수의
    하위 백분위**를 임계값으로 삼는다. percentile=5 로 학습하면 "학습 데이터의 5%가 이상으로
    잡히는 선"이 임계값이 되므로, 오탐률의 기준선을 직접 정할 수 있고 다른 세션의 판정 비율을
    그 기준과 비교해 해석할 수 있다.
    """

    def __init__(self, n_estimators: int = 300, percentile: float = 5.0, random_state: int = 0) -> None:
        self.n_estimators = n_estimators
        self.percentile = percentile
        self.random_state = random_state
        self._scaler = None                     # StandardScaler
        self._model = None                      # IsolationForest
        self.threshold: Optional[float] = None
        self.feature_names = list(ACTIVITY_FEATURES)
        self.train_mean: Optional[list] = None  # 기여도 계산에 쓸 학습 데이터 평균
        self.train_std: Optional[list] = None   # 기여도 계산에 쓸 학습 데이터 표준편차
        self.metadata: dict = {}

    def fit(self, windows: list) -> "AnomalyModel":
        """정상 플레이 창들로 학습한다."""
        # sklearn 은 무겁고 서버 실행 자체에는 필요 없으므로 여기서만 가져온다.
        import numpy as np
        from sklearn.ensemble import IsolationForest
        from sklearn.preprocessing import StandardScaler

        if len(windows) < 50:
            raise ValueError(
                f"학습 창이 {len(windows)}개뿐이다. 정상 범위를 배우기에 너무 적다. "
                "봇 수를 늘리거나 더 긴 세션이 필요하다."
            )

        X = np.array([w.to_vector() for w in windows])
        self._scaler = StandardScaler().fit(X)
        Z = self._scaler.transform(X)
        self._model = IsolationForest(
            n_estimators=self.n_estimators,
            random_state=self.random_state,
        ).fit(Z)

        # 학습 데이터 점수의 하위 percentile 을 임계값으로 삼는다. 근거는 클래스 설명 참조.
        train_scores = self._model.score_samples(Z)
        self.threshold = float(np.percentile(train_scores, self.percentile))

        # 기여도(z 점수) 계산을 위해 학습 데이터의 분포를 보관한다.
        self.train_mean = X.mean(axis=0).tolist()
        self.train_std = [float(v) if v > 1e-9 else 1.0 for v in X.std(axis=0)]

        self.metadata = {
            "n_windows": len(windows),
            "n_bots": len({w.entity_id for w in windows}),
            "percentile": self.percentile,
            "threshold": self.threshold,
        }
        return self

    def score(self, windows: list) -> list:
        """창들을 채점한다. 학습된 모델이 있어야 한다."""
        import numpy as np

        if self._model is None or self.threshold is None:
            raise RuntimeError("학습되지 않은 모델이다. fit() 하거나 load() 해야 한다.")
        if not windows:
            return []

        X = np.array([w.to_vector() for w in windows])
        scores = self._model.score_samples(self._scaler.transform(X))

        out = []
        for w, raw, row in zip(windows, scores, X):
            # 각 특징이 학습 데이터 평균에서 몇 표준편차 벗어났는지를 계산한다.
            # 이 값이 있어야 "왜 이상한가"를 사람에게 보여 줄 수 있다. 점수만으로는
            # 검사하라는 말은 되지만 무엇을 검사하라는 말은 되지 않는다.
            contrib = {
                name: (float(row[i]) - self.train_mean[i]) / self.train_std[i]
                for i, name in enumerate(self.feature_names)
            }
            out.append(AnomalyScore(
                entity_id=w.entity_id,
                start_time=w.start_time,
                end_time=w.end_time,
                score=float(raw),
                is_anomaly=bool(raw < self.threshold),
                contributions=contrib,
                values={name: float(row[i]) for i, name in enumerate(self.feature_names)},
            ))
        return out

    def save(self, path: str) -> None:
        """모델을 파일에 저장한다. 학습은 한 번 하고 재사용한다."""
        import joblib
        joblib.dump({
            "scaler": self._scaler,
            "model": self._model,
            "threshold": self.threshold,
            "feature_names": self.feature_names,
            "train_mean": self.train_mean,
            "train_std": self.train_std,
            "percentile": self.percentile,
            "metadata": self.metadata,
        }, path)

    @classmethod
    def load(cls, path: str) -> "AnomalyModel":
        """저장된 모델을 불러온다."""
        import joblib
        data = joblib.load(path)
        m = cls(percentile=data.get("percentile", 5.0))
        m._scaler = data["scaler"]
        m._model = data["model"]
        m.threshold = data["threshold"]
        m.feature_names = data["feature_names"]
        m.train_mean = data["train_mean"]
        m.train_std = data["train_std"]
        m.metadata = data.get("metadata", {})
        # 저장 당시의 특징 목록과 지금 코드의 목록이 다르면 값의 의미가 어긋난다.
        # 조용히 틀린 결과를 내는 것보다 여기서 멈추는 편이 낫다.
        if m.feature_names != list(ACTIVITY_FEATURES):
            raise ValueError(
                "저장된 모델의 특징 목록이 현재 코드와 다르다.\n"
                f"  저장됨: {m.feature_names}\n"
                f"  현재:   {list(ACTIVITY_FEATURES)}\n"
                "모델을 다시 학습해야 한다."
            )
        return m
