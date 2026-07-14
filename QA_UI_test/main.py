import sys
import os
from PyQt6.QtWidgets import (QApplication, QDialog, QFileDialog, QMainWindow,
                             QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QComboBox, QPushButton, QTabWidget, QTextEdit, QGroupBox,
                             QMessageBox)
from PyQt6 import uic
import logic

UI_DIR = os.path.dirname(os.path.abspath(__file__))


class QAUIapp(QMainWindow):
    def __init__(self):
        super().__init__()
        uic.loadUi(os.path.join(UI_DIR, "qa_first.ui"), self)

        self._normal_eps = None
        self._worker = None

        # stackedWidget이 창 크기에 맞게 늘어나도록 centralwidget에 레이아웃 부여
        # (원래 절대좌표라 세로가 561로 고정돼 위젯이 눌리던 문제 해결)
        cw = self.centralWidget()
        lay = QVBoxLayout(cw); lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.stackedWidget)
        self.resize(1120, 800)               # 넉넉한 기본 크기
        self.setMinimumSize(900, 680)

        self.errorReportHistory.itemClicked.connect(lambda item: logic.show_error_detail(self, item))
        self.actionerror_plus.triggered.connect(lambda: logic.open_error_plus_popup(self))

        self._build_register_page()
        self._build_result_tabs()
        self.stackedWidget.setCurrentWidget(self.register_page)

    # ==================================================================
    # 등록 화면 (탭: 행동 등록 / 화면 등록)
    # ==================================================================
    def _build_register_page(self):
        page = QWidget()
        outer = QVBoxLayout(page)

        title = QLabel("게임 QA 자동화 — 등록")
        f = title.font(); f.setPointSize(15); f.setBold(True); title.setFont(f)
        outer.addWidget(title)

        regTabs = QTabWidget()
        regTabs.addTab(self._behavior_register_tab(), "행동 분석 등록 (봇)")
        regTabs.addTab(self._vision_register_tab(), "화면 분석 등록 (비전)")
        outer.addWidget(regTabs)

        self.stackedWidget.addWidget(page)
        self.register_page = page
        self.regTabs = regTabs
        self._refresh_status(self.gameSelect.currentText())

    def _behavior_register_tab(self):
        """탭1: 우리 행동 분석 등록 — 데이터 준비/학습/실행 4버튼."""
        tab = QWidget()
        root = QVBoxLayout(tab)
        root.setContentsMargins(24, 18, 24, 18)   # 여백(덜 답답하게)
        root.setSpacing(14)

        row = QHBoxLayout()
        row.addWidget(QLabel("게임:"))
        self.gameSelect = QComboBox()
        self.gameSelect.addItems(list(logic.GAME_MAP.keys()))
        self.gameSelect.currentTextChanged.connect(self._refresh_status)
        row.addWidget(self.gameSelect); row.addStretch(1)
        root.addLayout(row)

        g1 = QGroupBox("1. 정상 데이터 준비")
        v1 = QVBoxLayout(g1); v1.setContentsMargins(12, 8, 12, 12); v1.setSpacing(8)
        b1 = QHBoxLayout(); b1.setSpacing(10)
        self.btnGen = QPushButton("봇으로 생성 (시연용 · 실제 배포 시엔 회사 제공)")
        self.btnLoad = QPushButton("파일로 등록 (실제)")
        self.btnGen.clicked.connect(self._on_gen_data)
        self.btnLoad.clicked.connect(self._on_load_data)
        b1.addWidget(self.btnGen); b1.addWidget(self.btnLoad)
        v1.addLayout(b1)
        self.dataStatus = QLabel("정상 데이터: 준비 안 됨")
        v1.addWidget(self.dataStatus)
        root.addWidget(g1)

        g2 = QGroupBox("2. 기준 모델 학습")
        v2 = QVBoxLayout(g2); v2.setContentsMargins(12, 8, 12, 12); v2.setSpacing(8)
        self.btnTrain = QPushButton("기준 모델 학습")
        self.btnTrain.clicked.connect(self._on_train)
        v2.addWidget(self.btnTrain)
        self.modelStatus = QLabel("")
        v2.addWidget(self.modelStatus)
        root.addWidget(g2)

        g3 = QGroupBox("3. QA 실행")
        v3 = QVBoxLayout(g3); v3.setContentsMargins(12, 8, 12, 12); v3.setSpacing(8)
        b3 = QHBoxLayout(); b3.setSpacing(10)
        self.btnRunDemo = QPushButton("시연 실행 (정상+결함)")
        self.btnRunDeploy = QPushButton("배포 실행 (정상만)")
        self.btnRunDemo.clicked.connect(lambda: self._on_run("demo"))
        self.btnRunDeploy.clicked.connect(lambda: self._on_run("deploy"))
        b3.addWidget(self.btnRunDemo); b3.addWidget(self.btnRunDeploy)
        v3.addLayout(b3)
        root.addWidget(g3)

        root.addWidget(QLabel("진행 로그"))
        self.regLog = QTextEdit(); self.regLog.setReadOnly(True); self.regLog.setMaximumHeight(110)
        root.addWidget(self.regLog)

        # 전체 초기화 (데이터 + 모델 리셋, 확인 팝업)
        self.btnResetAll = QPushButton("전체 초기화 (데이터·모델 리셋)")
        self.btnResetAll.clicked.connect(self._reset_all)
        root.addWidget(self.btnResetAll)

        # 버튼 높이 살짝 키워 균형 (테마와 어울리게)
        for b in [self.btnGen, self.btnLoad, self.btnTrain,
                  self.btnRunDemo, self.btnRunDeploy, self.btnResetAll]:
            b.setMinimumHeight(38)
        return tab

    def _vision_register_tab(self):
        """탭2: 화면 분석 등록 — 팀원의 기존 파일선택 위젯을 이동(비전 영역)."""
        tab = QWidget()
        root = QVBoxLayout(tab)
        root.addWidget(QLabel("화면(영상) 분석용 입력 — 비전 파이프라인 영역"))

        r1 = QHBoxLayout()
        r1.addWidget(self.gameFileOpen)      # 팀원 위젯 이동(mp4)
        r1.addWidget(self.gameFileRoute)
        root.addLayout(r1)
        r2 = QHBoxLayout()
        r2.addWidget(self.txtFileOpen)       # 팀원 위젯 이동(csv)
        r2.addWidget(self.txtFileRoute)
        root.addLayout(r2)

        # 팀원 기존 연결 유지
        self.gameFileOpen.clicked.connect(lambda: logic.open_game_file_dialog(self))
        self.txtFileOpen.clicked.connect(lambda: logic.open_txt_file_dialog(self))

        root.addWidget(QLabel("(영상 분석 실행은 비전 팀원이 연결)"))
        root.addStretch(1)
        return tab

    def _refresh_status(self, game=None):
        game = game or self.gameSelect.currentText()
        n = len(self._normal_eps) if self._normal_eps else 0
        self.dataStatus.setText(f"정상 데이터: {n}건 준비됨" if n else "정상 데이터: 준비 안 됨")
        self.modelStatus.setText("기준 모델: 학습됨 (QA 가능)" if logic.model_exists(game)
                                 else "기준 모델: 없음 (먼저 학습 필요)")

    def _reglog(self, m): self.regLog.append(m)

    def _set_busy(self, busy):
        for b in [self.btnGen, self.btnLoad, self.btnTrain, self.btnRunDemo, self.btnRunDeploy]:
            b.setEnabled(not busy)

    # ---- 1. 데이터 준비 ----
    def _on_gen_data(self):
        self._set_busy(True); self.regLog.append("── 봇으로 정상 데이터 생성 시작 ──")
        w = logic.DataGenWorker(self.gameSelect.currentText(), logic.N_TRAIN)
        w.log.connect(self._reglog); w.done.connect(self._gen_done); w.fail.connect(self._on_fail)
        self._worker = w; w.start()

    def _gen_done(self, eps):
        self._normal_eps = eps; self._set_busy(False); self._refresh_status()

    def _on_load_data(self):
        path, _ = QFileDialog.getOpenFileName(self, "정상 데이터(JSONL) 선택", "",
                                              "JSONL (*.jsonl);;All Files (*)")
        if not path: return
        try:
            from qa_core.schema import load_dataset
            self._normal_eps = load_dataset(path)
            self.regLog.append(f"파일 등록: {len(self._normal_eps)}건 ({os.path.basename(path)})")
            self._refresh_status()
        except Exception as e:
            self._on_fail(f"파일 로드 실패: {e}")

    # ---- 2. 학습 ----
    def _on_train(self):
        if not self._normal_eps:
            self.regLog.append("[안내] 먼저 정상 데이터를 준비하세요(1단계)."); return
        self._set_busy(True); self.regLog.append("── 기준 모델 학습 시작 ──")
        w = logic.TrainWorker(self.gameSelect.currentText(), self._normal_eps)
        w.log.connect(self._reglog); w.done.connect(self._train_done); w.fail.connect(self._on_fail)
        self._worker = w; w.start()

    def _train_done(self, model_path):
        self.regLog.append("학습 완료."); self._set_busy(False); self._refresh_status()

    # ---- 3. 실행 ----
    def _on_run(self, mode):
        game = self.gameSelect.currentText()
        if not logic.model_exists(game):
            self.regLog.append("[안내] 먼저 기준 모델을 학습하세요(2단계)."); return
        self._set_busy(True)
        self.stackedWidget.setCurrentWidget(self.qa_window)
        self.allLog.append(f"── QA 실행 시작 ({'시연' if mode=='demo' else '배포'}) ──")
        w = logic.QARunWorker(game, mode)
        w.log.connect(lambda m: logic.update_realtime_log(self, m))
        w.done.connect(self._run_done); w.fail.connect(self._on_fail)
        self._worker = w; w.start()

    def _run_done(self, report):
        logic.display_report(self, report); self._set_busy(False)

    def _on_fail(self, msg):
        self.regLog.append(f"[오류] {msg}")
        try: self.allLog.append(f"[오류] {msg}")
        except Exception: pass
        self._set_busy(False)

    def _go_register(self):
        self.stackedWidget.setCurrentWidget(self.register_page)

    # ---- 초기화 ----
    def _clear_results(self):
        """화면만 초기화: 에러 목록/상세/로그 비움. 데이터·모델은 유지."""
        self.errorReportHistory.clear()
        self.errorReport.clear()
        self.allLog.clear()
        logic.dummy_ai_reports.clear()

    def _reset_all(self):
        """전체 초기화: 준비 데이터 + 저장된 모델 삭제. (확인 팝업)"""
        game = self.gameSelect.currentText()
        reply = QMessageBox.question(
            self, "전체 초기화",
            f"'{game}'의 준비 데이터와 학습된 모델을 모두 삭제합니다.\n계속하시겠습니까?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        # 준비 데이터 리셋
        self._normal_eps = None
        # 모델 파일 삭제
        mp = logic.model_path_for(game)
        removed = False
        try:
            if os.path.exists(mp):
                os.remove(mp); removed = True
        except Exception as e:
            self.regLog.append(f"[오류] 모델 삭제 실패: {e}")
        # 화면도 비움
        self._clear_results()
        self.regLog.clear()
        self.regLog.append(f"전체 초기화 완료 (모델 {'삭제됨' if removed else '없었음'}).")
        self._refresh_status()

    # ==================================================================
    # QA 결과 화면: 상단 [등록으로] 버튼 + 결과 탭(행동/화면)
    # ==================================================================
    def _build_result_tabs(self):
        # qa_window를 레이아웃 기반으로: 상단바(돌아가기/결과지우기) + 결과 탭
        outer = QVBoxLayout(self.qa_window)
        outer.setContentsMargins(10, 8, 10, 10); outer.setSpacing(8)

        topbar = QHBoxLayout()
        self.backBtn = QPushButton("← 등록 화면으로")
        self.backBtn.clicked.connect(self._go_register)
        self.clearBtn = QPushButton("결과 지우기")
        self.clearBtn.clicked.connect(self._clear_results)
        topbar.addWidget(self.backBtn); topbar.addWidget(self.clearBtn); topbar.addStretch(1)
        outer.addLayout(topbar)

        tabs = QTabWidget()

        tab1 = QWidget()
        h = QHBoxLayout(tab1)
        left = QVBoxLayout()
        left.addWidget(QLabel("에러 목록 (규칙위반 먼저 → 이상점수순)"))
        left.addWidget(self.errorReportHistory)
        left.addWidget(QLabel("실시간 로그"))
        left.addWidget(self.allLog)
        h.addLayout(left, 2)
        rightbox = QVBoxLayout()
        rightbox.addWidget(QLabel("상세 리포트"))
        rightbox.addWidget(self.errorReport)
        h.addLayout(rightbox, 3)
        tabs.addTab(tab1, "행동 분석 (봇)")

        tab2 = QWidget()
        v = QVBoxLayout(tab2)
        v.addWidget(QLabel("화면(영상) 분석 결과 - 비전 파이프라인 영역"))
        v.addWidget(self.gameMovie)
        v.addWidget(self.label)
        tabs.addTab(tab2, "화면 분석 (비전)")

        outer.addWidget(tabs)
        self.qaTabs = tabs


# ======================================================================
# 디자인: 다크 테마 적용 (qdarktheme -> 내장 QSS 폴백 -> 기본)
# ======================================================================
_FALLBACK_QSS = """
QWidget { background-color: #1e1f22; color: #e6e6e6; font-family: "Malgun Gothic", "맑은 고딕", sans-serif; font-size: 10pt; }
QLabel { color: #d8d8d8; background: transparent; }
QGroupBox {
  border: 1px solid #3a3d42; border-radius: 8px;
  margin-top: 16px; padding: 18px 14px 14px 14px;
  font-weight: 600;
}
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; left: 14px; padding: 0 6px; color: #9aa0a6; }
QPushButton {
  background-color: #1f6feb; color: #ffffff; border: none; border-radius: 6px;
  padding: 10px 16px; font-weight: 600;
}
QPushButton:hover { background-color: #2f81f7; }
QPushButton:pressed { background-color: #1a5fc4; }
QPushButton:disabled { background-color: #3a3d42; color: #8a8d92; }
QComboBox { background-color: #26282c; border: 1px solid #3a3d42; border-radius: 6px; padding: 5px 8px; min-height: 22px; }
QTextEdit, QTextBrowser, QListWidget { background-color: #26282c; border: 1px solid #3a3d42; border-radius: 6px; padding: 6px; }
QListWidget::item { padding: 3px; }
QListWidget::item:selected { background-color: #1f6feb; color: white; }
QTabWidget::pane { border: 1px solid #3a3d42; border-radius: 8px; top: -1px; }
QTabBar::tab {
  background: #26282c; color: #b8b8b8; padding: 10px 18px;
  border-top-left-radius: 6px; border-top-right-radius: 6px; margin-right: 3px;
}
QTabBar::tab:selected { background: #1f6feb; color: white; }
QScrollBar:vertical { background: #26282c; width: 11px; margin: 0; border-radius: 5px; }
QScrollBar::handle:vertical { background: #4a4d52; border-radius: 5px; min-height: 24px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QMenuBar { background: #26282c; }
QMenuBar::item:selected { background: #1f6feb; }
"""

def _apply_theme(app):
    """다크 테마. 내장 QSS를 기본으로 사용(겹침/잘림 없이 안정적).
    qt-material을 쓰고 싶으면 USE_QT_MATERIAL=True 로 바꾸면 됨."""
    USE_QT_MATERIAL = False
    if USE_QT_MATERIAL:
        try:
            from qt_material import apply_stylesheet
            extra = {"font_family": "Malgun Gothic", "density_scale": "0"}
            apply_stylesheet(app, theme="dark_blue.xml", extra=extra)
            app.setStyleSheet(app.styleSheet() + _BUTTON_FIX_QSS)
            return
        except Exception:
            pass
    app.setStyleSheet(_FALLBACK_QSS)


# qt-material 위에 덧씌우는 보정: 버튼을 확실히 채우고(흰 글씨), 한글 안 잘리게, 겹침 방지
_BUTTON_FIX_QSS = """
* { font-family: "Malgun Gothic", "맑은 고딕", "Noto Sans KR", sans-serif; }
QPushButton {
  background-color: #1f6feb;
  color: #ffffff;
  border: 1px solid #1f6feb;
  border-radius: 6px;
  padding: 10px 16px;
  min-height: 20px;
  font-size: 10pt;
  font-weight: 600;
}
QPushButton:hover { background-color: #2f81f7; border-color: #2f81f7; color: #ffffff; }
QPushButton:pressed { background-color: #1a5fc4; border-color: #1a5fc4; color: #ffffff; }
QPushButton:disabled { background-color: #3a3f4b; border-color: #3a3f4b; color: #aab0b6; }
QPushButton:flat { background-color: #1f6feb; color: #ffffff; }
QGroupBox {
  border: 1px solid #3a3f4b;
  border-radius: 8px;
  margin-top: 14px;
  padding: 16px 12px 12px 12px;
}
QGroupBox::title {
  subcontrol-origin: margin;
  subcontrol-position: top left;
  left: 12px;
  padding: 0 6px;
}
QLabel { background: transparent; }
QComboBox { min-height: 24px; padding: 4px 8px; }
QTabBar::tab { padding: 10px 18px; }
"""


if __name__ == "__main__":
    app = QApplication(sys.argv)
    _apply_theme(app)                            # ← 디자인 한 줄(내부에서 안전 처리)
    window = QAUIapp()
    window.setWindowTitle("게임 QA 자동화")
    window.show()
    sys.exit(app.exec())
