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
    """ 세션(로그) 폴더 탐색기 열기 """
    # file_dialog = QFileDialog(self)
    # file_dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
    # file_dialog.setNameFilter("Game Files (*.mp4);;All Files (*)")
    
    # if file_dialog.exec():
    #     selected_file = file_dialog.selectedFiles()[0]
    #     self.gameFileRoute.setText(selected_file)  # 선택한 파일 경로를 텍스트 박스에 표시
    dir_path = QFileDialog.getExistingDirectory(
        self, "세션 폴더 선택")
    if dir_path:
        self.gameFileRoute.setText(dir_path)

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
    # file_dialog = QFileDialog(self)
    # file_dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
    # file_dialog.setNameFilter("QA Files (*.json);;All Files (*)")
    
    # if file_dialog.exec():
    #     selected_file = file_dialog.selectedFiles()[0]
    #     self.QAFileRoute.setText(selected_file)  # 선택한 파일 경로를 텍스트 박스에 표시

    dir_path = QFileDialog.getExistingDirectory(
        self, "세션 폴더 선택")
    if dir_path:
        self.QAFileRoute.setText(dir_path)

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

def check_session_dir(self, dir_path):
    """세션 폴더에 필수 파일이 있는지 확인"""
    required = ["events.jsonl", "perf.csv", "summary.json"]
    missing = [f for f in required
               if not os.path.exists(os.path.join(dir_path, f))]
    if missing:
        QMessageBox.warning(self, "파일 누락",
            f"세션 폴더에 다음 파일이 없습니다:\n{', '.join(missing)}")
        return False
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
    """ QA 대시보드 화면으로 이동 및 check_session_dir()(돌려놔야되면 check_path()) """

    if is_new_mode(self): # new모드일때
        if not check_session_dir(self, self.gameFileRoute): # 게임경로
            return # 통과 못하면 멈춰
        # if not check_session_dir(self, self.txtFileRoute, expected_ext=".txt"): # 문서경로
        #     return

        self.final_config = config_finish(self)
        self.step = 0
        self.found_error = [] 
        self.current_save_path = None
    else: # resume모드일때
        if not check_session_dir(self, self.QAFileRoute): # qa파일경로
            return
        
        ckpt = self.QAFileRoute.text()
        step, found_error, is_complete, saved_config = load_checkpoint(ckpt)

        if not saved_config.get("game_file"):
            QMessageBox.warning(self, "파일 오류",
                "이 QA 파일에는 게임/문서 경로 정보가 없습니다.\n"
                "새 테스트로 시작해 주세요.")
            return

        self.final_config = config_finish(self, saved_config)   # 조립은 위임

        if self.final_config["keep_going"] == "next": # 이어하기
            self.step = step
            self.found_error = found_error
            self.prev_is_complete = is_complete
            self.current_save_path = ckpt
            print(f"[enter] 이어하기 step={step}")
        else: # 처음부터
            self.step = 0
            self.found_error = []
            self.current_save_path = None
            print("[enter] 새 테스트 step=0")

    self.state = thread.RunState.IDLE
    qa_flow.restore_qa_result(self) # 화면 세팅(초기화를 하든 전에걸 불러오든)
    update_file_route(self) # 테스트 경로 세팅

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
        config["game_file"] = kwargs.get("game_file", "")
        config["txt_file"] = kwargs.get("txt_file", "")

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

def config_finish(ui, saved_config=None):
    """ 
    config 딕셔너리 완성.
    saved_config: resume 모드일 때 체크포인트에서 읽은 원본 config.
    game_file/txt_file을 여기서 계승받음
    """
    if is_new_mode(ui):
        # '새거' 모드일 때: UI 입력칸에서 글자를 가져옴
        return make_config(
            mode="new",
            game_file = ui.gameFileRoute.text(),
            txt_file = ui.txtFileRoute.text(),
        )
    return make_config(
        # '기존거' 모드일 때
        mode="resume",
        qa_file=ui.QAFileRoute.text(),
        keep_going=get_keep_going(ui),
        # 무엇을 테스트하는가 → 체크포인트에서 계승
        game_file=saved_config["game_file"],
        txt_file=saved_config["txt_file"],
    )

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
        # OSError: 권한/경로 없음 | TypeError: json이 못 담는 타입 섞임
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
    """ 테스트 중인 파일 경로를 UI에 띄움 """
    game_path = ui.final_config["game_file"]
    txt_path = ui.final_config["txt_file"]

    if ui.final_config["mode"] == "new": # 뉴모드일때
        file_path = f"게임: {os.path.basename(game_path)} | 문서: {os.path.basename(txt_path)}"
        ui.filesRoute.setText(file_path)
        ui.filesRoute.setToolTip(f"게임: {game_path}\n문서: {txt_path}")
    else: # resume일때
        qa_path = ui.QAFileRoute.text()
        file_path = f"게임: {os.path.basename(txt_path)} | 문서: {os.path.basename(txt_path)}"
        ui.filesRoute.setText(file_path)
        ui.filesRoute.setToolTip(f"게임: {game_path}\n문서: {txt_path}\nQA파일: {qa_path}")

    