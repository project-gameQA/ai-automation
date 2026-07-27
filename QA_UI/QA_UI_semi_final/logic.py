"""
logic.py
========
모드 판정, 경로 검증, QA 저장 파일(체크포인트) 입출력, 세션 준비.

축이 두 개다. 헷갈리지 말 것.

  축 1 — QA 작업물 (NEW / RESUME)       ← 사용자가 고르는 것
      NEW    : QA 기록 없음. 처음부터
      RESUME : 이전 QA 기록(.json)이 있음. 이어서 / 처음부터 다시

  축 2 — 세션 데이터 소스 (live / replay)  ← NEW 안에서 갈라지는 것
      live   : 지금 에이전트를 실행해서 로그가 쌓이는 걸 따라 읽음
      replay : 이미 끝난 세션 폴더를 열어서 결과를 다시 봄

저장 파일 스키마 v2 설계 원칙
----------------------------
    "무엇을 테스트했는가"만 저장한다.
    mode / keep_going / qa_file 경로는 '이번에 UI 버튼을 어떻게 눌렀나'일 뿐
    테스트의 정체성이 아니다. 저장하면 A→B→C로 서로를 가리키는 체인이 생긴다.
    → 저장하지 않는다. 그 결과 NEW와 RESUME이 같은 모양의 파일을 만든다.
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


# ════════════════════════════════════════════════════════════
# 1. 모드 판정 / 화면 전환
# ════════════════════════════════════════════════════════════

def is_new_mode(ui):
    """NEW 모드면 True, RESUME 모드면 False."""
    return ui.newToggle.isChecked()


def new_resume_toggle(ui):
    """NEW/RESUME 토글에 맞춰 아래쪽 입력 페이지를 바꾼다."""
    ui.newOrResumeWindow.setCurrentIndex(0 if is_new_mode(ui) else 1)


def get_keep_going(ui):
    """
    RESUME에서 '처음부터'/'이어서' 중 무엇이 선택됐는지.

    ⚠️ 이 값은 저장 파일에 들어가지 않는다.
       이번 실행을 어떻게 시작할지 정할 때만 쓰고 버린다.

    Returns: "reset" 또는 "next"
    """
    return "reset" if ui.btnReset.isChecked() else "next"


# ════════════════════════════════════════════════════════════
# 2. 입력 선택
# ════════════════════════════════════════════════════════════

def open_game_file_dialog(ui):
    """[NEW·replay] 이미 끝난 세션 폴더 선택."""
    dir_path = QFileDialog.getExistingDirectory(
        ui, "세션 폴더 선택", settings.AGENT_LOGS_DIR)
    if dir_path:
        ui.gameFileRoute.setText(dir_path)
        ui.txtFileRoute.clear()   # 소스는 하나만. 반대쪽을 비운다


def open_txt_file_dialog(ui):
    """
    [NEW·live] 지금 열려 있는 창 목록에서 QA 대상을 고른다.

    ⚠️ exe 파일을 고르는 게 아니다.
       사용자가 게임을 먼저 켜둔 다음 이 목록에서 고르는 흐름이다.
    """
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
    ui.gameFileRoute.clear()   # 소스는 하나만


def open_qa_file_dialog(ui):
    """
    [RESUME] QA 체크포인트(.json) '파일' 선택.

    ⚠️ 세션 폴더가 아니다.
       세션 폴더 경로는 체크포인트 안의 session_dir에서 계승한다.
    """
    file_path, _ = QFileDialog.getOpenFileName(
        ui, "QA 파일 선택", "", "QA 파일 (*.json);;모든 파일 (*)")
    if file_path:
        ui.QAFileRoute.setText(file_path)


# ════════════════════════════════════════════════════════════
# 3. 경로 검증
#    ⚠️ 두 함수 모두 '위젯'을 받는다. 문자열이 아니다.
#       시그니처를 통일해야 호출부에서 헷갈리지 않는다.
# ════════════════════════════════════════════════════════════

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


# ════════════════════════════════════════════════════════════
# 4. 세션 요약 / 이벤트 총량
# ════════════════════════════════════════════════════════════

def load_session_summary(session_dir):
    """
    세션 폴더의 summary.json을 UI가 쓰는 형태로 납작하게 편다.

    원본은 target이 {"pid":…, "title":…} 중첩 dict인데
    화면에도 저장 파일에도 title만 있으면 되므로 여기서 풀어준다.

    Returns: dict. 없거나 깨졌으면 {}
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
    """events.jsonl 총 줄 수. 진행률 표시용. 실패하면 0."""
    path = os.path.join(session_dir, "events.jsonl")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


# ════════════════════════════════════════════════════════════
# 5. 에러 항목 표준형
#    자동 검출 / 수동 추가 / 구버전 변환이 전부 여기로 수렴한다.
# ════════════════════════════════════════════════════════════

def make_error_entry(report, frame_path=None, source="auto"):
    """
    classify_event() 등이 만든 리포트를 저장 파일 표준 형태로 변환.

    ⚠️ 여기 없는 키는 저장되지 않는다.
       필드를 추가하려면 이 함수와 SESSION_CONTRACT.md를 같이 고칠 것.

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


# ════════════════════════════════════════════════════════════
# 6. 체크포인트 저장 / 불러오기
# ════════════════════════════════════════════════════════════

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
    """체크포인트를 json으로 저장. 성공하면 True."""
    if not qa_path:
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
    체크포인트를 읽는다. 구버전 파일은 자동으로 v2 형태로 변환.

    ⚠️ 변환은 메모리에서만 일어난다. 덮어쓰기 전까지 원본은 그대로 남는다.

    Returns: dict. 실패하면 None
    """
    try:
        with open(qa_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        print(f"[load] 파일 없음: {qa_path}")
        return None
    except json.JSONDecodeError:
        # 저장 중 강제 종료 등으로 파일이 반쯤 쓰인 경우
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


# ════════════════════════════════════════════════════════════
# 7. 세션 준비 → 대시보드 이동
# ════════════════════════════════════════════════════════════

def reset_session_state(ui):
    """세션 관련 상태를 전부 초기값으로."""
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
    NEW 모드에서 라이브/리플레이 중 무엇이 선택됐는지 판정한다.

    규칙: 둘 중 정확히 하나만 채워져 있어야 한다.
          둘 다 또는 아무것도 안 채워져 있으면 무엇을 원하는지 알 수 없다.

    Returns: ("live", 창제목) | ("replay", 폴더경로) | None(판정 실패)
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
    입력을 검증하고 세션 상태를 세팅한 뒤 QA 대시보드로 이동한다.

    ⚠️ 이 함수는 실패하면 조용히 return한다.
       그래서 btnGoDashbord에 다른 슬롯을 추가로 connect하면 안 된다.
       (검증 실패했는데 화면이 갱신되는 사고가 난다)
    """
    if is_new_mode(ui):
        # ── NEW ──────────────────────────────────────────
        source = resolve_new_source(ui)
        if source is None:
            return
        kind, value = source

        if kind == "replay":
            if not check_session_dir(ui, ui.gameFileRoute):
                return
            reset_session_state(ui)
            ui.source_mode = "replay"
            ui.session_dir = value
            ui.session_summary = load_session_summary(value)
            ui.event_total = count_events(value)
            print(f"[enter] 리플레이 | {value} | {ui.event_total}개 이벤트")

        else:   # live
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

    else:
        # ── RESUME ───────────────────────────────────────
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

        # 저장 이후 폴더가 옮겨졌을 수 있다
        if not os.path.isdir(session_dir):
            QMessageBox.warning(
                ui, "세션 폴더 없음",
                f"저장된 세션 폴더를 찾을 수 없습니다:\n{session_dir}\n\n"
                f"폴더를 옮겼다면 NEW로 다시 지정해 주세요.")
            return

        keep_going = get_keep_going(ui)   # 여기서만 쓰고 버린다

        reset_session_state(ui)
        ui.source_mode = "replay"         # 저장된 세션은 항상 끝난 세션이다
        ui.session_dir = session_dir
        ui.session_summary = (ckpt.get("session_summary")
                              or load_session_summary(session_dir))

        if keep_going == "next":
            prog = ckpt.get("progress") or {}
            ui.event_cursor = prog.get("event_cursor", 0)
            ui.event_total = (prog.get("event_total", 0)
                              or count_events(session_dir))
            ui.found_error = ckpt.get("found_error", [])
            ui.current_save_path = ckpt_path   # 이어하기는 같은 파일에 덮어쓴다
            print(f"[enter] 이어하기 | {ui.event_cursor}/{ui.event_total}")
        else:
            # 처음부터 → 세션 폴더만 물려받고 진행 상황은 버린다.
            # current_save_path를 None으로 두어 새 파일로 저장받는다.
            ui.event_total = count_events(session_dir)
            print(f"[enter] 처음부터 | {ui.event_total}개 이벤트")

    # ── 공통: 화면 세팅 후 대시보드로 ──
    qa_flow.restore_qa_result(ui)
    update_file_route(ui)
    ui.stackedWidget.setCurrentWidget(ui.qa_window)


# ════════════════════════════════════════════════════════════
# 8. 상단 정보 표시
# ════════════════════════════════════════════════════════════

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

    # 툴팁에는 자세히
    duration = summ.get("duration_s")
    tip = (
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
