# 문서 현행화 및 죽은 파일 정리

README가 결함 피벗 이전 상태로 남아 있어 현재 코드와 어긋나 있었다.
이 문서는 무엇을 왜 고쳤는지 기록한 것이다.

직전 스냅샷은 `v4_before_docs_sync/`에 있다 (구 README.md, 구 reports/report.py).


## 1. README가 왜 문제였나

README는 이 프로젝트의 정면이다. 그런데 내용이 `CHANGELOG_defect_pivot.md`,
`NOTE_oracle_vs_anomaly.md`, `NOTE_lstm_vs_window.md`에서 내린 결론을 하나도
반영하지 않고 있었다. 피벗 이전 시점에 멈춰 있었다.

| README의 서술 | 실제 코드 |
|---|---|
| 결함 3종: softlock / teleport / reward_bug | under_explore / low_entropy / oscillate_mid / teleport / stuck |
| "궤적을 고정 길이 특징 벡터로" | 가변 길이 스칼라 dict (`build_matrix`) |
| `qa_core`에 schema / features / detect 3개 | oracle / sequence / report / service 추가됨 |
| `demo/` 없음 | 결함 주입은 `demo/`로 분리됨 |
| 파이프라인 6단계 (평가 중심) | 3층 탐지 구조 (규칙 / 집계 / 시계열) |
| 결과: softlock 1.000, reward_bug 0.200 | 아래 재현표 참조 |

특히 결과표의 reward_bug는 `CHANGELOG_defect_pivot.md`에서 "규칙으로 잡는 것이
더 정확한 명백한 버그이므로 제거한다"고 결론 낸 결함이다. 그 결론을 내린 뒤에도
README에는 성과 지표로 남아 있었다. 읽는 사람이 프로젝트의 논거를 정반대로
이해하게 만드는 상태였다.


## 2. 수치 불일치를 재현으로 확정했다

under_explore 탐지율이 문서마다 달랐다.

- `CHANGELOG_defect_pivot.md`: 0.567
- 인수인계 문서: 0.26

`run.py`와 `run_vizdoom.py`를 동일 환경(gymnasium 1.3.0, minigrid 3.1.0,
scikit-learn 1.8.0, numpy 2.4.4, vizdoom 1.3.0)에서 재현한 결과, 현재 값은
0.26이 맞다. 0.567은 3층 구조(oracle, sequence) 도입 이전 값이다.

원인은 분모다. 3층 구조가 들어오면서 집계층 수치를 "규칙이 놓친 판" 기준으로
바꿔 출력하게 되었는데, 0.567은 전체 60판 기준의 옛 값이다.

README에는 재현으로 확인된 값만 실었다.


## 3. under_explore가 규칙층에 걸리는 것을 발견했다

재현 중에 나온 것이다. `run.py`의 출력 주석은 under_explore를 "규칙엔 안 걸려야
정상"으로 표시하는데, 실제로는 걸린다.

| 게임 | 규칙층에 걸린 under_explore |
|---|---|
| MiniGrid | 25/60 (42퍼센트) |
| Doom | 29/40 (73퍼센트) |

원인 추정: under_explore는 이동을 억제하는 결함이므로 정지 구간이 길어지고,
그 결과 `stuck` 규칙(`freeze_limit`)이 먼저 발동한다.

이것 자체는 오작동이 아니다. 규칙이 이상탐지를 이긴다는 설계 원칙대로 동작한
것이고, 규칙이 확정한 판을 집계층이 다시 볼 이유는 없다. 정상 오탐도 0을
유지하고 있다.

다만 두 가지 문제가 있다.

- `run.py`의 주석("규칙엔 안 걸려야 정상")이 사실과 다르다.
- under_explore는 집계층을 검증하려고 만든 결함인데, 표본의 42에서 73퍼센트가
  집계층에 도달하기 전에 규칙층에서 소진된다. 즉 집계층의 under_explore 성능은
  11개에서 35개라는 작은 표본으로만 측정되고 있다.

이번 변경 범위는 문서이므로 코드는 건드리지 않았다. README에는 "수치를 읽는 법"
절을 두어 집계층 분모가 무엇인지 명시했다. 결함 설계 자체의 조정은 별도 작업으로
남긴다.


## 4. 죽은 파일 정리

| 경로 | 무엇 | 처리 |
|---|---|---|
| `reports/report.py` | `qa_core/report.py`의 구버전 복사본. `hard_violations` / `anomaly_steps` 인자가 없는 피벗 이전 버전. import하는 곳이 없다. | 삭제 (스냅샷 보존) |
| `__pycache__/` (루트) | `collect` / `detect` / `env_defects` / `features`의 .pyc. 현재 구조에는 루트에 그 모듈들이 없다. v1 잔재다. | 삭제 |

`reports/`는 리포트 JSON 출력 폴더다. 거기에 코어의 구버전 코드가 섞여 있으면
읽는 사람이 그것을 현재 코드로 오인한다. 실제로 그 파일은 규칙층과 시계열층이
없던 시절의 리포트 구조를 담고 있어서, 지금의 3층 구조와 정반대의 인상을 준다.


## 5. 변경 파일

| 파일 | 처리 |
|---|---|
| `README.md` | 전면 재작성 |
| `CHANGELOG_04_docs_sync.md` | 신규 (이 문서) |
| `v4_before_docs_sync/README.md` | 신규 (구 README 스냅샷) |
| `v4_before_docs_sync/report.py` | 신규 (구 reports/report.py 스냅샷) |
| `reports/report.py` | 삭제 |
| `__pycache__/` (루트) | 삭제 |

코드 변경은 없다. 파이프라인 동작은 그대로다.


## 6. 남은 작업

- `run.py` / `run_vizdoom.py`의 under_explore 주석 수정 (사실과 다름)
- under_explore 결함 설계 조정 검토. 집계층 검증용 결함이 규칙층에서 소진되지
  않도록 이동 억제 대신 다른 방식을 쓸지 판단이 필요하다.
