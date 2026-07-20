# explain.md — 자동화 파이프라인 데이터 흐름 상세

## 0. 문서 작성 규칙
- 이 문서는 자동화 과정의 순서를 데이터 흐름 관점에서 아주 자세히 기록한다. 즉 데이터가 어디에서 들어와 어떤 메서드를 거쳐 무엇으로 반환되고, 그것을 무엇이 받아 어디로 넘기는지를 단계별로 적는다.
- 기존 기록은 삭제하지 않는다. 내용이 바뀌면 취소선과 `[제거 YYYY-MM-DD: 사유]`를 남기고, 새 내용을 아래에 `[추가 YYYY-MM-DD]`로 기록한다.
- 문서체는 평서형(~다)으로 통일한다.

---

## 1. 현재 구현 범위 (2026-07-20)
- 구현된 것은 오라클 3계층 중 **① 하드 인바리언트**뿐이다.
- 데이터 소스는 실제 게임이 아니라 **JSONL 리플레이 파일**이다. 게임 계측이 준비되기 전까지 검출 로직을 독립적으로 개발·시연하기 위한 임시 소스다.
- 출력은 표준 출력(콘솔)이다. 향후 집계 층(Bug Store)과 대시보드로 교체한다.

---

## 2. 파이프라인 한눈에 보기

    tests/sample_telemetry.jsonl        (원천 데이터: 한 줄 = 한 틱의 상태)
        │  파일 경로(str)
        ▼
    qa/replay_source.py
      iter_samples_from_jsonl(path)     (파일을 읽어 StateSample을 하나씩 생성)
        │  StateSample 스트림(제너레이터)
        ▼
    run_invariants.py  main(path)       (파이프라인 조립 및 반복)
        │  StateSample 하나
        ▼
    qa/invariants.py
      InvariantChecker.check(sample)    (모든 규칙 적용)
        │  list[Bug]  (그 틱에서 위반한 규칙들의 결과)
        ▼
    run_invariants.py  main(path)       (버그를 받아 출력, 개수 누적)
        │  (향후) 집계 층 → 대시보드
        ▼
    콘솔 출력 + 총 검출 개수 반환

---

## 3. 단계별 상세

### 3.1 원천 데이터: `tests/sample_telemetry.jsonl`
- 각 줄은 하나의 JSON 객체이며, 한 틱에서 관측된 한 엔티티(봇)의 상태를 담는다.
- 키는 `StateSample`의 필드와 정확히 일치한다: `tick, time, entity_id, x, y, z, vx, vy, vz, health, max_health`.
- 이 파일에는 정상 플레이 틱과 함께, 검출을 시연하기 위한 합성 버그(바닥 관통, 체력 초과, 과속, 경계 이탈, 끼임)가 의도적으로 섞여 있다.

### 3.2 소스: `qa/replay_source.py`의 `iter_samples_from_jsonl(path)`
- 입력: 텔레메트리 파일 경로 `path`(문자열).
- 처리 순서:
  1. `open(path, ...)`로 파일을 UTF-8 텍스트 모드로 연다.
  2. 파일을 한 줄씩 순회한다. 한 줄이 한 틱이다.
  3. `line.strip()`으로 공백/개행을 제거하고, 빈 줄은 건너뛴다.
  4. `json.loads(line)`으로 JSON 문자열을 파이썬 딕셔너리 `record`로 변환한다.
  5. `StateSample(**record)`로 딕셔너리를 키워드 인자로 풀어 `StateSample` 객체를 만든다.
- 반환: `yield`로 `StateSample`을 하나씩 흘려보내는 **제너레이터**다. 전체를 메모리에 올리지 않고 한 번에 하나씩 처리한다.
- 넘기는 곳: 이 스트림은 `run_invariants.py`의 `main`이 `for` 루프로 받는다.

### 3.3 조립·반복: `run_invariants.py`의 `main(path)`
- 준비 단계:
  1. `MapBounds(...)`로 맵 경계 값 객체를 만든다. (데모용 예시 값)
  2. `InvariantChecker(bounds, max_speed, stuck_seconds, stuck_epsilon)`로 검출기를 생성한다. 이때 임계값이 검출기 내부에 저장된다.
- 반복 단계:
  1. `iter_samples_from_jsonl(path)`가 주는 `sample`(StateSample)을 하나씩 받는다.
  2. 각 `sample`을 `checker.check(sample)`에 넘긴다.
  3. 반환된 `list[Bug]`를 순회하며 개수를 누적하고, 한 줄씩 콘솔에 출력한다.
- 반환: 처리한 전체 버그 개수(int). 처리 종료 후 요약 문구를 출력한다.
- 확장 지점: 이 함수가 파이프라인을 조립하는 자리다. 소스(3.2)를 게임 스트림 리더로, 출력부를 집계 층 전송으로 교체하면 검출 로직은 그대로 재사용된다.

### 3.4 검출: `qa/invariants.py`의 `InvariantChecker.check(sample)`
- 입력: `StateSample` 하나.
- 처리 순서:
  1. 이번 틱의 결과를 담을 빈 리스트 `bugs`를 만든다.
  2. 무상태 규칙 네 개를 차례로 호출한다: `_check_floor`, `_check_bounds`, `_check_health`, `_check_speed`. 각 규칙은 위반 시 `Bug`를, 아니면 `None`을 반환한다. `None`이 아니면 `bugs`에 추가한다.
  3. 상태가 필요한 규칙 `_check_stuck`를 호출한다. 이 규칙은 내부 이력을 갱신하며, 위반 시 `Bug`를 반환한다.
  4. `bugs`를 반환한다. 위반이 없으면 빈 리스트다.
- 반환: `list[Bug]`.
- 받는 곳: `run_invariants.py`의 `main`(3.3)이 받는다.

#### 각 규칙의 판정 기준
- `_check_floor`: `z < floor_z - floor_margin`이면 바닥 관통. 심각도 HIGH.
- `_check_bounds`: `x`/`y`가 min/max 경계를 벗어나거나 `z > ceiling_z`이면 경계 이탈. 심각도 HIGH.
- `_check_health`: `health < 0` 또는 `health > max_health`이면 체력 범위 위반. 심각도 HIGH.
- `_check_speed`: `sample.speed`(속도 벡터 크기)가 `max_speed`를 초과하면 과속. 심각도 MEDIUM.
- `_check_stuck`: 아래의 상태 기반 로직을 따른다. 심각도 MEDIUM.

#### `_check_stuck`의 상태 관리 (내부 이력 `self._history`)
- `self._history`는 `entity_id`를 키로, `{x, y, z, last_move_time, reported}`를 값으로 가지는 딕셔너리다.
- 처음 보는 엔티티는 현재 상태로 이력을 초기화하고 판정하지 않는다.
- 이전 유의미 위치로부터의 이동 거리 `moved`를 계산한다.
  - `moved > stuck_epsilon`이면 움직인 것으로 보고, 유의미 위치와 `last_move_time`을 현재로 갱신하며 `reported`를 해제한다.
  - 그렇지 않으면 `idle = time - last_move_time`을 계산한다. `idle >= stuck_seconds`이고 아직 보고하지 않았다면 `reported`를 세우고 `Bug`를 반환한다. `reported` 플래그는 같은 끼임을 매 틱 중복 보고하지 않게 막는다.

### 3.5 데이터 구조 참조: `qa/telemetry.py`
- `Severity`: 버그 심각도 열거형(LOW/MEDIUM/HIGH).
- `MapBounds`: 맵의 물리적 경계(min/max x·y, floor_z, ceiling_z).
- `StateSample`: 한 틱의 상태. `speed` 프로퍼티는 속도 벡터의 크기를 계산해 반환한다.
- `Bug`: 검출 결과. tick, time, entity_id, rule, severity, message, details(부가 정보)를 담는다. 집계 층과 대시보드는 이 객체를 그대로 소비한다.

---

## 4. 실행 방법
프로젝트 루트에서 다음을 실행한다.

    python run_invariants.py tests/sample_telemetry.jsonl

현재 샘플에서는 다음 5건이 검출된다: 바닥 관통(tick 5), 체력 초과(tick 7), 과속(tick 8), 경계 이탈(tick 9), 끼임(tick 14).

---

## 5. 향후 교체·확장 지점
- **소스 교체**: `iter_samples_from_jsonl`를 실제 게임 텔레메트리 스트림 리더로 바꾼다. 반환 타입이 `StateSample` 스트림이면 나머지는 수정 없이 동작한다.
- **싱크 교체**: `main`의 출력 부분을 집계 층(Bug Store)으로 넘기는 코드로 바꾸고, 대시보드가 이를 구독하게 한다.
- **규칙 추가**: `InvariantChecker`에 `_check_*` 메서드를 추가하고 `check`의 규칙 목록에 넣는다.
- **다음 오라클**: 이상탐지(ML), 크래시/프로세스 워치독을 별도 검출기로 추가하고, 집계 층에서 세 오라클의 출력을 합친다.

---

## 6. 변경 이력
- 2026-07-20: 문서 최초 작성. 하드 인바리언트 오라클(v1)의 데이터 흐름을 기록했다.
