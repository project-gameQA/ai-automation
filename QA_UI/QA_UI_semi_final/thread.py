"""
thread.py
=========
QAWorker(QThread) — 세션을 읽어서 UI로 흘려보내는 작업 스레드.

동작 두 가지:
    replay — 이미 끝난 세션 폴더를 읽는다
    live   — 에이전트를 실행하고, 생기는 세션 폴더를 실시간으로 따라 읽는다

⚠️ 이 스레드는 UI 위젯을 절대 직접 건드리지 않는다.
   시그널만 쏘고, 화면 변경은 전부 메인 스레드(qa_flow)에서 한다.
   워커 스레드에서 위젯을 만지면 랜덤하게 죽는다.
"""

import time
import traceback
from enum import Enum, auto

from PyQt6.QtCore import QThread, pyqtSignal

import settings
import session_reader


class RunState(Enum):
    """
    IDLE    = 시작 전
    RUNNING = 진행 중
    PAUSED  = 중지 -> 이어하기 가능
    DONE    = 끝까지 완주
    """
    IDLE = auto()
    RUNNING = auto()
    PAUSED = auto()
    DONE = auto()


class QAWorker(QThread):
    """세션을 읽어 로그/에러/진행률을 시그널 보냄"""

    # ── 시그널 ────────────────────────────────────────────
    log_signal = pyqtSignal(str)            # 로그 여러 줄(\n으로 연결)
    error_signal = pyqtSignal(object, object)   # (리포트 dict, 스크린샷 상대경로|None)
    progress_signal = pyqtSignal(int, int)  # (읽은 수, 전체 수)
    session_ready = pyqtSignal(str)         # 라이브에서 세션 폴더가 정해졌을 때
    finished_signal = pyqtSignal(bool)      # True=완주, False=중지/실패

    def __init__(self, session_dir="", mode="replay",
                 start_cursor=0, target_title=""):
        """
        Args:
            session_dir: 읽을 세션 폴더. live에서는 빈 문자열(나중에 정해짐)
            mode: "replay" 또는 "live"
            start_cursor: 이어하기 시작 지점(이미 읽은 이벤트 수)
            target_title: live에서 QA할 창 제목
        """
        super().__init__()
        self.working = True
        self.session_dir = session_dir
        self.mode = mode
        self.start_cursor = start_cursor
        self.target_title = target_title

        self.cursor = start_cursor # 현재까지 읽은 이벤트 수
        self.agent_proc = None # live에서 띄운 에이전트 프로세스

        # 로그 묶음 전송용 버퍼
        # (23,570개를 한 줄씩 쏘면 GUI 이벤트 큐가 터진다)
        self._log_buf = []
        self._last_flush = 0.0
        self._agent_done_at = None # 에이전트 종료 감지 시각

    # 로그 묶음 전송
    def _log(self, line):
        """단발 메시지. 즉시 보낸다."""
        self._flush_logs()
        self.log_signal.emit(line)

    def _queue_log(self, line):
        """이벤트 로그. 모아뒀다가 한꺼번에 보낸다."""
        self._log_buf.append(line)
        now = time.time()
        if (len(self._log_buf) >= settings.LOG_FLUSH_LINES
                or now - self._last_flush >= settings.LOG_FLUSH_INTERVAL_S):
            self._flush_logs()

    def _flush_logs(self):
        """버퍼에 쌓인 로그를 한 번에 내보낸다."""
        if not self._log_buf:
            return
        self.log_signal.emit("\n".join(self._log_buf))
        self._log_buf.clear()
        self._last_flush = time.time()


    def _should_stop(self):
        """
        읽기를 멈춰야 하는지. SessionReader에 콜백으로 넘긴다.

        중지 요청이 없어도, 라이브에서 에이전트가 끝났으면
        마지막 줄까지 읽을 시간을 3초 준 뒤 종료한다.
        (에이전트가 죽었는데 live 모드가 영원히 대기하는 걸 막는다)
        """
        if not self.working:
            return True

        if self.mode == "live" and self.agent_proc is not None:
            if self.agent_proc.poll() is not None:
                if self._agent_done_at is None:
                    self._agent_done_at = time.time()
                    self._log("ℹ️ 에이전트 종료 감지 — 남은 로그를 마저 읽습니다")
                elif time.time() - self._agent_done_at > 3:
                    return True

        return False

    def _launch_agent(self):
        """
        에이전트를 띄우고 새 세션 폴더가 생길 때까지 기다린다.

        Returns: 세션 폴더 경로(str). 실패하면 None
        """
        cmd = session_reader.build_agent_command(
            target_title=self.target_title,
            agent_main=settings.AGENT_MAIN,
            duration_s=settings.AGENT_DURATION_S,
            mode=settings.AGENT_MODE,
            record_video=settings.AGENT_RECORD_VIDEO,
            max_llm_calls=settings.AGENT_MAX_LLM_CALLS,
            presentmon_path=settings.PRESENTMON_PATH,
            gemini_api_key=settings.GEMINI_API_KEY,
        )

        if not settings.GEMINI_API_KEY:
            self._log("⚠️ GEMINI_API_KEY가 없습니다. 에이전트가 바로 죽을 수 있습니다.")

        self._log(f"🚀 에이전트 실행: {' '.join(cmd)}")
        try:
            self.agent_proc = session_reader.launch_agent(
                cmd, cwd=settings.AGENT_DIR)
        except (OSError, ValueError) as e:
            self._log(f"❌ 에이전트를 실행하지 못했습니다: {e}")
            return None

        self._log("⏳ 세션 폴더가 생기기를 기다리는 중...")
        found = session_reader.wait_for_new_session(
            settings.AGENT_LOGS_DIR,
            timeout_s=settings.SESSION_WAIT_TIMEOUT_S,
            should_stop=lambda: not self.working,
        )

        if found is None:
            self._log(
                f"❌ {settings.SESSION_WAIT_TIMEOUT_S}초 안에 세션 폴더가 "
                f"생기지 않았습니다.\n"
                f"   에이전트 실행 실패이거나 대상 창을 못 찾았을 수 있습니다.")
            session_reader.stop_agent(self.agent_proc)
            return None

        self._log(f"✅ 세션 폴더 확보: {found}")
        return str(found)

    def run(self):
        """QThread 본체"""
        self.working = True
        try:
            self._run_body()
        except Exception as e:
            # 콘솔에 스택 찍고 로그창에도
            traceback.print_exc()
            self._log(f"❌ 오류로 중단되었습니다: {e}")
            self.working = False
        finally:
            self._flush_logs()
            # 라이브였으면 에이전트도 같이 보내
            if self.agent_proc is not None:
                session_reader.stop_agent(self.agent_proc)
            self.finished_signal.emit(self.working)

    def _run_body(self):
        # 1) 라이브면 에이전트부터 띄운다
        session_dir = self.session_dir
        if self.mode == "live":
            session_dir = self._launch_agent()
            if session_dir is None:
                self.working = False
                return
            self.session_dir = session_dir
            self.session_ready.emit(session_dir)

        # 2) 리더 준비
        reader = session_reader.SessionReader(
            session_dir,
            mode=self.mode,
            speed=settings.REPLAY_SPEED,
            max_sleep_s=settings.REPLAY_MAX_SLEEP_S,
        )

        # replay는 몇줄인지 셀 수 있으나 live는 계속 늘어나니까 0
        total = reader.count_total_lines() if self.mode == "replay" else 0
        self.progress_signal.emit(self.cursor, total)

        if self.start_cursor > 0: # 이벤트 갯수가 0이상이면 == live가 아니면
            self._log(f"⏩ 이전 기록에 이어서 {self.start_cursor}번째 이벤트부터 읽습니다")
        self._log("🔍 세션 분석을 시작합니다...")

        # 3) 이벤트 스트림
        # 에러가 났을 때 '그 직전 화면'을 보여주려고 마지막 스크린샷을 따라간다.
        # ⚠️ 에러마다 파일을 다시 훑으면 O(n²)이 된다. 스트림 중에 기억해두면 O(1).
        last_shot = None

        for event in reader.iter_events(should_stop=self._should_stop):
            n = reader.event_count

            if event.get("type") == "screenshot":
                last_shot = (event.get("data") or {}).get("path")

            # 이어하기: 이미 읽은 구간은 화면에 다시 뿌리지 않는다.
            # (스크린샷 추적은 위에서 이미 했으므로 건너뛰어도 안전하다)
            if n <= self.start_cursor:
                continue

            self._queue_log(session_reader.format_log_line(event))

            report = session_reader.classify_event(event)
            if report is not None:
                self._flush_logs()   # 에러 앞뒤 로그 순서가 뒤집히지 않게
                self.error_signal.emit(report, last_shot)

            self.cursor = n
            if n % settings.PROGRESS_EVERY == 0: # 진행률 시그널 업뎃
                self.progress_signal.emit(n, total)

        self._flush_logs()
        self.progress_signal.emit(self.cursor, total or self.cursor)

        # 4) 성능 데이터 훑기
        # 이벤트 스트림이 끝난 뒤에. perf.csv는 이벤트와 시간축이 달라서 중간에 끼워넣기 어렵다네..
        if self.working:
            self._log("📊 성능 데이터를 분석합니다...")
            anomalies = reader.find_perf_anomalies(settings.CPU_SPIKE_THRESHOLD)
            for a in anomalies:
                self.error_signal.emit(a, None)
            self._log(f"   성능 이상 {len(anomalies)}건")

        # 5) 마무리
        if self.working:
            self._log(f"✅ 분석 완료 — 이벤트 {self.cursor}개를 확인했습니다.")
        else:
            self._log("🛑 사용자가 테스트를 중지했습니다.")


# ════════════════════════════════════════════════════════════
# 워커 수명 관리 (메인 스레드에서 호출)
# ════════════════════════════════════════════════════════════

def on_qa_finished(ui, ok):
    """finished_signal에 연결. 워커가 끝났을 때 화면 정리"""
    print(f"[finish] ok={ok} | save_path={ui.current_save_path}")

    ui.state = RunState.DONE if ok else RunState.PAUSED
    ui.btnStartQA.setText("▶ QA 시작")
    ui.btnStartQA.setStyleSheet("")
    ui.btnStartQA.setEnabled(not ok) # 완주했으면 다시 못 누르게.. 이걸 어떻게할까 살릴까 지울까?????

    # qa_stop()에서 '기록 남김'을 선택했을 때 저장
    if getattr(ui, "keep_record", True):
        if ui.current_save_path:
            import logic
            logic.save_checkpoint(ui, ui.current_save_path)
        else:
            import menu_bar
            menu_bar.save_as(ui)

    ui.keep_record = True # 다음을 위해 초기화

def shutdown_worker(ui, timeout_ms=5000):
    """
    워커에게 중지를 요청, 끝날 때까지 대기
    워커가 없거나 안 돌고 있으면 조용히 넘어감
    """
    worker = getattr(ui, "worker", None)
    if worker is None or not worker.isRunning():
        return True

    worker.working = False
    if worker.wait(timeout_ms):
        return True

    # 여기까지 왔으면 run() 루프에 working을 확인하지 않는 구간이 있다는 뜻.
    # 강제로 죽이지 말고 로그로 남긴다.
    print(f"[shutdown] 워커가 {timeout_ms}ms 내에 종료되지 않음")
    return False
