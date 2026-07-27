"""
settings.py
===========
경로와 기본값을 한 곳에 모은 파일. (신규)

왜 만들었나:
    전에는 "D:/project_gameQA/ai-automation/QA_UI/qa_first.ui" 같은
    절대경로가 main.py와 menu_bar.py에 흩어져 있었다.
    다른 PC에서 클론하면 바로 죽고, 발표용 노트북에서도 죽는다.
    바뀔 수 있는 값은 전부 여기 모아서 한 파일만 고치면 되게 했다.
"""

import os

# ── 경로 ─────────────────────────────────────────────────────
# __file__ = 이 파일(settings.py)의 경로
# → 그 폴더가 QA_UI, 그 위가 프로젝트 루트
BASE_DIR = os.path.dirname(os.path.abspath(__file__))          # .../QA_UI
PROJECT_ROOT = os.path.dirname(BASE_DIR)                       # .../ai-automation

# 에이전트(GamingAI)가 있는 곳. 팀원 폴더가 들어오면 여기만 맞추면 된다.
AGENT_DIR = os.path.join(PROJECT_ROOT, "GamingAI")
AGENT_MAIN = "main.py"                                          # AGENT_DIR 기준 상대
AGENT_LOGS_DIR = os.path.join(AGENT_DIR, "logs")                # 세션 폴더가 생기는 곳


def ui_path(filename):
    """QA_UI 폴더 안의 .ui 파일 절대경로를 만든다."""
    return os.path.join(BASE_DIR, filename)


# ── 에이전트 실행 옵션 ────────────────────────────────────────
# ⚠️ GamingAI/main.py의 기본값이 위험한 것들이라 여기서 명시적으로 덮어쓴다.
AGENT_DURATION_S = 300        # 에이전트 기본값 36000(10시간) → 시연용 5분
AGENT_MODE = "agent"          # ⚠️ 에이전트 기본값이 "monkey"다. 반드시 명시할 것
AGENT_MAX_LLM_CALLS = 300     # 에이전트 기본값 100은 금방 끊긴다
AGENT_RECORD_VIDEO = True

# 없으면 fps 컬럼이 전부 빈 칸이 된다. PresentMon 실행파일 경로.
PRESENTMON_PATH = None

# 환경변수 GEMINI_API_KEY를 먼저 보고, 없으면 여기 직접 넣어도 된다.
# ⚠️ 여기 키를 적어놓고 깃에 올리지 말 것.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# 에이전트를 띄운 뒤 세션 폴더가 생기기를 기다리는 최대 시간(초)
SESSION_WAIT_TIMEOUT_S = 60


# ── 리플레이 재생 속도 ────────────────────────────────────────
# 원본 세션의 시각 간격을 이 값으로 나눠서 재생한다.
#
#   계산 예시: 실제 세션 18,587초(약 5시간) / 200 ≈ 93초
#   → 로그가 1분 30초 동안 자연스럽게 흐른다. 시연 영상용으로 적당.
#
#   1   = 실시간 (5시간. 쓰지 말 것)
#   200 = 약 1분 30초  ← 기본값
#   0   = 지연 없이 최대 속도 (몇 초 만에 끝. 디버깅용)
REPLAY_SPEED = 200

# 한 번에 자는 최대 시간(초). 원본에 몇 분씩 벌어진 구간이 있어서
# 이걸 안 걸면 화면이 멈춘 것처럼 보인다.
REPLAY_MAX_SLEEP_S = 0.2


# ── 로그창 성능 ──────────────────────────────────────────────
# 이벤트 23,570개를 한 줄씩 시그널로 쏘면 GUI 이벤트 큐가 터진다.
# 모아서 한 번에 보내고, 문서 길이도 제한한다.
LOG_FLUSH_LINES = 25          # 이만큼 모이면 전송
LOG_FLUSH_INTERVAL_S = 0.15   # 또는 이 시간이 지나면 전송
MAX_LOG_BLOCKS = 5000         # allLog가 보관하는 최대 줄 수 (넘으면 위에서부터 버림)
PROGRESS_EVERY = 50           # 진행률 시그널 주기 (이벤트 N개마다)


# ── 판정 임계치 ──────────────────────────────────────────────
CPU_SPIKE_THRESHOLD = 90.0    # 이 이상이면 CPU 급증으로 본다
