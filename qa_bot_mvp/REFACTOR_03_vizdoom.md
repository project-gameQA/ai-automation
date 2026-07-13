# 리팩터링 기록 #3 — 실제 게임 전이 (ViZDoom 어댑터 추가)

> 목적: 실제 게임(ViZDoom / Doom)용 어댑터를 추가하여,
> qa_core(스키마·특징·탐지)를 **수정 없이 재사용**해 버그를 탐지한다.
> "어댑터만 갈아끼우면 코어는 그대로"라는 아키텍처를 실제 게임으로 입증.

## 0. 원칙 & 보존
- 기존 코어는 최대한 불변. 유일하게 바뀐 코어 파일 `qa_core/features.py`는
  `v3_before_vizdoom/features.py`에 스냅샷. MiniGrid 결과가 전과 동일함을 확인.
- ViZDoom 관련 파일은 전부 신규 추가(가산적).

## 1. 추가/변경 요약

| 파일 | 종류 | 내용 |
|------|------|------|
| `adapters/vizdoom/__init__.py`     | 신규 | 패키지 표시 |
| `adapters/vizdoom/collect.py`      | 신규 | 실제 Doom 플레이 수집 → 표준 Episode |
| `adapters/vizdoom/env_defects.py`  | 신규 | Doom용 결함 주입 (softlock, reward_bug) |
| `run_vizdoom.py`                   | 신규 | ViZDoom 파이프라인 (qa_core 재사용) |
| `qa_core/features.py`              | 변경 | 스케일 파라미터(move_eps, cell_size)를 meta에서 읽도록 일반화 |

## 2. 핵심 발견: 격자 vs 연속 좌표

MiniGrid는 정수 격자(한 칸=1, 한 틱 최대 1칸 이동)이지만,
Doom은 연속 좌표(한 틱에 6~40 유닛 이동)다. 이 때문에 격자를 가정한 특징이
그대로는 안 맞는다:
- `frac_no_move`/`max_freeze_streak`: "정확히 제자리(jumps==0)"는 연속계에서
  거의 안 생김 → "임계값 이하 이동"으로 일반화 필요.
- `unique_cells`: 연속 좌표는 매 위치가 고유 → 공간을 양자화해서 세야 함.

### 해결: qa_core/features.py 일반화 (코어는 한 벌 유지)
`Episode.meta`에서 게임 스케일을 읽어 자기 조정하게 함.
```python
move_eps  = ep.meta.get("move_eps", 0.0)    # 격자=0,   연속=작은 양수(Doom: 2.0)
cell_size = ep.meta.get("cell_size", 1.0)   # 격자=1,   연속=크게(Doom: 32.0)
no_move = (jumps <= move_eps)               # eps=0이면 MiniGrid의 jumps==0과 동일
cells = {(round(x/cell_size), round(y/cell_size)) for ...}  # cell_size=1이면 정수 그대로
```
기본값(0, 1)이 MiniGrid 동작을 완전히 보존 → **MiniGrid 결과 전과 동일(재검증 완료)**.
어댑터가 게임 스케일을 meta에 선언하고, 공유 특징 코드는 그대로 동작.

## 3. 아키텍처 입증: run.py vs run_vizdoom.py

두 실행기의 차이는 **import 두 줄(어댑터)뿐**이다. 코어 호출은 동일.
```python
# run.py            : from adapters.minigrid.collect import collect
# run_vizdoom.py    : from adapters.vizdoom.collect  import collect
#   (features/detect import은 양쪽 동일)
```

## 4. 결함 주입 차이 (정직한 한계)
- softlock  : 트리거 이후 무행동(입력 무시) → 정지. (양쪽 게임 모두 구현)
- reward_bug: 보상에 가짜 값 주입. (양쪽 구현)
- teleport  : ViZDoom은 파이썬으로 플레이어 좌표를 직접 세팅 불가(엔진 ACS 스크립팅 필요).
              → 이번 어댑터에서 **제외**. 향후 확장.

## 5. 결과 (실제 Doom, my_way_home 시나리오)

수집 규모: 정상 150판 / softlock 40 / reward_bug 40. 판당 ~0.3s, 헤드리스.

```
=== 전체 탐지 성능 (실제 Doom) ===
   precision: 0.904
      recall: 0.825
          f1: 0.863
     roc_auc: 0.879

=== 버그 종류별 탐지율 (recall) ===
    softlock: 1.000   (40/40)
  reward_bug: 0.650   (26/40)

  정상 오탐율(false positive): 0.175
```

특징별 평균(정상 vs 버그) 핵심:
- `max_freeze_streak`: 7.65 vs 71.40  → softlock 완벽 분리
- `total_reward`     : 0.04 vs 2.04   → reward_bug 분리
- `max_jump`         : 34.35 vs 30.26 → (연속계라 baseline이 ~34, MiniGrid의 1과 대비)

## 6. MiniGrid vs Doom 비교 (같은 코어)

| | MiniGrid (격자) | Doom (연속, 실제 게임) |
|---|:---:|:---:|
| softlock recall | 1.000 | 1.000 |
| reward_bug recall | 0.200 | **0.650** |
| 코어(스키마/특징/탐지) | 동일 | 동일 (무수정 재사용) |
| 어댑터 | adapters/minigrid | adapters/vizdoom (신규) |

reward_bug가 Doom에서 더 잘 잡히는 이유: Doom 정상 보상이 거의 0으로 일정해
보상 누출이 더 두드러짐. (게임 특성에 따라 특징 신뢰도가 달라진다는 관찰)

## 7. 실행

```bash
pip install vizdoom          # manylinux wheel, 컴파일 불필요
python run.py                # MiniGrid (격자)
python run_vizdoom.py        # 실제 Doom (연속)
```

## 8. 얻은 것 / 다음
- 스키마 + 탐지 모델은 게임 무관하게 완전히 재사용됨을 실제 게임으로 입증.
- 특징은 '공유 코드 + 게임별 스케일 파라미터(meta)'로 일반화.
- 다음: (a) reward 특징 강화로 reward_bug↑, (b) teleport용 ACS 스크립팅,
  (c) 픽셀 기반(비전) 탐지로 확장.
