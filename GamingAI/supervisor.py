"""Auto-relaunching supervisor for gameqa agent-mode sessions.

main.py run --mode agent already handles the WITHIN-session part of quota
exhaustion (switching across GEMINI_API_KEY, _2, _3, ... on a daily-quota
429 -- see game_agent.py's _switch_to_next_key). What it can't do on its
own is the ACROSS-session part: once every available key is exhausted for
the day, the process exits with stop_reason "sustained_llm_failure" and
just sits there until a human notices and relaunches it once quota resets.
That noticing-and-relaunching was being done by hand, in a chat session,
all day -- this script is that same decision automated: watch each run's
outcome, and if it died from exhausted quota, wait for a plausible reset
and try again, indefinitely, with no human required to be present.

Usage mirrors `main.py run`: any flag main.py's `run` subcommand accepts
(--gemini-api-key-fallback, --disable-heuristic-escapes, etc.) can be
passed here too and is forwarded through unchanged.

    python supervisor.py --target "Maze Trials" --mode agent --duration 600 --max-llm-calls 150
"""
import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# Re-check cadence once a predicted quota-reset wait has already been tried
# and quota is STILL exhausted right after waking -- rather than trusting a
# single predicted reset time and re-deriving another full ~24h wait if
# that guess was even slightly off (reset delay, clock skew, a key on a
# different reset schedule than the others).
RETRY_INTERVAL_S = 1800
# Small buffer past the predicted reset instant -- resets don't necessarily
# clear the instant the clock ticks over.
RESET_BUFFER_S = 300


def next_pt_midnight_wait_s():
    # Gemini's free-tier daily quota (GenerateRequestsPerDayPerProjectPerModel)
    # resets at Pacific-time midnight, confirmed live earlier the same day
    # this script was written (a session that failed with all keys
    # exhausted worked again once real time passed roughly that boundary).
    # Uses the IANA tz database via zoneinfo so this stays correct across
    # PST/PDT rather than hardcoding a fixed UTC offset that silently goes
    # wrong twice a year. Returns None (caller should fall back to
    # RETRY_INTERVAL_S) if the system has no tz database available --
    # common on a bare Windows Python install without the `tzdata` package.
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("America/Los_Angeles")
    except Exception:
        return None
    now = datetime.now(tz)
    tomorrow = (now + timedelta(days=1)).date()
    reset = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 0, 0, tzinfo=tz) + timedelta(seconds=RESET_BUFFER_S)
    return max((reset - now).total_seconds(), 0)


def latest_session_dir(logs_dir):
    dirs = [p for p in Path(logs_dir).iterdir() if p.is_dir()]
    return max(dirs, key=lambda p: p.stat().st_mtime) if dirs else None


def read_stop_reason(session_dir):
    summary_path = session_dir / "summary.json"
    if not session_dir or not summary_path.exists():
        return None
    try:
        return json.loads(summary_path.read_text(encoding="utf-8")).get("stop_reason")
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser(
        description="Repeatedly runs `main.py run`, automatically waiting out and retrying Gemini daily-quota exhaustion instead of needing a human to relaunch it",
    )
    parser.add_argument("--target", required=True)
    parser.add_argument("--logs-dir", default="logs")
    parser.add_argument("--max-sessions", type=int, default=None, help="Stop after this many sessions; omit to run indefinitely")
    parser.add_argument("--retry-interval-s", type=float, default=RETRY_INTERVAL_S)
    args, extra = parser.parse_known_args()

    session_count = 0
    consecutive_quota_failures = 0
    while args.max_sessions is None or session_count < args.max_sessions:
        cmd = [sys.executable, "main.py", "run", "--target", args.target] + extra
        print(f"[supervisor] launching session {session_count + 1}: {' '.join(cmd)}", flush=True)
        subprocess.run(cmd)
        session_count += 1

        session_dir = latest_session_dir(args.logs_dir)
        stop_reason = read_stop_reason(session_dir)
        print(f"[supervisor] session ended (dir={session_dir}, stop_reason={stop_reason})", flush=True)

        if stop_reason == "sustained_llm_failure":
            consecutive_quota_failures += 1
            wait_s = None
            if consecutive_quota_failures == 1:
                wait_s = next_pt_midnight_wait_s()
            if wait_s is None:
                wait_s = args.retry_interval_s
            print(f"[supervisor] all keys exhausted -- waiting {wait_s / 60:.0f} min before retry (attempt {consecutive_quota_failures})", flush=True)
            time.sleep(wait_s)
        elif stop_reason == "crash":
            print("[supervisor] target crashed -- stopping rather than looping into a crash storm", flush=True)
            break
        else:
            consecutive_quota_failures = 0
            time.sleep(5)  # brief pause so a fast crash-restart loop can't hammer the target


if __name__ == "__main__":
    main()
