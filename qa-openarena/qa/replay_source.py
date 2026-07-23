"""리플레이용 텔레메트리 소스를 정의하는 모듈이다.

JSON Lines(.jsonl) 파일에 저장된 텔레메트리를 읽어 StateSample 스트림으로 변환한다.
파일의 각 줄은 하나의 StateSample에 대응하는 JSON 객체다.

이 소스는 파일을 처음부터 끝까지 한 번 읽는다. 파일의 끝을 따라가며 새 줄만 읽는 실시간
소스는 tail_source.py 에 따로 있다. 용도가 달라 파일을 나눴다.

깨진 줄 처리(2026-07-23 수정):
이전에는 파싱에 실패하면 예외를 던져 처리 전체가 멈췄다. 그런데 실제 텔레메트리와 세션
사본에는 완결되지 않은 줄이 정상적으로 섞인다.
- 게임이 기록하는 중에 세션이 끝나면 마지막 줄이 절반만 쓰인 상태로 남는다.
- 세션 사본은 읽은 바이트를 그대로 복사하므로 그 상태가 사본에도 반영된다.
tail_source.py 는 이런 줄을 건너뛰고 세는데, 이쪽만 예외를 던지면 같은 데이터를 실시간
경로는 견디고 오프라인 경로는 못 견디게 된다. 그래서 동작을 맞췄다.

건너뛴 줄을 조용히 묻지 않기 위해 on_skip 콜백으로 알린다. 도구는 이 수를 화면에 표시한다.
"""

import json  # 각 줄의 JSON 문자열을 파이썬 객체로 변환하기 위해 표준 json 모듈을 사용한다.
from typing import Callable, Iterator, Optional  # 제너레이터와 콜백을 타입으로 표현하기 위해 사용한다.

from .telemetry import StateSample  # 파싱한 딕셔너리를 담을 대상 데이터 구조를 가져온다.


def iter_samples_from_jsonl(
    path: str,
    on_skip: Optional[Callable[[int, str], None]] = None,
) -> Iterator[StateSample]:
    """JSONL 파일 경로를 받아 StateSample을 하나씩 생성하는 제너레이터다.

    on_skip 을 주면 파싱에 실패한 줄마다 (줄 번호, 줄 내용)으로 호출한다. 주지 않으면
    조용히 건너뛴다. 도구에서는 항상 넘겨 건너뛴 수를 사용자에게 알리는 것이 바람직하다.
    """
    # errors="replace" 로 여는 이유: 사본의 첫 줄이나 마지막 줄이 여러 바이트로 이뤄진 문자의
    # 중간에서 잘려 있을 수 있다. 그 경우 엄격한 디코딩은 파일 전체를 열지 못하게 만든다.
    # 어차피 그 줄은 아래에서 파싱에 실패해 건너뛰게 되므로, 여는 단계에서 막을 이유가 없다.
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line_no, line in enumerate(f, start=1):  # 파일을 한 줄씩 순회한다. 한 줄이 한 틱의 상태다.
            line = line.strip()     # 줄 끝 개행과 앞뒤 공백을 제거한다.
            if not line:            # 내용이 없는 빈 줄이면
                continue            # 건너뛴다.
            try:
                record = json.loads(line)    # JSON 문자열을 파이썬 딕셔너리로 파싱한다.
                sample = StateSample(**record)  # 키를 키워드 인자로 풀어 StateSample을 만든다.
            except Exception:
                # 완결되지 않은 줄이거나 필드가 어긋난 줄이다. 한 줄 때문에 전체 처리가
                # 멈추면 안 되므로 건너뛰되, 호출한 쪽에는 알린다.
                if on_skip is not None:
                    on_skip(line_no, line)
                continue
            yield sample  # 완성된 상태를 흘려보낸다.


class SkipCounter:
    """건너뛴 줄을 세는 작은 도우미다.

    제너레이터는 반환값으로 통계를 넘길 수 없으므로, 호출하는 쪽이 이 객체를 만들어
    on_skip 으로 넘기고 처리가 끝난 뒤 count 를 읽는다.
    """

    def __init__(self) -> None:
        self.count = 0
        self.first_line: Optional[int] = None  # 처음 건너뛴 줄 번호. 원인 파악에 쓴다.
        self.last_line: Optional[int] = None   # 마지막으로 건너뛴 줄 번호.

    def __call__(self, line_no: int, line: str) -> None:
        self.count += 1
        if self.first_line is None:
            self.first_line = line_no
        self.last_line = line_no
