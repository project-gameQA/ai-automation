from PyQt6.QtWidgets import QMessageBox
from PyQt6 import uic
import time
from PyQt6.QtCore import QThread, pyqtSignal
import logic, menu_bar
from enum import Enum, auto

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
            if not self.working: # 유저가 테스트 중지함
                self.log_signal.emit("🛑 사용자가 테스트를 중지했습니다. 대기 상태로 돌아갑니다.")
                self.finished_signal.emit(False)
                return
            
            if self.step >= self.max_steps: # 유저가 설정한 최대 스텝 도달
                self.log_signal.emit(f"✅ 최대 스텝({self.max_steps}) 도달 — 세션 종료")
                break

            if time.time() - start_time > self.max_duration: # 유저가 설정한 제한 시간 도달
                self.log_signal.emit("✅ 제한 시간 도달 — 세션 종료")
                break
                
            # 일하는 척(백엔드팀 함수 호출 예정)
            self.log_signal.emit("🚀 시연용 게임(test.exe) 실행 중...")
            time.sleep(1) 
            self.log_signal.emit("📸 게임 화면 캡처 및 로그 수집 중...")
            time.sleep(1) 
            self.log_signal.emit("🤖 수집된 데이터 AI 분석 진행 중...")
            time.sleep(1) 

            # 가짜로 찾아낸 AI 에러 리포트 (나중에 백엔드팀 함수이름넣을거고 지금은 더미)
            dummy_result = {
                "title": f"[UI/시각] 상점 진입 시 골드 텍스트 겹침 { self.step }",
                "content": "Expected: 상점 팝업 중앙 정상 출력\nActual: 폰트 깨짐 및 10% 우측 치우침 발생."
            }
            
            self.step += 1
            print(f"[worker] step={self.step}")
            self.step_signal.emit(self.step)
            
            self.log_signal.emit("✅ AI 분석 완료! 결과를 대시보드에 띄웁니다.")
            self.error_signal.emit(dummy_result, self.step)
            
        
        self.log_signal.emit(f"✅ 입력된 영상(파일)의 분석을 모두 마쳤습니다! ({self.step}턴 종료)")
        self.finished_signal.emit(self.working) # 완료 신호

def on_qa_finished(ui, ok):   # finished_signal 연결
    """ qa가 일시중지 되면 finished_signal에 던져줄 것들"""

    # on_qa_finished 맨 앞
    print(f"[finish] ok={ok} | save_path={ui.current_save_path}")
    ui.state = RunState.DONE if ok else RunState.PAUSED
    ui.btnStartQA.setText("▶ QA 시작")
    ui.btnStartQA.setStyleSheet("")

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