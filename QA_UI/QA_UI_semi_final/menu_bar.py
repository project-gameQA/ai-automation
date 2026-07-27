"""
menu_bar.py
===========
메뉴바 기능: 첫 화면 복귀, 저장/다른 이름으로 저장, Export,
검색(Ctrl+F), 수동 에러 추가, 프로그램 종료.
"""

import os
from collections import Counter
from datetime import datetime

from PyQt6 import uic
from PyQt6.QtGui import QTextCursor, QTextDocument
from PyQt6.QtWidgets import (QApplication, QDialog, QFileDialog, QLayout,
                             QMessageBox, QTextBrowser)

import settings
import session_reader
import logic
import thread
import qa_flow


# ════════════════════════════════════════════════════════════
# 1. 첫 화면으로
# ════════════════════════════════════════════════════════════

def splash_screen(ui):
    """진행 중인 QA가 있으면 물어보고 정리한 뒤 첫 화면으로 돌아간다."""
    if ui.state == thread.RunState.RUNNING:
        reply = QMessageBox.question(
            ui, "QA 중단",
            "진행 중인 QA가 있습니다. 중단하시겠습니까?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return   # 사용자가 취소 → 화면 그대로

        # finished_signal을 먼저 끊는다.
        # 안 끊으면 워커가 끝나면서 on_qa_finished가 돌고,
        # 방금 초기화한 상태를 다시 저장하려 든다.
        try:
            ui.worker.finished_signal.disconnect()
        except (TypeError, RuntimeError, AttributeError):
            pass   # 이미 끊겼거나 워커가 없으면 무시

        thread.shutdown_worker(ui)

    logic.reset_session_state(ui)
    ui.stackedWidget.setCurrentWidget(ui.start_window)


# ════════════════════════════════════════════════════════════
# 2. 저장
# ════════════════════════════════════════════════════════════

def save(ui):
    """Ctrl+S. 저장 경로가 이미 있으면 안 묻고 덮어쓴다."""
    print(f"[save] current_save_path={ui.current_save_path}")
    if not ui.current_save_path:
        return save_as(ui)
    return logic.save_checkpoint(ui, ui.current_save_path)


def save_as(ui):
    """Ctrl+Shift+S. 경로를 새로 받아 저장한다."""
    path, _ = QFileDialog.getSaveFileName(
        ui, "저장 위치", "", "QA 파일 (*.json);;모든 파일 (*)")
    if not path:
        return False   # 사용자가 취소

    # 확장자를 안 붙였으면 붙여준다
    if not path.lower().endswith(".json"):
        path += ".json"

    ui.current_save_path = path
    return logic.save_checkpoint(ui, path)


# ════════════════════════════════════════════════════════════
# 3. Export (사람이 읽는 리포트)
# ════════════════════════════════════════════════════════════

def export_file(ui):
    """[Export] 텍스트 리포트로 내보낸다."""
    file_path, _ = QFileDialog.getSaveFileName(
        ui, "Export", "QA_Error_Report.txt",
        "Text Files (*.txt);;모든 파일 (*)")
    if not file_path:
        return False

    # ⚠️ current_save_path(체크포인트 .json)를 건드리지 않는다.
    #    전에는 여기서 덮어써서, Export 후 Ctrl+S를 누르면
    #    .txt 파일에 json이 써지면서 리포트가 날아갔다.
    ui.last_export_path = file_path

    return write_data_to_file(ui, file_path)


def write_data_to_file(ui, path):
    """실제 파일 쓰기."""
    summ = ui.session_summary or {}

    # 심각도/분류별 집계. 발표 때 "자동 N건, 수동 M건"을 말하기 위한 것.
    by_severity = Counter(e.get("severity", "?") for e in ui.found_error)
    by_source = Counter(e.get("source", "?") for e in ui.found_error)
    by_category = Counter(e.get("category", "?") for e in ui.found_error)

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("=" * 50 + "\n")
            f.write("  GAME QA AUTOMATION - ERROR REPORT\n")
            f.write("=" * 50 + "\n\n")

            f.write("[세션 정보]\n")
            f.write(f"  대상          : {summ.get('target') or ui.target_title or '?'}\n")
            f.write(f"  세션 폴더      : {ui.session_dir}\n")
            duration = summ.get("duration_s")
            f.write(f"  총 시간        : {f'{duration:.0f}초' if duration else '?'}\n")
            f.write(f"  액션 수        : {summ.get('actions_total', '?')}\n")
            f.write(f"  LLM 호출 수    : {summ.get('llm_call_count', '?')}\n")
            f.write(f"  종료 사유      : {summ.get('stop_reason', '?')}\n")
            f.write(f"  확인한 이벤트   : {ui.event_cursor} / {ui.event_total}\n")
            f.write(f"  내보낸 시각     : "
                    f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")

            f.write("[검출 요약]\n")
            f.write(f"  전체           : {len(ui.found_error)}건\n")
            for sev in ("critical", "high", "medium", "low"):
                if by_severity.get(sev):
                    label = session_reader.SEVERITY_LABELS[sev]
                    f.write(f"  - {label:<4s}       : {by_severity[sev]}건\n")
            for cat, n in by_category.items():
                f.write(f"  · {cat}     : {n}건\n")
            f.write(f"  · 자동 검출     : {by_source.get('auto', 0)}건\n")
            f.write(f"  · 수동 추가     : {by_source.get('manual', 0)}건\n\n")

            if not ui.found_error:
                f.write("(검출되거나 추가된 에러가 없습니다.)\n")
            else:
                f.write("=" * 50 + "\n")
                f.write("[상세]\n")
                f.write("=" * 50 + "\n\n")
                for i, e in enumerate(ui.found_error, 1):
                    tag = "수동" if e.get("source") == "manual" else "자동"
                    f.write(f"#{i} [{tag}] {e.get('title', '')}\n")
                    f.write(f"{e.get('content', '')}\n")
                    if e.get("frame_path"):
                        f.write(f"화면: {e['frame_path']}\n")
                    f.write("-" * 40 + "\n\n")

    except OSError as e:
        QMessageBox.critical(ui, "저장 실패",
                             f"파일을 저장하지 못했습니다.\n{e}")
        return False

    QMessageBox.information(ui, "저장 완료",
                            f"리포트를 저장했습니다.\n경로: {path}")
    return True


# ════════════════════════════════════════════════════════════
# 4. 종료
# ════════════════════════════════════════════════════════════

def close_application(ui):
    """닫아도 되면 True, 취소하면 False."""
    if not getattr(ui, "is_saved", True):
        reply = QMessageBox.warning(
            ui, "저장되지 않은 작업",
            "저장되지 않은 QA 기록이 있습니다.\n저장하시겠습니까?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save)

        if reply == QMessageBox.StandardButton.Save:
            # ⚠️ save()를 쓴다. 경로가 없으면 save_as로 넘어간다.
            #    전에는 save_checkpoint(ui)를 인자 하나로 불러서 TypeError가 났다.
            if not save(ui):
                return False   # 저장 실패/취소 → 종료도 취소
        elif reply == QMessageBox.StandardButton.Cancel:
            return False
        # Discard면 그냥 진행

    else:
        reply = QMessageBox.question(
            ui, "프로그램 종료", "QA 자동화 프로그램을 종료하시겠습니까?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return False

    # ⚠️ ui.worker.working = False 를 직접 쓰면
    #    QA를 한 번도 안 돌렸을 때 worker가 None이라 AttributeError가 난다.
    thread.shutdown_worker(ui)
    return True


# ════════════════════════════════════════════════════════════
# 5. 검색 (Ctrl+F)
# ════════════════════════════════════════════════════════════

class SearchDialog(QDialog):
    """계속 떠 있는 전용 검색창."""

    def __init__(self, target_widget, parent=None):
        super().__init__(parent)
        uic.loadUi(settings.ui_path("find_ctrl_f.ui"), self)
        layout = self.layout()
        if layout is not None:
            # 레이아웃이 있으면 내용물 크기에 맞춰 창을 고정
            layout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
        else:
            # 레이아웃이 없으면 .ui에 적힌 크기(317x61) 그대로 고정
            self.setFixedSize(self.size())

        self.target_widget = target_widget

        self.btnSearchDown.clicked.connect(self.find_down)
        self.btnSearchUp.clicked.connect(self.find_up)
        self.searchText.returnPressed.connect(self.find_down)

    def _search(self, backward=False):
        """찾고, 끝에 닿으면 반대쪽 끝으로 감아서 한 번 더 찾는다."""
        text = self.searchText.text()
        if not text:
            return

        flags = (QTextDocument.FindFlag.FindBackward if backward
                 else QTextDocument.FindFlag(0))

        if self.target_widget.find(text, flags):
            return

        # 못 찾았으면 커서를 끝으로 보내고 재시도
        self.target_widget.moveCursor(
            QTextCursor.MoveOperation.End if backward
            else QTextCursor.MoveOperation.Start)

        if not self.target_widget.find(text, flags):
            QMessageBox.information(self, "검색 결과",
                                    "일치하는 내용이 없습니다.")

    def find_down(self):
        self._search(backward=False)

    def find_up(self):
        self._search(backward=True)


def open_search(ui):
    """커서가 텍스트 창에 있으면 검색창을 띄운다."""
    current = QApplication.focusWidget()

    if isinstance(current, QTextBrowser):
        ui.search_dialog = SearchDialog(current, ui)
        ui.search_dialog.show()
    else:
        QMessageBox.warning(ui, "알림", "검색할 텍스트 창을 먼저 클릭해주세요.")


# ════════════════════════════════════════════════════════════
# 6. 수동 에러 추가
# ════════════════════════════════════════════════════════════

class ErrorPlusDialog(QDialog):
    """에러 보고서 수동 추가 팝업."""

    def __init__(self, parent=None):
        super().__init__(parent)
        uic.loadUi(settings.ui_path("error_report_plus.ui"), self)
        self.saveAndHistoryAdd.clicked.connect(self.accept)


def open_error_plus_popup(ui):
    """
    사람이 직접 에러를 추가한다.

    ⚠️ 전에는 report_cache에만 넣고 found_error에는 안 넣어서
       수동으로 추가한 에러가 저장되지 않았다.
       이제 표준형으로 만들어 found_error에도 넣는다.
    """
    dialog = ErrorPlusDialog(ui)
    if not dialog.exec():
        return

    body = dialog.content.toPlainText().strip()
    if not body:
        print("[manual] 내용이 비어있어 저장하지 않음")
        return

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 첫 줄을 제목으로. 너무 길면 자른다.
    raw_title = body.split("\n")[0]
    if len(raw_title) > 20:
        raw_title = raw_title[:20] + "..."

    entry = logic.make_error_entry(
        {
            "title": f"✍️ [수동 보고] {raw_title}",
            "content": (
                f"■ 보고 방식: 수동 리포트\n"
                f"■ 발생 시각: {now}\n"
                f"■ 심각도: 중간\n"
                f"■ 분류: {session_reader.CATEGORY_GAME}\n"
                f"----------------------------------------\n"
                f"■ 상세:\n{body}"
            ),
            "severity": session_reader.SEVERITY_MEDIUM,
            "category": session_reader.CATEGORY_GAME,
            "event_type": "manual",
            "seq": ui.event_cursor,   # 지금 몇 번째 이벤트를 보고 있었는지
        },
        source="manual",
    )

    if not ui.found_error:
        ui.errorReportHistory.clear()   # "아직 없습니다" 안내 제거

    ui.found_error.append(entry)
    ui.report_cache[entry["title"]] = entry["content"]
    qa_flow.add_error_item(ui, entry)

    ui.stackedWidget.setCurrentWidget(ui.qa_window)
    ui.errorReport.setText(entry["content"])
    ui.is_saved = False

    print("[manual] 새 에러가 히스토리에 추가됨")
