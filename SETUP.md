# 실행 가이드 (설치 · 라이브러리 · 실행 방법)

이 문서는 팀원이 처음부터 프로젝트를 실행할 수 있도록 필요한 것을 정리한다.
프로젝트는 두 부분으로 구성된다.
  - qa_bot_mvp : 게임 QA 자동화 코어(봇 실행, 이상탐지, 리포트)
  - QA_UI      : PyQt6 데스크톱 UI (qa_bot_mvp를 호출)


## 1. 폴더 구조

두 폴더는 같은 부모 폴더 안에 나란히 있어야 한다. UI가 상대경로(../qa_bot_mvp)로
코어를 찾기 때문이다.

```
ai-automation/            (부모 폴더, 이름은 자유)
├── qa_bot_mvp/           코어
│   ├── qa_core/          탐지 코어(게임 무관)
│   ├── adapters/         게임별 어댑터(정상 봇, 배포용)
│   ├── demo/             결함 주입(시연/검증용)
│   ├── reports/          리포트 출력
│   ├── models/           학습된 모델 저장(자동 생성)
│   ├── run.py            MiniGrid 파이프라인
│   └── run_vizdoom.py    ViZDoom 파이프라인
└── QA_UI/                UI (main.py, logic.py, *.ui)
```


## 2. 필요 라이브러리

파이썬 3.10 이상 권장. 아래는 검증에 사용한 버전이며, 호환되는 버전이면 된다.

| 라이브러리 | 검증 버전 | 용도 |
|---|---|---|
| numpy | 2.4.4 | 수치 연산(전반) |
| gymnasium | 1.3.0 | 강화학습 환경 인터페이스(MiniGrid 실행) |
| minigrid | 3.1.0 | MiniGrid 게임 환경 |
| scikit-learn | 1.8.0 | IsolationForest, 시계열 MLP, 스케일러 |
| joblib | 1.5.3 | 학습된 모델 저장/로드 |
| vizdoom | 1.3.0 | ViZDoom(Doom) 게임 환경 |
| PyQt6 | 최신 | 데스크톱 UI |
| qt-material | 최신(선택) | UI 다크 테마(없어도 실행됨) |

주의:
  - torch(파이토치)는 필요 없다. 개발 중 LSTM을 시험했으나 최종본은 scikit-learn
    윈도우 모델을 쓰므로 torch 없이 동작한다.
  - qt-material은 테마용이라 없어도 앱은 뜬다(내장 다크 테마로 대체됨).


## 3. 설치

같은 폴더에 있는 requirements.txt로 한 번에 설치한다.

```
pip install -r requirements.txt
```

또는 개별 설치:

```
pip install numpy gymnasium minigrid scikit-learn joblib vizdoom PyQt6 qt-material
```

### ViZDoom 설치 주의 (Windows)
ViZDoom은 게임 엔진이라 설치가 까다로울 수 있다.
  - Windows에서 pip 설치가 실패하면 Visual C++ 재배포 패키지 설치가 필요할 수 있다.
  - MiniGrid만으로도 전체 기능(봇 실행, 이상탐지, 시계열, 리포트, UI)이 동작하므로,
    ViZDoom 설치가 어려우면 MiniGrid로 먼저 실행해도 된다.


## 4. 실행 방법

### 4-1. 코어 파이프라인 단독 실행 (UI 없이)
동작 확인용. 봇이 정상+결함을 플레이하고 탐지 결과를 콘솔에 출력한다.

```
cd qa_bot_mvp
python run.py            # MiniGrid
python run_vizdoom.py    # ViZDoom
```

정상 동작 시 규칙 층/이상탐지 층/시계열 층 결과와 종합 탐지율이 출력되고
reports/ 에 리포트 JSON이 저장된다.

### 4-2. UI 실행
```
cd QA_UI
python main.py
```

UI 사용 순서:
  1. 등록 화면 "행동 분석 등록" 탭에서 게임 선택
  2. "1. 정상 데이터 준비" - 봇으로 생성(시연) 또는 파일로 등록(실제)
  3. "2. 기준 모델 학습" - 준비된 데이터로 학습(1회, 시간 걸림)
  4. "3. QA 실행" - 시연 실행(정상+결함) 또는 배포 실행(정상만)
  결과는 QA 화면 "행동 분석" 탭에 목록/상세/로그로 표시된다.

첫 실행은 봇이 실제로 게임을 플레이하므로 시간이 걸린다(정상 동작).
모델을 한 번 학습하면 이후 QA 실행은 저장된 모델을 불러와 빠르게 동작한다.


## 5. 자주 나는 문제

| 증상 | 원인/해결 |
|---|---|
| ModuleNotFoundError: PyQt6 | pip install PyQt6 |
| ModuleNotFoundError: qa_core 등 | QA_UI 가 qa_bot_mvp 와 같은 부모 폴더에 있는지 확인 |
| 리포트를 못 읽음 / 빈 목록 | 모델을 먼저 학습(2단계)했는지 확인 |
| ViZDoom 설치/실행 실패 | Windows C++ 재배포 설치, 또는 MiniGrid로 먼저 실행 |
| 창은 뜨는데 색이 밋밋함 | pip install qt-material (다크 테마 적용) |
| 파일 더블클릭 시 창이 바로 닫힘 | 터미널에서 python main.py 로 실행(오류 확인 위해) |


## 6. 참고

  - 정상 데이터는 봇이 자동 플레이로 생성한다(회사 제공 데이터가 없어도 됨).
    실서비스에서는 회사가 제공한 정상 로그를 "파일로 등록"으로 넣을 수 있다.
  - 결함 주입(demo/)은 시연/검증용이며 실제 배포 어댑터(adapters/)에는 포함되지 않는다.
