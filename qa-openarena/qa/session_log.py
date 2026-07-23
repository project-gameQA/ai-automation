"""버그 사건을 파일로 남기는 세션 기록기다.

서버는 실시간 감시 중 사건을 메모리에 들고 있지만, 프로세스가 죽으면 전부 사라진다.
이 프로젝트가 잡으려는 대상 중에 크래시와 행이 있는데(성능/크래시 워치독 오라클) 정작
기록 시스템이 크래시에 다 날아가면 앞뒤가 맞지 않는다. 그래서 사건을 디스크에도 남긴다.

세션이 끝날 때 한 번에 덤프하지 않고 **사건이 닫힐 때마다 한 줄씩 덧붙이는** 방식을 쓴다.
이유는 두 가지다.
- 크래시 내성: 프로세스가 언제 죽어도 그때까지 닫힌 사건은 파일에 남는다. 손실은 마지막
  한 줄 이하다. 배열 하나로 감싼 JSON이라면 닫는 괄호를 못 써서 파일 전체가 깨진다.
- 비용: 덧붙이기는 이미 쓴 내용을 다시 쓰지 않는다. 사건이 늘어도 쓰기 비용이 늘지 않는다.

형식은 JSON Lines다. 텔레메트리가 이미 같은 형식이라 도구를 공유할 수 있고, 한 줄이 깨져도
나머지 줄은 그대로 읽힌다.

파일은 세 종류가 만들어진다.
- `session_<시각>.jsonl`            : 사건 목록(한 줄에 하나)
- `session_<시각>.summary.json`     : 세션 요약과 그때 사용한 검출 설정값
- `session_<시각>.telemetry.jsonl`  : 그 세션 동안 읽은 텔레메트리 원본 사본

원본 사본을 남기는 이유:
게임은 맵이 로드될 때마다 텔레메트리 파일을 FS_WRITE 로 새로 열어 이전 내용을 지운다.
덕분에 파일이 무한히 커지지 않지만, 반대로 맵을 바꾸는 순간 직전 매치의 원본이 사라진다.
사건 기록만 남기면 "그때 왜 이렇게 판정했는가"를 나중에 다시 따져볼 수 없다. 임계값을
조정하고 같은 매치를 재분석하는 일이 불가능해진다.

복사 시점이 중요하다. 맵 전환은 '파일이 작아졌다'로 사후에 감지하므로, 그때 복사하려 하면
원본은 이미 지워진 뒤다. 그래서 세션 종료 시 복사하지 않고, 서버가 새 바이트를 읽을 때마다
그 바이트를 여기에 그대로 덧붙인다. 어차피 읽고 있는 내용이라 추가 비용이 거의 없고,
맵이 언제 갑자기 전환돼도 그 직전까지가 이미 디스크에 있다.

요약에 설정값을 함께 남기는 이유는 재현성 때문이다. "이 결과는 어떤 경계값과 임계값으로
나온 것인가"를 나중에 알 수 없으면 기록의 근거가 사라진다.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional


class SessionLog:
    """한 세션 동안의 버그 사건을 파일에 기록한다."""

    def __init__(self, directory: str, session_id: Optional[str] = None,
                 archive_telemetry: bool = True) -> None:
        # 세션 식별자는 시작 시각으로 만든다. 사람이 파일 이름만 보고 언제 것인지 알 수 있어야 한다.
        self.session_id = session_id or time.strftime("%Y%m%d_%H%M%S")
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)  # 폴더가 없으면 만든다.

        self.events_path = self.directory / f"session_{self.session_id}.jsonl"
        self.summary_path = self.directory / f"session_{self.session_id}.summary.json"
        self.telemetry_path = self.directory / f"session_{self.session_id}.telemetry.jsonl"

        self.started_at = time.time()  # 세션이 시작된 실제 시각(요약에 남긴다)
        self.written = 0               # 지금까지 파일에 쓴 사건 수
        self.archived_bytes = 0        # 지금까지 사본에 쓴 텔레메트리 바이트 수
        self.archive_telemetry = archive_telemetry
        # 덧붙이기 모드로 열어 둔다. 매번 열고 닫으면 사건이 잦을 때 비용이 커진다.
        self._file = open(self.events_path, "a", encoding="utf-8")
        # 텔레메트리 사본은 첫 바이트가 들어올 때 연다. 미리 열면 데이터가 한 줄도 없는
        # 세션에도 0바이트 파일이 생겨 폴더가 지저분해진다.
        self._telemetry_file = None

    def archive(self, chunk: bytes) -> None:
        """서버가 읽은 텔레메트리 바이트를 사본에 그대로 덧붙인다.

        가공하지 않는 것이 핵심이다. 절반만 쓰인 줄이 섞여 있어도 손대지 않는다. 나머지
        절반은 다음 호출에서 이어 붙으므로, 결과적으로 사본은 원본과 바이트 단위로 같아진다.
        파싱해서 다시 쓰는 방식이면 숫자 표기가 미묘하게 달라져 사본이 원본이 아니게 된다.
        """
        if not self.archive_telemetry or not chunk:
            return
        if self._telemetry_file is None:  # 첫 바이트가 들어온 시점에 파일을 연다.
            self._telemetry_file = open(self.telemetry_path, "ab")
        self._telemetry_file.write(chunk)
        # 사건과 달리 텔레메트리는 초당 수십 KB가 흐르므로 매번 flush 하면 낭비다.
        # 파일을 닫을 때와 운영체제의 버퍼 정책에 맡기고, 여기서는 쓰기만 한다.
        self.archived_bytes += len(chunk)

    def append_event(self, event) -> None:
        """닫힌 사건 하나를 파일에 한 줄로 덧붙인다.

        집계기의 on_close 콜백으로 연결해 쓴다. 집계기는 이 함수가 무엇을 하는지 모른 채
        "사건이 닫혔다"는 사실만 알린다.
        """
        record = event.to_dict()          # 사건을 JSON 친화적 형태로 바꾼다.
        record["session_id"] = self.session_id  # 나중에 여러 세션 파일을 합쳐도 출처를 알 수 있게 한다.
        self._file.write(json.dumps(record, ensure_ascii=False) + "\n")
        # 파이썬 버퍼에만 있으면 프로세스가 죽을 때 사라진다. 즉시 운영체제로 내보낸다.
        # 사건은 프레임과 달리 드물게 발생하므로 매번 flush 해도 비용이 문제되지 않는다.
        self._file.flush()
        self.written += 1

    def flush_open_events(self, open_events) -> int:
        """아직 닫히지 않은(진행 중인) 사건들을 파일에 남긴다.

        세션을 끝낼 때 호출한다. 진행 중이던 사건도 기록에 남아야 하며, ongoing 표시가
        붙어 있으므로 "끝을 못 본 사건"임을 나중에 구분할 수 있다.
        """
        count = 0
        for event in open_events:
            self.append_event(event)
            count += 1
        return count

    def write_summary(self, summary: dict) -> None:
        """세션 요약을 별도 파일에 쓴다. 세션당 한 번만 쓰므로 일반 JSON으로 남긴다."""
        payload = dict(summary)                       # 호출한 쪽의 딕셔너리를 건드리지 않도록 복사한다.
        payload["session_id"] = self.session_id
        payload["events_file"] = self.events_path.name
        # 사본이 실제로 만들어졌을 때만 요약에 적는다. 없는 파일을 가리키면 안 된다.
        payload["telemetry_file"] = self.telemetry_path.name if self._telemetry_file else None
        payload["telemetry_bytes"] = self.archived_bytes
        payload["started_at"] = self.started_at
        payload["ended_at"] = time.time()
        payload["events_written"] = self.written
        self.summary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def close(self) -> None:
        """열어 둔 파일들을 닫는다. 닫는 시점에 버퍼가 비워져 사본이 온전해진다."""
        if not self._file.closed:
            self._file.close()
        if self._telemetry_file is not None and not self._telemetry_file.closed:
            self._telemetry_file.close()
