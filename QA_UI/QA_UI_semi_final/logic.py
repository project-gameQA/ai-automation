"""
모드 관련: is_new_mode, new_resume_toggle, get_keep_going
경로 관련: open_game_file_dialog, open_txt_file_dialog, open_qa_file_dialog, check_path, check_session_dir, update_file_route
qa파일에 저장할거: count_events, make_error_entry, build_checkpoint
저장및 로드: save_checkpoint, load_checkpoint
더미데이터 쓰던 시절의 데이터를.. 혹시모르니까: migrate_v1_to_v2
'이번세션'에 필요한거: load_session_summary, reset_session_state, resolve_new_source
화면이동: go_dashboard
"""

import json
import os
from datetime import datetime

from PyQt6.QtWidgets import QFileDialog, QMessageBox, QInputDialog

import settings
import session_reader
import thread
import qa_flow

# 저장 파일 스키마 버전. 구조를 바꾸면 올리고 migrate 함수를 추가한다.
SCHEMA_VERSION = 2

# 세션 폴더에 반드시 있어야 하는 파일 (replay 기준)
REQUIRED_SESSION_FILES = ["events.jsonl", "perf.csv", "summary.json"]


def is_new_mode(ui):
    """ new면 True, resume면 False """
    return ui.newToggle.isChecked()

def new_resume_toggle(ui):
    """ new-resume 화면 전환 """    
    ui.newOrResumeWindow.setCurrentIndex(0 if is_new_mode(ui) else 1)

def get_keep_going(ui):
    """ 'reset' 또는 'next' 반환. """
    return "reset" if ui.btnReset.isChecked() else "next"

def open_game_file_dialog(ui):
    """세션(로그) 폴더 탐색기 열기"""
    dir_path = QFileDialog.getExistingDirectory(
        ui, "세션 폴더 선택", settings.AGENT_LOGS_DIR)
    if dir_path:
        ui.gameFileRoute.setText(dir_path)
        ui.txtFileRoute.clear() # 소스는 하나만. 반대쪽 비움

def open_txt_file_dialog(ui):
    """창 열기(디코 화면공유할때 화면 하나 특정하는 그런거)"""
    try:
        windows = session_reader.list_game_windows()
    except RuntimeError as e:
        QMessageBox.warning(ui, "창 목록을 못 가져옴", str(e))
        return

    if not windows:
        QMessageBox.information(
            ui, "창 없음",
            "열려 있는 창을 찾지 못했습니다.\n게임을 먼저 실행해 주세요.")
        return

    labels = [session_reader.format_window_label(w) for w in windows]
    label, ok = QInputDialog.getItem(
        ui, "QA 대상 선택", "실행 중인 창 중에서 고르세요:", labels, 0, False)
    if not ok:
        return

    # 라벨에서 다시 원본 dict를 찾아 제목만 꺼낸다
    chosen = windows[labels.index(label)]
    ui.txtFileRoute.setText(chosen["title"])
    ui.gameFileRoute.clear() # 소스는 하나만. 반대쪽 비움

def open_qa_file_dialog(ui):
    """QA파일(.json) 탐색기 열기"""
    file_path, _ = QFileDialog.getOpenFileName(
        ui, "QA 파일 선택", "", "QA 파일 (*.json);;모든 파일 (*)")
    if file_path:
        ui.QAFileRoute.setText(file_path)


# 경로 유효성 검증
def check_path(ui, line_edit, expected_ext=None):
    """파일이 실제로 존재하는지 + 확장자가 맞는지. 통과하면 True."""
    path = line_edit.text().strip()

    if not os.path.isfile(path):
        line_edit.setStyleSheet("border: 1px solid red;")
        QMessageBox.warning(ui, "경로 이상",
                            f"파일을 찾을 수 없습니다.\n현재 경로: {path}")
        return False

    if expected_ext and not path.lower().endswith(expected_ext):
        line_edit.setStyleSheet("border: 1px solid orange;")
        QMessageBox.warning(ui, "확장자 이상",
                            f"확장자를 확인해주세요.\n필요 확장자: {expected_ext}")
        return False

    line_edit.setStyleSheet("")
    return True

def check_session_dir(ui, line_edit):
    """세션 폴더에 필수 파일이 다 있는지. 통과하면 True."""
    dir_path = line_edit.text().strip()

    if not os.path.isdir(dir_path):
        line_edit.setStyleSheet("border: 1px solid red;")
        QMessageBox.warning(ui, "경로 이상",
                            f"폴더를 찾을 수 없습니다.\n현재 경로: {dir_path}")
        return False

    missing = [f for f in REQUIRED_SESSION_FILES
               if not os.path.exists(os.path.join(dir_path, f))]
    if missing:
        line_edit.setStyleSheet("border: 1px solid orange;")
        QMessageBox.warning(ui, "파일 누락",
                            f"세션 폴더에 다음 파일이 없습니다:\n{', '.join(missing)}")
        return False

    line_edit.setStyleSheet("")
    return True

def load_session_summary(session_dir):
    """
    세션 폴더의 summary.json을 UI가 쓰는 형태로 납작하게 편다.

    원본은 target이 {"pid":…, "title":…} 중첩 dict인데
    화면에도 저장 파일에도 title만 있으면 되므로 여기서 풀어준다.

    return: dict. 없거나 깨졌으면 {}
    """
    path = os.path.join(session_dir, "summary.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}

    target = raw.get("target") or {}
    return {
        "target": target.get("title", ""),
        "pid": target.get("pid"),
        "duration_s": raw.get("duration_s"),
        "actions_total": raw.get("actions_total"),
        "turn_count": raw.get("turn_count"),
        "llm_call_count": raw.get("llm_call_count"),
        "stop_reason": raw.get("stop_reason"),
    }


def count_events(session_dir):
    """events.jsonl 총 몇줄?
    진행률 표시용. 실패하면 0."""
    path = os.path.join(session_dir, "events.jsonl")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0

def make_error_entry(report, frame_path=None, source="auto"):
    """
    검출된 에러를 저장하기 좋게 정리

    Args:
        frame_path: 스크린샷 상대경로 (session_dir 기준). 절대경로 금지
        source: "auto"(자동 검출) 또는 "manual"(사람이 손으로 추가)
    """
    return {
        "seq": report.get("seq"),
        "ts": report.get("ts"),
        "title": report.get("title", "(제목 없음)"),
        "content": report.get("content", ""),
        "severity": report.get("severity", "medium"),
        "category": report.get("category", session_reader.CATEGORY_GAME),
        "event_type": report.get("event_type", "unknown"),
        "frame_path": frame_path,
        "source": source,
    }

def build_checkpoint(ui):
    """현재 UI 상태를 저장용 dict로 조립. (스키마 v2)"""
    return {
        "schema_version": SCHEMA_VERSION,
        "saved_at": datetime.now().isoformat(timespec="seconds"),

        # ── 무엇을 테스트했는가 (이 파일의 정체성) ──
        "session_dir": ui.session_dir,

        # ── 어디까지 읽었는가 ──
        "progress": {
            "event_cursor": ui.event_cursor,
            "event_total": ui.event_total,
            "is_complete": (ui.state == thread.RunState.DONE),
        },

        # ── 세션 폴더가 없는 PC에서도 요약은 보이도록 사본 ──
        "session_summary": ui.session_summary,

        # ── 검출/추가된 에러 목록 ──
        "found_error": ui.found_error,
    }

def save_checkpoint(ui, qa_path):
    """
    QA기록용 저장 파일(json)
    return: 저장 성공/실패(bool)
    """
    if not qa_path: # 경로 없으면 스킵해
        ui.is_saved = False
        return False

    try:
        with open(qa_path, "w", encoding="utf-8") as f:
            json.dump(build_checkpoint(ui), f, ensure_ascii=False, indent=2)
    except (OSError, TypeError) as e:
        # OSError: 권한/경로 문제 | TypeError: json이 못 담는 타입이 섞임
        print(f"[save] 저장 실패: {e}")
        QMessageBox.critical(ui, "저장 실패", f"저장하지 못했습니다.\n{e}")
        ui.is_saved = False
        return False

    print(f"📍 [체크포인트 저장] {os.path.abspath(qa_path)}")
    ui.is_saved = True
    return True


def load_checkpoint(qa_path):
    """
    QA기록용 저장파일 불러오기(json)
    return: dict
    """
    try:
        with open(qa_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        print(f"[load] 파일 없음: {qa_path}")
        return None
    except json.JSONDecodeError:
        # 저장 중 이슈로 파일이 이상해
        print(f"[load] 파일 파손: {qa_path}")
        return None
    except OSError as e:
        print(f"[load] 읽기 실패: {e}")
        return None

    if not isinstance(raw, dict):
        print(f"[load] 최상위가 dict가 아님: {type(raw)}")
        return None

    # schema_version이 없으면 v1(구버전)
    if raw.get("schema_version", 1) < SCHEMA_VERSION:
        print("[load] 구버전 파일 감지 → v2로 변환")
        raw = migrate_v1_to_v2(raw)

    return raw


def migrate_v1_to_v2(old):
    """
    구버전 저장 파일을 v2로 변환.

    v1은 config 안에 mode/keep_going/qa_file/game_file/txt_file이 섞여 있었다.
    그중 v2에서 의미가 남는 건 '세션 폴더 경로'뿐이다. 나머지는 버린다.
    """
    old_config = old.get("config") or {}

    # 세션 폴더가 있을 만한 자리를 순서대로 뒤진다
    session_dir = (old_config.get("session_dir")
                   or old_config.get("game_file")
                   or "")

    errors = []
    for e in old.get("found_error", []):
        errors.append({
            # v1에는 seq 개념이 없었다. source_event_seq → step 순으로 찾는다
            "seq": e.get("source_event_seq", e.get("step")),
            "ts": e.get("ts"),
            "title": e.get("title", "(제목 없음)"),
            "content": e.get("content", ""),
            # v1에 없던 필드 → 안전한 기본값
            "severity": e.get("severity", "medium"),
            "category": e.get("category", session_reader.CATEGORY_GAME),
            "event_type": e.get("event_type", "unknown"),
            "frame_path": e.get("frame_path"),
            "source": e.get("source", "auto"),
        })

    return {
        "schema_version": SCHEMA_VERSION,
        "saved_at": old.get("saved_at", ""),
        "session_dir": session_dir,
        "progress": {
            "event_cursor": old.get("step", 0),
            "event_total": old.get("total_steps", 0),
            "is_complete": old.get("is_complete", False),
        },
        "session_summary": old.get("session_summary", {}),
        "found_error": errors,
    }

def reset_session_state(ui):
    """세션 관련 상태 초기화"""
    ui.session_dir = ""
    ui.session_summary = {}
    ui.event_cursor = 0
    ui.event_total = 0
    ui.found_error = []
    ui.report_cache = {}
    ui.current_save_path = None
    ui.is_saved = True
    ui.state = thread.RunState.IDLE
    ui.source_mode = "replay"
    ui.target_title = ""
    ui.has_log = False

def resolve_new_source(ui):
    """
    new -> live? replay?

    return: ("live", 창제목) | ("replay", 폴더경로) | None(판정 실패)
    """
    target = ui.txtFileRoute.text().strip()
    folder = ui.gameFileRoute.text().strip()

    if target and folder:
        QMessageBox.warning(
            ui, "선택이 둘",
            "'대상 창'과 '세션 폴더'가 둘 다 채워져 있습니다.\n"
            "하나만 남기고 지워주세요.\n\n"
            "· 대상 창  → 지금 게임을 QA (라이브)\n"
            "· 세션 폴더 → 끝난 세션 결과 보기 (리플레이)")
        return None

    if target:
        return ("live", target)
    if folder:
        return ("replay", folder)

    QMessageBox.warning(
        ui, "입력 없음",
        "무엇을 QA할지 선택해주세요.\n\n"
        "· 대상 창  → 지금 게임을 QA (라이브)\n"
        "· 세션 폴더 → 끝난 세션 결과 보기 (리플레이)")
    return None


def go_dashboard(ui):
    """
    입력을 검증하고 세션 상태를 세팅한 뒤 QA 대시보드로 이동
    """
    if is_new_mode(ui): # new모드일때
        source = resolve_new_source(ui)
        if source is None:
            return
        kind, value = source

        if kind == "replay": # new-replay일때
            if not check_session_dir(ui, ui.gameFileRoute):
                return
            reset_session_state(ui)
            ui.source_mode = "replay"
            ui.session_dir = value
            ui.session_summary = load_session_summary(value)
            ui.event_total = count_events(value)
            print(f"[enter] 리플레이 | {value} | {ui.event_total}개 이벤트")

        else: # new-live
            # 에이전트를 아직 안 띄웠으므로 세션 폴더를 알 수 없다.
            # 워커가 폴더를 찾은 뒤 session_ready 시그널로 채워준다.
            if not os.path.isdir(settings.AGENT_DIR):
                QMessageBox.warning(
                    ui, "에이전트 없음",
                    f"에이전트 폴더를 찾을 수 없습니다:\n{settings.AGENT_DIR}\n\n"
                    f"settings.py의 AGENT_DIR를 확인해주세요.")
                return
            reset_session_state(ui)
            ui.source_mode = "live"
            ui.target_title = value
            print(f"[enter] 라이브 | 대상='{value}'")

    else: # resume일때
        if not check_path(ui, ui.QAFileRoute, expected_ext=".json"):
            return

        ckpt_path = ui.QAFileRoute.text().strip()
        ckpt = load_checkpoint(ckpt_path)
        if ckpt is None:
            QMessageBox.warning(ui, "파일 오류",
                                "QA 파일을 읽을 수 없습니다.\n"
                                "파일이 손상되었을 수 있습니다.")
            return

        session_dir = ckpt.get("session_dir", "")
        if not session_dir:
            QMessageBox.warning(ui, "파일 오류",
                                "이 QA 파일에는 세션 폴더 정보가 없습니다.\n"
                                "새 테스트로 시작해 주세요.")
            return

        # 저장 이후 폴더가 이동햇으면 ㅜ?
        if not os.path.isdir(session_dir):
            QMessageBox.warning(
                ui, "세션 폴더 없음",
                f"저장된 세션 폴더를 찾을 수 없습니다:\n{session_dir}\n\n"
                f"폴더를 옮겼다면 NEW로 다시 지정해 주세요.")
            return

        keep_going = get_keep_going(ui)   # 여기서만 쓰고 버린다

        reset_session_state(ui) # 초기화
        ui.source_mode = "replay"
        ui.session_dir = session_dir
        ui.session_summary = (ckpt.get("session_summary")
                              or load_session_summary(session_dir))

        if keep_going == "next":
            prog = ckpt.get("progress") or {}
            ui.event_cursor = prog.get("event_cursor", 0)
            ui.event_total = (prog.get("event_total", 0)
                              or count_events(session_dir))
            ui.found_error = ckpt.get("found_error", [])
            ui.current_save_path = ckpt_path # 이어하기는 같은 파일에 덮어씀
            print(f"[enter] 이어하기 | {ui.event_cursor}/{ui.event_total}")
        else: # reset일때
            ui.event_total = count_events(session_dir)
            print(f"[enter] 처음부터 | {ui.event_total}개 이벤트")

    # 화면 세팅 후 대시보드로
    qa_flow.restore_qa_result(ui)
    update_file_route(ui)
    ui.stackedWidget.setCurrentWidget(ui.qa_window)

def update_file_route(ui):
    """대시보드 상단에 지금 무엇을 테스트 중인지 표시한다."""
    summ = ui.session_summary or {}

    if ui.source_mode == "live":
        head = f"🎮 라이브  |  대상: {ui.target_title or '?'}"
        if ui.session_dir:
            head += f"  |  {os.path.basename(ui.session_dir)}"
    else:
        folder = os.path.basename(ui.session_dir.rstrip("/\\")) or "(폴더 없음)"
        head = f"📁 리플레이  |  {folder}  |  대상: {summ.get('target') or '?'}"

    if ui.event_total:
        head += f"  |  {ui.event_cursor}/{ui.event_total}"

    ui.filesRoute.setText(head)

    duration = summ.get("duration_s")
    tip = ( # 툴팁에 들어갈 내용
        f"소스: {'라이브(에이전트 실행)' if ui.source_mode == 'live' else '리플레이(기록된 세션)'}\n"
        f"세션 폴더: {ui.session_dir or '(아직 없음)'}\n"
        f"대상: {summ.get('target') or ui.target_title or '?'}\n"
        f"총 시간: {f'{duration:.0f}초' if duration else '?'}\n"
        f"액션 수: {summ.get('actions_total', '?')}\n"
        f"종료 사유: {summ.get('stop_reason', '?')}\n"
        f"이벤트: {ui.event_cursor} / {ui.event_total}"
    )
    if ui.current_save_path:
        tip += f"\nQA 파일: {ui.current_save_path}"
    ui.filesRoute.setToolTip(tip)
