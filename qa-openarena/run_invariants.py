"""하드 인바리언트 오라클의 실행 진입점이다.

리플레이 소스에서 텔레메트리를 읽어 InvariantChecker에 넘기고, 검출된 버그를
표준 출력에 사람이 읽을 수 있는 형태로 출력한다. 이 파일은 파이프라인을 조립하는
자리다. 향후 소스를 실제 게임 스트림으로, 출력 부분을 집계 층/대시보드 전송으로
교체하면 검출 로직 자체는 바꾸지 않아도 된다.

실행 예: python run_invariants.py tests/sample_telemetry.jsonl
"""

import sys  # 커맨드라인 인자로 텔레메트리 파일 경로를 받기 위해 사용한다.

from qa.telemetry import MapBounds  # 검출기에 넘길 맵 경계 값을 만들기 위해 가져온다.
from qa.invariants import InvariantChecker  # 하드 인바리언트 검출기를 가져온다.
from qa.replay_source import iter_samples_from_jsonl  # 텔레메트리 소스를 가져온다.


def main(path: str) -> int:
    """텔레메트리 파일을 처리하고 검출한 버그 개수를 반환한다."""
    # 데모용 맵 경계를 정의한다. 실제로는 맵마다 다른 값을 계측/설정에서 주입한다.
    # bounds = MapBounds(min_x=-1000, max_x=1000, min_y=-1000, max_y=1000, floor_z=0, ceiling_z=800)

    bounds = MapBounds(
        min_x=-852, max_x=1721,
        min_y=-483, max_y=2097,
        floor_z=-29, ceiling_z=622,
    )
    
    # 검출기를 생성한다. 임계값들은 예시/게임 규칙에 맞춘 값이다.
    # max_possible_health=250: OpenArena의 정당한 체력 상한(약 200, 2*handicap)보다 넉넉히 위,
    # 주입 오버플로우(999)보다는 아래로 잡아 정상 오버힐은 통과시키고 진짜 오버플로우만 잡는다.
    checker = InvariantChecker(
        bounds=bounds, max_speed=1214, stuck_seconds=2.0, stuck_epsilon=2.0, max_possible_health=250
    )

    total = 0  # 지금까지 검출한 버그 개수를 누적할 변수다.
    for sample in iter_samples_from_jsonl(path):  # 소스가 흘려보내는 상태를 하나씩 받는다.
        bugs = checker.check(sample)  # 한 틱의 상태를 검출기에 넘겨 버그 리스트를 돌려받는다.
        for bug in bugs:              # 이번 틱에서 나온 버그들을 순회한다.
            total += 1                # 누적 개수를 늘린다.
            # 심각도, 틱, 시간, 엔티티, 규칙, 메시지를 한 줄로 정렬해 출력한다.
            print(
                f"[{bug.severity.value.upper():6}] tick={bug.tick:<4} t={bug.time:5.2f}s "
                f"entity={bug.entity_id} {bug.rule}: {bug.message}"
            )
    print(f"\n총 {total}건의 버그를 검출했다.")  # 처리가 끝나면 요약을 출력한다.
    return total  # 호출한 쪽이 결과 개수를 활용할 수 있도록 반환한다.


if __name__ == "__main__":  # 이 파일이 스크립트로 직접 실행될 때만 아래를 수행한다.
    # 인자로 경로가 주어지면 그 파일을, 아니면 기본 샘플 파일을 사용한다.
    telemetry_path = sys.argv[1] if len(sys.argv) > 1 else "tests/sample_telemetry.jsonl"
    main(telemetry_path)  # 진입 함수를 호출한다.
