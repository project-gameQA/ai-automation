"""
main.py
=======
프로그램 진입점. 메인 윈도우 클래스와 시그널 연결.

실행:
    python main.py
"""

import sys

from PyQt6 import uic
from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget
from qt_material import apply_stylesheet, QtStyleTools

import settings
import logic
import menu_bar
import thread
import qa_flow


class QAUIapp(QMainWindow, QtStyleTools):

    BASE_WIDTH = 1042
    BASE_HEIGHT = 700
    MIN_RATIO = 0.8   # 이보다 작아지면 글씨가 안 읽힘
    MAX_RATIO = 1.5   # 이보다 커지면 레이아웃이 터짐

    def __init__(self):
        super().__init__()

        # ⚠️ 절대경로 대신 settings.ui_path()를 쓴다.
        #    "D:/project_gameQA/..." 로 박아두면 다른 PC에서 바로 죽는다.
        uic.loadUi(settings.ui_path("qa_first.ui"), self)

        self._setup_fonts()
        self._init_state()
        self._relabel_widgets()
        self._connect_signals()

        # 초기 화면
        qa_flow.restore_qa_result(self)
        self.stackedWidget.setCurrentWidget(self.start_window)
        self.newOrResumeWindow.setCurrentWidget(self.newPage)

    # ── 초기화 ────────────────────────────────────────────

    def _setup_fonts(self):
        """폰트 반응형을 위해 위젯별 기본 크기를 기억해둔다."""
        self._base_fonts = {}
        for w in self.findChildren(QWidget):
            size = w.font().pointSizeF()
            if size > 0:   # -1이면 픽셀 단위 지정 → 건너뜀
                self._base_fonts[w] = size
        self._last_ratio = 0.0   # 중복 적용 방지
        self.setMinimumSize(self.minimumSizeHint())

    def _init_state(self):
        """세션 관련 상태 초기화. 실제 값은 logic.reset_session_state가 채운다."""
        # ── 무엇을 테스트하는가 ──
        self.session_dir = ""       # 세션 폴더 절대경로
        self.session_summary = {}   # summary.json에서 읽은 요약
        self.source_mode = "replay"  # "live" | "replay"
        self.target_title = ""      # 라이브일 때 대상 창 제목

        # ── 어디까지 봤는가 ──
        self.event_cursor = 0       # events.jsonl을 몇 줄까지 읽었나
        self.event_total = 0        # events.jsonl 총 줄 수

        # ── 결과 ──
        self.found_error = []       # 표준형 에러 항목 리스트
        self.report_cache = {}      # title -> content (검색/조회용)

        # ── 저장 상태 ──
        self.current_save_path = None   # 체크포인트(.json) 경로
        self.last_export_path = None    # Export(.txt) 경로 (별도 관리)
        self.is_saved = True

        # ── 실행 상태 ──
        # ⚠️ worker를 None으로 미리 만들어둔다.
        #    없으면 QA를 한 번도 안 돌리고 창을 닫을 때 AttributeError가 난다.
        self.worker = None
        self.state = thread.RunState.IDLE
        self.keep_record = True
        self.has_log = False

    def _relabel_widgets(self):
        """
        .ui를 못 고치므로 라벨 텍스트를 코드에서 바꾼다.

        위젯 용도가 바뀌었다:
            gameFileRoute → 세션 폴더 (리플레이)
            txtFileRoute  → 대상 창   (라이브)
            QAFileRoute   → QA 파일(.json)
        """
        self.gameLabel.setText("세션 폴더")
        self.txtLabel.setText("대상 창(라이브)")
        self.QALabel.setText("QA 파일")

        self.gameFileRoute.setPlaceholderText("끝난 세션 폴더를 열어 결과 보기")
        self.txtFileRoute.setPlaceholderText("실행 중인 게임 창을 골라 지금 QA하기")
        self.QAFileRoute.setPlaceholderText("이전에 저장한 QA 파일(.json)")

        self.btnGameFileOpen.setToolTip("이미 끝난 세션 폴더를 고릅니다 (리플레이)")
        self.btnTxtFileOpen.setToolTip("열려 있는 창 목록에서 QA 대상을 고릅니다 (라이브)")

    def _connect_signals(self):
        """버튼/메뉴 연결."""
        # ── 시작 화면 ──
        self.newToggle.toggled.connect(lambda: logic.new_resume_toggle(self))
        self.resumeToggle.toggled.connect(lambda: logic.new_resume_toggle(self))

        self.btnGameFileOpen.clicked.connect(
            lambda: logic.open_game_file_dialog(self))   # 세션 폴더
        self.btnTxtFileOpen.clicked.connect(
            lambda: logic.open_txt_file_dialog(self))    # 대상 창
        self.btnQAFileOpen.clicked.connect(
            lambda: logic.open_qa_file_dialog(self))     # QA 파일

        # ⚠️ 여기에 update_file_route를 추가로 연결하면 안 된다.
        #    go_dashboard는 검증 실패 시 조용히 return하는데,
        #    두 번째 슬롯은 그것과 무관하게 실행돼서 화면이 잘못 갱신된다.
        #    go_dashboard가 내부에서 update_file_route를 부른다.
        self.btnGoDashbord.clicked.connect(lambda: logic.go_dashboard(self))

        # ── QA 화면 ──
        self.btnStartQA.clicked.connect(self.toggle_qa_test)
        self.errorReportHistory.itemClicked.connect(
            lambda item: qa_flow.show_error_detail(self, item))
        self.errorReportHistory.itemDoubleClicked.connect(
            lambda item: qa_flow.open_error_frame(self, item))

        # ── 메뉴바 ──
        self.actionsplash_screen.triggered.connect(
            lambda: menu_bar.splash_screen(self))
        self.actionopen_new_window.triggered.connect(self.open_new_window)
        self.actionsave.triggered.connect(lambda: menu_bar.save(self))
        self.actionsave_as.triggered.connect(lambda: menu_bar.save_as(self))
        self.actionexport.triggered.connect(lambda: menu_bar.export_file(self))
        self.actionclose.triggered.connect(self.close)
        self.actionFind.triggered.connect(lambda: menu_bar.open_search(self))
        self.actionerror_plus.triggered.connect(
            lambda: menu_bar.open_error_plus_popup(self))

        # 아직 기능이 없는 메뉴는 잠가둔다.
        # (발표 중에 눌렀을 때 아무 반응이 없는 게 제일 나쁘다)
        for name in ("actionlog_kakuninn", "actionall_log",
                     "actiongenzai_log", "actionscreen_shot",
                     "actionsaishin_error_log"):
            action = getattr(self, name, None)
            if action is not None:
                action.setEnabled(False)
                action.setToolTip("아직 준비 중인 기능입니다")

    # ── 동작 ──────────────────────────────────────────────

    def toggle_qa_test(self):
        """[▶ QA 시작] / [⏹ QA 중지] 토글."""
        if self.state != thread.RunState.RUNNING:
            qa_flow.qa_start(self)
        else:
            qa_flow.qa_stop(self)

    def open_new_window(self):
        """새 창 열기 (Ctrl+N)."""
        self.new_window = QAUIapp()
        self.new_window.show()

    # ── Qt 이벤트 재정의 ──────────────────────────────────

    def closeEvent(self, event):
        """창 닫기. 저장 여부를 확인하고 워커를 정리한다."""
        if menu_bar.close_application(self):
            event.accept()
        else:
            event.ignore()

    def resizeEvent(self, event):
        """폰트 반응형. 창 크기가 바뀔 때 Qt가 자동 호출한다."""
        super().resizeEvent(event)

        # 가로/세로 중 더 빡빡한 쪽 기준.
        # 둘 다 봐야 '넓고 납작한 창'에서 글씨가 안 잘린다.
        ratio = min(self.width() / self.BASE_WIDTH,
                    self.height() / self.BASE_HEIGHT)
        ratio = max(self.MIN_RATIO, min(self.MAX_RATIO, ratio))

        # 미세한 변화는 무시 (드래그 중 렉 방지)
        if abs(ratio - self._last_ratio) < 0.03:
            return
        self._last_ratio = ratio

        for widget, base in self._base_fonts.items():
            font = widget.font()          # 패밀리·굵기는 그대로 두고
            font.setPointSizeF(base * ratio)   # 크기만 교체
            widget.setFont(font)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    apply_stylesheet(app, theme="dark_amber.xml")

    window = QAUIapp()
    window.show()

    sys.exit(app.exec())
