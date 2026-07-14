import sys
from PyQt6.QtWidgets import QApplication, QMainWindow
from PyQt6 import uic
import logic  # logic.py 파일을 가져옵니다.
import menu_bar  # menu_bar.py 파일을 가져옵니다.
from qt_material import apply_stylesheet, QtStyleTools, list_themes

class QAUIapp(QMainWindow, QtStyleTools):
    def __init__(self):
        super().__init__()
        uic.loadUi("D:/project_gameQA/ai-automation/QA_UI/qa_first.ui", self)

        # 초기화
        self.stackedWidget.setCurrentWidget(self.start_window) 
        self.newOrResumeWindow.setCurrentWidget(self.newPage)
        self.current_save_path = None 

        # 버튼이 눌렸을 때(clicked) 실행할 함수 연결
        self.newToggle.toggled.connect(lambda: logic.new_resume_toggle(self))
        self.resumeToggle.toggled.connect(lambda: logic.new_resume_toggle(self))        
        
        self.btnGameFileOpen.clicked.connect(lambda: logic.open_game_file_dialog(self)) # 게임 파일 선택(파일탐색기)
        self.btnTxtFileOpen.clicked.connect(lambda: logic.open_txt_file_dialog(self)) # 텍스트 파일 선택(파일탐색기)
        self.btnQAFileOpen.clicked.connect(lambda: logic.open_qa_file_dialog(self)) # QA 파일 선택(파일탐색기)
    
        self.btnGoDashbord.clicked.connect(lambda: logic.go_dashboard(self)) # 대시보드로 이동
        self.btnStartQA.clicked.connect(self.toggle_qa_test) # QA 시작 버튼 클릭 시 QA 테스트 시작(QThread)

        self.errorReportHistory.addItems(self.dummy_ai_reports.keys()) # 더미데이터 삽입
        
        # 리스트에서 특정 항목(item)이 '클릭'되면 -> show_error_detail 함수 실행해!
        self.errorReportHistory.itemClicked.connect(lambda item: logic.show_error_detail(self, item))

       
        # 메뉴바 애들 ~~~
        self.actionsplash_screen.triggered.connect(lambda: menu_bar.splash_screen(self)) # 딴거 시작하고 싶을때 첫 화면으로
        self.actionopen_new_window.triggered.connect(self.open_new_window) # 새 창 열기
        self.actionsave_as.triggered.connect(lambda: menu_bar.save_file_as(self)) # 다른 이름으로 저장
        self.actionsave.triggered.connect(lambda: menu_bar.save_file(self)) # 저장
        self.actionclose.triggered.connect(lambda: menu_bar.close_application(self)) # 프로그램 종료
        self.actionFind.triggered.connect(lambda: menu_bar.open_search(self))

        # 에러 추가 팝업+거기서 저장하면 히스토리로 감
        self.actionerror_plus.triggered.connect(lambda: menu_bar.open_error_plus_popup(self))

    
    def toggle_qa_test(self):
        """
        [▶ QA 시작] 버튼을 누르면 QA 및 QThread 실행
        [▶ QA 종료] 버튼을 누르면 QA 및 QThread 종료
        """
        if self.btnStartQA.text() == "▶ QA 시작": # 버튼 글씨가 '시작'일 경우            
            # 버튼을 빨간색 '중지' 버튼으로 바꿈(CSS 스타일 적용)
            self.btnStartQA.setText("⏹ QA 중지")
            self.btnStartQA.setStyleSheet("background-color: #E74C3C; color: white; font-weight: bold;")
            
            final_config = logic.config_finish(self) # 최종 config 딕셔너리 생성
            logic.update_file_route(self, final_config) # 파일 경로를 UI에 띄움

            self.worker = logic.QAWorker()
            
            self.worker.config = final_config # QThread에 최종 config 딕셔너리 전달
            self.worker.log_signal.connect(lambda message: logic.update_realtime_log(self, message)) # 평소 로그
            self.worker.error_signal.connect(lambda result_data: logic.show_qa_result(self, result_data)) # 에러 리포트
            
            self.worker.start()
            
        else: # 버튼 글씨가 '중지'일 경우
            # QAWorker(QThread) 종료(퇴근 플래그)
            if hasattr(self, 'worker') and self.worker.isRunning():
                # hasattr == self에 worker가 있으면 True, 없으면 False
                self.worker.is_running = False
            
            # 버튼 원상복구
            self.btnStartQA.setText("▶ QA 시작")
            self.btnStartQA.setStyleSheet("") # 스타일 초기화
            
            # (선택) 여기에 "테스트가 중지되었습니다. 기록이 삭제됩니다." 같은 팝업을 띄워도 됩니다.
            print("🛑 사용자가 테스트를 강제 중지했습니다!")

    def open_new_window(self):
        """
        새창 열기
        """
        self.new_window = QAUIapp()  # 새 창 인스턴스 생성
        self.new_window.show()  # 새 창 표시

    # 가짜 ai 리포트
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
        - 조치 권고: 파티클 메모리 해제 로직 점검.""",

        "에러 #4: 집에 가고싶음": """[AI 분석 리포트]
        - 카테고리: 불명
        - 예상 결과: 집에 감
        - 실제 결과: 집에 못감
        - 조치 권고: 집에 보내줘.""",

        "에러 #5: 흐으음": """[AI 분석 리포트]
        - 카테고리: 불명
        - 예상 결과: 뭐라고 해야할지 모르겠음
        - 실제 결과: 뭐라고 해야하지 진짜?
        - 조치 권고: 알수없음."""
    }


        


if __name__ == "__main__":
    # 1. 프로그램 시작
    app = QApplication(sys.argv)

    apply_stylesheet(app, theme='dark_amber.xml')

    # 2. 번듯한 건물(메인 윈도우) 짓기!
    window = QAUIapp()
    window.show()

    # 3. 공장장에게 프로그램 계속 돌리라고 명령
    sys.exit(app.exec())