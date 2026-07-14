import json
import os
import sys
from PyQt6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox
from PyQt6 import uic
import time
from PyQt6.QtCore import QThread, pyqtSignal
from datetime import datetime

def new_resume_toggle(self):
    """ [new]과 [resume] 탭을 왔다갔다 할 때, ui 화면을 바꿔주는 함수 """
    if self.newToggle.isChecked():
        self.newOrResumeWindow.setCurrentIndex(0)
        return "new"
    else:
        self.newOrResumeWindow.setCurrentIndex(1)
        return "resume"

def open_game_file_dialog(self):
    """ 게임 파일 탐색기 열기 """
    file_dialog = QFileDialog(self)
    file_dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
    file_dialog.setNameFilter("Game Files (*.mp4);;All Files (*)")
    
    if file_dialog.exec():
        selected_file = file_dialog.selectedFiles()[0]
        self.gameFileRoute.setText(selected_file)  # 선택한 파일 경로를 텍스트 박스에 표시

def open_txt_file_dialog(self):
    """ 텍스트 파일 탐색기 열기 """
    file_dialog = QFileDialog(self)
    file_dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
    file_dialog.setNameFilter("Text Files (*.txt);;All Files (*)")

    if file_dialog.exec():
        selected_file = file_dialog.selectedFiles()[0]
        self.txtFileRoute.setText(selected_file)  # 선택한 파일 경로를 텍스트 박스에 표시

def open_qa_file_dialog(self):
    """ QA 파일 탐색기 열기 """
    file_dialog = QFileDialog(self)
    file_dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
    file_dialog.setNameFilter("QA Files (*.txt);;All Files (*)")
    
    if file_dialog.exec():
        selected_file = file_dialog.selectedFiles()[0]
        self.QAFileRoute.setText(selected_file)  # 선택한 파일 경로를 텍스트 박스에 표시

def check_path(self, line_edit, expected_ext=None):
    """ 경로 유효성을 검사 """
    path = line_edit.text().strip()
    if not os.path.exists(path):
        line_edit.setStyleSheet("border: 1px solid red;")
        QMessageBox.information(self, "경로 이상", f"경로를 확인해주세요!\n현재 경로: {path}")
        return False
    
    # 파일 경로(path)가 특정 확장자(expected_ext)로 끝나는지 검사
    if expected_ext and not path.lower().endswith(expected_ext):
        line_edit.setStyleSheet("border: 1px solid orange;")
        QMessageBox.information(self, "확장자 이상", f"확장자를 확인해주세요!\n필요 확장자: {expected_ext}")
        return False
    line_edit.setStyleSheet("")
    return True

def qa_reset_next(self):
    """ 기존 QA파일을 처음부터 다시 테스트할지, 아니면 이어서 테스트할지 선택하는 토글 """
    if self.btnReset.isChecked():
        return "reset"
    else:
        return "next"
    
def go_dashboard(self):
    """ QA 대시보드 화면으로 이동 및 check_path() """

    if new_resume_toggle(self) == "new": # new모드일때
        if not check_path(self, self.gameFileRoute, expected_ext=".mp4"): # 게임경로
            return # 통과 못하면 멈춰
        if not check_path(self, self.txtFileRoute, expected_ext=".txt"): # 문서경로
            return # 통과 못하면 멈춰
        
    else: # resume모드일때
        if not check_path(self, self.QAFileRoute, expected_ext=".txt"): # qa파일경로
            return # 통과 못하면 멈춰
        
    self.stackedWidget.setCurrentWidget(self.qa_window)  # qa_window으로 이동


def make_config(mode, **kwargs):
    """ UI에서 긁어온 값을 받아서 표준 config 딕셔너리로 변환 """
    config = {"mode": mode}

    if mode == "new":
        config["game_file"] = kwargs.get("game_file", "")
        config["txt_file"] = kwargs.get("txt_file", "")
    elif mode == "resume":
        config["qa_file"] = kwargs.get("qa_file", "")
        config["resume"] = kwargs.get("resume", "reset")

        if config["resume"] == "next":
            config["next_index"] = kwargs.get("next_index", 0)
        else:
            config["next_index"] = 0  # reset 모드에서는 next_index를 0으로 설정

    return config

def check_config(config):
    """ config 딕셔너리의 유효성을 검사하는 함수 """
    mode = config.get("mode")

    if mode == "new":
        if not config.get("game_file"):
            return False, "게임 파일이 선택되지 않았습니다."
        if not config.get("txt_file"):
            return False, "텍스트 파일이 선택되지 않았습니다."
    elif mode == "resume":
        if not config.get("qa_file"):
            return False, "QA 파일이 선택되지 않았습니다."
        if config.get("resume") not in ["reset", "next"]:
            return False, "유효하지 않은 이어서 테스트 옵션입니다."
        if config.get("resume") == "next" and not isinstance(config.get("next_index"), int):
            return False, "다음 인덱스가 유효하지 않습니다."
    else:
        return False, "유효하지 않은 모드입니다."

    return True, ""

def save_checkpoint(qa_path, step):
    """
    게임 테스트를 어디까지 했는지 파일로 저장
    예: save.qa → save.checkpoint.json 로 저장
    """
    checkpoint_path = qa_path + ".checkpoint.json"

    with open(checkpoint_path, "w") as file:
        json.dump({"step": step}, file)
    return checkpoint_path

def load_checkpoint(qa_path):
    """
    게임 테스트를 어디까지 했는지 파일에서 불러오기
    예: save.qa → save.checkpoint.json 로 불러오기
    """
    checkpoint_path = qa_path + ".checkpoint.json"
    try:
        with open(checkpoint_path, "r") as file:
            data = json.load(file)
            return data.get("step", 0)  # step 값 반환, 없으면 0 반환
    except FileNotFoundError:
        return 0  # 체크포인트 파일이 없으면 0 반환

# 클릭했을 때 텍스트 창을 바꿔치기(에러 리포트)
def show_error_detail(self, item):
    selected_title = item.text() # 클릭한 항목의 제목을 가져옴
    detail_report = self.dummy_ai_reports[selected_title] # 제목에 맞는 상세 리포트 내용 가져오기
    self.errorReport.setText(detail_report) # 내용을 errorReport에 쏴주기

# QThread 관련
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
        
def config_finish(ui):
    """ 
    QA 시작 버튼을 눌렀을 때 실행되는 함수
    config 딕셔너리를 최종 완성해서 QThread에 넘김
    """
    # 1. 아까 만드셨던 함수를 써서 "new" 인지 "resume" 인지 파악합니다.
    current_mode = "new" if new_resume_toggle(ui) else "resume"

    # 2. current_mode에 따라 화면(UI)에서 각기 다른 재료를 긁어옵니다.
    if current_mode == "new":
        # '새거' 모드일 때: UI 입력칸에서 글자를 가져옴
        input_game = ui.gameFileRoute.text()
        input_txt = ui.txtFileRoute.text()
        
        # 긁어온 값들을 make_config 파라미터 이름에 맞춰서 쏙쏙 던져줍니다!
        final_config = make_config(mode=current_mode, game_file=input_game, txt_file=input_txt)

    elif current_mode == "resume":
        # '기존거' 모드일 때: 기존 QA 파일 경로를 가져옴
        input_qa = ui.QAFileRoute.text()
        
        # 처음부터(reset)인지 이어서(next)인지 UI에서 확인 (예시)
        if ui.qa_reset_next == "next":
            resume_status = "next"
            # 이어서 할 경우, 몇 번째 인덱스부터 할지 UI나 내부 데이터에서 가져오기
            next_idx = ui.load_checkpoint(input_qa)
        else:
            resume_status = "reset"
            next_idx = 0
            
        # 긁어온 값들을 던져줍니다!
        final_config = make_config(mode=current_mode, qa_file=input_qa, resume=resume_status, next_index=next_idx)

    # 3. 완성된 final_config 딕셔너리 확인! (이걸 QThread로)
    print("완성된 설정값:", final_config) # EX. 완성된 설정값: {'mode': 'new', 'game_file': '', 'txt_file': ''}
    return final_config

def update_file_route(ui, final_config):
    """ 파일 경로를 UI에 띄움 """
    game_path = os.path.basename(final_config["game_file"])
    txt_path = os.path.basename(final_config["txt_file"])
    file_path = f"게임: {game_path} | 문서: {txt_path}"
    
    ui.filesRoute.setText(file_path)

def update_realtime_log(ui, message):
    """ 일꾼이 보내준 메시지를 ui에 업뎃 """
    print(f"[시스템] {message}")
    ui.allLog.append(message) 

def show_qa_result(ui, result_data):
    """ QThread에서 보내준 결과를 저장 및 세팅 """
    
    title = result_data["title"]
    content = result_data["content"]
    
    ui.dummy_ai_reports[title] = content # 에러 내용 저장
    ui.errorReportHistory.addItem(title) # 에러 히스토리에 제목 넣기
    ui.errorReport.setText(content) # 에러 상세 리포트 창에 내용 띄우기