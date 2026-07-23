from PyQt6.QtWidgets import QMessageBox, QListWidgetItem
from PyQt6 import uic
import time
from PyQt6.QtCore import QThread, pyqtSignal, Qt
import thread, logic

def show_qa_result(ui, result_data, step):
    """ QThread에서 보내준 결과를 저장 및 세팅 """
    
    title = result_data["title"]
    content = result_data["content"]
    
    if not ui.found_error: # 처음 에러 들어오면 안내 제거
        ui.errorReportHistory.clear()

    ui.report_cache[title] = content # 에러 내용 저장
    ui.errorReportHistory.addItem(title) # 에러 히스토리에 제목 넣기
    ui.errorReport.setText(content) # 에러 상세 리포트 창에 내용 띄우기

    ui.found_error.append({ # 에러리스트 업뎃
        "step": step,
        "title": title,
        "content": content
    })
    ui.is_saved = False # 저장 안됨 상태로 변경

def restore_qa_result(ui):
    """ 에러 목록 화면을 현재 found_error 기준으로 다시 그림.
    새 테스트(빈 리스트) / 이어하기(불러온 리스트) 둘 다 이거 하나로 처리 """

    # 목록/상세 초기화 (이전 세션 잔여물 제거)
    ui.errorReportHistory.clear()
    ui.errorReport.clear()
    ui.report_cache = {}

    if not ui.found_error:
        # 빈 상태 → 히스토리에 안내, 상세창도 안내
        show_empty_hint(ui)
        ui.errorReport.setText("에러가 검출됐을 시, 에러를 선택하면 상세 내용이 표시됩니다.")
        ui.allLog.setText("아직 수집된 로그가 없습니다.")
        return

    ui.has_log = False
    
    for error in ui.found_error:
        ui.report_cache[error["title"]] = error["content"]
        ui.errorReportHistory.addItem(error["title"])

    ui.errorReport.setText(ui.found_error[-1]["content"])

    # for error in ui.found_error:
    #     title = error["title"]
    #     content = error["content"]

    #     ui.report_cache[title] = content
    #     ui.errorReportHistory.addItem(title)

    # if ui.found_error: # 마지막 항목을 상세창에
    #     ui.errorReport.setText(ui.found_error[-1]["content"])
    # else: # 아무것도 없어용
    #     ui.allLog.setText("아직 수집된 로그가 없습니다.")

def show_error_detail(ui, item):
    """ 클릭했을 때 텍스트 창을 바꿔치기(에러 리포트) """

    if item.data(Qt.ItemDataRole.UserRole) == "hint":
        return # 에러 아직 없어용 문구 클릭하면 무시
    
    selected_title = item.text() # 클릭한 항목의 제목을 가져옴

    if selected_title not in ui.report_cache:
        return # 캐시에 없는 항목도 무시
    
    detail_report = ui.report_cache[selected_title] # 제목에 맞는 상세 리포트 내용 가져오기
    ui.errorReport.setText(detail_report) # 내용을 errorReport에 쏴주기

def show_empty_hint(ui):
    """에러 목록이 비었을 때 안내 항목 하나 넣기"""
    item = QListWidgetItem("아직 검출된 에러가 없습니다.")
    item.setFlags(Qt.ItemFlag.NoItemFlags)          # 클릭·선택 안 됨
    item.setData(Qt.ItemDataRole.UserRole, "hint")  # 식별용 꼬리표
    ui.errorReportHistory.addItem(item)

def update_realtime_log(ui, message):
    """ 일꾼이 보내준 메시지를 ui에 업뎃 """
    if not getattr(ui, 'has_log', False):
        ui.allLog.clear()
        ui.has_log = True
    ui.allLog.append(message)

def qa_start(ui):
    """ QAWorker(QThread) 시작 """
    print(f"[start] 함수 진입 | ui.step={ui.step}")
    if ui.state == thread.RunState.RUNNING:
        return # 이미 돌아가고있으면 함수 나가
    
    # 버튼을 빨간색 '중지' 버튼으로 바꿈(CSS 스타일 적용)
    ui.btnStartQA.setText("⏹ QA 중지")
    ui.btnStartQA.setStyleSheet("background-color: #E74C3C; color: white; font-weight: bold;")

    # ui.step은 이미 정답:
    #   IDLE   → go_dashboard가 세팅 (0 또는 체크포인트 step)
    #   PAUSED → step_signal이 갱신해둔 값
    # 여기서 다시 계산하지 않는다
    ui.state = thread.RunState.RUNNING
    print(f"[start] 워커 생성 | ui.step={ui.step}")

    ui.worker = thread.QAWorker(step=ui.step, config=ui.final_config)  

    ui.worker.config = ui.final_config # QThread에 최종 config 딕셔너리 전달
    ui.worker.log_signal.connect(
        lambda message: update_realtime_log(ui, message)) # 평소 로그
    ui.worker.error_signal.connect(
        lambda result_data, step: show_qa_result(
            ui, result_data, step)) # 에러 리포트
    ui.worker.step_signal.connect(lambda step: (print(f"[main] 받음 {step}"), setattr(ui, 'step', step)))
    ui.worker.finished_signal.connect(lambda ok: thread.on_qa_finished(ui, ok)) # 퇴근 플래그

    ui.worker.start()

def qa_stop(ui):
    """ QAWorker(QThread) 종료 """
    reply = QMessageBox.question(
        ui, '테스트 중지',
        '진행 중인 테스트를 중지합니다.\n여기까지의 기록을 남길까요?',
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    
    ui.keep_record = (reply == QMessageBox.StandardButton.Yes)
    working = ui.worker.isRunning() if hasattr(ui, 'worker') else None
    print(f"[stop] 호출됨 | ui.step={ui.step} | hasattr={hasattr(ui,'worker')} | isRunning={working}")

    # 퇴근 플래그
    if hasattr(ui, 'worker') and ui.worker.isRunning():
        # hasattr == self에 worker가 있으면 True, 없으면 False
        ui.worker.working = False # 워커 퇴근
        ui.is_paused = True # 일시중지임
        print(f"[main] 중지 시점 ui.step={ui.step}")

    print("🛑 사용자가 테스트를 강제 중지했습니다!")
