import sys
import os
import json
from PyQt6.QtWidgets import QApplication, QDialog, QFileDialog, QMainWindow
from PyQt6 import uic
import time
from PyQt6.QtCore import QThread, pyqtSignal
from datetime import datetime
import importlib

UI_DIR = os.path.dirname(os.path.abspath(__file__))

# [통합] qa_bot_mvp 패키지 경로 추가 (QA_UI 옆에 qa_bot_mvp 가 있다고 가정).
QA_BOT_DIR = os.path.join(UI_DIR, "..", "qa_bot_mvp")
if QA_BOT_DIR not in sys.path:
    sys.path.insert(0, QA_BOT_DIR)

REPORTS_DIR = os.path.join(QA_BOT_DIR, "reports")
MODELS_DIR = os.path.join(QA_BOT_DIR, "models")

# 게임 -> 어댑터/결함모듈/모델 매핑
GAME_MAP = {
    "MiniGrid-DoorKey-8x8": {
        "collect_mod": "adapters.minigrid.collect",
        "defect_mod": "demo.minigrid_defects",
        "game_id": "minigrid-doorkey-8x8",
        "model": "minigrid_baseline.joblib",
        "defects": ["teleport", "under_explore", "low_entropy"],
    },
    "ViZDoom-my-way-home": {
        "collect_mod": "adapters.vizdoom.collect",
        "defect_mod": "demo.vizdoom_defects",
        "game_id": "vizdoom-my-way-home",
        "model": "vizdoom_baseline.joblib",
        "defects": ["stuck", "under_explore", "low_entropy"],
    },
}

# 실행 규모(시연에 적당하게. 필요시 조절)
N_TRAIN = 120          # 학습용 정상 판 수
N_DEMO_NORMAL = 15     # 시연: 정상 판
N_DEMO_DEFECT = 5      # 시연: 결함 종류별 판
N_DEPLOY = 20          # 배포: 정상 판


def model_path_for(game):
    return os.path.join(MODELS_DIR, GAME_MAP[game]["model"])

def model_exists(game):
    return os.path.exists(model_path_for(game))


# ============================================================================
# 파일 다이얼로그(팀원 원본 유지)
# ============================================================================
def open_game_file_dialog(self):
    file_dialog = QFileDialog(self)
    file_dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
    file_dialog.setNameFilter("Game Files (*.mp4);;All Files (*)")
    if file_dialog.exec():
        self.gameFileRoute.setText(file_dialog.selectedFiles()[0])

def open_txt_file_dialog(self):
    file_dialog = QFileDialog(self)
    file_dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
    file_dialog.setNameFilter("Text Files (*.csv);;All Files (*)")
    if file_dialog.exec():
        self.txtFileRoute.setText(file_dialog.selectedFiles()[0])

def go_to_qa_window(self):
    self.stackedWidget.setCurrentWidget(self.qa_window)

def show_error_detail(self, item):
    self.errorReport.setText(dummy_ai_reports[item.text()])


# ============================================================================
# 수동 에러 추가 팝업(팀원 원본 유지)
# ============================================================================
class Error_Plus_Dialog(QDialog):
    def __init__(self):
        super().__init__()
        uic.loadUi(os.path.join(UI_DIR, "error_report_plus.ui"), self)
        self.saveAndHistoryAdd.clicked.connect(self.accept)

def open_error_plus_popup(ui):
    dialog = Error_Plus_Dialog()
    if dialog.exec():
        full_content = dialog.content.toPlainText().strip()
        if not full_content:
            return
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        raw_title = full_content.split('\n')[0]
        if len(raw_title) > 15:
            raw_title = raw_title[:15] + "..."
        new_title = f"[수동 보고] {raw_title}"
        full_content = (f"■ 보고 방식: 수동 리포트\n■ 발생 시간: {current_time}\n"
                        f"----------------------------------------\n■ 상세 내용:\n{full_content}")
        ui.errorReportHistory.addItem(new_title)
        dummy_ai_reports[new_title] = full_content
        ui.stackedWidget.setCurrentWidget(ui.qa_window)
        ui.errorReport.setText(full_content)


# ============================================================================
# 리포트 -> 목록/상세 표시
# ============================================================================
def format_episode_title(ep):
    idx = ep["index"]
    if ep["hard_violations"]:
        types = ", ".join(v["type"] for v in ep["hard_violations"])
        return f"[규칙] 판 #{idx} · {types}"
    if ep["flagged"]:
        return f"[이상] 판 #{idx} · score {ep['anomaly_score']:.2f}"
    return f"판 #{idx}"

def format_episode_detail(ep):
    lines = [f"[QA 리포트] 판 #{ep['index']} (seed {ep['seed']})",
             f"- 결과(outcome): {ep['outcome']}"]
    if ep["hard_violations"]:
        for v in ep["hard_violations"]:
            loc = f"step {v['step']}" + (f"~{v['end']}" if "end" in v else "")
            lines.append(f"- [규칙 위반] {v['type']} ({loc})")
    else:
        lines.append("- [규칙 위반] 없음")
    lines.append(f"- 이상 점수(anomaly_score): {ep['anomaly_score']:.3f}  (flagged={ep['flagged']})")
    steps = ep.get("anomaly_steps", [])
    if steps:
        shown = ", ".join(map(str, steps[:15]))
        more = f" 외 {len(steps) - 15}개" if len(steps) > 15 else ""
        lines.append(f"- 이상 스텝 위치(시계열): {shown}{more}")
    lines.append("- 특징(features):")
    for k, v in ep["features"].items():
        lines.append(f"    {k}: {v}")
    return "\n".join(lines)

def display_report(ui, report):
    """리포트(dict)를 탭1 목록/상세에 표시."""
    episodes = report.get("episodes", [])
    triage = [e for e in episodes if e["hard_violations"] or e["flagged"]]
    ui.errorReportHistory.clear()
    dummy_ai_reports.clear()
    for ep in triage:
        title = format_episode_title(ep)
        dummy_ai_reports[title] = format_episode_detail(ep)
        ui.errorReportHistory.addItem(title)
    s = report.get("summary", {})
    ui.allLog.append(f"[완료] 규칙위반 {s.get('n_hard_violations',0)}판 + "
                     f"이상 {s.get('n_flagged',0)}판 (전체 {s.get('n_episodes',0)}판). "
                     f"트리아지 {len(triage)}건 표시.")
    if triage:
        first = ui.errorReportHistory.item(0).text()
        ui.errorReport.setText(dummy_ai_reports[first])

def update_realtime_log(ui, message):
    print(f"[시스템] {message}")
    ui.allLog.append(message)


# ============================================================================
# 백그라운드 작업 스레드 (UI 안 멈추게)
# ============================================================================
class _Base(QThread):
    log = pyqtSignal(str)
    done = pyqtSignal(object)     # 결과 객체
    fail = pyqtSignal(str)

class DataGenWorker(_Base):
    """[봇으로 정상 데이터 생성 (시연용)] 정상 봇 N판 플레이."""
    def __init__(self, game, n): super().__init__(); self.game=game; self.n=n
    def run(self):
        try:
            self.log.emit(f"정상 봇으로 데이터 생성 중... ({self.n}판)")
            mod = importlib.import_module(GAME_MAP[self.game]["collect_mod"])
            eps = mod.collect(self.n, seed0=0)
            self.log.emit(f"정상 데이터 {len(eps)}건 생성 완료")
            self.done.emit(eps)
        except Exception as e:
            self.fail.emit(f"데이터 생성 실패: {e}")

class TrainWorker(_Base):
    """[기준 모델 학습] 정상 데이터로 학습 + 저장."""
    def __init__(self, game, normal_eps): super().__init__(); self.game=game; self.eps=normal_eps
    def run(self):
        try:
            from qa_core.service import train_baseline
            info = GAME_MAP[self.game]
            train_baseline(self.eps, game_id=info["game_id"],
                           model_path=model_path_for(self.game),
                           progress=lambda m: self.log.emit(m))
            self.done.emit(model_path_for(self.game))
        except Exception as e:
            self.fail.emit(f"학습 실패: {e}")

class QARunWorker(_Base):
    """[QA 실행] 봇 플레이 -> 저장모델로 검사 -> 리포트. mode: 'demo'|'deploy'."""
    def __init__(self, game, mode): super().__init__(); self.game=game; self.mode=mode
    def run(self):
        try:
            info = GAME_MAP[self.game]
            cmod = importlib.import_module(info["collect_mod"])
            self.log.emit(f"정상 봇 플레이 중... ({N_DEMO_NORMAL if self.mode=='demo' else N_DEPLOY}판)")
            if self.mode == "demo":
                eps = cmod.collect(N_DEMO_NORMAL, seed0=9000)
                dmod = importlib.import_module(info["defect_mod"])
                for i, d in enumerate(info["defects"]):
                    self.log.emit(f"결함 봇 플레이 중... ({d})")
                    eps += dmod.collect_defective(N_DEMO_DEFECT, defect=d, seed0=9100 + i*100)
            else:
                eps = cmod.collect(N_DEPLOY, seed0=9000)   # 배포: 정상만

            self.log.emit("저장된 기준 모델로 분석 중...")
            from qa_core.service import run_qa
            rpt = os.path.join(REPORTS_DIR, f"report_ui_{info['game_id']}.json")
            report = run_qa(eps, model_path=model_path_for(self.game),
                            report_path=rpt, progress=lambda m: self.log.emit(m))
            self.done.emit(report)
        except Exception as e:
            self.fail.emit(f"QA 실행 실패: {e}")


# 상세 저장소(제목 -> 상세문자열)
dummy_ai_reports = {}
