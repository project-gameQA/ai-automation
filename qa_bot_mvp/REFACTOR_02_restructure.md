# 리팩터링 기록 #2 — 역할별 패키지 구조로 재구성

> 목적: 평평하게 섞여 있던 파일들을 역할(재사용 코어 / 게임별 어댑터 /
> 표준 궤적)에 따라 폴더로 분리한다. 폴더 이름만으로 아키텍처가 드러나게 하고,
> 이후 어댑터를 여러 개 붙여도 구조가 깨지지 않게 한다.

## 0. 원칙 & 보존

- **동작 불변**: 재구성 전후 탐지 수치가 같아야 한다.
- **원본 보존**: 재구성 직전 상태를 `v2_before_restructure/`에 스냅샷(원본 flat 6파일).

## 1. before / after 폴더 구조

### BEFORE (flat — 모든 파일이 한 폴더)
```
game-qa-mvp/
├── schema.py
├── features.py
├── detect.py
├── collect.py
├── env_defects.py
├── run.py
└── README.md
```

### AFTER (역할별 패키지)
```
game-qa-mvp/
├── run.py                      # 실행 진입점 (루트)
├── README.md
├── REFACTOR_01_schema.md
├── REFACTOR_02_restructure.md
│
├── qa_core/                    # 재사용 코어 (게임 무관)
│   ├── __init__.py
│   ├── schema.py               #   표준 궤적 포맷 (계약)
│   ├── features.py             #   궤적 → 특징 벡터
│   └── detect.py               #   이상탐지 학습/평가
│
├── adapters/                   # 게임별 어댑터
│   ├── __init__.py
│   └── minigrid/
│       ├── __init__.py
│       ├── collect.py          #   MiniGrid 플레이 수집
│       └── env_defects.py      #   MiniGrid 결함 주입 (A안: 어댑터 안에 둠)
│
├── v1_before_schema/           # 스냅샷 (스키마 리팩터링 전)
└── v2_before_restructure/      # 스냅샷 (이번 재구성 전, flat 구조)
```

## 2. 설계 결정

- **`env_defects.py` 위치 = A안**: 결함 주입 아이디어는 범용이지만 현재 구현이
  MiniGrid `step()`에 특화돼 있으므로, 지금은 `adapters/minigrid/` 안에 둔다.
  ViZDoom에도 결함 주입이 필요해지면 그때 공통부를 뽑아낸다(성급한 추상화 회피).
- **패키지화**: 각 폴더에 `__init__.py`를 두어 파이썬 패키지로 만든다.
  폴더로 나누면 기존 `from schema import ...` 류 import가 깨지므로, 패키지 경로로 교체.

## 3. import 변경 (핵심)

파일을 폴더로 옮기면 import가 깨진다. 아래처럼 절대 패키지 경로로 수정.

### `adapters/minigrid/collect.py`
```python
# BEFORE
from env_defects import DefectWrapper
from schema import Episode, Step
# AFTER
from adapters.minigrid.env_defects import DefectWrapper
from qa_core.schema import Episode, Step
```

### `run.py`
```python
# BEFORE
from collect import collect
from features import featurize, FEATURE_NAMES
from detect import train, evaluate
# AFTER
from adapters.minigrid.collect import collect
from qa_core.features import featurize, FEATURE_NAMES
from qa_core.detect import train, evaluate
```

변경 없는 파일: `qa_core/schema.py`, `qa_core/features.py`, `qa_core/detect.py`,
`adapters/minigrid/env_defects.py` (내부 import가 없거나 외부 라이브러리뿐).

## 4. 주의: 폴더명 `minigrid` 와 라이브러리 `minigrid` 충돌?

`collect.py`에는 `import minigrid`(PyPI 라이브러리, 환경 등록용)가 있고,
우리 폴더도 `adapters/minigrid/`다. 하지만 **충돌하지 않는다.**
- 우리 폴더는 top-level이 아니라 `adapters.minigrid`로만 접근된다.
- 루트에는 top-level `minigrid/` 폴더가 없으므로 `import minigrid`는
  site-packages의 라이브러리로 정확히 해석된다.
- 실제 실행으로 정상 동작 확인됨.

## 5. 실행 방법

루트(`game-qa-mvp/`)에서:
```bash
python run.py
```
루트가 sys.path에 들어가므로 `qa_core.*`, `adapters.*` 절대 import가 해석된다.

## 6. 검증 결과 (재구성 후, 완전 일치)

| 지표 | BEFORE | AFTER |
|------|:---:|:---:|
| precision | 0.910 | 0.910 |
| recall | 0.678 | 0.678 |
| f1 | 0.777 | 0.777 |
| roc_auc | 0.797 | 0.797 |
| softlock recall | 1.000 | 1.000 |
| teleport recall | 0.833 | 0.833 |
| reward_bug recall | 0.200 | 0.200 |
| 정상 오탐율 | 0.120 | 0.120 |

→ 동작 불변 확인. 코드 이동 + import 수정만 했고 로직은 그대로.

## 7. 이 재구성으로 얻는 것

- 폴더 구조가 곧 아키텍처 문서: `qa_core/`(재사용) vs `adapters/`(게임별).
- ViZDoom 추가 시 `adapters/vizdoom/`만 생기고 `qa_core/`는 무수정 재사용.
- 다음 작업: `adapters/vizdoom/` 어댑터 작성(실제 게임 전이).
