"""
qa_flow.py
==========
QA 대시보드 화면의 표시 로직 + 워커 시작/중지.

⚠️ 여기 있는 함수는 전부 '메인 스레드'에서 돈다.
   워커가 쏜 시그널이 여기로 들어와서 위젯을 바꾼다.
"""

import os

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QColor, QDesktopServices
from PyQt6.QtWidgets import QListWidgetItem, QMessageBox

import settings
import session_reader
import thread
import logic


# ════════════════════════════════════════════════════════════
# 1. 에러 목록 / 상세
# ════════════════════════════════════════════════════════════

def add_error_item(ui, entry):
    """
    표준형 에러 항목을 목록에 추가한다. 심각도에 따라 글자색이 달라진다.

    ⚠️ 항목에 dict를 통째로 심어둔다(UserRole).
       나중에 클릭했을 때 frame_path 같은 걸 꺼내 쓰려면 제목만으론 부족하다.
    """
    item = QListWidgetItem(entry.get("title", "(제목 없음)"))

    severity = entry.get("severity", "medium")
    color = session_reader.SEVERITY_COLORS.get(severity, "#AAAAAA")
    item.setForeground(QColor(color))

    # 수동 추가는 기울임으로 구분 → 발표 때 "이건 사람이 넣은 겁니다"가 명확해진다
    if entry.get("source") == "manual":
        font = item.font()
        font.setItalic(True)
        item.setFont(font)

    item.setData(Qt.ItemDataRole.UserRole, entry)
    ui.errorReportHistory.addItem(item)
    return item


def show_empty_hint(ui):
    """에러 목록이 비었을 때 안내 항목 하나."""
    item = QListWidgetItem("아직 검출된 에러가 없습니다.")
    item.setFlags(Qt.ItemFlag.NoItemFlags)             # 클릭·선택 안 됨
    item.setData(Qt.ItemDataRole.UserRole, "hint")     # 식별용 꼬리표
    ui.errorReportHistory.addItem(item)


def restore_qa_result(ui):
    """
    에러 목록 화면을 현재 found_error 기준으로 다시 그린다.
    새 테스트(빈 리스트)든 이어하기(불러온 리스트)든 이거 하나로 처리한다.
    """
    ui.errorReportHistory.clear()
    ui.errorReport.clear()
    ui.report_cache = {}

    # ⚠️ 아래 early return보다 위에 있어야 한다.
    #    밑에 두면 이전 세션의 has_log=True가 남아서
    #    allLog가 초기화되지 않고 안내문 아래에 로그가 덧붙는다.
    ui.has_log = False

    # 로그창이 무한정 길어지지 않게 상한을 건다.
    # (넘으면 위에서부터 자동으로 버려진다)
    ui.allLog.document().setMaximumBlockCount(settings.MAX_LOG_BLOCKS)

    if not ui.found_error:
        show_empty_hint(ui)
        ui.errorReport.setText("에러가 검출되면 목록에서 선택해 상세 내용을 볼 수 있습니다.")
        ui.allLog.setText("아직 수집된 로그가 없습니다.")
        return

    for entry in ui.found_error:
        ui.report_cache[entry["title"]] = entry["content"]
        add_error_item(ui, entry)

    # 마지막 항목을 상세창에 띄워둔다
    ui.errorReport.setText(ui.found_error[-1]["content"])


def show_qa_result(ui, report, frame_path=None):
    """
    워커가 보낸 리포트를 표준형으로 바꿔 저장하고 화면에 반영한다.
    error_signal에 연결된다.
    """
    entry = logic.make_error_entry(report, frame_path=frame_path, source="auto")

    if not ui.found_error:
        ui.errorReportHistory.clear()   # "아직 없습니다" 안내 제거

    ui.found_error.append(entry)
    ui.report_cache[entry["title"]] = entry["content"]

    add_error_item(ui, entry)
    ui.errorReport.setText(build_detail_text(ui, entry))
    ui.is_saved = False


def build_detail_text(ui, entry):
    """상세창에 띄울 본문. 스크린샷이 있으면 안내를 덧붙인다."""
    text = entry.get("content", "")
    rel = entry.get("frame_path")
    if rel:
        text += (f"\n----------------------------------------\n"
                 f"📷 관련 화면: {rel}\n"
                 f"   (목록에서 이 항목을 더블클릭하면 이미지가 열립니다)")
    return text


def show_error_detail(ui, item):
    """목록에서 항목을 클릭했을 때 상세창을 바꾼다."""
    entry = item.data(Qt.ItemDataRole.UserRole)
    if entry == "hint" or not isinstance(entry, dict):
        return   # 안내 항목 클릭은 무시
    ui.errorReport.setText(build_detail_text(ui, entry))


def open_error_frame(ui, item):
    """
    항목을 더블클릭하면 그 시점 스크린샷을 기본 이미지 뷰어로 연다.

    ⚠️ frame_path는 session_dir 기준 상대경로다.
       여기서 session_dir과 합쳐 절대경로를 만든다.
       (저장 파일에 절대경로를 넣지 않는 이유가 이거다 —
        폴더를 옮겨도 안 깨진다)
    """
    entry = item.data(Qt.ItemDataRole.UserRole)
    if not isinstance(entry, dict):
        return

    rel = entry.get("frame_path")
    if not rel:
        QMessageBox.information(ui, "화면 없음",
                                "이 항목에는 연결된 스크린샷이 없습니다.")
        return

    full = os.path.join(ui.session_dir, rel)
    if not os.path.exists(full):
        QMessageBox.warning(ui, "파일 없음",
                            f"스크린샷을 찾을 수 없습니다:\n{full}")
        return

    QDesktopServices.openUrl(QUrl.fromLocalFile(full))


# ════════════════════════════════════════════════════════════
# 2. 로그 / 진행률
# ════════════════════════════════════════════════════════════

def update_realtime_log(ui, message):
    """
    워커가 보낸 로그를 allLog에 덧붙인다. log_signal에 연결된다.

    message는 여러 줄이 \n으로 묶여서 온다.
    (한 줄씩 시그널을 쏘면 이벤트 큐가 터진다)
    """
    if not getattr(ui, "has_log", False):
        ui.allLog.clear()
        ui.has_log = True
    ui.allLog.append(message)


def update_progress(ui, cursor, total):
    """진행률을 상단에 반영한다. progress_signal에 연결된다."""
    ui.event_cursor = cursor
    if total:
        ui.event_total = total
    logic.update_file_route(ui)


def on_session_ready(ui, session_dir):
    """
    라이브에서 세션 폴더가 정해졌을 때. session_ready에 연결된다.
    이 시점부터 스크린샷 경로 계산이 가능해진다.
    """
    ui.session_dir = session_dir
    ui.session_summary = logic.load_session_summary(session_dir)
    logic.update_file_route(ui)


# ════════════════════════════════════════════════════════════
# 3. 워커 시작 / 중지
# ════════════════════════════════════════════════════════════

def qa_start(ui):
    """[▶ QA 시작] — 워커를 만들어 실행한다."""
    if ui.state == thread.RunState.RUNNING:
        return

    # replay인데 세션 폴더가 없으면 시작할 게 없다
    if ui.source_mode == "replay" and not ui.session_dir:
        QMessageBox.warning(ui, "세션 없음", "읽을 세션 폴더가 지정되지 않았습니다.")
        return

    ui.btnStartQA.setText("⏹ QA 중지")
    ui.btnStartQA.setStyleSheet(
        "background-color: #E74C3C; color: white; font-weight: bold;")

    ui.state = thread.RunState.RUNNING
    print(f"[start] mode={ui.source_mode} cursor={ui.event_cursor}")

    ui.worker = thread.QAWorker(
        session_dir=ui.session_dir,
        mode=ui.source_mode,
        start_cursor=ui.event_cursor,
        target_title=ui.target_title,
    )

    # ── 시그널 연결 ──
    ui.worker.log_signal.connect(lambda msg: update_realtime_log(ui, msg))
    ui.worker.error_signal.connect(
        lambda report, frame: show_qa_result(ui, report, frame))
    ui.worker.progress_signal.connect(
        lambda cur, total: update_progress(ui, cur, total))
    ui.worker.session_ready.connect(lambda d: on_session_ready(ui, d))
    ui.worker.finished_signal.connect(lambda ok: thread.on_qa_finished(ui, ok))

    ui.worker.start()


def qa_stop(ui):
    """[⏹ QA 중지] — 워커에게 중지를 요청한다."""
    worker = getattr(ui, "worker", None)
    if worker is None or not worker.isRunning():
        print("[stop] 워커가 돌고 있지 않음")
        return

    reply = QMessageBox.question(
        ui, "테스트 중지",
        "진행 중인 테스트를 중지합니다.\n여기까지의 기록을 남길까요?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)

    ui.keep_record = (reply == QMessageBox.StandardButton.Yes)

    worker.working = False   # 워커 루프 탈출 요청

    # 즉시 피드백. 실제 종료는 현재 읽던 이벤트가 끝난 뒤.
    ui.btnStartQA.setText("⏹ 중단하는 중...")
    ui.btnStartQA.setEnabled(False)
    print(f"[stop] 중지 요청 | cursor={ui.event_cursor}")
