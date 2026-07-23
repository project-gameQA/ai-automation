"""하드 인바리언트 오라클의 실행 진입점이다.

텔레메트리 파일을 읽어 검출기에 넘기고, 결과를 사람이 읽을 수 있는 형태로 출력한다.
서버를 띄우지 않고 파일 하나를 통째로 분석할 때 쓴다.

기본 출력은 **사건 단위**다. 하드 인바리언트는 매 프레임 판정하므로, 봇이 5초간 바닥
아래로 떨어지면 같은 탐지가 백여 건 반복해서 나온다. 사람이 검토할 단위는 그 백여 건이
아니라 "봇 3이 12:04부터 5.1초간 바닥을 뚫고 떨어졌다"는 사건 하나이므로, 서버와 같은
집계 층(qa/aggregate.py)을 여기서도 쓴다.

--raw 를 붙이면 집계 전 원시 탐지를 그대로 출력한다. 집계가 무엇을 어떻게 묶었는지
대조해 보고 싶을 때 쓴다.

실행 예:
    python run_invariants.py tests/qa_telemetry.jsonl
    python run_invariants.py tests/qa_telemetry.jsonl --raw
"""

import sys  # 커맨드라인 인자를 받기 위해 사용한다.

from qa import config  # 검출 설정값과 생성 함수를 한곳에서 가져온다.
from qa.replay_source import iter_samples_from_jsonl, SkipCounter
from qa.aggregate import PEAK_LABEL  # 극값의 사람이 읽는 이름을 가져온다.

# 경계값·임계값은 qa/config.py 에 있다. 서버(dashboard/server/app.py)도 같은 곳을 보므로,
# 같은 텔레메트리를 CLI 와 서버에 넣으면 같은 판정이 나온다. 예전에는 두 파일에 값이 각각
# 적혀 있어 한쪽만 고치면 결과가 어긋날 수 있었다.


def _report_skipped(skipped) -> None:
    """건너뛴 줄이 있으면 알린다. 조용히 묻히면 데이터가 온전한지 알 수 없다."""
    if skipped.count == 0:
        return
    print(f"\n건너뛴 줄 {skipped.count}개 (줄 번호 {skipped.first_line}~{skipped.last_line})")
    print("  텔레메트리 파일과 세션 사본은 첫 줄이나 마지막 줄이 완결되지 않을 수 있다.")
    print("  게임이 기록하는 도중에 세션이 끝나면 마지막 줄이 절반만 쓰인 상태로 남기 때문이다.")
    print("  몇 줄 수준이면 정상이며, 수가 많다면 파일이 손상됐거나 형식이 다른 것이다.")


def main(path: str, raw: bool = False) -> int:
    """텔레메트리 파일을 처리하고 사건(또는 원시 탐지) 수를 반환한다."""
    checker = config.build_checker()      # 공용 설정으로 검출기를 만든다.
    aggregator = config.build_aggregator()  # 공용 설정으로 집계기를 만든다.

    raw_total = 0     # 집계 전 원시 탐지 수
    last_time = None  # 텔레메트리의 마지막 게임 시간
    skipped = SkipCounter()  # 파싱에 실패해 건너뛴 줄을 센다.

    for sample in iter_samples_from_jsonl(path, on_skip=skipped):  # 텔레메트리를 한 틱씩 읽는다.
        last_time = sample.time
        for bug in checker.check(sample):         # 그 틱에서 나온 버그들을 순회한다.
            raw_total += 1
            if raw:  # --raw 모드에서는 탐지를 그대로 한 줄씩 출력한다.
                print(
                    f"[{bug.severity.value.upper():6}] tick={bug.tick:<5} t={bug.time:6.2f}s "
                    f"entity={bug.entity_id} {bug.rule}: {bug.message}"
                )
            aggregator.feed(bug, sample)  # 집계기에도 넣는다.

    aggregator.finalize(last_time)  # 스트림 끝을 알려 남은 사건의 진행 중 여부를 확정한다.
    events = aggregator.events()

    _report_skipped(skipped)

    if raw:  # 원시 모드에서는 요약만 덧붙이고 끝낸다.
        print(f"\n원시 탐지 {raw_total}건 (집계하면 {len(events)}개 사건)")
        return raw_total

    for e in events:  # 사건을 한 줄씩 출력한다.
        mark = " [진행 중]" if e.ongoing else ""  # 파일이 사건 도중에 끝난 경우를 표시한다.
        peak = ""
        if e.peak_value is not None:  # 극값이 정의된 규칙이면 함께 보여준다.
            peak = f" {PEAK_LABEL.get(e.rule, '극값')}={e.peak_value:.1f}"
        print(
            f"[{e.severity:6}] entity={e.entity_id} {e.rule:20} "
            f"{e.start_time:7.2f}~{e.end_time:7.2f}s ({e.duration:5.2f}s, {e.hits:4}프레임)"
            f"{peak}{mark}"
        )
        print(f"          {e.message}")  # 설명은 들여쓴 다음 줄에 둔다.

    print(f"\n원시 탐지 {raw_total}건을 {len(events)}개 사건으로 묶었다.")
    return len(events)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]  # 옵션이 아닌 인자만 고른다.
    raw_mode = "--raw" in sys.argv                              # 원시 출력 여부를 판단한다.
    telemetry_path = args[0] if args else "tests/sample_telemetry.jsonl"  # 경로가 없으면 샘플을 쓴다.
    main(telemetry_path, raw=raw_mode)
