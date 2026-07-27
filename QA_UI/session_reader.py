"""
session_reader.py
=================
에이전트(GamingAI)가 만든 세션 폴더를 읽어서 UI에 넘겨주는 모듈.

세션 폴더 구조 (GamingAI/gameqa/logger.py가 만듦):
    logs/Maze_Trials_1784807416/
        ├── events.jsonl     한 줄에 JSON 하나. 에이전트의 모든 행동/사건
        ├── perf.csv         2초 간격 성능 샘플 (CPU/메모리/GPU/응답여부)
        ├── screenshots/     000001.png, 000002.png, ...
        └── summary.json     세션 요약 (총 시간, 액션 수, 종료 사유)

두 가지 읽기 모드:
    replay — 이미 끝난 세션을 처음부터 끝까지 읽는다. 파일 끝나면 종료.
    live   — 에이전트가 지금 돌고 있는 중. 파일 끝에 도달해도 기다렸다가
             새로 쓰인 줄을 계속 읽는다. (tail -f 와 같은 방식)

    이 두 모드는 '파일 끝에서 멈출 것인가, 기다릴 것인가'만 다르다.
    나머지 로직은 완전히 동일하다.

UI 연동 예시 (QThread 안에서):
    reader = SessionReader(session_dir, mode="live")
    for event in reader.iter_events(should_stop=lambda: not self.working):
        self.log_signal.emit(reader.format_log_line(event))
        report = classify_event(event)
        if report:
            self.error_signal.emit(report, reader.event_count)
"""

import csv
import json
import os
import time
from pathlib import Path

# pywin32는 '열려있는 창 목록'을 뽑을 때만 필요하다.
# 세션 폴더 읽기만 할 거면 없어도 동작하도록 선택적 import.
try:
    import win32gui
    import win32process
    import psutil
    _HAS_WIN32 = True
except ImportError:
    _HAS_WIN32 = False


# ════════════════════════════════════════════════════════════
# 1. 창 목록 가져오기 (UI 콤보박스 채우기용)
# ════════════════════════════════════════════════════════════

def list_game_windows(exclude_system=True):
    """
    지금 열려 있는 창들의 목록을 반환한다.
    UI의 '대상 선택' 콤보박스를 채우는 데 쓴다.

    사용자가 창 제목을 직접 타이핑하면 오타가 나기 쉽다.
    이 함수로 목록을 뽑아서 고르게 하면 그 위험이 사라진다.

    ⚠️ 여기 나오는 건 '이미 실행 중인 창'이다.
       exe 파일을 넣는 게 아니라, 사용자가 게임을 먼저 켜둔 다음
       이 목록에서 고르는 흐름이다.

    GamingAI/gameqa/target.py의 _enum_windows()와 동일한 방식.
    에이전트가 창을 찾는 방식과 같아야 같은 프로세스를 잡는다.

    Args:
        exclude_system: True면 흔한 시스템/도구 창을 걸러낸다

    Returns:
        list[dict]: [{"hwnd":…, "pid":…, "title":…, "name":…, "exe":…}, ...]
                    제목 기준 정렬됨
    """
    if not _HAS_WIN32:
        raise RuntimeError(
            "창 목록을 가져오려면 pywin32와 psutil이 필요합니다:\n"
            "    pip install pywin32 psutil"
        )

    # 게임일 리 없는 창들. 콤보박스를 깔끔하게 유지하려고 거른다.
    SYSTEM_NOISE = {
        "explorer.exe", "textinputhost.exe", "searchapp.exe",
        "shellexperiencehost.exe", "applicationframehost.exe",
        "systemsettings.exe", "startmenuexperiencehost.exe",
    }

    results = []

    def callback(hwnd, _):
        # 1) 화면에 보이지 않는 창은 제외 (백그라운드 윈도우가 대부분)
        if not win32gui.IsWindowVisible(hwnd):
            return

        # 2) 제목 없는 창 제외 (거의 다 시스템 내부 창)
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return

        # GetWindowThreadProcessId는 (스레드ID, 프로세스ID) 튜플을 반환
        _, pid = win32process.GetWindowThreadProcessId(hwnd)

        exe, name = None, None
        try:
            proc = psutil.Process(pid)
            exe = proc.exe()
            name = proc.name()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            # 권한이 없거나 방금 죽은 프로세스. 이름 없이 목록엔 남긴다.
            pass

        # 3) 시스템 노이즈 제외
        if exclude_system and name and name.lower() in SYSTEM_NOISE:
            return

        results.append({
            "hwnd": hwnd,
            "pid": pid,
            "title": title,
            "name": name,
            "exe": exe,
        })

    win32gui.EnumWindows(callback, None)

    # 콤보박스에서 찾기 쉽도록 제목 가나다/알파벳 순 정렬
    results.sort(key=lambda w: w["title"].lower())
    return results


def format_window_label(window):
    """
    창 정보를 콤보박스에 표시할 한 줄 문자열로 만든다.

    예: "Maze Trials  (MazeTrials.exe, pid=9864)"

    UI에서 쓰는 법:
        for w in list_game_windows():
            # 보이는 건 사람이 읽기 좋은 라벨,
            # 실제 값은 dict 통째로 숨겨서 저장
            ui.targetCombo.addItem(format_window_label(w), userData=w)

        # 나중에 꺼낼 때
        w = ui.targetCombo.currentData()
        pid, title = w["pid"], w["title"]
    """
    name = window.get("name") or "?"
    return f"{window['title']}  ({name}, pid={window['pid']})"


# ════════════════════════════════════════════════════════════
# 2. 세션 폴더 찾기
# ════════════════════════════════════════════════════════════

def find_latest_session(logs_dir="logs"):
    """
    logs 폴더에서 가장 최근에 수정된 세션 폴더를 반환한다.

    왜 필요한가:
        GamingAI/main.py 72~74줄을 보면 세션 폴더 이름이
            f"{safe_title}_{int(time.time())}"
        로 만들어진다. 즉 '창을 찾은 다음'에야 이름이 정해진다.
        → UI는 에이전트를 실행하기 전에 폴더 이름을 알 수 없다.

        그래서 에이전트를 띄운 뒤 잠깐 기다렸다가
        가장 최신 폴더를 잡는 방식을 쓴다.
        (GamingAI/supervisor.py 60줄의 latest_session_dir와 같은 방식)

    Args:
        logs_dir: 로그 최상위 폴더 경로

    Returns:
        Path: 최신 세션 폴더 경로
        None: 폴더가 없거나 비어있음
    """
    base = Path(logs_dir)
    if not base.exists():
        return None

    dirs = [p for p in base.iterdir() if p.is_dir()]
    if not dirs:
        return None

    # 수정시각(mtime)이 가장 최신인 폴더
    return max(dirs, key=lambda p: p.stat().st_mtime)


def wait_for_new_session(logs_dir="logs", timeout_s=30, poll_s=0.5):
    """
    에이전트를 실행한 직후에 호출한다.
    새 세션 폴더가 생길 때까지 기다렸다가 그 경로를 반환한다.

    동작 방식:
        호출 시점에 이미 있던 폴더들을 기억해두고,
        그 목록에 없는 폴더가 나타나면 그게 방금 만들어진 세션이다.
        (단순히 '최신 폴더'만 보면 이전 세션을 잘못 잡을 수 있다)

    Args:
        logs_dir: 로그 최상위 폴더
        timeout_s: 최대 대기 시간(초)
        poll_s: 확인 주기(초)

    Returns:
        Path: 새로 생긴 세션 폴더
        None: timeout_s 안에 안 생김 (에이전트 실행 실패 가능성)
    """
    base = Path(logs_dir)
    base.mkdir(parents=True, exist_ok=True)

    # 시작 시점의 폴더 목록을 스냅샷으로 저장
    before = {p.name for p in base.iterdir() if p.is_dir()}

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        current = {p.name for p in base.iterdir() if p.is_dir()}
        new_dirs = current - before
        if new_dirs:
            # 새 폴더가 여러 개면 이름 순으로 마지막 것
            # (이름에 타임스탬프가 들어있어서 사전순 = 시간순)
            newest = sorted(new_dirs)[-1]
            return base / newest
        time.sleep(poll_s)

    return None


def validate_session_dir(session_dir):
    """
    세션 폴더에 필요한 파일이 있는지 검사한다.

    Returns:
        (bool, str): (유효한가, 사람이 읽을 메시지)

    UI에서 쓰는 법:
        ok, msg = validate_session_dir(path)
        if not ok:
            QMessageBox.warning(self, "폴더 확인", msg)
            return
    """
    path = Path(session_dir)

    if not path.exists():
        return False, f"폴더가 존재하지 않습니다:\n{path}"
    if not path.is_dir():
        return False, f"폴더가 아닙니다:\n{path}"

    # events.jsonl은 필수. 이게 없으면 읽을 게 없다.
    if not (path / "events.jsonl").exists():
        return False, (
            f"events.jsonl이 없습니다.\n"
            f"세션 폴더가 맞는지 확인해주세요:\n{path}"
        )

    # perf.csv, summary.json은 없어도 동작은 한다.
    # (세션이 아직 진행 중이면 summary.json이 아직 없을 수 있다)
    missing = [f for f in ("perf.csv", "summary.json")
               if not (path / f).exists()]
    if missing:
        return True, f"(참고) 다음 파일이 아직 없습니다: {', '.join(missing)}"

    return True, ""


# ════════════════════════════════════════════════════════════
# 3. 이벤트 → 에러 리포트 변환
# ════════════════════════════════════════════════════════════

# 심각도 정의. UI에서 색깔로 구분할 때 쓴다.
SEVERITY_CRITICAL = "critical"   # ⛔ 치명 — 게임이 죽음
SEVERITY_HIGH = "high"           # 🔴 높음 — 게임이 멈춤
SEVERITY_MEDIUM = "medium"       # 🟡 중간 — 성능 저하
SEVERITY_LOW = "low"             # 🔵 낮음 — 도구 자체 문제

# 심각도별 표시 색 (QListWidgetItem의 배경색 등에 쓰면 됨)
SEVERITY_COLORS = {
    SEVERITY_CRITICAL: "#8B0000",   # 진한 빨강
    SEVERITY_HIGH: "#E74C3C",       # 빨강
    SEVERITY_MEDIUM: "#F39C12",     # 주황
    SEVERITY_LOW: "#3498DB",        # 파랑
}

# 분류 카테고리.
# '게임 결함'과 '도구 오류'를 구분하는 게 중요하다.
# Gemini 할당량 초과는 게임 버그가 아니라 우리 도구의 문제이므로
# 그걸 게임 버그처럼 보고하면 발표에서 지적당한다.
CATEGORY_GAME = "게임 결함"
CATEGORY_TOOL = "도구 오류"


def classify_event(event):
    """
    이벤트 하나를 보고, 에러 리포트로 올릴 만한지 판단한다.

    Args:
        event: events.jsonl의 한 줄을 파싱한 dict
               {"ts":…, "seq":…, "type":…, "data":{…}}

    Returns:
        dict: UI에 띄울 에러 리포트
              {"title", "content", "severity", "category",
               "seq", "ts", "event_type"}
        None: 에러가 아닌 평범한 이벤트 (로그창에만 흐르면 됨)
    """
    etype = event.get("type", "")
    data = event.get("data", {}) or {}
    seq = event.get("seq")
    ts = event.get("ts")

    # 사람이 읽을 시각 문자열로 변환
    ts_str = ""
    if ts:
        try:
            ts_str = time.strftime("%H:%M:%S", time.localtime(float(ts)))
        except (ValueError, TypeError, OSError):
            pass

    # ── 크래시: 가장 심각 ─────────────────────────────────
    # monitor.py 226~241줄에서 프로세스 사망을 감지하면 남긴다
    if etype == "crash":
        exit_code = data.get("exit_code")
        method = data.get("detection_method", "unknown")
        return {
            "title": f"⛔ [크래시] 게임 프로세스 비정상 종료 (seq {seq})",
            "content": (
                f"■ 발생 시각: {ts_str}\n"
                f"■ 심각도: 치명\n"
                f"■ 분류: {CATEGORY_GAME}\n"
                f"■ 종료 코드: {exit_code}\n"
                f"■ 탐지 방법: {method}\n"
                f"----------------------------------------\n"
                f"■ 상세:\n"
                f"게임 프로세스가 예기치 않게 종료되었습니다.\n"
                f"{data.get('details', '(추가 정보 없음)')}"
            ),
            "severity": SEVERITY_CRITICAL,
            "category": CATEGORY_GAME,
            "seq": seq,
            "ts": ts,
            "event_type": etype,
        }

    # ── 응답없음(hang) ────────────────────────────────────
    # monitor.py 244~249줄. IsHungAppWindow가 True를 반환하면 기록
    if etype == "hang":
        return {
            "title": f"🔴 [응답없음] 게임 창이 응답하지 않음 (seq {seq})",
            "content": (
                f"■ 발생 시각: {ts_str}\n"
                f"■ 심각도: 높음\n"
                f"■ 분류: {CATEGORY_GAME}\n"
                f"■ 탐지 방법: {data.get('detection_method', 'is_hung_app_window')}\n"
                f"----------------------------------------\n"
                f"■ 상세:\n"
                f"게임 창이 5초 이상 메시지를 처리하지 못했습니다.\n"
                f"사용자 입력에 반응하지 않는 상태입니다."
            ),
            "severity": SEVERITY_HIGH,
            "category": CATEGORY_GAME,
            "seq": seq,
            "ts": ts,
            "event_type": etype,
        }

    # ── error 이벤트 ──────────────────────────────────────
    # 주의: 업로드한 실제 세션에서 error 25건은 전부 Gemini 할당량 초과였다.
    #      이건 게임 버그가 아니라 '우리 도구의 문제'다.
    #      정직하게 도구 오류로 분류한다.
    if etype == "error":
        source = data.get("source", "unknown")
        message = str(data.get("message", "(내용 없음)"))

        # 메시지가 너무 길면 제목용으로 잘라낸다
        short = message.split("\n")[0][:50]

        # Gemini 관련이면 도구 오류로 분류
        is_llm_issue = any(k in message for k in
                           ("Gemini", "RESOURCE_EXHAUSTED", "quota", "429"))

        if is_llm_issue:
            severity, category = SEVERITY_LOW, CATEGORY_TOOL
            icon, label = "🔵", "AI 호출 실패"
        else:
            severity, category = SEVERITY_MEDIUM, CATEGORY_TOOL
            icon, label = "🟡", "에이전트 오류"

        return {
            "title": f"{icon} [{label}] {short} (seq {seq})",
            "content": (
                f"■ 발생 시각: {ts_str}\n"
                f"■ 심각도: {'낮음' if is_llm_issue else '중간'}\n"
                f"■ 분류: {category}\n"
                f"■ 발생 위치: {source}\n"
                f"----------------------------------------\n"
                f"■ 상세:\n{message}"
            ),
            "severity": severity,
            "category": category,
            "seq": seq,
            "ts": ts,
            "event_type": etype,
        }

    # 그 외 이벤트는 에러가 아니다 (로그창에만 흐르면 됨)
    return None


# ════════════════════════════════════════════════════════════
# 4. 세션 읽기 본체
# ════════════════════════════════════════════════════════════

class SessionReader:
    """
    세션 폴더를 읽어서 이벤트를 하나씩 흘려보낸다.

    replay 모드: 파일 끝에 도달하면 종료
    live 모드:   파일 끝에 도달하면 대기했다가 새 줄을 계속 읽음

    두 모드의 차이는 '파일 끝에서 멈추느냐 기다리느냐'뿐이다.
    """

    def __init__(self, session_dir, mode="replay", speed=1.0):
        """
        Args:
            session_dir: 세션 폴더 경로
            mode: "replay" 또는 "live"
            speed: replay 재생 속도 배율.
                   1.0이면 실제 시간 간격대로,
                   0이면 최대 속도로 쭉 읽는다.
                   ⚠️ 실제 세션이 18,587초(5시간)짜리였으므로
                      시연에서는 0 또는 아주 큰 값을 쓸 것.
        """
        self.dir = Path(session_dir)
        self.mode = mode
        self.speed = speed

        self.events_path = self.dir / "events.jsonl"
        self.perf_path = self.dir / "perf.csv"
        self.summary_path = self.dir / "summary.json"

        # 진행 상황 추적용
        self.event_count = 0    # 지금까지 읽은 이벤트 수
        self.error_count = 0    # 그중 에러로 분류된 수
        self.total_lines = None  # replay 모드에서만 미리 셀 수 있다

    # ── 요약 정보 ─────────────────────────────────────────

    def load_summary(self):
        """
        summary.json을 읽는다.

        ⚠️ 세션이 진행 중이면 이 파일이 아직 없다.
           (에이전트가 종료할 때 쓴다)
           그래서 없어도 예외를 던지지 않고 빈 dict를 반환한다.

        Returns:
            dict: 요약 정보. 없으면 {}
        """
        if not self.summary_path.exists():
            return {}
        try:
            with open(self.summary_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[session_reader] summary.json 읽기 실패: {e}")
            return {}

    def count_total_lines(self):
        """
        events.jsonl의 총 줄 수를 센다. 진행률 표시(프로그레스바)용.

        ⚠️ live 모드에서는 의미가 없다 (계속 늘어나므로).
        ⚠️ 파일 전체를 훑으므로 아주 큰 파일에선 잠깐 걸린다.
           (실제 세션 23,570줄 기준으로는 순식간)

        Returns:
            int: 줄 수. 실패하면 0
        """
        try:
            with open(self.events_path, "r", encoding="utf-8") as f:
                # 메모리에 전부 올리지 않고 세는 방식
                count = sum(1 for _ in f)
            self.total_lines = count
            return count
        except OSError:
            return 0

    # ── 이벤트 스트림 ─────────────────────────────────────

    def iter_events(self, should_stop=None, idle_sleep=0.5):
        """
        이벤트를 하나씩 반환하는 제너레이터.

        Args:
            should_stop: 인자 없는 함수. True를 반환하면 읽기를 중단한다.
                         UI에서 중지 버튼을 눌렀을 때 쓴다.
                         예: should_stop=lambda: not self.working
            idle_sleep: live 모드에서 새 줄을 기다릴 때 쉬는 시간(초)

        Yields:
            dict: 파싱된 이벤트

        UI 연동 예:
            for event in reader.iter_events(should_stop=lambda: not self.working):
                self.log_signal.emit(reader.format_log_line(event))
        """
        # 파일이 아직 없을 수 있다 (live 모드에서 에이전트가 막 시작한 경우)
        # 잠깐 기다려본다.
        wait_deadline = time.time() + 10
        while not self.events_path.exists():
            if should_stop and should_stop():
                return
            if time.time() > wait_deadline:
                raise FileNotFoundError(
                    f"events.jsonl을 찾을 수 없습니다: {self.events_path}"
                )
            time.sleep(0.3)

        prev_ts = None      # 직전 이벤트 시각 (재생 속도 조절용)
        buffer = ""         # 아직 완성되지 않은 줄을 담아두는 버퍼

        # 파일을 열어두고 조금씩 읽는다.
        # ⚠️ readlines()로 전부 메모리에 올리지 않는 이유:
        #    live 모드에서는 파일이 계속 자라므로 한 번에 못 읽는다.
        #    replay에서도 큰 파일이면 메모리 낭비다.
        with open(self.events_path, "r", encoding="utf-8", errors="replace") as f:
            while True:
                if should_stop and should_stop():
                    return

                chunk = f.readline()

                # ── 읽을 게 없을 때 ──
                if not chunk:
                    if self.mode == "replay":
                        # 리플레이는 파일 끝 = 세션 끝
                        return
                    else:
                        # 라이브는 에이전트가 더 쓸 때까지 기다린다
                        time.sleep(idle_sleep)
                        continue

                # ── 줄이 아직 완성되지 않은 경우 ──
                # 에이전트가 쓰는 도중에 읽으면 줄 끝의 \n이 없을 수 있다.
                # 그런 조각은 버퍼에 모아뒀다가 완성되면 처리한다.
                if not chunk.endswith("\n"):
                    buffer += chunk
                    if self.mode == "live":
                        time.sleep(idle_sleep)
                        continue
                    else:
                        # replay에서 마지막 줄에 개행이 없는 건 정상
                        line = buffer + ""
                        buffer = ""
                        if not line.strip():
                            return
                else:
                    line = buffer + chunk
                    buffer = ""

                line = line.strip()
                if not line:
                    continue

                # ── JSON 파싱 ──
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    # 깨진 줄은 조용히 건너뛴다.
                    # (강제 종료 중에 반쯤 쓰인 줄이 남을 수 있다)
                    continue

                self.event_count += 1

                # ── 재생 속도 조절 ──
                # speed가 0이면 지연 없이 최대 속도로 읽는다.
                if self.speed and self.speed > 0 and self.mode == "replay":
                    cur_ts = event.get("ts")
                    if prev_ts is not None and cur_ts is not None:
                        try:
                            gap = (float(cur_ts) - float(prev_ts)) / self.speed
                            # 한 번에 0.2초 넘게 자지 않는다.
                            # (원본 간격이 몇 분씩 벌어진 구간이 있어서
                            #  그대로 자면 UI가 멈춘 것처럼 보인다)
                            if gap > 0:
                                time.sleep(min(gap, 0.2))
                        except (ValueError, TypeError):
                            pass
                    prev_ts = event.get("ts")

                yield event

    # ── 표시용 포맷 ───────────────────────────────────────

    def format_log_line(self, event):
        """
        이벤트를 로그창에 뿌릴 한 줄 문자열로 만든다.

        타입마다 중요한 정보가 다르므로 조금씩 다르게 표시한다.
        """
        etype = event.get("type", "?")
        data = event.get("data", {}) or {}
        seq = event.get("seq", "?")

        ts_str = ""
        ts = event.get("ts")
        if ts:
            try:
                ts_str = time.strftime("%H:%M:%S", time.localtime(float(ts)))
            except (ValueError, TypeError, OSError):
                pass

        # 타입별로 요약 문구를 만든다
        if etype == "screenshot":
            detail = f"캡처 {data.get('path', '')}"
        elif etype == "agent_action":
            detail = f"AI 판단 → {data.get('action', '?')}"
        elif etype == "dfs_direct_action":
            detail = f"탐색 이동 → {data.get('action', '?')}"
        elif etype == "action_override":
            detail = "AI 판단을 탐색 로직이 덮어씀"
        elif etype == "hang":
            detail = "⚠️ 창 응답없음 감지"
        elif etype == "crash":
            detail = f"💥 프로세스 종료 (code={data.get('exit_code')})"
        elif etype == "error":
            detail = str(data.get("message", ""))[:60]
        elif etype == "rate_limit_backoff":
            detail = f"호출 제한 대기 {data.get('sleep_s', '?')}초"
        elif etype == "session_start":
            detail = "세션 시작"
        elif etype == "session_end":
            detail = "세션 종료"
        else:
            detail = ""

        return f"[{ts_str}] #{seq} {etype}  {detail}".rstrip()

    def get_screenshot_path(self, event):
        """
        이벤트에 연결된 스크린샷의 절대 경로를 반환한다.
        에러 리포트에서 '이때 화면 보기' 기능에 쓴다.

        Returns:
            str: 이미지 파일 경로. 없으면 None
        """
        data = event.get("data", {}) or {}
        rel = data.get("path")
        if not rel:
            return None
        full = self.dir / rel
        return str(full) if full.exists() else None

    def find_nearest_screenshot(self, target_seq):
        """
        지정한 seq 근처에서 가장 가까운 스크린샷을 찾는다.

        에러 이벤트 자체에는 스크린샷 경로가 없다.
        하지만 그 직전에 찍힌 화면을 보여주면
        "이때 뭐가 보이고 있었나"를 확인할 수 있다.

        ⚠️ 파일을 처음부터 훑으므로 자주 호출하면 느리다.
           에러 목록에서 사용자가 클릭했을 때만 호출할 것.

        Args:
            target_seq: 기준 이벤트의 seq 번호

        Returns:
            str: 스크린샷 절대 경로. 없으면 None
        """
        best_path, best_seq = None, -1

        try:
            with open(self.events_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if ev.get("type") != "screenshot":
                        continue

                    seq = ev.get("seq", 0)
                    # target보다 앞이면서 가장 가까운 것
                    if seq <= target_seq and seq > best_seq:
                        p = (ev.get("data") or {}).get("path")
                        if p:
                            best_seq, best_path = seq, p
                    elif seq > target_seq:
                        break  # 정렬돼 있으므로 더 볼 필요 없다
        except OSError:
            return None

        if not best_path:
            return None
        full = self.dir / best_path
        return str(full) if full.exists() else None

    # ── 성능 데이터 ───────────────────────────────────────

    def load_perf(self, max_rows=None):
        """
        perf.csv를 읽어서 리스트로 반환한다.

        ⚠️ 빈 값 주의: 실제 데이터에서 fps 컬럼은 전부 비어있었다.
           (--presentmon 옵션을 안 줘서 fps_source가 unavailable)
           그래서 숫자 변환을 하나씩 안전하게 처리한다.

        Args:
            max_rows: 최대 읽을 행 수. None이면 전부

        Returns:
            list[dict]: 각 행. 숫자 컬럼은 float 또는 None
        """
        if not self.perf_path.exists():
            return []

        rows = []

        def to_float(v):
            """빈 문자열이나 이상한 값이 와도 터지지 않게."""
            if v is None:
                return None
            v = str(v).strip()
            if not v:
                return None
            try:
                return float(v)
            except ValueError:
                return None

        try:
            with open(self.perf_path, "r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                for i, row in enumerate(reader):
                    if max_rows is not None and i >= max_rows:
                        break
                    rows.append({
                        "timestamp": row.get("timestamp", ""),
                        "elapsed_s": to_float(row.get("elapsed_s")),
                        "cpu_percent": to_float(row.get("cpu_percent")),
                        "mem_rss_mb": to_float(row.get("mem_rss_mb")),
                        "mem_percent": to_float(row.get("mem_percent")),
                        "gpu_percent": to_float(row.get("gpu_percent")),
                        "fps": to_float(row.get("fps")),
                        "fps_source": row.get("fps_source", ""),
                        # CSV에는 "TRUE"/"FALSE" 문자열로 들어온다
                        "window_responsive":
                            str(row.get("window_responsive", "")).strip().upper() == "TRUE",
                    })
        except OSError as e:
            print(f"[session_reader] perf.csv 읽기 실패: {e}")

        return rows

    def find_perf_anomalies(self, cpu_spike_threshold=90.0):
        """
        perf.csv에서 이상 구간을 찾는다.

        조작 없이도 진짜 데이터에서 뽑아낼 수 있는 탐지 결과다.
        결함 주입으로 만든 hang/crash와 별개로,
        자잘한 경고를 추가로 띄우는 데 쓴다.

        Args:
            cpu_spike_threshold: 이 값을 넘는 CPU 사용률을 이상으로 본다

        Returns:
            list[dict]: 에러 리포트 형식과 동일한 dict들
        """
        anomalies = []
        rows = self.load_perf()
        if not rows:
            return anomalies

        for row in rows:
            # 1) 창 응답없음 — 결함 주입의 직접 증거
            if not row["window_responsive"]:
                anomalies.append({
                    "title": f"🔴 [응답없음] {row['timestamp']}",
                    "content": (
                        f"■ 발생 시각: {row['timestamp']}\n"
                        f"■ 심각도: 높음\n"
                        f"■ 분류: {CATEGORY_GAME}\n"
                        f"■ 경과: {row['elapsed_s']}초\n"
                        f"----------------------------------------\n"
                        f"■ 상세:\n"
                        f"성능 샘플링 시점에 창이 응답하지 않는 상태였습니다.\n"
                        f"CPU {row['cpu_percent']}% / 메모리 {row['mem_rss_mb']}MB"
                    ),
                    "severity": SEVERITY_HIGH,
                    "category": CATEGORY_GAME,
                    "seq": None,
                    "ts": None,
                    "event_type": "perf_unresponsive",
                })

            # 2) CPU 급증
            cpu = row["cpu_percent"]
            if cpu is not None and cpu >= cpu_spike_threshold:
                anomalies.append({
                    "title": f"🟡 [성능] CPU 사용률 급증 {cpu}% ({row['timestamp']})",
                    "content": (
                        f"■ 발생 시각: {row['timestamp']}\n"
                        f"■ 심각도: 중간\n"
                        f"■ 분류: {CATEGORY_GAME}\n"
                        f"----------------------------------------\n"
                        f"■ 상세:\n"
                        f"CPU 사용률이 임계치({cpu_spike_threshold}%)를 초과했습니다.\n"
                        f"현재 {cpu}% / GPU {row['gpu_percent']}%"
                    ),
                    "severity": SEVERITY_MEDIUM,
                    "category": CATEGORY_GAME,
                    "seq": None,
                    "ts": None,
                    "event_type": "perf_cpu_spike",
                })

        return anomalies


# ════════════════════════════════════════════════════════════
# 5. 에이전트 실행 (subprocess)
# ════════════════════════════════════════════════════════════

def build_agent_command(target_title,
                        duration_s=120,
                        mode="agent",
                        record_video=True,
                        max_llm_calls=300,
                        presentmon_path=None,
                        gemini_api_key=None,
                        python_exe=None,
                        main_py="main.py"):
    """
    에이전트 실행 명령어를 조립한다.

    ⚠️ 반드시 주의할 것들 (GamingAI/main.py의 build_parser 확인 결과):

    1) --mode 기본값이 "monkey"다!
       명시적으로 "agent"를 넘기지 않으면
       Gemini 에이전트가 아니라 랜덤 몽키 테스트가 돌아간다.

    2) --max-llm-calls 기본값이 100이다.
       실제 세션은 1730회를 썼다. 기본값이면 금방 끊긴다.

    3) --presentmon을 안 넘기면 perf.csv의 fps가 전부 빈 칸이 된다.
       (실제 업로드 데이터가 그랬다: fps_source=unavailable)

    4) API 키가 없으면 llm_vision.py 85줄에서 예외가 난다.
       --gemini-api-key로 넘기거나 GEMINI_API_KEY 환경변수를 설정할 것.

    Returns:
        list[str]: subprocess.Popen에 넘길 명령어 리스트
    """
    import sys as _sys
    exe = python_exe or _sys.executable

    cmd = [
        exe, main_py, "run",
        "--target", target_title,
        "--duration", str(duration_s),
        "--mode", mode,                          # ← 반드시 명시!
        "--max-llm-calls", str(max_llm_calls),   # ← 기본 100은 너무 작음
    ]

    if record_video:
        # 이 플래그 하나면 logs/<session>/video.mp4가 생성된다.
        # (main.py 169줄. 팀원이 코드를 고칠 필요 없음)
        cmd.append("--record-video")

    if presentmon_path:
        cmd += ["--presentmon", presentmon_path]

    if gemini_api_key:
        cmd += ["--gemini-api-key", gemini_api_key]

    return cmd


def launch_agent(cmd, cwd=None):
    """
    에이전트를 별도 프로세스로 실행한다.

    ⚠️ UI 스레드에서 subprocess.run()을 쓰면 화면이 멈춘다.
       Popen으로 띄우고 바로 반환해야 한다.

    Args:
        cmd: build_agent_command()가 만든 명령어 리스트
        cwd: 작업 디렉터리. main.py가 있는 GamingAI 폴더를 넘길 것.

    Returns:
        subprocess.Popen: 실행 중인 프로세스 핸들
                          .poll()로 종료 여부 확인,
                          .terminate()로 중지 가능
    """
    import subprocess

    return subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        # 출력 인코딩. Windows 한글 환경에서 깨지지 않게.
        encoding="utf-8",
        errors="replace",
        # 콘솔 창이 따로 뜨지 않게 (Windows 전용)
        creationflags=getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0)
        if os.name == "nt" else 0,
    )


# ════════════════════════════════════════════════════════════
# 단독 실행 테스트
# ════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="세션 폴더 읽기 테스트")
    parser.add_argument("session_dir", nargs="?", default=None,
                        help="세션 폴더 경로 (생략하면 창 목록만 출력)")
    parser.add_argument("--windows", action="store_true",
                        help="열려있는 창 목록 출력")
    parser.add_argument("--limit", type=int, default=20,
                        help="출력할 이벤트 수")
    args = parser.parse_args()

    # 창 목록 테스트
    if args.windows or not args.session_dir:
        print("=== 열려있는 창 목록 ===")
        try:
            for w in list_game_windows():
                print(" ", format_window_label(w))
        except RuntimeError as e:
            print(f"  (건너뜀) {e}")
        if not args.session_dir:
            raise SystemExit(0)

    # 세션 읽기 테스트
    ok, msg = validate_session_dir(args.session_dir)
    print(f"\n=== 폴더 검사 ===\n  유효: {ok}\n  메시지: {msg or '(없음)'}")
    if not ok:
        raise SystemExit(1)

    reader = SessionReader(args.session_dir, mode="replay", speed=0)

    print("\n=== 요약 ===")
    for k, v in reader.load_summary().items():
        print(f"  {k}: {v}")

    print(f"\n=== 총 이벤트 수 ===\n  {reader.count_total_lines()}줄")

    print(f"\n=== 이벤트 (처음 {args.limit}개) ===")
    for i, ev in enumerate(reader.iter_events()):
        if i >= args.limit:
            break
        print(" ", reader.format_log_line(ev))

    print("\n=== 에러로 분류된 이벤트 ===")
    reader2 = SessionReader(args.session_dir, mode="replay", speed=0)
    found = 0
    for ev in reader2.iter_events():
        report = classify_event(ev)
        if report:
            found += 1
            if found <= 10:
                print(f"  [{report['severity']:8s}] {report['title']}")
    print(f"  총 {found}건")

    print("\n=== perf.csv 이상 구간 ===")
    anomalies = reader.find_perf_anomalies()
    for a in anomalies[:10]:
        print(f"  [{a['severity']:8s}] {a['title']}")
    print(f"  총 {len(anomalies)}건")
