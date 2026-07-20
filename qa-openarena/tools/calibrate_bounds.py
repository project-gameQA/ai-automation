"""텔레메트리에서 실제 맵 경계와 속도 상한을 산출하는 보정 도구다.

검출기의 MapBounds(맵 경계)와 max_speed(속도 상한)에 박아둔 데모용 임시값은
실제 OpenArena 맵과 맞지 않아 정상 플레이를 오탐한다. 이 도구는 정상 플레이
텔레메트리에서 봇 좌표의 최소·최대와 최대 속력을 뽑아, 거기에 여유(margin)를
두어 실제 맵에 맞는 값을 제안한다.

주의: 입력 파일에는 위치를 오염시키는 주입(바닥 관통·경계 이탈 등)이 없어야 한다.
그런 주입이 켜진 구간이 섞이면 경계가 잘못 넓어진다. 현재는 체력만 주입하므로
위치는 모두 정상이지만, 위치 주입기를 추가한 뒤에는 주입을 끈 상태로 수집한
파일을 써야 한다.

실행 예: python tools/calibrate_bounds.py qa_telemetry.jsonl
"""

import json  # 텔레메트리 각 줄의 JSON을 파싱하기 위해 사용한다.
import sys   # 커맨드라인 인자로 파일 경로를 받기 위해 사용한다.


def calibrate(path: str, margin_ratio: float = 0.2) -> None:
    """텔레메트리 파일을 읽어 경계·속도 통계를 계산하고 제안값을 출력한다.

    margin_ratio는 각 축의 관측 범위에 더할 여유 비율이다(기본 20%). 여유를 둬야
    정상 플레이가 경계 안쪽에 편하게 들어오고, 경계 근처의 정상 움직임이 오탐되지
    않는다.
    """
    # 각 축의 최솟값·최댓값을 추적할 변수를 초기화한다. 첫 표본에서 실제 값으로 대체된다.
    min_x = min_y = min_z = float("inf")   # 아직 본 값이 없으므로 양의 무한대로 시작한다.
    max_x = max_y = max_z = float("-inf")  # 아직 본 값이 없으므로 음의 무한대로 시작한다.
    max_speed = 0.0   # 관측된 최대 속력을 담는다.
    count = 0         # 처리한 표본 수를 센다.

    with open(path, "r", encoding="utf-8") as f:  # 텔레메트리 파일을 연다.
        for line in f:                 # 한 줄씩 순회한다. 한 줄이 한 표본이다.
            line = line.strip()        # 앞뒤 공백과 개행을 제거한다.
            if not line:               # 빈 줄이면
                continue               # 건너뛴다.
            r = json.loads(line)       # JSON 문자열을 딕셔너리로 파싱한다.
            count += 1                 # 표본 수를 늘린다.

            min_x = min(min_x, r["x"]); max_x = max(max_x, r["x"])  # X 좌표의 최소·최대를 갱신한다.
            min_y = min(min_y, r["y"]); max_y = max(max_y, r["y"])  # Y 좌표의 최소·최대를 갱신한다.
            min_z = min(min_z, r["z"]); max_z = max(max_z, r["z"])  # Z 좌표의 최소·최대를 갱신한다.

            speed = (r["vx"] ** 2 + r["vy"] ** 2 + r["vz"] ** 2) ** 0.5  # 속도 벡터의 크기(속력)를 계산한다.
            max_speed = max(max_speed, speed)  # 최대 속력을 갱신한다.

    if count == 0:  # 표본이 하나도 없으면(빈 파일 등)
        print("표본이 없다. 봇 매치에서 수집한 텔레메트리인지 확인한다.")  # 안내하고
        return  # 종료한다.

    # 각 축의 관측 범위에 여유를 더해 경계를 넓힌다.
    pad_x = (max_x - min_x) * margin_ratio  # X축 여유 크기
    pad_y = (max_y - min_y) * margin_ratio  # Y축 여유 크기
    pad_z = (max_z - min_z) * margin_ratio  # Z축 여유 크기

    # 여유를 반영한 최종 경계값을 계산한다.
    b_min_x = min_x - pad_x; b_max_x = max_x + pad_x
    b_min_y = min_y - pad_y; b_max_y = max_y + pad_y
    b_floor_z = min_z - pad_z    # 바닥: 관측 최저 Z보다 더 아래로 내려가면 낙하로 본다.
    b_ceiling_z = max_z + pad_z  # 천장: 관측 최고 Z보다 더 위로 올라가면 이상으로 본다.

    # 관측 요약을 출력한다.
    print(f"표본 수: {count}")
    print(f"관측 범위  x=[{min_x:.1f}, {max_x:.1f}]  y=[{min_y:.1f}, {max_y:.1f}]  z=[{min_z:.1f}, {max_z:.1f}]")
    print(f"관측 최대 속력: {max_speed:.1f}")
    print()
    # run_invariants.py 에 그대로 붙여 넣을 수 있는 형태로 제안값을 출력한다.
    print("아래를 run_invariants.py 의 값으로 사용한다 (여유 %.0f%% 반영):" % (margin_ratio * 100))
    print(f"    bounds = MapBounds(")
    print(f"        min_x={b_min_x:.0f}, max_x={b_max_x:.0f},")
    print(f"        min_y={b_min_y:.0f}, max_y={b_max_y:.0f},")
    print(f"        floor_z={b_floor_z:.0f}, ceiling_z={b_ceiling_z:.0f},")
    print(f"    )")
    # 속도 상한은 관측 최대 속력보다 넉넉히 위로 잡아 정상 이동을 통과시킨다.
    print(f"    max_speed = {max_speed * 1.5:.0f}   # 관측 최대 {max_speed:.0f}의 약 1.5배")


if __name__ == "__main__":  # 스크립트로 직접 실행될 때만 아래를 수행한다.
    telemetry_path = sys.argv[1] if len(sys.argv) > 1 else "qa_telemetry.jsonl"  # 인자가 있으면 그 경로를, 없으면 기본값을 쓴다.
    calibrate(telemetry_path)  # 보정을 실행한다.
