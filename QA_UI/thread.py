from PyQt6.QtWidgets import QMessageBox
from PyQt6 import uic
import time
from PyQt6.QtCore import QThread, pyqtSignal
import logic, menu_bar
from enum import Enum, auto
import random

class QAWorker(QThread):
    """
    run 함수 안에 나중에 백엔드 팀이 만든 게임 실행이나 AI API 관련함수 넣기.
    """
    # 1. 일꾼이 사장님(메인 UI)에게 보낼 귓속말(Signal) 종류를 정의합니다.
    finished_signal = pyqtSignal(bool) # QThread 퇴근 신호
    log_signal = pyqtSignal(str)       # 지금 뭐하고잇어용
    error_signal = pyqtSignal(object, int) # 분석 퉤
    step_signal = pyqtSignal(int) # 몇번째 스텝이에용

    def __init__(self, step=0, config=None, max_steps=10, max_duration=600):
        super().__init__()
        self.working = True          # 계속 돌아라 플래그
        self.step = step             # 이어서 시작할 지점
        self.config = config or {}   # 밖에서 안 꽂아도 안전하게
        self.max_steps = max_steps       # 스텝 예산
        self.max_duration = max_duration # 시간 예산(초)

    def run(self):
        self.working = True  # QAWorker(QThread) 실행 플래그
        self.log_signal.emit("🚀 파일(영상) 분석 테스트를 시작합니다...")
        start_time = time.time()
        
        while True:
            # -------- 출구들 ---------
            if not self.working:
                break # 중지 요청
            if self.step >= self.max_steps:
                self.log_signal.emit(f"✅ 최대 스텝({self.max_steps}) 도달 — 세션 종료")
                break
            if time.time() - start_time > self.max_duration:
                self.log_signal.emit("✅ 제한 시간 도달 — 세션 종료")
                break

            # ── 스텝 실행 (백엔드팀 함수로 교체 예정) ──
            self.log_signal.emit("🚀 시연용 게임(test.exe) 실행 중...")
            if not self.wait_interruptible(1): break
            self.log_signal.emit("📸 게임 화면 캡처 및 로그 수집 중...")
            if not self.wait_interruptible(1): break
            self.log_signal.emit("🤖 수집된 데이터 AI 분석 진행 중...")
            if not self.wait_interruptible(1): break

            # ── 스텝 완료, +1 ──
            self.step += 1
            self.step_signal.emit(self.step)

            # ── 판정: 에러가 있을 때만 리포트 ──
            result = self.analyze()
            if result is None:
                self.log_signal.emit(f"✅ 스텝 {self.step} 통과 — 이상 없음")
            else:
                self.log_signal.emit(f"⚠️ 스텝 {self.step} 에러 검출")
                self.error_signal.emit(result, self.step)   
        
        if self.working:
            self.log_signal.emit(f"✅ 분석을 모두 마쳤습니다! ({self.step}턴 종료)")
        else:
            self.log_signal.emit("🛑 사용자가 테스트를 중지했습니다.")

        self.finished_signal.emit(self.working) # 완료 신호

    def wait_interruptible(self, seconds):
        """0.1초씩 나눠 자면서 중지 요청을 확인.
        스텝 실행 중에도 반응하게 만드는 게 목적.
        나중에 로딩 같은거 기다릴때 사용할듯?
        Return: 정상 완료면 True, 중지 요청 받으면 False"""
        for _ in range(int(seconds * 10)):
            if not self.working:
                return False
            self.msleep(100)
        return True

    def analyze(self):
        """더미 판정. 나중에 AI 분석 함수로 교체.
        에러 있으면 dict, 없으면 None"""
        if random.random() < 0.3:      # 30% 확률로 에러
            return {
                "title": f"[UI/시각] 상점 진입 시 골드 텍스트 겹침 {self.step}",
                "content": 
                    "Expected: 상점 팝업 중앙 정상 출력\n"
                    "Actual: 폰트 깨짐 및 10% 우측 치우침 발생."
            }
        return None

def on_qa_finished(ui, ok):   # finished_signal 연결
    """ qa가 일시중지 되면 finished_signal에 던져줄 것들"""

    # on_qa_finished 맨 앞
    print(f"[finish] ok={ok} | save_path={ui.current_save_path}")
    ui.state = RunState.DONE if ok else RunState.PAUSED
    ui.btnStartQA.setText("▶ QA 시작")
    ui.btnStartQA.setStyleSheet("")
    ui.btnStartQA.setEnabled(not ok) # 완주했으면 잠금

    if getattr(ui, 'keep_record', True): # qa_stop()에서 기록 남긴다 했을때
        if ui.current_save_path:
            logic.save_checkpoint(ui, ui.current_save_path)
        else:
            menu_bar.save_as(ui)

    ui.keep_record = True   # 다음을 위해 초기화

def shutdown_worker(ui):
    """워커에게 중지 요청하고 실제로 끝날 때까지 기다림"""
    if hasattr(ui, 'worker') and ui.worker.isRunning():
        ui.worker.working = False
        ui.worker.wait(3000)          # 최대 3초 대기
        if ui.worker.isRunning():     # 그래도 안 죽으면
            ui.worker.terminate()     # 강제 (최후수단)
            ui.worker.wait()



class RunState(Enum):
    """
    IDLE = 시작 전
    RUNNING = 진행 중
    PAUSED = 중지(껏다켯다)
    DONE = 끝까지 완주
    """
    IDLE    = auto()   # 시작 전
    RUNNING = auto()   # 진행 중
    PAUSED  = auto()   # 중지됨 → 이어하기 가능
    DONE    = auto()   # 끝까지 완주 → 회귀 비교 가능

# def update_input_enabled(ui):
#     """state에 따라 입력 필드 잠그기"""
#     editable = (ui.state == RunState.IDLE)   # 시작 전에만 수정 가능
#     ui.QAFileRoute.setEnabled(editable)
#     ui.new_resume_toggle.setEnabled(editable)