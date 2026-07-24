import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# Window titles can contain arbitrary Unicode (CJK, emoji, etc.) that the
# active console codepage (e.g. cp949 on Korean Windows) can't represent.
# Switch the console to UTF-8 so titles display correctly; reconfigure is a
# fallback so an unencodable character degrades to an escape instead of
# crashing the whole command if the codepage switch isn't possible.
if sys.platform == "win32":
    try:
        import ctypes

        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")

from gameqa import target as target_mod
from gameqa import capture as capture_mod
from gameqa import replay as replay_mod
from gameqa.capture import Capture
from gameqa.config import RunConfig
from gameqa.logger import SessionLogger
from gameqa.monitor import Monitor
from gameqa.monkey_agent import MonkeyAgent
from gameqa.game_agent import GameAgent


def cmd_list(args):
    for w in target_mod.list_windows():
        print(f"pid={w['pid']:<7} proc={str(w['name']):<28} title={w['title'][:60]!r}")


def cmd_run(args):
    target_mod.set_dpi_awareness()
    tgt = target_mod.resolve(args.target)
    print(f"Target resolved: pid={tgt.pid} title={tgt.title!r}")

    config = RunConfig(
        target=args.target,
        duration_s=args.duration,
        max_actions=args.max_actions,
        action_interval_s=args.interval,
        record_video=args.record_video,
        video_fps=args.video_fps,
        ocr_enabled=args.ocr,
        presentmon_path=args.presentmon,
        llm_fallback_enabled=args.llm_fallback,
        gemini_api_key=args.gemini_api_key,
        gemini_api_key_fallback=args.gemini_api_key_fallback,
        gemini_model=args.gemini_model,
        mode=args.mode,
        agent_interval_s=args.agent_interval,
        max_llm_calls=args.max_llm_calls,
    )
    if args.memory_summarize_every_n_turns is not None:
        config.memory_summarize_every_n_turns = args.memory_summarize_every_n_turns
    config.disable_heuristic_escapes = args.disable_heuristic_escapes
    if args.key_pool:
        config.key_pool = [k.strip() for k in args.key_pool.split(",") if k.strip()]
    if args.llm_fallback and "llm_suggest" not in config.escalation_ladder:
        config.escalation_ladder = ["llm_suggest"] + config.escalation_ladder

    safe_title = "".join(c if c.isalnum() else "_" for c in tgt.title[:30]).strip("_") or "session"
    session_name = f"{safe_title}_{int(time.time())}"
    logger = SessionLogger(config.logs_dir, session_name)
    logger.set_video_fps(config.video_fps)
    logger.log_event(
        "session_start",
        {
            "config": vars(config),
            "target": {"pid": tgt.pid, "title": tgt.title, "hwnd": tgt.hwnd, "exe_path": tgt.exe},
        },
    )

    capture_mod.ensure_foreground(tgt.hwnd)
    cap = Capture(tgt.hwnd)

    stop_reason = {"reason": None}

    def on_crash():
        stop_reason["reason"] = "crash"

    def on_hang():
        if stop_reason["reason"] is None:
            stop_reason["reason"] = "hang"

    monitor = Monitor(tgt, logger, config, on_crash=on_crash, on_hang=on_hang)
    monitor.start()

    is_agent_mode = config.mode == "agent"
    if is_agent_mode:
        print(f"Agent mode: gemini_model={config.gemini_model} max_llm_calls={config.max_llm_calls}")
        agent = GameAgent(tgt, cap, logger, config, monitor=monitor)
    else:
        agent = MonkeyAgent(tgt, cap, logger, config, monitor=monitor)

    start = time.time()
    actions_done = 0
    try:
        while True:
            if monitor.crash_detected():
                stop_reason["reason"] = "crash"
                break
            if config.duration_s and time.time() - start >= config.duration_s:
                stop_reason["reason"] = "duration_reached"
                break
            if config.max_actions and actions_done >= config.max_actions:
                stop_reason["reason"] = "max_actions_reached"
                break
            try:
                if is_agent_mode:
                    if not agent.step():
                        stop_reason["reason"] = agent.stop_reason
                        break
                else:
                    agent.step()
                    time.sleep(config.action_interval_s)
            except Exception as e:
                logger.log_event("error", {"source": "agent", "message": str(e)})
            actions_done += 1
    except KeyboardInterrupt:
        stop_reason["reason"] = "manual_stop"
    finally:
        monitor.stop()
        logger.log_event("session_end", {"reason": stop_reason["reason"], "exit_code": None})
        summary = {
            "target": {"pid": tgt.pid, "title": tgt.title},
            "duration_s": round(time.time() - start, 2),
            "actions_total": actions_done,
            "stop_reason": stop_reason["reason"],
            "session_dir": str(logger.dir),
        }
        if is_agent_mode:
            summary["turn_count"] = agent.turn_count
            summary["llm_call_count"] = agent.llm_call_count
        else:
            summary["action_counts"] = agent.action_counts
            summary["confirmed_stuck_count"] = agent.confirmed_stuck_count
        logger.write_summary(summary)
        logger.close()
        print(f"Session ended: {stop_reason['reason']}. Logs at {logger.dir}")


def cmd_replay(args):
    replay_mod.replay(args.events_file, speed=args.speed)


def build_parser():
    p = argparse.ArgumentParser(description="Windows Game QA monkey-testing tool")
    sub = p.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="List visible windows/processes")
    p_list.set_defaults(func=cmd_list)

    p_run = sub.add_parser("run", help="Run a monkey-testing session against a target window")
    p_run.add_argument("--target", required=True, help="Window title substring or process name")
    p_run.add_argument("--duration", type=float, default=600.0, help="Max session duration in seconds")
    p_run.add_argument("--max-actions", type=int, default=None)
    p_run.add_argument("--interval", type=float, default=1.0, help="Seconds between agent actions")
    p_run.add_argument("--record-video", action="store_true", help="Write logs/<session>/video.mp4 (timelapse encoded at --video-fps, not real-time)")
    p_run.add_argument("--video-fps", type=int, default=5, help="Encoding fps for --record-video's timelapse output")
    p_run.add_argument("--ocr", action="store_true", help="Enable OCR re-ranking of UI candidates")
    p_run.add_argument("--key-pool", type=str, default=None, help="Comma-separated key list override")
    p_run.add_argument("--presentmon", type=str, default=None, help="Path to PresentMon.exe for FPS capture")
    p_run.add_argument("--llm-fallback", action="store_true", help="Ask Gemini for a suggested action when stuck-escalation is triggered (requires GEMINI_API_KEY or --gemini-api-key)")
    p_run.add_argument("--gemini-api-key", type=str, default=None, help="Gemini API key (defaults to GEMINI_API_KEY env var)")
    p_run.add_argument("--gemini-api-key-fallback", type=str, default=None, help="Second Gemini API key to switch to automatically when the primary key's daily quota is exhausted (defaults to GEMINI_API_KEY_2 env var); --mode agent only")
    p_run.add_argument("--gemini-model", type=str, default="gemini-flash-lite-latest", help="Gemini model for --llm-fallback / --mode agent")
    p_run.add_argument("--mode", choices=["monkey", "agent"], default="monkey", help="monkey = cheap random exploration for QA (default); agent = continuous Gemini-driven play for genuine autonomous progression (slower, costs far more API calls -- see README)")
    p_run.add_argument("--agent-interval", type=float, default=1.0, help="Minimum seconds between --mode agent Gemini calls (rate-limit backoff floor)")
    p_run.add_argument("--max-llm-calls", type=int, default=100, help="Hard budget on Gemini calls per session in --mode agent; session stops gracefully when hit")
    p_run.add_argument("--memory-summarize-every-n-turns", type=int, default=None, help="Turns of full chat history (screenshots + reasoning) kept in --mode agent before it's compressed into a short text summary; default 40 (RunConfig)")
    p_run.add_argument("--disable-heuristic-escapes", action="store_true", help="Diagnostic: never let code-level escape tiers act directly when stuck -- every turn goes through a real LLM call instead, to compare against the normal escape-assisted behavior")
    p_run.set_defaults(func=cmd_run)

    p_replay = sub.add_parser("replay", help="Replay a session's events.jsonl input sequence")
    p_replay.add_argument("events_file")
    p_replay.add_argument("--speed", type=float, default=1.0)
    p_replay.set_defaults(func=cmd_replay)

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
