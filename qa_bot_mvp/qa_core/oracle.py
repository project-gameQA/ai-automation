"""
oracle.py  (코어, 하드 오라클 = 규칙 층)
---------------------------------------
'이동형 게임이면 항상 참이어야 하는 불변식'을 규칙으로 검사한다.
위반이면 100퍼센트 버그로 확정한다(이상탐지처럼 애매하게 판단하지 않는다).

게임 무관: 규칙 로직만 여기 두고, 스케일/경계 값은 Episode.meta 에서 읽어
자기 조정한다. 그래서 격자(MiniGrid)든 연속(ViZDoom)이든 같은 코드가 적용된다.
새 이동형 게임을 붙여도 이 파일은 수정하지 않는다. 어댑터가 meta 에 물리
파라미터만 넣으면 된다(features.py 가 cell_size/move_eps 를 meta 에서 읽던 것과 동일).

이 규칙들은 '봇이 공간을 이동한다'만 전제한다. 게임별 스펙(퀘스트, 아이템 등)은
다루지 않는다. 그런 것은 실제 서비스에서 개발자가 코드에 심는 assertion 의 몫이며,
여기서 다루는 것은 어떤 이동형 게임에나 공통인 최소 불변식뿐이다.

meta 에서 읽는 파라미터(없으면 해당 검사는 건너뜀):
  - max_step_dist : 한 스텝에 물리적으로 가능한 최대 이동거리(맨해튼).
                    이보다 멀면 관통/순간이동 버그. (격자=1, 연속=엔진 최대)
  - map_bounds    : (xmin, xmax, ymin, ymax). 이 밖의 좌표는 맵 이탈/좌표 붕괴.
  - freeze_limit  : 이 스텝 수 이상 연속 완전 정지면 끼임/진행 불가.
"""
import math


def _positions(ep):
    """init_pos + 각 스텝의 pos 를 순서대로. (features.py 와 동일한 복원 방식)"""
    init = ep.meta.get("init_pos")
    seq = []
    if init is not None:
        seq.append(tuple(float(v) for v in init))
    for s in ep.steps:
        seq.append(tuple(float(v) for v in s.pos))
    return seq


def _manhattan(a, b):
    return sum(abs(a[i] - b[i]) for i in range(min(len(a), len(b))))


def check_invariants(ep):
    """
    Episode 하나를 검사해 위반 목록을 반환한다.
    각 원소는 {"type": 위반이름, "step": 발생 스텝} 형태로, '어느 스텝에서'까지 짚는다.
    (stuck 은 연속 구간이므로 "step"=시작, "end"=끝 도 포함)
    비어 있으면 규칙상 정상. 규칙은 정상 플레이에서는 절대 발동하면 안 된다(오탐 0 원칙).

    스텝 번호 기준: pos[0]=시작위치(스텝 실행 전), pos[i+1]=스텝 i 실행 후.
    따라서 pos[i]->pos[i+1] 사이 위반은 '스텝 i'로 본다.
    """
    violations = []
    pos = _positions(ep)
    if not pos:
        return violations

    # (1) invalid_coord: 좌표에 NaN/inf. 첫 발생 스텝을 기록.
    for j, p in enumerate(pos):
        if any(math.isnan(v) or math.isinf(v) for v in p):
            violations.append({"type": "invalid_coord", "step": max(0, j - 1)})
            break

    # (2) teleport: 한 스텝 이동거리가 물리적 최대 초과. 첫 발생 스텝.
    max_step = ep.meta.get("max_step_dist")
    if max_step is not None:
        for i in range(len(pos) - 1):
            if _manhattan(pos[i], pos[i + 1]) > max_step:
                violations.append({"type": "teleport", "step": i})
                break

    # (3) out_of_bounds: 맵 경계 밖 좌표. 첫 발생 스텝.
    bounds = ep.meta.get("map_bounds")
    if bounds is not None:
        xmin, xmax, ymin, ymax = bounds
        for j, p in enumerate(pos):
            if not (xmin <= p[0] <= xmax and ymin <= p[1] <= ymax):
                violations.append({"type": "out_of_bounds", "step": max(0, j - 1)})
                break

    # (4) stuck: freeze_limit 스텝 이상 연속 정지. 정지 구간의 시작~끝 스텝.
    freeze_limit = ep.meta.get("freeze_limit")
    if freeze_limit is not None and len(pos) >= 2:
        move_eps = ep.meta.get("move_eps", 0.0)
        streak = 0
        start = 0
        for i in range(len(pos) - 1):
            if _manhattan(pos[i], pos[i + 1]) <= move_eps:
                if streak == 0:
                    start = i
                streak += 1
                if streak >= freeze_limit:
                    violations.append({"type": "stuck", "step": start, "end": i})
                    break
            else:
                streak = 0

    return violations


def violation_types(violations):
    """위반 목록에서 종류 이름만 뽑는다(요약/카운트용)."""
    return [v["type"] for v in violations]
