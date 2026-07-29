import json
import os
import sys
from PyQt6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox
from PyQt6 import uic
import time
from PyQt6.QtCore import QThread, pyqtSignal
from datetime import datetime

class QAWorker(QThread):
    """
    run 함수 안에 나중에 백엔드 팀이 만든 게임 실행이나 AI API 관련함수 넣기.
    """
    # 1. 일꾼이 사장님(메인 UI)에게 보낼 귓속말(Signal) 종류를 정의합니다.
    final_config_signal = pyqtSignal(dict)  # 최종 config 딕셔너리
    finished_signal = pyqtSignal() # QThread 퇴근 신호
    log_signal = pyqtSignal(str)       # "지금 게임 켰어요~" 같은 텍스트 진행 상황
    error_signal = pyqtSignal(dict) # 분석이 끝났을 때 뱉어낼 최종 AI 리포트 결과

    def run(self):
        current_mode = self.config.get("mode")
        if current_mode == "new": # 새로 시작하는 모드라면
            game_path = self.config.get("game_file")
            txt_path = self.config.get("txt_file")
        elif current_mode == "resume": # 기존걸 다시 하는 모드라면
            qa_path = self.config.get("qa_file")

        self.is_running = True  # QAWorker(QThread) 실행 플래그
        self.log_signal.emit("🚀 파일(영상) 분석 테스트를 시작합니다...")
        
        # 영상이든 파일이든 총 길이(나중에 백엔드에서 주겟지)
        total_length = 30
        
        # 1초부터 total_length까지 반복하며, 1초마다 진행 상황을 로그로 보냄
        for current_time in range(1, total_length + 1):
            
            # 🚨 1. 유저가 중간에 [테스트 중지]를 눌렀을 때 (강제 중단)
            if not self.is_running:
                self.log_signal.emit("🛑 사용자가 테스트를 중지했습니다. 대기 상태로 돌아갑니다.")
                return # 스레드 즉시 종료
                
            # 일하는 척(백엔드팀 함수 호출 예정)
            self.log_signal.emit("🚀 시연용 게임(test.exe) 실행 중...")
            time.sleep(1.5) 
            self.log_signal.emit("📸 게임 화면 캡처 및 로그 수집 중...")
            time.sleep(2) 
            self.log_signal.emit("🤖 수집된 데이터 AI 분석 진행 중...")
            time.sleep(2) 

            # 가짜로 찾아낸 AI 에러 리포트 (나중에 백엔드팀 함수이름넣을거고 지금은 더미)
            dummy_result = {
                "title": "[UI/시각] 상점 진입 시 골드 텍스트 겹침",
                "content": "Expected: 상점 팝업 중앙 정상 출력\nActual: 폰트 깨짐 및 10% 우측 치우침 발생."
            }
            
            self.log_signal.emit("✅ AI 분석 완료! 결과를 대시보드에 띄웁니다.")
            self.error_signal.emit(dummy_result)
            
        if self.is_running:
            self.log_signal.emit("✅ 입력된 영상(파일)의 분석을 모두 마쳤습니다! (1턴 종료)")
            self.finished_signal.emit() # 완료 신호

import time
from PyQt6.QtCore import QThread, pyqtSignal
# fault_injector.py에서 가져오기
from fault_injector import FaultInjector, find_process_by_window_title
import qa_flow, thread

class FaultInjectorWorker(QThread):
    """
    게임 프로세스에 결함주입 전용 큐쓰레드
    """
    # 사장님(UI)에게 텍스트 로그를 보낼 시그널
    log_signal = pyqtSignal(str) 
    # 일꾼 퇴근(완료) 신호 (버튼 다시 켜줄 때 사용)
    finished_signal = pyqtSignal() 

    def __init__(self, target_title="Maze Trials", parent=None):
        super().__init__(parent)
        self.target_title = target_title
        self.demoWorking = True

    def run(self):
        self.log_signal.emit(f"👾 [결함 주입기] '{self.target_title}' 프로세스 찾는 중...")
        pid = find_process_by_window_title(self.target_title)
        
        if not pid:
            self.log_signal.emit(f"❌ [결함 주입기] '{self.target_title}' 창을 찾을 수 없습니다! 게임을 먼저 켜주세요.")
            self.finished_signal.emit()
            return

        try:
            # fault_injector가 뱉는 로그를 람다 함수로 낚아채서 우리의 log_signal로 쏴줍니다.
            with FaultInjector(pid, on_log=lambda msg: self.log_signal.emit(f"👾 [결함 주입기] {msg}")) as fi:
                
                self.log_signal.emit("🎬 [데모 시작] 8초간 응답 없음(Hang) 유발...")
                fi.hang(8)

                if not self.demoWorking: return # 강제 중지 체크

                self.log_signal.emit("🎬 [데모 진행] 12초간 성능 저하(Stutter) 유발...")
                fi.stutter(12)

                # if not self.demoWorking: return
                # fi.crash() # 🚨 크래시는 게임이 완전히 꺼지므로, 원하실 때만 주석을 푸세요!

                self.log_signal.emit("✅ [데모 완료] 결함 주입 시나리오가 끝났습니다.")
        
        except Exception as e:
            self.log_signal.emit(f"❌ [결함 주입기] 오류 발생: {e}")
        finally:
            self.finished_signal.emit() # 정상 종료든 에러든 끝났다고 알림

def start_demo_injection(ui):
    # 1. 다다닥 연타 방지를 위해 데모 진행 중에는 버튼 잠금!
    ui.btnDemoBug.setEnabled(False)

    # 2. 결함 주입 일꾼 고용 (가비지 컬렉터에 안 날아가게 ui 객체에 묶어둠)
    ui.demo_worker = FaultInjectorWorker(target_title="Maze Trials")

    # 3. 일꾼의 로그를 지영님의 기존 함수(update_realtime_log)에 연결
    ui.demo_worker.log_signal.connect(lambda msg: qa_flow.update_realtime_log(ui, msg))

    # 4. 일이 다 끝나면 다시 버튼 잠금 해제
    ui.demo_worker.finished_signal.connect(lambda: ui.btnDemoBug.setEnabled(True))

    # 5. 일꾼 출발!
    ui.demo_worker.start()