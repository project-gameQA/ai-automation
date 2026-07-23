import json
import os
import sys
from PyQt6.QtWidgets import QApplication, QDialog, QTextBrowser, QFileDialog, QMessageBox, QLayout
from PyQt6 import uic
import time
from PyQt6.QtGui import QTextCursor, QTextDocument
from datetime import datetime
import logic, thread

def splash_screen(ui):
    """ 첫 화면으로 돌아가기 """
    if ui.state == thread.RunState.RUNNING: # 이미 돌고있으면
        reply = QMessageBox.question(
            ui, "QA 중단",
            "진행 중인 QA가 있습니다. 중단하시겠습니까?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return   # 사용자가 취소 → 화면 그대로

        # finished_signal 끊기 (아래 ※ 설명 참고)
        try:
            ui.worker.finished_signal.disconnect()
        except TypeError:
            pass   # 이미 끊겨 있으면 TypeError → 무시

        ui.worker.working = False   # 워커 루프 탈출 요청
        ui.worker.wait(3000)            # 실제로 끝날 때까지 대기

    # 세션 초기화
    ui.state = thread.RunState.IDLE
    ui.step = 0
    ui.found_error = []
    ui.current_save_path = None
    ui.final_config = None

    ui.stackedWidget.setCurrentWidget(ui.start_window) # 첫 화면으로 이동
    # ui.newOrResumeWindow.setCurrentWidget(ui.newPage) # 새 파일 선택 화면으로 이동

def save(ui):
    """ ctrl+s. 저장 경로 이미 있으면 안 묻고 덮어쓰기 """
    print(f"[save] current_save_path={ui.current_save_path}")
    if not ui.current_save_path:
        return save_as(ui)

    return logic.save_checkpoint(ui, ui.current_save_path)

def save_as(ui):
    """ ctrl+shift+s. 새로 만들기 """
    path, _ = QFileDialog.getSaveFileName(
        ui, "저장 위치", "", "QA 파일 (*.json);;모든 파일 (*)")
    if not path: # 사용자가 취소
        return False
    ui.current_save_path = path

    return logic.save_checkpoint(ui, ui.current_save_path)

def export_file(ui):
    """ [Export] : 예쁜 문서로 저장 """
    
    # QFileDialog.getSaveFileName(부모, 창제목, 기본파일명, 파일형식)
    file_path, _ = QFileDialog.getSaveFileName(ui, "Export",
        "QA_Error_Report.txt", "Text Files (*.txt);;All Files (*)")
    if not file_path: # 저장 안하고 취소하면 false 반환
        return False
        
    ui.current_save_path = file_path # 선택 경로 기억(나중에 그냥 저장하면 덮어씌움)
    write_data_to_file(ui, file_path) # 실제 파일로 만들어서 저장
    ui.is_saved = True # 저장됨 상태로 변경
    return True # 저장 완료! 

def write_data_to_file(ui, path):
    """ 실제 하드디스크 파일에 정리해서 넣음 """
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("=========================================\n")
            f.write(" 🔥 GAME QA AUTOMATION AI ERROR REPORT 🔥 \n")
            f.write("=========================================\n\n")
            
            if not ui.report_cache: # 에러 리포트가 잇는게 없어
                f.write(f"[파일 기본정보]: {ui.final_config}\n")
                f.write("(수집되거나 추가된 에러 리포트가 없습니다.)\n")
            else: # 에러 리포트가 있으면
                f.write(f"[파일 기본정보]: {ui.final_config}\n")
                for title, content in ui.report_cache.items():
                    f.write(f"📌 [에러 리포트 제목] : {title}\n")
                    f.write(f"📝 [리포트 상세 내용] :\n{content}\n")
                    f.write("-" * 40 + "\n\n")
                    
        # 저장이 성공하면 화면 우측 하단에 알림을 띄우거나 팝업을 줍니다.
        QMessageBox.information(ui, "저장 완료", f"에러 리포트가 성공적으로 저장되었습니다!\n경로: {path}")
        
    except Exception as e:
        QMessageBox.critical(ui, "저장 실패", f"파일을 저장하는 도중 에러가 발생했습니다.\n{str(e)}")

def close_application(ui):
    """ 닫아도 되면 True 안되면 False """

    if not getattr(ui, 'is_saved', True): 
        reply = QMessageBox.warning(
            ui, '저장되지 않은 작업', 
            '저장되지 않은 데이터(에러 리포트)가 있습니다.\n저장하시겠습니까?',
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save  # 엔터 치면 기본으로 눌릴 버튼
        )

        if reply == QMessageBox.StandardButton.Save:
            is_success = logic.save_checkpoint(ui)

            if is_success: # 저장 성공
                ui.worker.working = False # 큐쓰레드 종료 플래그
                print("저장을 완료하고 프로그램을 종료합니다.")
                return True
            else: # 저장 실패ㅜ
                return False
            
        elif reply == QMessageBox.StandardButton.Discard:
            ui.worker.working = False            
            print("저장하지 않고 프로그램을 바로 종료합니다.")
            return True
            
        else: # Cancel(취소) 또는 창 닫기(X 버튼)를 누른 경우
            print("종료를 취소합니다.")
            return  False
    else:    
        reply = QMessageBox.question( # 유저에게 예/아니오 팝업창 띄움
            ui, '프로그램 종료', 'QA 자동화 프로그램을 종료하시겠습니까?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, 
            QMessageBox.StandardButton.No)

        if reply == QMessageBox.StandardButton.Yes: # yes 누르면 프로그램 종료
            ui.worker.working = False
            return True
        elif reply == QMessageBox.StandardButton.No:
            return False

# 💡 계속 떠 있는 전용 검색창 만들기
class SearchDialog(QDialog):
    """
    텍스트 검색창(ctrl+F)
    """
    def __init__(self, target_widget, parent=None):
        super().__init__(parent)
        uic.loadUi("QA_UI/find_ctrl_f.ui", self)
        self.layout().setSizeConstraint(
            QLayout.SizeConstraint.SetFixedSize)
        
        self.target_widget = target_widget

        # 버튼 눌렀을 때 실행될 기능
        self.btnSearchDown.clicked.connect(self.find_down)
        self.btnSearchUp.clicked.connect(self.find_up)
        self.searchText.returnPressed.connect(self.find_down) # 엔터치면 내려감

    def find_down(self):
        """
        ▼ 버튼 & 엔터
        """
        search_text = self.searchText.text()
        if not search_text: return
        
        found = self.target_widget.find(search_text)
        if not found: # 못찾앗엉
            self.target_widget.moveCursor(QTextCursor.MoveOperation.Start) # 맨위로가
            found = self.target_widget.find(search_text) # 거기서부터 다시찾아
            if not found: # 못찾앗엉222
                QMessageBox.information(self, "검색 결과", "더 이상 일치하는 내용이 없습니다.")

    def find_up(self):
        """
        ▲ 버튼
        """
        search_text = self.searchText.text()
        if not search_text: return
        
        # PyQt6 전용 '거꾸로 찾기(FindBackward)' 옵션 적용
        found = self.target_widget.find(search_text, QTextDocument.FindFlag.FindBackward)
        if not found: # 못찾앗엉
            self.target_widget.moveCursor(QTextCursor.MoveOperation.End) # 맨밑으로가
            found = self.target_widget.find(search_text, QTextDocument.FindFlag.FindBackward) # 거기서부터 다시찾아
            if not found: # 못찾앗엉222
                QMessageBox.information(self, "검색 결과", "더 이상 일치하는 내용이 없습니다.")

def open_search(self):
    """ SearchDialog 클래스 실행 함수 """
    current_widget = QApplication.focusWidget() # 내 커서 어딧어?

    if isinstance(current_widget, QTextBrowser): # textBrowser면
        self.search_dialog = SearchDialog(current_widget, self)
        self.search_dialog.show()
    else: # textBrowser가 아니면
        QMessageBox.warning(self, "알림", "검색할 텍스트 창을 먼저 클릭해주세요.")

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
        ui.report_cache[new_title] = full_content # 데이터 창고에도 저장
        ui.stackedWidget.setCurrentWidget(ui.qa_window) # 리스트가 있는 QA 화면으로 자동 이동
        ui.errorReport.setText(full_content) # 방금 추가한 에러의 '상세 내용'을 텅 빈 리포트 창에 즉시 띄워

        print("새 에러가 히스토리에 추가되었습니다.")


