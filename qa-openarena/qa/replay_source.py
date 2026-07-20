"""리플레이용 텔레메트리 소스를 정의하는 모듈이다.

실제 게임 계측이 준비되기 전까지, JSON Lines(.jsonl) 파일에 저장된 텔레메트리를
읽어 StateSample 스트림으로 변환한다. 파일의 각 줄은 하나의 StateSample에 대응하는
JSON 객체다. 소스를 파일로 분리해 두면 검출 로직을 게임과 독립적으로 개발하고
테스트할 수 있다. 향후 이 함수를 게임 스트림 리더로 교체하면 나머지 코드는 그대로
동작한다.
"""

import json  # 각 줄의 JSON 문자열을 파이썬 객체로 변환하기 위해 표준 json 모듈을 사용한다.
from typing import Iterator  # 이 함수가 값을 하나씩 흘려보내는 제너레이터임을 타입으로 표현한다.

from .telemetry import StateSample  # 파싱한 딕셔너리를 담을 대상 데이터 구조를 가져온다.


def iter_samples_from_jsonl(path: str) -> Iterator[StateSample]:
    """JSONL 파일 경로를 받아 StateSample을 하나씩 생성하는 제너레이터다."""
    with open(path, "r", encoding="utf-8") as f:  # 파일을 UTF-8 텍스트 모드로 연다. with로 열어 자동으로 닫히게 한다.
        for line in f:              # 파일을 한 줄씩 순회한다. 한 줄이 한 틱의 상태다.
            line = line.strip()     # 줄 끝 개행과 앞뒤 공백을 제거한다.
            if not line:            # 내용이 없는 빈 줄이면
                continue            # 건너뛴다.
            record = json.loads(line)   # JSON 문자열을 파이썬 딕셔너리로 파싱한다.
            yield StateSample(**record)  # 딕셔너리의 키를 키워드 인자로 풀어 StateSample을 만들어 흘려보낸다.
