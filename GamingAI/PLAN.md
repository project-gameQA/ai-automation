# Windows Game QA Automation Tool (Python)

## Context

The user wants a Python program for Windows that automatically recognizes whatever game window is running, plays it autonomously to exercise the game (QA purpose), and logs the session for bug/regression analysis. Confirmed scope via clarifying questions:

- **Generic framework** — works on arbitrary Windows games, not one specific title.
- **Play strategy**: rule-based + random exploration ("monkey testing"), not RL, not an LLM-vision agent per-action (too slow/costly for this use case).
- **Logging**: input events + timestamps, screenshots/video, crash/error/hang detection, performance metrics (FPS, CPU/GPU, memory).
- Working directory `E:\GamingAI` is currently empty — greenfield project, no existing code/git repo.

Intended outcome: a reusable CLI tool a QA tester can point at any running game window and let it explore unattended, producing a session log that helps catch crashes, hangs, and softlocks, with enough detail (input trace) to reproduce issues found.

## Architecture

Package `E:\GamingAI\gameqa\`, CLI entrypoint `E:\GamingAI\main.py`.

- **`target.py`** — enumerate/select target window+process via `win32gui.EnumWindows` + `psutil` (match by process name or title substring, or interactive picker). At `main.py` startup, before any enumeration/capture, set **Per-Monitor-V2 DPI awareness** (`ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)`, falling back to `shcore.SetProcessDpiAwareness(2)` then `SetProcessDPIAware()`) — without this, window rects are DPI-virtualized and silently misaligned with captured pixels / click coordinates.

- **`capture.py`** — screen capture via `mss` (fast; works for windowed/borderless-fullscreen, the common case). Since `mss` captures a screen *region* (BitBlt-style), the target window must stay foreground/unminimized: re-foreground via `SetForegroundWindow`/`ShowWindow(SW_RESTORE)` when `monitor.py` detects loss of foreground or minimize. Use `GetClientRect` + `ClientToScreen` (not `GetWindowRect`) so captured pixels and click coordinates share the same client-area coordinate space. `PrintWindow(..., PW_RENDERFULLCONTENT)` as an optional fallback backend is a stretch goal, not v1.

- **`input_controller.py`** — custom `ctypes` wrapper directly around `SendInput()` (INPUT/KEYBDINPUT/MOUSEINPUT structs), keyboard via `KEYEVENTF_SCANCODE` (scan codes are respected by more games than virtual-key codes), mouse supporting both relative (`MOUSEEVENTF_MOVE`) and absolute (`MOUSEEVENTF_ABSOLUTE`, normalized 0–65535 across the *virtual screen* for multi-monitor correctness). Skip the `pydirectinput` package — it's a thin, unmaintained wrapper around the same `SendInput` call with `pyautogui`-style global delays we don't want; a ~100–150 line custom wrapper gives full control over coordinate mapping and future gestures (drag, scroll).

- **`vision.py`** — generic "clickable candidate" detector: OpenCV Canny edge + `findContours`, filtered by aspect ratio (~1.5–8), relative area (~0.2%–8% of window area), rectangularity (contour/bbox area ratio > ~0.7). This is heuristic — good on 2D menu/dialog screens, noisy during 3D gameplay (acceptable, since `monkey_agent.py` always has non-vision fallback actions). Optional OCR re-ranking via `pytesseract`: crop each candidate, OCR it, boost weight if text matches a keyword list (`OK, Start, Continue, Play, Settings, Exit, Resume, Menu, Yes, No, Retry`).

- **`monkey_agent.py`** — decision loop, see pseudocode below. Weighted random choice over: click a vision candidate, press a common game key, random click in window bounds, drag, scroll. Stuck-state detection via perceptual hash (`imagehash.average_hash`) of consecutive frames; escalation ladder on repeated no-op state.

- **`monitor.py`** — independent polling thread:
  - Process alive/exit code via `psutil`.
  - Hang detection via `win32gui.IsHungAppWindow(hwnd)`.
  - **Crash detection, authoritative source**: watch the Windows **Application** Event Log via `win32evtlog` for Event ID 1000 (Application Error) / 1001 (WER report) correlated by PID/process name + timestamp — generic across any engine/game, unlike matching crash-dialog window titles (which is fragile/localization-dependent). Keep dialog-title scanning as a secondary, faster (but non-authoritative) signal.
  - **GPU%**: `win32pdh` reading `\GPU Engine(*)\Utilization Percentage`, enumerate instances via `PdhEnumObjectItems`, filter by `pid_<PID>_`, **sum** across matching engine instances (3D/Copy/VideoDecode/VideoEncode), clamp to 100. Fall back to "unavailable" on hybrid-GPU/driver setups that don't expose these counters — never report a fabricated number.
  - **FPS**: optional integration with **PresentMon** (`GameTechDev/PresentMon`, MIT, vendor-agnostic across DX/GL/Vulkan) — shell out with `--process_name <name> --output_file <path> --multi_csv`, tail the CSV, compute avg/min/max/p99 for `summary.json`. If PresentMon isn't installed, fall back to a capture-diff-rate estimate, but tag it distinctly (`fps_source`) and document it as a rough visual-change proxy, not true frame timing.
  - CPU/mem via `psutil.Process` (`cpu_percent`, `memory_info`).

- **`logger.py`** — per-run session folder `logs/<name>_<timestamp>/`: `events.jsonl`, `screenshots/`, optional `video.mp4` (OpenCV `VideoWriter` from captured frames), `perf.csv`, `summary.json`.

- **`replay.py`** — replay a session's `events.jsonl` input sequence against the same target window/coordinate space, for deterministic-ish bug repro.

- **`config.py`** — dataclasses for run config: `action_interval_s`, `stuck_hash_distance_threshold`, `stuck_action_window`, `escalation_ladder`, `key_pool`, `action_weights`, `ocr_enabled`, `confirmed_stuck_terminates_run` (default `False` — keep exploring but flag loudly), video/perf toggles and intervals.

- **`main.py`** (repo root) — argparse CLI: `--target`, `--duration`/`--max-actions`, `--record-video`, `--key-pool`, `--ocr`, etc. Sets DPI awareness, resolves target, starts `monitor.py` thread, runs the monkey loop on the main thread, on crash/hang/duration-reached stops gracefully and writes `summary.json`.

### Dependencies (`requirements.txt`)
`mss`, `opencv-python`, `numpy`, `pywin32`, `psutil`, `imagehash`, `Pillow`, `pytesseract` (optional feature — needs system Tesseract binary, documented separately in README, not required for core operation). PresentMon is an optional external `.exe` dependency, documented in README, not bundled.

## Data Schemas

**`events.jsonl`** — one JSON object per line, common envelope `{"ts": <float>, "seq": <int>, "type": <str>, "data": {...}}`. Event `type`s:
- `session_start` — `{config: {...}, target: {pid, title, hwnd, exe_path}}`
- `session_end` — `{reason: "duration_reached"|"max_actions_reached"|"crash"|"hang"|"manual_stop", exit_code}`
- `input` — `{action: "key_press"|"key_release"|"mouse_click"|"mouse_move"|"mouse_drag"|"scroll", key, button, x, y, coord_space: "client"|"screen"}`
- `screenshot` — `{path, trigger: "periodic"|"pre_action"|"on_event", frame_hash}`
- `vision_candidates` — `{count, candidates: [{x,y,w,h,score,ocr_text|null}]}` (sampled, not every frame)
- `stuck` — `{consecutive_unchanged_frames, hash_distance, escalation_level}`
- `hang` — `{detection_method: "is_hung_app_window", duration_s}`
- `crash` — `{pid, exit_code, detection_method: "process_exit"|"wer_event_log"|"crash_dialog_title", event_log_id?, details}`
- `error` — `{source: "capture"|"input"|"vision"|"monitor", message, traceback}`

**`perf.csv`** columns: `timestamp, elapsed_s, cpu_percent, mem_rss_mb, mem_percent, gpu_percent, gpu_engine_breakdown_json, fps, fps_source, window_responsive`. `fps_source` ∈ `presentmon|capture_diff_estimate|unavailable`.

**`summary.json`** — target/run metadata, echoed config, action counts by type, crash/hang/stuck counts with `events.jsonl` line references, FPS stats (avg/min/max/p99 + source), final exit code, artifact paths.

## Monkey Agent Decision Loop (implement directly from this pseudocode)

```
ACTION_WEIGHTS = {click_vision_candidate: .45, press_common_key: .30,
                   random_click_in_bounds: .15, mouse_drag: .05, scroll_wheel: .05}
COMMON_KEY_POOL = [w,a,s,d,up,down,left,right,space,enter,esc,tab,1,2,3]
STUCK_HASH_DISTANCE_THRESHOLD = 2   # aHash Hamming distance considered "same"
STUCK_ACTION_WINDOW = 8             # consecutive unchanged actions before flag
ESCALATION_LADDER = [esc, tab+enter, click_corner_x_button, log_confirmed_stuck]

loop each tick (interval = config.action_interval_s):
    frame = capture.grab(target)
    log_screenshot_if_due(frame); perf.sample_and_log_if_due()

    frame_hash = imagehash.average_hash(frame)
    unchanged_count = unchanged_count+1 if hash_close(frame_hash, last_hash) else 0
    if unchanged_count == 0: escalation_level = 0
    last_hash = frame_hash

    if unchanged_count >= STUCK_ACTION_WINDOW:
        log_event(stuck, {...})
        execute_escalation(ESCALATION_LADDER[min(escalation_level, len-1)])
        escalation_level += 1; unchanged_count = 0
        continue   # skip normal action selection this tick

    candidates = vision.detect_candidates(frame)  # may be empty
    weights = redistribute(ACTION_WEIGHTS, if not candidates: fold click_vision_candidate
              into random_click_in_bounds/press_common_key)
    action_type = weighted_random_choice(weights)
    dispatch action_type -> input_controller call (click/key/drag/scroll)
    log_event(input, action_details)

    if monitor.crash_detected() or monitor.hang_detected(): break
```

## Build Order (implement + manually verify in this sequence)

1. **M0 Scaffolding** — `config.py`, `logger.py` (jsonl writer only). Verify: write dummy events, confirm valid JSONL.
2. **M1 Target+Capture** — `target.py` + `capture.py` + DPI awareness in `main.py`. Verify against a real running window: enumerate, select by title substring, grab region, save PNG, confirm pixel alignment (no DPI offset).
3. **M2 Input** — `input_controller.py` `SendInput` wrapper. Verify a click lands exactly at a captured-pixel coordinate — this capture/input coordinate-space alignment is the riskiest thing to get wrong, verify in isolation first.
4. **M3 Trivial loop** — capture+input+target combined into "click random point every N seconds" + `events.jsonl` + periodic screenshots, run 5 min against a real test game, review logs.
5. **M4 Monitor** — process alive/exit, `IsHungAppWindow`, WER event-log crash detection. Verify by deliberately crashing/hanging a small test app.
6. **M5 Vision** — contour/edge candidates, visualize as boxes drawn on saved screenshots for manual tuning against 2–3 real menu/settings screens, before wiring into the agent.
7. **M6 Full monkey_agent** — weighted loop + stuck detection + escalation, run unattended 15–30 min against a real game, tune weights/thresholds from logs.
8. **M7 Perf metrics** — psutil CPU/mem, win32pdh GPU%, optional PresentMon integration. Verify `perf.csv` against Task Manager for the same process.
9. **M8 Replay** — replay `events.jsonl` against the same target, verify best-effort repro.
10. **M9 (stretch)** — OCR-boosted ranking, `PrintWindow` occluded-capture fallback, `video.mp4` assembly.

## Limitations to document in README

- **Anti-cheat**: `SendInput`/screen-capture are commonly detected/blocked by kernel-level anti-cheat (EAC, BattlEye, Vanguard). Tool targets internal/authorized QA builds without such protection — not for third-party live-service titles, no evasion attempted.
- **Exclusive fullscreen**: `mss`/GDI capture can't see true DX exclusive-fullscreen surfaces (same limitation as OBS). Recommend Borderless/Windowed mode for the tested build.
- **Occlusion/minimize**: target window must stay foreground/unminimized; tool re-foregrounds it automatically but isn't suited to background multi-window rigs without the M9 `PrintWindow` fallback.
- **FPS accuracy**: without PresentMon, FPS is a rough capture-diff-rate proxy, not true frame timing, and can't show stutter/frame-time variance — always paired with `fps_source` in output.
- **GPU% availability**: per-process GPU Engine counters can be missing on some hybrid-GPU/laptop driver setups — reports "unavailable" rather than a wrong number.
- **Vision heuristics**: tuned for 2D menu/dialog screens; degrades to effectively-random clicking during 3D gameplay by design (that's still valid monkey testing).
- **Scope**: intended only for games the user owns/is authorized to QA-test.

## Verification

No automated test suite for this kind of tool — verification is manual, per the build-order milestones above, run against a real Windows game (or a simple placeholder app for M0–M4 if no test game is available yet). Each milestone has an explicit manual check before moving to the next.
