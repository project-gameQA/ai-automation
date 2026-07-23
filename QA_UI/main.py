import sys
from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget
from PyQt6 import uic
import logic, menu_bar, thread, qa_flow
from qt_material import apply_stylesheet, QtStyleTools, list_themes

class QAUIapp(QMainWindow, QtStyleTools):

    BASE_WIDTH = 1042
    BASE_HEIGHT = 700

    MIN_RATIO = 0.8    # 이보다 작아지면 글씨가 안 읽힘
    MAX_RATIO = 1.5    # 이보다 커지면 레이아웃이 터짐

    def __init__(self):
        super().__init__()
        uic.loadUi("D:/project_gameQA/ai-automation/QA_UI/qa_first.ui", self)

        # ui 폰트
        self._base_fonts = {}
        for w in self.findChildren(QWidget):
            size = w.font().pointSizeF()
            if size > 0: # -1이면 픽셀 단위 지정된 경우 → 건너뜀
                self._base_fonts[w] = size
        self._last_ratio = 0.0    # 중복 적용 방지용

        self.setMinimumSize(self.minimumSizeHint()) # 최소 창 크기

        # 초기화
        self.is_saved = True # 저장돼있나용?
        self.step = 0 # 몇번째인가용
        self.found_error = [] # 어떤에러를 찾았나용
        self.final_config = "" # 파일 기본정보는용
        self.session = None      
        self.state = thread.RunState.IDLE # 쓰레드 상태 어떠세용

        qa_flow.restore_qa_result(self)
        self.stackedWidget.setCurrentWidget(self.start_window) 
        self.newOrResumeWindow.setCurrentWidget(self.newPage)

        # 버튼이 눌렸을 때(clicked) 실행할 함수 연결
        self.newToggle.toggled.connect(lambda: logic.new_resume_toggle(self))
        self.resumeToggle.toggled.connect(lambda: logic.new_resume_toggle(self))        
        
        self.btnGameFileOpen.clicked.connect(lambda: logic.open_game_file_dialog(self)) # 게임 파일 선택(파일탐색기)
        self.btnTxtFileOpen.clicked.connect(lambda: logic.open_txt_file_dialog(self)) # 텍스트 파일 선택(파일탐색기)
        self.btnQAFileOpen.clicked.connect(lambda: logic.open_qa_file_dialog(self)) # QA 파일 선택(파일탐색기)
    
        self.btnGoDashbord.clicked.connect(lambda: logic.go_dashboard(self)) # 대시보드로 이동
        self.btnStartQA.clicked.connect(self.toggle_qa_test) # QA 시작 버튼 클릭 시 QA 테스트 시작(QThread)
        self.btnGoDashbord.clicked.connect(lambda: logic.update_file_route(self)) # 파일 경로를 UI에 띄움

        self.errorReportHistory.addItems(self.report_cache.keys()) # 데이터 삽입
        
        # 리스트에서 특정 항목(item)이 '클릭'되면 -> show_error_detail 함수 실행해!
        self.errorReportHistory.itemClicked.connect(lambda item:qa_flow.show_error_detail(self, item))

       
        # 메뉴바 애들 ~~~
        self.actionsplash_screen.triggered.connect(lambda: menu_bar.splash_screen(self)) # 딴거 시작하고 싶을때 첫 화면으로
        self.actionopen_new_window.triggered.connect(self.open_new_window) # 새 창 열기
        self.actionexport.triggered.connect(lambda: menu_bar.export_file(self)) # 다른 이름으로 저장
        self.actionsave.triggered.connect(lambda: menu_bar.save(self)) # 저장
        self.actionsave_as.triggered.connect(lambda: menu_bar.save_as(self))
        self.actionclose.triggered.connect(self.close) # 프로그램 종료
        self.actionFind.triggered.connect(lambda: menu_bar.open_search(self))

        # 에러 추가 팝업+거기서 저장하면 히스토리로 감
        self.actionerror_plus.triggered.connect(lambda: menu_bar.open_error_plus_popup(self))

    
    def toggle_qa_test(self):
        """
        [▶ QA 시작] 버튼을 누르면 QA 및 QThread 실행
        [▶ QA 종료] 버튼을 누르면 QA 및 QThread 종료
        """
        if not self.state == thread.RunState.RUNNING: # 시작 안함            
            qa_flow.qa_start(self)
        else: # 시작함
            qa_flow.qa_stop(self)

    def open_new_window(self):
        """
        새창 열기
        """
        self.new_window = QAUIapp()  # 새 창 인스턴스 생성
        self.new_window.show()  # 새 창 표시

    def closeEvent(self, event):
        """
        창 끄기(원래 있는 함수 덮어쓰기)
        """
        if menu_bar.close_application(self):
            event.accept()      # 닫기 허용
        else:
            event.ignore()      # 닫기 취소

    def resizeEvent(self, event):
        """
        폰트 반응형. Qt가 창 크기 변경 시 자동 호출
        """
        super().resizeEvent(event)
        # print(f"[resize] 폭={self.width()} ratio={self.width()/self.BASE_WIDTH:.2f}")

        # ── 가로/세로 중 더 빡빡한 쪽을 기준으로 ──
        # 둘 다 봐야 "넓고 납작한 창"에서 글씨가 안 잘림
        ratio = min(self.width()  / self.BASE_WIDTH,
                    self.height() / self.BASE_HEIGHT)

        ratio = max(self.MIN_RATIO, min(self.MAX_RATIO, ratio))

        # 미세한 변화는 무시 (드래그 중 렉 방지)
        if abs(ratio - self._last_ratio) < 0.03:
            return
        self._last_ratio = ratio

        for widget, base in self._base_fonts.items():
            font = widget.font()            # 패밀리·굵기는 그대로 두고
            font.setPointSizeF(base * ratio)  # 크기만 교체
            widget.setFont(font)

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