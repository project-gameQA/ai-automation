"""
session_reader.py
=================
에이전트(GamingAI)가 만든 세션 폴더를 읽어서 UI에 넘겨주는 모듈.

세션 폴더 구조:
    logs/Maze_Trials_1784807416/
        ├── events.jsonl     한 줄에 JSON 하나. 에이전트의 모든 행동/사건
        ├── perf.csv         2초 간격 성능 샘플
        ├── screenshots/     000001.png, 000002.png, ...
        └── summary.json     세션 요약

읽기 모드 두 가지:
    replay — 이미 끝난 세션. 파일 끝에 도달하면 종료.
    live   — 에이전트가 지금 돌고 있음. 파일 끝에서 기다렸다가 새 줄을 계속 읽음.

    차이는 '파일 끝에서 멈추냐 기다리냐' 하나뿐이다. 나머지는 완전히 동일하다.
"""

import csv
import json
import os
import subprocess
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
# 1. 창 목록 가져오기 (라이브 모드에서 대상 선택용)
# ════════════════════════════════════════════════════════════

# 게임일 리 없는 창들. 목록을 깔끔하게 유지하려고 거른다.
SYSTEM_NOISE = {
    "explorer.exe", "textinputhost.exe", "searchapp.exe",
    "shellexperiencehost.exe", "applicationframehost.exe",
    "systemsettings.exe", "startmenuexperiencehost.exe",
    "python.exe", "pythonw.exe",   # 우리 UI 자신도 목록에 뜨면 헷갈린다
}


def list_game_windows(exclude_system=True):
    """
    지금 열려 있는 창들의 목록을 반환한다.

    ⚠️ 여기 나오는 건 '이미 실행 중인 창'이다.
       exe 파일을 고르는 게 아니라, 사용자가 게임을 먼저 켜둔 다음
       이 목록에서 고르는 흐름이다.

    Returns:
        list[dict]: [{"hwnd", "pid", "title", "name", "exe"}, ...] 제목순 정렬
    """
    if not _HAS_WIN32:
        raise RuntimeError(
            "창 목록을 가져오려면 pywin32와 psutil이 필요합니다:\n"
            "    pip install pywin32 psutil"
        )

    results = []

    def callback(hwnd, _):
        # 1) 화면에 보이지 않는 창은 제외
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
            pass  # 권한 없거나 방금 죽은 프로세스. 이름 없이 목록엔 남긴다

        # 3) 시스템 노이즈 제외
        if exclude_system and name and name.lower() in SYSTEM_NOISE:
            return

        results.append({"hwnd": hwnd, "pid": pid, "title": title,
                        "name": name, "exe": exe})

    win32gui.EnumWindows(callback, None)
    results.sort(key=lambda w: w["title"].lower())
    return results


def format_window_label(window):
    """창 정보를 목록에 표시할 한 줄로. 예: 'Maze Trials  (MazeTrials.exe, pid=9864)'"""
    name = window.get("name") or "?"
    return f"{window['title']}  ({name}, pid={window['pid']})"


# ════════════════════════════════════════════════════════════
# 2. 세션 폴더 찾기
# ════════════════════════════════════════════════════════════

def find_latest_session(logs_dir):
    """logs 폴더에서 가장 최근에 수정된 세션 폴더. 없으면 None."""
    base = Path(logs_dir)
    if not base.exists():
        return None
    dirs = [p for p in base.iterdir() if p.is_dir()]
    if not dirs:
        return None
    return max(dirs, key=lambda p: p.stat().st_mtime)


def wait_for_new_session(logs_dir, timeout_s=60, poll_s=0.5, should_stop=None):
    """
    에이전트를 실행한 직후에 호출한다.
    새 세션 폴더가 생길 때까지 기다렸다가 그 경로를 반환한다.

    동작 방식:
        호출 시점에 이미 있던 폴더들을 기억해두고,
        그 목록에 없는 폴더가 나타나면 그게 방금 만들어진 세션이다.
        (단순히 '최신 폴더'만 보면 이전 세션을 잘못 잡을 수 있다)

    Args:
        should_stop: True를 반환하면 대기를 포기한다 (UI 중지 버튼용)

    Returns:
        Path 또는 None (시간 초과 / 중지됨)
    """
    base = Path(logs_dir)
    base.mkdir(parents=True, exist_ok=True)

    # 시작 시점의 폴더 목록 스냅샷
    before = {p.name for p in base.iterdir() if p.is_dir()}

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if should_stop and should_stop():
            return None
        new_dirs = {p.name for p in base.iterdir() if p.is_dir()} - before
        if new_dirs:
            # 폴더 이름에 타임스탬프가 들어있어서 사전순 = 시간순
            return base / sorted(new_dirs)[-1]
        time.sleep(poll_s)

    return None


def validate_session_dir(session_dir):
    """
    세션 폴더에 필요한 파일이 있는지 검사.

    Returns: (bool, str) — (유효한가, 사람이 읽을 메시지)
    """
    path = Path(session_dir)

    if not path.exists():
        return False, f"폴더가 존재하지 않습니다:\n{path}"
    if not path.is_dir():
        return False, f"폴더가 아닙니다:\n{path}"

    # events.jsonl은 필수. 이게 없으면 읽을 게 없다.
    if not (path / "events.jsonl").exists():
        return False, f"events.jsonl이 없습니다.\n세션 폴더가 맞는지 확인해주세요:\n{path}"

    # perf.csv, summary.json은 없어도 동작한다.
    # (세션이 진행 중이면 summary.json이 아직 없다)
    missing = [f for f in ("perf.csv", "summary.json")
               if not (path / f).exists()]
    if missing:
        return True, f"(참고) 아직 없는 파일: {', '.join(missing)}"

    return True, ""


# ════════════════════════════════════════════════════════════
# 3. 심각도 / 분류 정의
# ════════════════════════════════════════════════════════════

SEVERITY_CRITICAL = "critical"   # ⛔ 게임이 죽음
SEVERITY_HIGH = "high"           # 🔴 게임이 멈춤
SEVERITY_MEDIUM = "medium"       # 🟡 성능 저하
SEVERITY_LOW = "low"             # 🔵 도구 자체 문제

# 목록에서 글자색으로 쓴다
SEVERITY_COLORS = {
    SEVERITY_CRITICAL: "#FF4D4D",
    SEVERITY_HIGH: "#E74C3C",
    SEVERITY_MEDIUM: "#F39C12",
    SEVERITY_LOW: "#5DADE2",
}

SEVERITY_LABELS = {
    SEVERITY_CRITICAL: "치명",
    SEVERITY_HIGH: "높음",
    SEVERITY_MEDIUM: "중간",
    SEVERITY_LOW: "낮음",
}

# 정렬/집계용 순위 (숫자가 클수록 심각)
SEVERITY_RANK = {
    SEVERITY_CRITICAL: 4, SEVERITY_HIGH: 3,
    SEVERITY_MEDIUM: 2, SEVERITY_LOW: 1,
}

# '게임 결함'과 '도구 오류'를 구분하는 게 중요하다.
# Gemini 할당량 초과는 게임 버그가 아니라 우리 도구의 문제다.
CATEGORY_GAME = "게임 결함"
CATEGORY_TOOL = "도구 오류"


def _fmt_time(ts):
    """유닉스 타임스탬프를 HH:MM:SS로. 실패하면 빈 문자열."""
    if not ts:
        return ""
    try:
        return time.strftime("%H:%M:%S", time.localtime(float(ts)))
    except (ValueError, TypeError, OSError):
        return ""


# ════════════════════════════════════════════════════════════
# 4. 이벤트 → 에러 리포트 변환
# ════════════════════════════════════════════════════════════

def classify_event(event):
    """
    이벤트 하나를 보고 에러 리포트로 올릴 만한지 판단한다.

    Args:
        event: events.jsonl 한 줄을 파싱한 dict
               {"ts", "seq", "type", "data"}

    Returns:
        dict: 에러 리포트. 에러가 아니면 None
    """
    etype = event.get("type", "")
    data = event.get("data") or {}
    seq = event.get("seq")
    ts = event.get("ts")
    ts_str = _fmt_time(ts)

    # ── 크래시: 가장 심각 ──────────────────────────────────
    if etype == "crash":
        return {
            "title": f"⛔ [크래시] 게임 프로세스 비정상 종료 (seq {seq})",
            "content": (
                f"■ 발생 시각: {ts_str}\n"
                f"■ 심각도: 치명\n"
                f"■ 분류: {CATEGORY_GAME}\n"
                f"■ 종료 코드: {data.get('exit_code')}\n"
                f"■ 탐지 방법: {data.get('detection_method', 'unknown')}\n"
                f"----------------------------------------\n"
                f"■ 상세:\n"
                f"게임 프로세스가 예기치 않게 종료되었습니다.\n"
                f"{data.get('details', '(추가 정보 없음)')}"
            ),
            "severity": SEVERITY_CRITICAL, "category": CATEGORY_GAME,
            "seq": seq, "ts": ts, "event_type": etype,
        }

    # ── 응답없음(hang) ────────────────────────────────────
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
            "severity": SEVERITY_HIGH, "category": CATEGORY_GAME,
            "seq": seq, "ts": ts, "event_type": etype,
        }

    # ── error 이벤트 ──────────────────────────────────────
    # ⚠️ 실제 세션의 error 25건은 전부 Gemini 할당량 초과였다.
    #    이건 게임 버그가 아니라 '우리 도구의 문제'다. 정직하게 분류한다.
    if etype == "error":
        message = str(data.get("message", "(내용 없음)"))
        short = message.split("\n")[0][:50]

        is_llm_issue = any(k in message for k in
                           ("Gemini", "RESOURCE_EXHAUSTED", "quota", "429"))
        if is_llm_issue:
            severity, icon, label = SEVERITY_LOW, "🔵", "AI 호출 실패"
        else:
            severity, icon, label = SEVERITY_MEDIUM, "🟡", "에이전트 오류"

        return {
            "title": f"{icon} [{label}] {short} (seq {seq})",
            "content": (
                f"■ 발생 시각: {ts_str}\n"
                f"■ 심각도: {SEVERITY_LABELS[severity]}\n"
                f"■ 분류: {CATEGORY_TOOL}\n"
                f"■ 발생 위치: {data.get('source', 'unknown')}\n"
                f"----------------------------------------\n"
                f"■ 상세:\n{message}"
            ),
            "severity": severity, "category": CATEGORY_TOOL,
            "seq": seq, "ts": ts, "event_type": etype,
        }

    # 나머지는 에러가 아니다 (로그창에만 흐르면 됨)
    return None


# ════════════════════════════════════════════════════════════
# 5. 로그 한 줄 포맷
# ════════════════════════════════════════════════════════════

def _detail_screenshot(d):
    return f"캡처 {d.get('path', '')}"


def _detail_action(prefix):
    def f(d):
        return f"{prefix} {d.get('action', '?')}"
    return f


# type → data를 받아 요약 문구를 만드는 함수
# 실제 세션 15종을 전부 확인해서 채웠다.
_DETAIL_MAP = {
    "session_start":          lambda d: "세션 시작",
    "session_end":            lambda d: f"세션 종료 (사유: {d.get('reason', '?')})",
    "screenshot":             _detail_screenshot,
    "agent_action":           _detail_action("AI 판단 →"),
    "dfs_direct_action":      _detail_action("탐색 이동 →"),
    "heuristic_action":       _detail_action("휴리스틱 →"),
    "action_override":        lambda d: "AI 판단을 탐색 로직이 덮어씀",
    "camera_scroll":          lambda d: f"카메라 이동 {d.get('direction', d.get('action', ''))}".rstrip(),
    "scene_elements":         lambda d: "화면 요소 인식",
    "goal_canvas_track":      lambda d: "목표 지점 추적",
    "agent_memory_update":    lambda d: "에이전트 기억 갱신",
    "api_key_switch":         lambda d: f"API 키 전환 → #{d.get('new_key_index', '?')}",
    "bucket_size_calibrated": lambda d: "탐색 격자 크기 보정",
    # ⚠️ 실제 데이터의 키는 sleep_s가 아니라 backoff_s다
    "rate_limit_backoff":     lambda d: f"호출 제한 대기 {d.get('backoff_s', '?')}초",
    "hang":                   lambda d: "⚠️ 창 응답없음 감지",
    "crash":                  lambda d: f"💥 프로세스 종료 (code={d.get('exit_code')})",
    "error":                  lambda d: str(d.get("message", ""))[:60],
}


def format_log_line(event):
    """이벤트를 로그창에 뿌릴 한 줄로 만든다."""
    etype = event.get("type", "?")
    data = event.get("data") or {}
    seq = event.get("seq", "?")
    ts_str = _fmt_time(event.get("ts"))

    fn = _DETAIL_MAP.get(etype)
    try:
        detail = fn(data) if fn else ""
    except Exception:
        # 어떤 형태의 data가 와도 로그 한 줄 때문에 세션이 죽으면 안 된다
        detail = ""

    return f"[{ts_str}] #{seq} {etype}  {detail}".rstrip()


# ════════════════════════════════════════════════════════════
# 6. 세션 읽기 본체
# ════════════════════════════════════════════════════════════

class SessionReader:
    """
    세션 폴더를 읽어서 이벤트를 하나씩 흘려보낸다.

    replay 모드: 파일 끝에 도달하면 종료
    live 모드:   파일 끝에 도달하면 대기했다가 새 줄을 계속 읽음
    """

    def __init__(self, session_dir, mode="replay", speed=200.0, max_sleep_s=0.2):
        """
        Args:
            speed: replay 재생 속도 배율. 원본 시각 간격을 이 값으로 나눈다.
                   0이면 지연 없이 최대 속도.
            max_sleep_s: 한 번에 자는 최대 시간. 원본에 몇 분씩 벌어진 구간이
                         있어서 이걸 안 걸면 화면이 멈춘 것처럼 보인다.
        """
        self.dir = Path(session_dir)
        self.mode = mode
        self.speed = speed
        self.max_sleep_s = max_sleep_s

        self.events_path = self.dir / "events.jsonl"
        self.perf_path = self.dir / "perf.csv"
        self.summary_path = self.dir / "summary.json"

        self.event_count = 0     # 지금까지 읽은 이벤트 수
        self.total_lines = None  # replay에서만 미리 셀 수 있다

    # ── 요약 / 총량 ───────────────────────────────────────

    def load_summary(self):
        """summary.json을 읽는다. 세션 진행 중이면 아직 없으므로 {} 반환."""
        if not self.summary_path.exists():
            return {}
        try:
            with open(self.summary_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[session_reader] summary.json 읽기 실패: {e}")
            return {}

    def count_total_lines(self):
        """events.jsonl 총 줄 수. 진행률 표시용. live에서는 의미 없음."""
        try:
            with open(self.events_path, "r", encoding="utf-8",
                      errors="replace") as f:
                count = sum(1 for _ in f)   # 메모리에 다 올리지 않고 센다
            self.total_lines = count
            return count
        except OSError:
            return 0

    # ── 이벤트 스트림 ─────────────────────────────────────

    def iter_events(self, should_stop=None, idle_sleep=0.3):
        """
        이벤트를 하나씩 반환하는 제너레이터.

        Args:
            should_stop: 인자 없는 함수. True를 반환하면 읽기를 중단한다.
            idle_sleep: live 모드에서 새 줄을 기다릴 때 쉬는 시간(초)
        """
        # live 모드에서는 에이전트가 파일을 아직 안 만들었을 수 있다
        wait_deadline = time.time() + 15
        while not self.events_path.exists():
            if should_stop and should_stop():
                return
            if time.time() > wait_deadline:
                raise FileNotFoundError(
                    f"events.jsonl을 찾을 수 없습니다: {self.events_path}")
            time.sleep(0.3)

        prev_ts = None
        buffer = ""   # 아직 완성되지 않은 줄

        # ⚠️ readlines()로 전부 올리지 않는 이유:
        #    live에서는 파일이 계속 자라고, replay에서도 4.5MB를 통째로
        #    올릴 이유가 없다.
        with open(self.events_path, "r", encoding="utf-8",
                  errors="replace") as f:
            while True:
                if should_stop and should_stop():
                    return

                chunk = f.readline()

                # ── 읽을 게 없을 때 ──
                if not chunk:
                    if self.mode == "replay":
                        return                    # 파일 끝 = 세션 끝
                    time.sleep(idle_sleep)        # live는 더 쓰일 때까지 대기
                    continue

                # ── 줄이 아직 완성되지 않은 경우 ──
                # 에이전트가 쓰는 도중에 읽으면 줄 끝의 \n이 없을 수 있다.
                if not chunk.endswith("\n"):
                    buffer += chunk
                    if self.mode == "live":
                        time.sleep(idle_sleep)
                        continue
                    # replay에서 마지막 줄에 개행이 없는 건 정상
                    line, buffer = buffer, ""
                    if not line.strip():
                        return
                else:
                    line, buffer = buffer + chunk, ""

                line = line.strip()
                if not line:
                    continue

                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    # 강제 종료 중에 반쯤 쓰인 줄이 남을 수 있다. 조용히 건너뛴다.
                    continue

                self.event_count += 1

                # ── 재생 속도 조절 (replay 전용) ──
                if self.mode == "replay" and self.speed and self.speed > 0:
                    cur_ts = event.get("ts")
                    if prev_ts is not None and cur_ts is not None:
                        try:
                            gap = (float(cur_ts) - float(prev_ts)) / self.speed
                            if gap > 0:
                                time.sleep(min(gap, self.max_sleep_s))
                        except (ValueError, TypeError):
                            pass
                    prev_ts = event.get("ts")

                yield event

    # ── 스크린샷 ──────────────────────────────────────────

    def resolve_screenshot(self, rel_path):
        """
        상대경로(screenshots/000042.png)를 절대경로로.
        파일이 실제로 없으면 None.
        """
        if not rel_path:
            return None
        full = self.dir / rel_path
        return str(full) if full.exists() else None

    # ── 성능 데이터 ───────────────────────────────────────

    def load_perf(self, max_rows=None):
        """
        perf.csv를 읽어서 리스트로 반환.

        ⚠️ 실제 데이터에서 fps 컬럼은 전부 비어있었다
           (--presentmon을 안 줘서 fps_source=unavailable).
           그래서 숫자 변환을 하나씩 안전하게 처리한다.
        """
        if not self.perf_path.exists():
            return []

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

        def to_responsive(v):
            """
            'True'/'False' 문자열 → bool.

            ⚠️ 값이 비어있거나 컬럼이 없으면 True로 본다.
               False로 두면 '데이터 누락'이 '응답없음'으로 둔갑해서
               있지도 않은 결함이 리포트에 올라간다.
            """
            if v is None:
                return True
            s = str(v).strip().upper()
            if s == "":
                return True
            return s not in ("FALSE", "0", "N", "NO")

        rows = []
        try:
            with open(self.perf_path, "r", encoding="utf-8", newline="") as f:
                for i, row in enumerate(csv.DictReader(f)):
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
                        "window_responsive":
                            to_responsive(row.get("window_responsive")),
                    })
        except OSError as e:
            print(f"[session_reader] perf.csv 읽기 실패: {e}")

        return rows

    def find_perf_anomalies(self, cpu_spike_threshold=90.0):
        """
        perf.csv에서 이상 구간을 찾는다.

        ⚠️ 응답없음은 '연속 구간당 1건'으로 묶는다.
           perf.csv는 2초 간격이라 8초 동결이면 4행이 연달아 False가 된다.
           행마다 리포트를 만들면 같은 사건이 4건으로 보인다.

        Returns: 에러 리포트 형식 dict의 리스트
        """
        anomalies = []
        rows = self.load_perf()
        if not rows:
            return anomalies

        # ── 1) 응답없음: 연속 구간 묶기 ──
        run_start = None       # 구간 시작 행
        run_last = None        # 구간의 마지막 행
        for row in rows + [None]:   # 끝에 None → 마지막 구간도 닫힌다
            bad = (row is not None and not row["window_responsive"])

            if bad:
                if run_start is None:
                    run_start = row
                run_last = row
                continue

            if run_start is not None:
                start_s = run_start["elapsed_s"] or 0
                end_s = (run_last or run_start)["elapsed_s"] or start_s
                dur = end_s - start_s
                anomalies.append({
                    "title": f"🔴 [응답없음] {run_start['timestamp']} (약 {dur:.0f}초)",
                    "content": (
                        f"■ 발생 시각: {run_start['timestamp']}\n"
                        f"■ 심각도: 높음\n"
                        f"■ 분류: {CATEGORY_GAME}\n"
                        f"■ 지속 시간: 약 {dur:.0f}초\n"
                        f"■ 경과: {start_s:.0f}초 지점\n"
                        f"----------------------------------------\n"
                        f"■ 상세:\n"
                        f"창이 사용자 입력에 반응하지 않는 상태가 이어졌습니다.\n"
                        f"CPU {run_start['cpu_percent']}% / "
                        f"메모리 {run_start['mem_rss_mb']}MB"
                    ),
                    "severity": SEVERITY_HIGH, "category": CATEGORY_GAME,
                    "seq": None, "ts": None, "event_type": "perf_unresponsive",
                })
                run_start = run_last = None

        # ── 2) CPU 급증: 이것도 연속 구간으로 묶는다 ──
        spike_start = None
        spike_peak = 0.0
        for row in rows + [None]:
            cpu = row["cpu_percent"] if row else None
            hot = (cpu is not None and cpu >= cpu_spike_threshold)

            if hot:
                if spike_start is None:
                    spike_start = row
                    spike_peak = cpu
                else:
                    spike_peak = max(spike_peak, cpu)
                continue

            if spike_start is not None:
                anomalies.append({
                    "title": f"🟡 [성능] CPU 급증 최대 {spike_peak:.0f}% "
                             f"({spike_start['timestamp']})",
                    "content": (
                        f"■ 발생 시각: {spike_start['timestamp']}\n"
                        f"■ 심각도: 중간\n"
                        f"■ 분류: {CATEGORY_GAME}\n"
                        f"----------------------------------------\n"
                        f"■ 상세:\n"
                        f"CPU 사용률이 임계치({cpu_spike_threshold}%)를 초과했습니다.\n"
                        f"구간 최대 {spike_peak:.1f}% / "
                        f"GPU {spike_start['gpu_percent']}%"
                    ),
                    "severity": SEVERITY_MEDIUM, "category": CATEGORY_GAME,
                    "seq": None, "ts": None, "event_type": "perf_cpu_spike",
                })
                spike_start = None
                spike_peak = 0.0

        return anomalies


# ════════════════════════════════════════════════════════════
# 7. 에이전트 실행 (라이브 모드)
# ════════════════════════════════════════════════════════════

def build_agent_command(target_title, agent_main="main.py", duration_s=300,
                        mode="agent", record_video=True, max_llm_calls=300,
                        presentmon_path=None, gemini_api_key=None,
                        python_exe=None):
    """
    에이전트 실행 명령어를 조립한다.

    ⚠️ 반드시 주의할 것 (GamingAI/main.py의 기본값이 위험하다):
      1) --mode 기본값이 "monkey"다. 명시 안 하면 랜덤 몽키 테스트가 돈다.
      2) --max-llm-calls 기본값 100은 금방 끊긴다.
      3) --presentmon을 안 넘기면 perf.csv의 fps가 전부 빈 칸이 된다.
      4) API 키가 없으면 에이전트가 예외로 죽는다.

    Returns: subprocess에 넘길 명령어 리스트
    """
    import sys as _sys
    exe = python_exe or _sys.executable

    cmd = [
        exe, agent_main, "run",
        "--target", target_title,
        "--duration", str(duration_s),
        "--mode", mode,                          # ← 반드시 명시
        "--max-llm-calls", str(max_llm_calls),
    ]
    if record_video:
        cmd.append("--record-video")
    if presentmon_path:
        cmd += ["--presentmon", presentmon_path]
    if gemini_api_key:
        cmd += ["--gemini-api-key", gemini_api_key]

    return cmd


def launch_agent(cmd, cwd=None):
    """
    에이전트를 별도 프로세스로 실행한다.

    ⚠️ stdout을 PIPE로 열어놓고 아무도 읽지 않으면
       파이프 버퍼(약 64KB)가 차는 순간 에이전트가 write에서 영구 블록된다.
       에이전트는 로그를 많이 뿜으므로 실제로 걸린다.
       우리는 events.jsonl을 직접 읽으므로 stdout은 필요 없다 → DEVNULL.

    Returns: subprocess.Popen (poll()로 종료 확인, terminate()로 중지)
    """
    kwargs = {
        "cwd": cwd,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
    }
    # 콘솔 창이 따로 뜨지 않게 (Windows 전용 플래그)
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    return subprocess.Popen(cmd, **kwargs)


def stop_agent(proc, timeout_s=5):
    """
    에이전트를 정상 종료시킨다.

    terminate()로 먼저 부탁하고, 시간 내에 안 죽으면 kill().
    ⚠️ 바로 kill()하면 에이전트가 summary.json을 못 쓰고 죽는다.
    """
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        proc.kill()
    except Exception as e:
        print(f"[session_reader] 에이전트 종료 실패: {e}")
