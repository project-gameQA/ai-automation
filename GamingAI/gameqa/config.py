from dataclasses import dataclass, field
from typing import Optional

DEFAULT_ACTION_WEIGHTS = {
    "click_vision_candidate": 0.45,
    "press_common_key": 0.30,
    "random_click_in_bounds": 0.15,
    "mouse_drag": 0.05,
    "scroll_wheel": 0.05,
}

DEFAULT_KEY_POOL = [
    "w", "a", "s", "d", "up", "down", "left", "right",
    "space", "enter", "esc", "tab", "1", "2", "3",
]

DEFAULT_ESCALATION_LADDER = ["esc", "tab_enter", "click_corner", "log_confirmed_stuck"]


@dataclass
class RunConfig:
    target: str
    duration_s: float = 600.0
    max_actions: Optional[int] = None
    action_interval_s: float = 1.0
    screenshot_interval_s: float = 5.0
    perf_interval_s: float = 2.0
    key_pool: list = field(default_factory=lambda: list(DEFAULT_KEY_POOL))
    action_weights: dict = field(default_factory=lambda: dict(DEFAULT_ACTION_WEIGHTS))
    stuck_hash_distance_threshold: int = 2
    stuck_action_window: int = 8
    escalation_ladder: list = field(default_factory=lambda: list(DEFAULT_ESCALATION_LADDER))
    ocr_enabled: bool = False
    record_video: bool = False
    video_fps: int = 5
    confirmed_stuck_terminates_run: bool = False
    presentmon_path: Optional[str] = None
    logs_dir: str = "logs"
    llm_fallback_enabled: bool = False
    gemini_api_key: Optional[str] = None  # falls back to GEMINI_API_KEY env var if unset
    gemini_api_key_fallback: Optional[str] = None  # falls back to GEMINI_API_KEY_2 env var if unset; used when the primary key's daily quota is exhausted (see game_agent.py's key-switch logic)
    gemini_model: str = "gemini-flash-lite-latest"

    # "monkey" = cheap random exploration + occasional Gemini nudge when stuck
    # (default, good for broad crash/bug QA). "agent" = continuous per-turn
    # Gemini-driven play for genuine autonomous progression -- see
    # gameqa/game_agent.py and README for the cost/latency tradeoff.
    mode: str = "monkey"
    agent_interval_s: float = 1.0  # minimum spacing between agent-mode Gemini calls
    max_llm_calls: Optional[int] = 100  # hard budget per session; None = unlimited
    # How many real LLM turns the chat session keeps its full history
    # (every prior screenshot + reasoning) before it's wiped and replaced
    # with a short text summary (see game_agent.py's _summarize_memory).
    # Confirmed live this was too aggressive at the old default of 8: a
    # 150-turn session reset 10 times, and each surviving summary retained
    # only one narrow fact (e.g. "this exact spot is a confirmed dead
    # end"), not any real map memory -- effectively making the model
    # re-orient from near-scratch every 8 turns. gemini-flash-lite-latest's
    # context window comfortably fits far more than 8 turns of
    # screenshot+text, so raised substantially; still bounded (not
    # disabled) as a safety valve against unbounded token growth on very
    # long sessions.
    memory_summarize_every_n_turns: int = 20
    # Diagnostic-only toggle: when True, stuck_warning still gets computed
    # and shown to the model in its per-turn note, but the code-level
    # escape tiers (which normally act directly, without an LLM call, once
    # stuck) never fire -- every turn goes through a real LLM call instead.
    # For comparing "how does the model's own visual judgment handle this
    # unassisted" against the normal escape-assisted behavior.
    disable_heuristic_escapes: bool = False
