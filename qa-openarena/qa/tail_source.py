"""텔레메트리 파일의 끝을 따라가며 새로 붙은 줄만 읽는 소스다.

replay_source.py 는 파일을 처음부터 끝까지 한 번 읽는 소스이고, 이 모듈은 "어디까지
읽었는지 기억했다가 그 뒤부터만 읽는" 소스다. 정적 분석과 실시간 감시라는 다른 용도이므로
파일을 나눴다. 검출기와 집계기는 둘 중 무엇이 상태를 흘려보내는지 알 필요가 없다.

실시간에서 파일을 읽을 때 반드시 처리해야 하는 문제가 두 가지 있고, 이 모듈이 그것을 맡는다.

1) 반쯤 쓰인 줄
   게임은 계속 파일에 덧붙이고 있으므로, 읽는 순간 마지막 줄이 절반만 쓰여 있을 수 있다.
   그대로 파싱하면 예외가 난다. 그래서 마지막 개행까지만 소비하고, 남은 조각은 버퍼에
   두었다가 다음 읽기에서 앞에 이어 붙인다.

2) 게임 재시작
   새 매치를 시작하면 텔레메트리 파일이 잘리거나 새로 만들어진다. 그때 예전 오프셋으로
   읽으면 엉뚱한 위치를 읽는다. 파일 크기가 기억하던 오프셋보다 작아지면 파일이 초기화된
   것으로 보고 처음부터 다시 읽으며, 그 사실을 호출한 쪽에 알린다(검출기와 집계기의 상태도
   함께 버려야 하기 때문이다).

파일을 텍스트가 아니라 바이트로 읽는 이유도 (1) 때문이다. 텍스트 모드에서는 여러 바이트로
이뤄진 문자가 중간에 잘렸을 때 디코딩이 실패하는데, 바이트 버퍼에 담아 두면 완결된 줄만
디코딩하므로 그 문제가 생기지 않는다.
"""

from __future__ import annotations

import json  # 각 줄의 JSON 문자열을 파이썬 객체로 변환하기 위해 사용한다.
import os    # 파일 크기를 조회해 초기화 여부를 판단하기 위해 사용한다.
from dataclasses import dataclass, field

from .telemetry import StateSample  # 파싱한 딕셔너리를 담을 대상 데이터 구조다.


@dataclass
class PollResult:
    """한 번의 읽기 결과를 담는다.

    값이 세 가지나 되므로 튜플 대신 이름 있는 구조로 돌려준다. 특히 raw 를 함께 돌려주는
    이유가 중요하다. 호출한 쪽(서버)이 읽은 바이트를 세션 폴더에 그대로 복사해 두기 때문이다.
    이 모듈이 직접 복사하지 않고 바이트만 넘기는 것은, 파일을 읽는 책임과 쓰는 책임을 섞지
    않기 위해서다. 그리고 실질적인 이유가 하나 더 있다. 파일이 초기화된 경우 이번에 읽은
    바이트는 '새 매치'의 것이므로 새 세션 폴더에 들어가야 하는데, 그 판단은 세션을 관리하는
    서버만 할 수 있다.
    """

    samples: list = field(default_factory=list)  # 이번에 새로 읽어 파싱한 상태들
    restarted: bool = False                      # 파일이 초기화(게임 재시작)되었는지
    raw: bytes = b""                             # 이번에 읽은 원본 바이트(그대로 보존용)


class TailSource:
    """텔레메트리 파일 끝을 따라가며 새 상태만 내보내는 소스다."""

    def __init__(self, path: str) -> None:
        self.path = path        # 감시할 텔레메트리 파일 경로
        self._offset = 0        # 지금까지 읽은 바이트 위치. 다음 읽기는 여기서부터 시작한다.
        self._buffer = b""      # 개행으로 끝나지 않은 마지막 조각을 담아 두는 버퍼
        self.skipped = 0        # 파싱에 실패해 건너뛴 줄 수(집계에서 제외되므로 세어 둔다)

    def reset(self) -> None:
        """읽기 위치와 버퍼를 처음 상태로 되돌린다."""
        self._offset = 0
        self._buffer = b""

    @property
    def pending(self) -> bytes:
        """아직 개행을 만나지 못해 보류 중인 줄 조각이다.

        세션을 바꿀 때 필요하다. 이 조각은 **이전 세션의 사본에 이미 기록돼 있고**, 다음 읽기는
        이 줄의 중간부터 시작한다. 그대로 두면 새 세션 사본의 첫 줄이 조각난 채로 시작해
        오프라인 도구가 그 줄을 읽지 못한다. 새 사본을 이 조각으로 시작하면 첫 줄이 온전해진다.
        몇 바이트가 두 사본에 겹치지만, 그래야 각 파일이 독립적으로 읽힌다.
        """
        return self._buffer

    def poll(self) -> PollResult:
        """마지막 호출 이후 새로 붙은 상태들을 읽어 반환한다.

        restarted 가 참이면 게임이 재시작된 것이므로, 호출한 쪽은 검출기와 집계기도 새로
        만들어야 한다. 이때 함께 반환되는 상태와 바이트는 이미 새 파일의 처음부터 읽은 것이다.

        제너레이터가 아니라 리스트를 반환하는 이유는, 한 번에 읽는 양이 폴링 간격만큼으로
        제한되어 있어(1초에 수십 줄) 목록으로 들고 있어도 부담이 없고, 호출한 쪽에서
        '이번에 몇 줄 들어왔는지'를 바로 알 수 있어야 하기 때문이다.
        """
        restarted = False

        if not os.path.exists(self.path):  # 파일이 아직 없으면(게임을 안 켰거나 경로가 틀림)
            if self._offset != 0:          # 이전에 읽던 파일이 사라진 경우라면
                self.reset()               # 상태를 버리고
                restarted = True           # 초기화된 것으로 알린다.
            return PollResult(restarted=restarted)  # 읽을 것이 없다.

        size = os.path.getsize(self.path)  # 현재 파일 크기를 조회한다.
        if size < self._offset:            # 기억하던 위치보다 파일이 작아졌으면 잘린 것이다.
            self.reset()                   # 처음부터 다시 읽도록 되돌리고
            restarted = True               # 호출한 쪽에 알린다.

        with open(self.path, "rb") as f:   # 바이트 모드로 연다(잘린 문자 문제를 피하기 위함).
            f.seek(self._offset)           # 지난번에 멈춘 위치로 이동한다.
            chunk = f.read()               # 그 뒤로 붙은 내용을 전부 읽는다.
            self._offset = f.tell()        # 다음 읽기를 위해 현재 위치를 기억한다.

        if not chunk and not self._buffer:  # 새로 붙은 것도, 남은 조각도 없으면
            return PollResult(restarted=restarted)  # 할 일이 없다.

        data = self._buffer + chunk         # 지난번에 남긴 조각 앞에 이어 붙인다.
        parts = data.split(b"\n")           # 개행으로 자른다.
        self._buffer = parts.pop()          # 마지막 조각은 개행으로 끝나지 않았을 수 있으므로 남겨 둔다.
        # (파일이 개행으로 끝났다면 마지막 조각은 빈 바이트열이라 버퍼가 비게 된다.)

        samples: list[StateSample] = []
        for raw in parts:                   # 완결된 줄만 순회한다.
            line = raw.strip()              # 앞뒤 공백과 캐리지 리턴을 제거한다.
            if not line:                    # 빈 줄이면
                continue                    # 건너뛴다.
            try:
                record = json.loads(line.decode("utf-8"))  # 완결된 줄이므로 안전하게 디코딩·파싱한다.
                samples.append(StateSample(**record))      # 키를 인자로 풀어 상태 객체를 만든다.
            except Exception:
                # 게임이 비정상 종료하며 깨진 줄을 남기는 경우가 있다. 한 줄 때문에 전체 감시가
                # 멈추면 안 되므로 세어만 두고 넘어간다. 숫자는 API 로 노출해 조용히 묻히지 않게 한다.
                self.skipped += 1

        # raw 에는 이번에 읽은 바이트를 가공 없이 그대로 담는다. 절반만 쓰인 줄이 섞여 있어도
        # 손대지 않는다. 나머지 절반은 다음 읽기에서 이어 붙으므로, 이대로 복사해 두면
        # 사본이 원본과 바이트 단위로 같아진다.
        return PollResult(samples=samples, restarted=restarted, raw=chunk)
