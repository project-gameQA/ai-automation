import sys
from PyQt6.QtWidgets import QApplication, QDialog, QFileDialog, QMainWindow
from PyQt6 import uic
import time
from PyQt6.QtCore import QThread, pyqtSignal
from datetime import datetime

def open_game_file_dialog(self):
    # 파일 다이얼로그 열기
    file_dialog = QFileDialog(self)
    file_dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
    file_dialog.setNameFilter("Game Files (*.mp4);;All Files (*)")
    
    if file_dialog.exec():
        selected_file = file_dialog.selectedFiles()[0]
        self.gameFileRoute.setText(selected_file)  # 선택한 파일 경로를 텍스트 박스에 표시

def open_txt_file_dialog(self):
    # 텍스트 파일 다이얼로그 열기
    file_dialog = QFileDialog(self)
    file_dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
    file_dialog.setNameFilter("Text Files (*.csv);;All Files (*)")

    if file_dialog.exec():
        selected_file = file_dialog.selectedFiles()[0]
        self.txtFileRoute.setText(selected_file)  # 선택한 파일 경로를 텍스트 박스에 표시

def go_to_qa_window(self):
    print("qa화면으로 이동합니다.")
    self.stackedWidget.setCurrentWidget(self.qa_window)

# 클릭했을 때 텍스트 창을 바꿔치기(에러 리포트)
def show_error_detail(self, item):
    selected_title = item.text() # 클릭한 항목의 제목을 가져옴
    
    detail_report = dummy_ai_reports[selected_title] # 제목에 맞는 상세 리포트 내용 가져오기
    
    self.errorReport.setText(detail_report) # 내용을 errorReport에 쏴주기

# 에러 수동추가 팝업
class Error_Plus_Dialog(QDialog):
    """ 에러 보고서 수동 추가 팝업 클래스 """
    def __init__(self):
        super().__init__()
        uic.loadUi("D:/project_gameQA/ai-automation/QA_UI/error_report_plus.ui", self) # 방금 만든 팝업창 ui 로드
        self.saveAndHistoryAdd.clicked.connect(self.accept) # 저장 버튼 누르면 'OK'하고 닫기

def open_error_plus_popup(ui):
    """ 사람이 수동으로 에러 추가할 때 팝업창 띄우고, 저장하면 히스토리에 추가하는 함수 """
    dialog = Error_Plus_Dialog()
    
    # ⭐️ 팝업창을 띄우고 기다립니다. 유저가 [저장]을 눌러야 다음 줄로 넘어갑니다.
    if dialog.exec(): 
        full_content = dialog.content.toPlainText().strip() # 입력 내용
        
        if not full_content:
            print("내용이 비어있어서 저장하지 않습니다.")
            return 
        
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S") # 현재 시간 가져오기
        
        raw_title = full_content.split('\n')[0] # 첫번째 줄만 떼오기
        if len(raw_title) > 15: # 제목이 너무 길면 15글자까지만 보여주고 "..." 붙이기
            raw_title = raw_title[:15] + "..."

        new_title = f"[수동 보고] {raw_title}"
        
        full_content = ( # 상세 내용 양식 적용
            f"■ 보고 방식: 수동 리포트\n"
            f"■ 발생 시간: {current_time}\n"
            f"■ 첨부 이미지: (여기에 나중에 이미지 경로 들어감)\n"
            f"----------------------------------------\n"
            f"■ 상세 내용:\n{full_content}"
        )

        ui.errorReportHistory.addItem(new_title) # 히스토리에 저장할 제목
        dummy_ai_reports[new_title] = full_content # 데이터 창고에도 저장

        ui.stackedWidget.setCurrentWidget(ui.qa_window) # 리스트가 있는 QA 화면으로 자동 이동
        ui.errorReport.setText(full_content) # 방금 추가한 에러의 '상세 내용'을 텅 빈 리포트 창에 즉시 띄워

        print("새 에러가 히스토리에 추가되었습니다.")

# QThread 관련
class QAWorker(QThread):
    """
    run 함수 안에 나중에 백엔드 팀이 만든 게임 실행이나 AI API 관련함수 넣기.
    """
    # 1. 일꾼이 사장님(메인 UI)에게 보낼 귓속말(Signal) 종류를 정의합니다.
    log_signal = pyqtSignal(str)       # "지금 게임 켰어요~" 같은 텍스트 진행 상황
    finished_signal = pyqtSignal(dict) # 분석이 끝났을 때 뱉어낼 최종 AI 리포트 결과

    def run(self):
        # 일하는 척
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
        
        # 분석이 다 끝났으니 사장님한테 결과물을 제출합니다!
        self.log_signal.emit("✅ AI 분석 완료! 결과를 대시보드에 띄웁니다.")
        self.finished_signal.emit(dummy_result)

def update_realtime_log(ui, message):
    """ 일꾼이 보내준 메시지를 ui 화면에 찍어줍니다 """
    print(f"[시스템] {message}")
    ui.allLog.append(message) 

def show_qa_result(ui, result_data):
    """ QThread에서 보내준 결과를 저장 및 세팅 """
    
    title = result_data["title"]
    content = result_data["content"]
    
    dummy_ai_reports[title] = content # 에러 내용 저장
    ui.errorReportHistory.addItem(title) # 에러 히스토리에 제목 넣기
    ui.errorReport.setText(content) # 에러 상세 리포트 창에 내용 띄우기

    
# 백엔드가 아직 안 만들어졌으니, 내가 임의로 만든 가짜 AI 분석 결과
dummy_ai_reports = {
    "에러 #1: 상점 텍스트 깨짐": """[AI 분석 리포트]
- 카테고리: UI/UX (시각 버그)
- 예상 결과: 상점 진입 시 '구매하기' 버튼 텍스트가 정상 출력되어야 함.
- 실제 결과: 폰트 파일 누락으로 인해 '???' 형태로 출력됨.
- 조치 권고: 클라이언트 폰트 에셋 확인 요망.""",

    "에러 #2: 인벤토리 아이템 증발": """[AI 분석 리포트]
- 카테고리: 로직/서버
- 예상 결과: 몬스터 사냥 시 획득한 '낡은 검'이 인벤토리에 추가되어야 함.
- 실제 결과: 드롭 로그는 발생했으나, 인벤토리 DB 업데이트 로그가 없음.
- 조치 권고: 아이템 획득 패킷 전송 및 DB 저장 로직 확인 요망.""",

    "에러 #3: 보스전 클라이언트 튕김": """[AI 분석 리포트]
- 카테고리: 성능/크래시
- 예상 결과: 보스 광역기 패턴 시 프레임 저하 없이 진행되어야 함.
- 실제 결과: 메모리 사용량이 급증(Memory Leak)하며 클라이언트 강제 종료됨.
- 조치 권고: 파티클 메모리 해제 로직 점검."""
}