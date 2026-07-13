import sys
from PyQt6.QtWidgets import QApplication, QDialog, QFileDialog, QMainWindow
from PyQt6 import uic
import logic  # logic.py 파일을 가져옵니다.

class QAUIapp(QMainWindow):
    def __init__(self):
        super().__init__()
        uic.loadUi("D:/project_gameQA/ai-automation/QA_UI/qa_first.ui", self)

        # 버튼이 눌렸을 때(clicked) 실행할 함수 연결
        self.stackedWidget.setCurrentWidget(self.start_window) # 첫 화면으로 초기화

        self.gameFileOpen.clicked.connect(lambda: logic.open_game_file_dialog(self)) # 게임 파일 선택(파일탐색기)
        self.txtFileOpen.clicked.connect(lambda: logic.open_txt_file_dialog(self)) # 텍스트 파일 선택(파일탐색기)
        self.qaStart.clicked.connect(lambda: logic.go_to_qa_window(self)) # QA 시작 버튼 클릭 시 QA 화면으로 이동
        self.qaStart.clicked.connect(self.start_qa_test) # QA 시작 버튼 클릭 시 QA 테스트 시작(QThread)

        self.errorReportHistory.addItems(logic.dummy_ai_reports.keys()) # 더미데이터 삽입
        
        # 리스트에서 특정 항목(item)이 '클릭'되면 -> show_error_detail 함수 실행해!
        self.errorReportHistory.itemClicked.connect(lambda item: logic.show_error_detail(self, item))

        # 에러 추가 팝업+거기서 저장하면 히스토리로 감
        self.actionerror_plus.triggered.connect(lambda: logic.open_error_plus_popup(self))

    def start_qa_test(self):
        """ [테스트 시작] 버튼을 누르면 실행되는 함수. QThread """

        self.worker = logic.QAWorker()
        
        self.worker.log_signal.connect(lambda message: logic.update_realtime_log(self, message))
        self.worker.finished_signal.connect(lambda result_data: logic.show_qa_result(self, result_data))
        
        self.worker.start()


if __name__ == "__main__":
    # 1. 프로그램 시작
    app = QApplication(sys.argv)

    # 2. 번듯한 건물(메인 윈도우) 짓기!
    window = QAUIapp()
    window.show()

    # 3. 공장장에게 프로그램 계속 돌리라고 명령
    sys.exit(app.exec())