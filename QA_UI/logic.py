import json
import os
from PyQt6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox
from PyQt6 import uic
from PyQt6.QtCore import QThread, pyqtSignal
from datetime import datetime
import thread, qa_flow

def is_new_mode(ui):
    """ new면 True, resume면 False """
    return ui.newToggle.isChecked()

# ── 화면 전환용 ──
def new_resume_toggle(ui):
    """ new-resume 화면 전환 """
    ui.newOrResumeWindow.setCurrentIndex(0 if is_new_mode(ui) else 1)

def open_game_file_dialog(self):
    """ 게임 파일 탐색기 열기 """
    file_dialog = QFileDialog(self)
    file_dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
    file_dialog.setNameFilter("Game Files (*.mp4);;All Files (*)")
    
    if file_dialog.exec():
        selected_file = file_dialog.selectedFiles()[0]
        self.gameFileRoute.setText(selected_file)  # 선택한 파일 경로를 텍스트 박스에 표시

def open_txt_file_dialog(self):
    """ 텍스트 파일 탐색기 열기 """
    file_dialog = QFileDialog(self)
    file_dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
    file_dialog.setNameFilter("Text Files (*.txt);;All Files (*)")

    if file_dialog.exec():
        selected_file = file_dialog.selectedFiles()[0]
        self.txtFileRoute.setText(selected_file)  # 선택한 파일 경로를 텍스트 박스에 표시

def open_qa_file_dialog(self):
    """ QA 파일 탐색기 열기 """
    file_dialog = QFileDialog(self)
    file_dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
    file_dialog.setNameFilter("QA Files (*.json);;All Files (*)")
    
    if file_dialog.exec():
        selected_file = file_dialog.selectedFiles()[0]
        self.QAFileRoute.setText(selected_file)  # 선택한 파일 경로를 텍스트 박스에 표시

def check_path(self, line_edit, expected_ext=None):
    """ 경로 유효성을 검사 """
    path = line_edit.text().strip()
    if not os.path.exists(path):
        line_edit.setStyleSheet("border: 1px solid red;")
        QMessageBox.information(self, "경로 이상", f"경로를 확인해주세요!\n현재 경로: {path}")
        return False
    
    # 파일 경로(path)가 특정 확장자(expected_ext)로 끝나는지 검사
    if expected_ext and not path.lower().endswith(expected_ext):
        line_edit.setStyleSheet("border: 1px solid orange;")
        QMessageBox.information(self, "확장자 이상", f"확장자를 확인해주세요!\n필요 확장자: {expected_ext}")
        return False
    line_edit.setStyleSheet("")
    return True

def qa_reset_next(self):
    """ 기존 QA파일을 처음부터 다시 테스트할지, 아니면 이어서 테스트할지 선택하는 토글 """
    if self.btnReset.isChecked():
        return True # reset
    else:
        return False # next
    
# ── reset-next 판정용 ──
def get_keep_going(ui):
    """ 'reset' 또는 'next' 반환. """
    return "reset" if ui.btnReset.isChecked() else "next"
    
def go_dashboard(self):
    """ QA 대시보드 화면으로 이동 및 check_path() """

    if is_new_mode(self): # new모드일때
        if not check_path(self, self.gameFileRoute, expected_ext=".mp4"): # 게임경로
            return # 통과 못하면 멈춰
        if not check_path(self, self.txtFileRoute, expected_ext=".txt"): # 문서경로
            return
    else: # resume모드일때
        if not check_path(self, self.QAFileRoute, expected_ext=".json"): # qa파일경로
            return

    self.final_config = config_finish(self)

    # ── 3) 세션 데이터 준비 ──
    if (self.final_config["mode"] == "resume"
            and self.final_config["keep_going"] == "next"):
        # 이어하기: 체크포인트에서 이전 상태 복원
        ckpt = self.final_config["qa_file"]
        step, found_error, is_complete, config = load_checkpoint(ckpt)

        self.step = step
        self.found_error = found_error
        self.prev_is_complete = is_complete
        self.current_save_path = ckpt      # 읽은 파일 = 앞으로 저장할 파일
        print(f"[enter] 이어하기 step={step}")
    else:
        # 새 테스트 / reset: 처음부터
        self.step = 0
        self.found_error = []
        self.current_save_path = None
        print("[enter] 새 테스트 step=0")

    self.state = thread.RunState.IDLE
    qa_flow.restore_qa_result(self)     

    self.stackedWidget.setCurrentWidget(self.qa_window)  # qa_window으로 이동

def make_config(mode, **kwargs):
    """ UI에서 긁어온 값을 받아서 표준 config 딕셔너리로 변환 """
    config = {"mode": mode}

    if mode == "new":
        config["game_file"] = kwargs.get("game_file", "")
        config["txt_file"] = kwargs.get("txt_file", "")
    elif mode == "resume":
        config["qa_file"] = kwargs.get("qa_file", "")
        config["keep_going"] = kwargs.get("keep_going", "reset")

    return config

def check_config(config):
    """ config 딕셔너리의 유효성을 검사하는 함수 """
    mode = config.get("mode")

    if mode == "new":
        if not config.get("game_file"):
            return False, "게임 파일이 선택되지 않았습니다."
        if not config.get("txt_file"):
            return False, "텍스트 파일이 선택되지 않았습니다."
    elif mode == "resume":
        if not config.get("qa_file"):
            return False, "QA 파일이 선택되지 않았습니다."
        if config.get("keep_going") not in ["reset", "next"]:
            return False, "유효하지 않은 이어서 테스트 옵션입니다."
    else:
        return False, "유효하지 않은 모드입니다."

    return True, ""

def config_finish(ui):
    """ 
    QA 시작 버튼을 눌렀을 때 실행되는 함수
    config 딕셔너리를 최종 완성해서 QThread에 넘김
    """
    if is_new_mode(ui):
        # '새거' 모드일 때: UI 입력칸에서 글자를 가져옴
        final_config = make_config(
            mode="new",
            input_game = ui.gameFileRoute.text(),
            input_txt = ui.txtFileRoute.text()
        )
    elif not is_new_mode(ui):
        # '기존거' 모드일 때
        final_config = make_config(
            mode="resume", 
            qa_file=ui.QAFileRoute.text(), 
            keep_going=get_keep_going(ui)
        )

    print("완성된 설정값:", final_config)
    return final_config

def save_checkpoint(ui, qa_path):
    """
    QA기록용 저장 파일(json)
    return: 저장 성공/실패(bool)
    """

    if not qa_path: # 경로 없으면 스킵해
        ui.is_saved = False
        return False

    save_check = { # 체크포인트 딕셔너리
        "step": ui.step,
        "found_error": ui.found_error,
        "is_complete": (ui.state == thread.RunState.DONE),
        "config": ui.final_config,
    }

    try:
        with open(qa_path, "w", encoding="utf-8") as file:
            json.dump(save_check, file, ensure_ascii=False, indent=4)
    except (OSError, TypeError) as e:
        # OSError: 권한 없음, 경로 없음 / TypeError: json이 못 담는 타입 섞임
        print(f"[save] 저장 실패: {e}")
        ui.is_saved = False
        return False
    
    print("📍 [체크포인트 저장 위치]:", os.path.abspath(qa_path))

    ui.is_saved = True
    return True

def load_checkpoint(qa_path):
    """
    QA기록용 저장파일 불러오기(json)
    return: step, found_error, is_complete, config
    """
    empty = (0, [], False, {}) # 실패했을 때 기본값
    try:
        with open(qa_path, "r", encoding="utf-8") as f:
            prev = json.load(f)
        step = prev.get("step", 0)  # step 값 반환, 없으면 0 반환
        found_error = prev.get("found_error", [])
        is_complete = prev.get("is_complete", False) 
        config = prev.get("config", {})

        return step, found_error, is_complete, config
        
    except FileNotFoundError:
        return empty
    
    except json.JSONDecodeError:
        # 파일은 있는데 내용이 깨진 경우 (저장 중 강제 종료 등)
        print(f"[load] 체크포인트 파손: {qa_path}")
        return empty

def update_file_route(ui):
    """ 파일 경로를 UI에 띄움 """
    if is_new_mode(ui):
        game_path = os.path.basename(ui.gameFileRoute.text())
        txt_path = os.path.basename(ui.txtFileRoute.text())
        file_path = f"게임: {game_path} | 문서: {txt_path}"
    else:
        qa_path = os.path.basename(ui.QAFileRoute.text())
        file_path = f"QA파일: {qa_path}"
    
    ui.filesRoute.setText(file_path)