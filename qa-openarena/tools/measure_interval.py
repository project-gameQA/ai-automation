"""텔레메트리의 실제 프레임 간격을 재고, 기록이 끊긴 구간을 찾는 도구다.

무엇을 재는가:
게임은 `ClientEndFrame`에서 봇마다 한 줄씩 기록한다. 따라서 **같은 봇의 연속된 두 줄
사이 time 차이**가 곧 서버 프레임 주기다. 이 값을 알아야 집계 층의 gap_seconds 를
근거 있는 값으로 정할 수 있다.

왜 필요한가:
현재 gap_seconds 는 0.5초이고, 그 근거는 "서버가 20Hz면 프레임 주기가 0.05초이니
0.5초는 약 10프레임"이라는 **가정**이다. 관측에서 역산한 값이 아니다. 맵 경계를 실제
플레이에서 보정했던 것처럼 이 값도 실측으로 뒷받침하는 편이 낫다.

무엇을 더 보는가:
프레임 주기보다 훨씬 큰 공백이 있으면 그 구간은 기록이 끊긴 것이다. 봇이 죽어 있는
동안이 대표적이다. 이 공백이 gap_seconds 보다 크면 **하나의 버그 사건이 둘로 갈라진다.**
따라서 공백의 크기와 빈도가 gap_seconds 를 정하는 실질적인 기준이 된다.

주의: 결함 주입을 켠 채 뽑은 텔레메트리로 재도 프레임 간격 자체는 왜곡되지 않는다.
주입기는 상태값을 바꿀 뿐 기록 주기를 바꾸지 않기 때문이다. 다만 바닥 관통 주입은
봇을 무한 낙하시켜 기록이 비정상적으로 길어지므로, 가급적 정상 플레이로 재는 편이 낫다.

실행 예:
    python tools/measure_interval.py sessions/session_20260723_120000.telemetry.jsonl
"""

import json          # 텔레메트리 각 줄을 파싱하기 위해 사용한다.
import statistics    # 중앙값을 구하기 위해 사용한다.
import sys           # 커맨드라인 인자로 파일 경로를 받기 위해 사용한다.
from collections import Counter, defaultdict  # 간격 분포와 봇별 이력을 담기 위해 사용한다.

# 프레임 주기의 몇 배를 넘으면 '기록이 끊긴 공백'으로 볼지 정하는 배수다.
# 3배로 잡은 이유: 서버 프레임이 한두 번 밀리는 것은 정상 범위의 흔들림이지만,
# 세 프레임 이상 통째로 비었다면 기록 자체가 멈춘 것으로 보는 편이 타당하다.
GAP_FACTOR = 3

# 권장 gap_seconds 를 계산할 때 정상 흔들림 위로 얼마나 여유를 둘지 정하는 배수다.
# 관측된 정상 간격의 최댓값에 이만큼 곱해, 흔들림은 묶고 진짜 공백은 가르는 선을 만든다.
MARGIN_FACTOR = 2.0


def load_times(path):
    """파일을 읽어 봇별로 (시각) 목록을 만든다.

    반환값은 {entity_id: [time, time, ...]} 형태이며, 각 목록은 파일에 기록된 순서를
    그대로 따른다. 텔레메트리는 시간 순으로 append 되므로 별도 정렬이 필요 없다.
    """
    per_bot = defaultdict(list)  # 봇 번호를 키로, 그 봇이 기록된 시각 목록을 값으로 담는다.
    total = 0                    # 읽은 줄 수
    broken = 0                   # 파싱에 실패한 줄 수

    with open(path, "r", encoding="utf-8") as f:
        for line in f:                    # 한 줄이 한 봇의 한 프레임이다.
            line = line.strip()           # 개행과 앞뒤 공백을 제거한다.
            if not line:                  # 빈 줄은 건너뛴다.
                continue
            total += 1
            try:
                record = json.loads(line)                      # JSON 한 줄을 딕셔너리로 만든다.
                per_bot[record["entity_id"]].append(record["time"])  # 봇별 시각 목록에 넣는다.
            except Exception:
                # 게임이 비정상 종료하며 남긴 깨진 줄이 있을 수 있다. 세어만 두고 넘어간다.
                broken += 1

    return per_bot, total, broken


def main(path):
    """텔레메트리 파일 하나를 분석해 결과를 출력한다."""
    per_bot, total, broken = load_times(path)

    if not per_bot:  # 읽을 내용이 없으면 더 진행할 수 없다.
        print("텔레메트리를 읽지 못했다. 파일이 비었거나 형식이 다르다.")
        return 1

    # 봇별로 연속된 두 기록 사이의 간격을 모두 구한다.
    # 봇을 나눠서 보는 이유: 한 서버 프레임에 봇 여러 마리가 연달아 기록되므로,
    # 봇을 섞어서 보면 같은 프레임 안의 줄 사이 간격(0초)이 섞여 들어와 값이 망가진다.
    intervals = []                  # 모든 간격을 모은 목록
    per_bot_gaps = defaultdict(list)  # 봇별 (공백 시작 시각, 공백 크기) 목록
    for bot, times in per_bot.items():
        for i in range(1, len(times)):
            delta = round(times[i] - times[i - 1], 3)  # 부동소수 오차를 밀리초 단위로 정리한다.
            if delta <= 0:   # 같은 시각에 두 번 기록된 경우다. 간격으로 볼 수 없다.
                continue
            intervals.append(delta)
            per_bot_gaps[bot].append((times[i - 1], delta))

    if not intervals:  # 봇마다 기록이 한 줄뿐이면 간격을 잴 수 없다.
        print("간격을 잴 수 있는 연속 기록이 없다. 더 긴 텔레메트리가 필요하다.")
        return 1

    counts = Counter(intervals)          # 간격별 등장 횟수
    median = statistics.median(intervals)  # 중앙값. 이상치에 강해 프레임 주기의 대표값으로 적합하다.
    gap_threshold = median * GAP_FACTOR  # 이보다 크면 기록이 끊긴 공백으로 본다.

    # 공백을 제외한 '정상 간격'만 따로 모은다. 권장값 계산의 기준이 된다.
    normal = [d for d in intervals if d <= gap_threshold]
    normal_max = max(normal) if normal else median

    # 공백 목록을 모아 크기 순으로 정렬한다.
    gaps = []
    for bot, items in per_bot_gaps.items():
        for at, delta in items:
            if delta > gap_threshold:
                gaps.append((delta, bot, at))
    gaps.sort(reverse=True)

    # ── 출력 ────────────────────────────────────────────────────────────────
    all_times = [t for times in per_bot.values() for t in times]
    span = max(all_times) - min(all_times)

    print()
    print("=" * 62)
    print(f"  텔레메트리 프레임 간격 측정: {path}")
    print("=" * 62)
    print(f"  총 줄 수      {total:,}" + (f"  (깨진 줄 {broken})" if broken else ""))
    print(f"  봇 수         {len(per_bot)}  (번호: {', '.join(str(b) for b in sorted(per_bot))})")
    print(f"  기록 구간     {min(all_times):.2f} ~ {max(all_times):.2f}초  ({span:.1f}초)")
    print()

    print("  간격 분포 (많이 나온 순)")
    for delta, n in counts.most_common(8):
        share = n / len(intervals) * 100
        bar = "#" * max(1, int(share / 2))  # 비율을 막대로 표시한다. 2%당 한 칸이다.
        mark = "  <- 공백" if delta > gap_threshold else ""
        print(f"    {delta:7.3f}초  {n:7,}회  {share:5.1f}%  {bar}{mark}")
    print()

    # 중앙값에서 초당 프레임 수를 역산한다. 이 값이 sv_fps 와 맞는지 보면 계측이 정상인지 알 수 있다.
    fps = 1 / median if median > 0 else 0
    print(f"  중앙값(프레임 주기)  {median:.3f}초   →  초당 약 {fps:.1f}프레임")
    print(f"  정상 간격 최댓값     {normal_max:.3f}초   (공백 판정선 {gap_threshold:.3f}초 이하 중)")
    print()

    if gaps:
        print(f"  기록이 끊긴 구간 {len(gaps)}건 (프레임 주기의 {GAP_FACTOR}배 초과)")
        print("    봇이 죽어 있는 동안일 가능성이 높다. 이 크기가 gap_seconds 보다 크면")
        print("    하나의 버그 사건이 둘로 갈라진다.")
        for delta, bot, at in gaps[:8]:
            print(f"    {at:8.2f}초부터  봇 {bot}  {delta:.2f}초 동안 기록 없음")
        if len(gaps) > 8:
            print(f"    ... 외 {len(gaps) - 8}건")
        biggest = gaps[0][0]
    else:
        print("  기록이 끊긴 구간: 없음")
        biggest = 0.0
    print()

    # ── 권장값 ──────────────────────────────────────────────────────────────
    # 정상 흔들림은 묶고 진짜 공백은 가르는 선을 제안한다.
    suggested = round(normal_max * MARGIN_FACTOR, 2)

    print("-" * 62)
    print("  gap_seconds 판단 근거")
    print("-" * 62)
    print(f"  정상 흔들림을 묶으려면 최소   {normal_max:.3f}초 초과")
    if biggest:
        print(f"  가장 큰 공백을 가르려면 최대  {biggest:.2f}초 미만")
        if suggested >= biggest:
            # 두 조건이 충돌하는 경우다. 어느 쪽을 택해도 부작용이 있으므로 사람이 정해야 한다.
            print()
            print("  주의: 두 조건이 충돌한다. 정상 흔들림과 기록 공백의 크기가 겹쳐,")
            print("        어떤 값을 골라도 사건이 갈라지거나 붙는 문제가 남는다.")
            print("        공백이 사망·리스폰이라면 그 구간을 하나로 묶는 것이 오히려")
            print("        부적절할 수 있으니, 갈라지는 쪽을 택하는 편이 나을 수 있다.")
    print()
    print(f"  권장 gap_seconds ≈ {suggested}초")
    print(f"  (현재 설정값 0.5초)")
    print()
    print("  반영할 위치")
    print("    qa/aggregate.py       DEFAULT_GAP_SECONDS")
    print("    dashboard/server/app.py   GAP_SECONDS")
    print("    run_invariants.py         GAP_SECONDS")
    print()
    print("  이 도구는 값을 제안할 뿐 결정하지 않는다. 위 분포와 공백을 보고 판단한다.")
    print()
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:  # 경로를 주지 않으면 사용법만 안내한다.
        print("사용법: python tools/measure_interval.py <텔레메트리 파일>")
        print("예:     python tools/measure_interval.py sessions/session_20260723_120000.telemetry.jsonl")
        sys.exit(1)
    sys.exit(main(sys.argv[1]))
