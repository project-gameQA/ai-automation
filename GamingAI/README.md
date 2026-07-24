# GameQA — Windows Game QA Automation Tool

Points at any running Windows game window and plays it unattended, logging
inputs, screenshots, crashes/hangs, and performance metrics for QA analysis.
Two modes, chosen with `--mode`:

- **`monkey`** (default) — cheap rule-based + random exploration ("monkey
  testing"), with an occasional Gemini nudge only when stuck. Good for broad
  crash/bug QA over a long unattended run; doesn't try to complete the game.
- **`agent`** — Gemini decides close to every action, with its own memory of
  what it's learned this session. Genuinely plays toward objectives (any
  genre, not just menus) instead of just avoiding softlocks, at real
  per-action API cost/latency. See **Agent mode** below before reaching for it.

## Setup

```
pip install -r requirements.txt
```

- **OCR (optional)**: `--ocr` requires the [Tesseract](https://github.com/UB-Mannheim/tesseract/wiki) binary installed and on `PATH` in addition to `pytesseract`.
- **FPS (optional)**: `--presentmon <path>` requires [PresentMon](https://github.com/GameTechDev/PresentMon) (Intel, MIT license) downloaded separately. Without it, `perf.csv` reports `fps_source: unavailable` rather than an estimate (see limitations).
- **Multi-monitor**: mouse coordinates are computed against the full virtual desktop (`MOUSEEVENTF_VIRTUALDESK`), so targets on a secondary monitor — including one positioned left/above the primary with a negative origin — are supported and were specifically verified during development.
- **LLM fallback (optional)**: `--llm-fallback` requires a [Gemini API key](https://aistudio.google.com/apikey) (free tier available, no card required) set via `GEMINI_API_KEY` or `--gemini-api-key`, and the `google-genai` package. See the LLM fallback section below before enabling it.

## Usage

```
# See available windows/processes
python main.py list

# Run a 10-minute monkey-testing session against a window whose title or
# process name contains "MyGame"
python main.py run --target MyGame --duration 600

# Enable OCR-boosted menu navigation and FPS capture
python main.py run --target MyGame --duration 600 --ocr --presentmon "C:\Tools\PresentMon.exe"

# Also record a timelapse video of the session (see video.mp4 note below)
python main.py run --target MyGame --duration 600 --record-video --video-fps 5

# Ask Gemini for a suggested action whenever the bot gets stuck, instead of
# just the fixed esc/tab+enter/corner-click ladder (see LLM fallback below)
set GEMINI_API_KEY=your-key-here
python main.py run --target MyGame --duration 600 --llm-fallback

# Agent mode: Gemini decides close to every action, to actually make progress
# rather than just avoid softlocks (see Agent mode below) -- costs far more
# API calls, budgeted with --max-llm-calls
set GEMINI_API_KEY=your-key-here
python main.py run --target MyGame --duration 600 --mode agent --max-llm-calls 100

# Replay a session's recorded inputs against the same target -- only valid if
# the target window hasn't moved since the recording (see limitations)
python main.py replay logs/MyGame_1234567890/events.jsonl
```

If `--target` matches more than one window, the tool picks the first match and
prints a warning listing all matches to stderr — pass a more specific
substring (e.g. the exact process name) to disambiguate.

Each run writes to `logs/<window-title>_<timestamp>/`:

- `events.jsonl` — structured event log (inputs, screenshots, stuck/hang/crash/error events), one JSON object per line.
- `screenshots/` — periodic + on-event captures.
- `video.mp4` — only with `--record-video`: a **timelapse** of captured frames encoded at `--video-fps`, not a real-time recording (frames are only captured at `--interval` cadence). Use `events.jsonl` timestamps to reconstruct real elapsed time between frames if needed.
- `perf.csv` — CPU%, memory, GPU%, FPS, window-responsiveness samples over time.
- `summary.json` — end-of-run report (action counts, crash/hang/stuck counts, stop reason, artifact paths).

## LLM fallback for monkey mode (`--llm-fallback`)

By default, when the bot gets stuck (screen unchanged for `stuck_action_window`
ticks) it works through a fixed, non-adaptive escalation ladder: press esc,
then tab+enter, then click the top-right corner, then give up and just flag
it. With `--llm-fallback` enabled, the *first* escalation step instead sends
a screenshot to Gemini (`gemini-flash-lite-latest` by default — see
`--gemini-model` to override) and asks it to suggest one concrete action (a
key to press or a point to click), then executes that suggestion. If the
Gemini call fails for any reason — missing/invalid key, no network, rate
limit, malformed response, a retired/renamed model ID — it's logged as an
`error` event and the tool presses `esc` and moves on; a broken LLM fallback
degrades to the old behavior rather than blocking or crashing the session.

**Model IDs move fast, and so do their free-tier quotas — both measured, not
assumed.** `gemini-2.5-flash-lite`, the model this feature was originally
written against, was retired for new API keys shortly after — a live call
returned `404 ... no longer available to new users`. Its replacement,
`gemini-3.5-flash` (the first available non-retired model at the time), free-tier
capped at **20 requests/day** on a live key — trivial for `--llm-fallback`
(only called when stuck) but exhausted in minutes under `--mode agent`'s
per-action calling, surfaced as `429 RESOURCE_EXHAUSTED` with
`GenerateRequestsPerDayPerProjectPerModel-FreeTier` in the error body. The
default is now `gemini-flash-lite-latest` — an alias Google keeps pointed at
its current lite-tier flash model (so it doesn't go stale like a dated
snapshot) and which had much more free-tier headroom in the same test. If
`--gemini-model` ever starts 429ing or 404ing, run `client.models.list()`
(see the Gemini API docs) to see what your key currently has access to.

This does **not** make the bot understand the game or plan ahead — it's a
single-frame "what looks clickable/pressable right now" judgment call, used
only in the rare stuck case, not on every tick, and it has no memory across
calls. For genuine goal-directed play, use `--mode agent` (below) instead.

**Click accuracy — measured, not assumed.** Asking Gemini for a raw pixel
`(x, y)` point on a 1920x1080 screenshot was tested against a real button
with a known location: it was consistently off by ~64px (missed the button,
landing just above it — the same direction and magnitude across repeated
calls, i.e. a systematic bias, not random noise). Asking for a bounding box
around the raw-pixel estimate instead didn't help, since the box was
shifted by the same bias. Switching to a **0-1000 normalized coordinate
system** (Google's documented recommendation for localization on Gemini,
scaled to real pixels in `llm_vision.py`) brought repeated measurements to
within 2-3px of the true button center. If you fork this for a different
vision provider, don't skip straight to raw pixel coordinates without
checking whether that provider has the same failure mode.

**Setup**: get a free API key at [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
(no card required), then either `set GEMINI_API_KEY=...` (Windows) or pass
`--gemini-api-key`. Install `google-genai` (already in `requirements.txt`).

**Cost/privacy**: Gemini's free tier has per-minute and per-day request caps
that vary by model and change over time — check current limits for whatever
`--gemini-model` you're using at [ai.google.dev/gemini-api/docs/rate-limits](https://ai.google.dev/gemini-api/docs/rate-limits)
before relying on it. Since this is only called on the stuck path (not every
tick), a typical session uses very few requests. On the free tier, **prompts
and images you send may be used by Google to improve their products** —
don't point `--llm-fallback` at an unreleased or confidential build unless
that's acceptable, or use a paid Gemini tier which doesn't have that
data-usage clause.

## Agent mode (`--mode agent`)

Where `--llm-fallback` is an occasional nudge, `--mode agent` replaces the
monkey-testing loop entirely: every tick, `gameqa/game_agent.py` sends the
current screenshot to Gemini in a running multi-turn chat and asks for
exactly one next action, executes it, and tells Gemini next turn whether
that action actually changed the screen — so the model can tell a working
guess from a dead end without being asked to self-grade. The prompt has no
"puzzle" or genre-specific wording; it's framed as "make genuine progress,
whatever that means for this game."

**Change detection is pixel-diff-based, not a perceptual hash.** An early
version compared frames via `imagehash.average_hash` (an 8x8 downsample) —
confirmed live to miss small UI elements entirely: a thin pagination
chevron next to a hint popup didn't shift the hash enough to register as
"changed," so the agent was told its own click "did NOT change the screen"
when something real had in fact appeared. `_diff_regions` now does a real
pixel `absdiff`, and when the changed area is small/localized it draws red
boxes around exactly those regions on the frame sent to Gemini (with
normalized coordinates in the text note) — generalizes across games instead
of us hand-coding which UI conventions to look for. When a huge fraction of
the frame changed at once (a modal/panel opening dims the whole background,
which — confirmed live — can out-diff the small new element sitting on top
of busy background art), localizing to specific boxes would just draw one
uninformative box covering nearly the whole screen, so that case falls back
to a plain "something changed significantly, inspect the whole image"
note instead.

**Three action types: click, key, drag.** Click and key were enough for
menu navigation, but real gameplay needs more — a live session against a
puzzle game showed the model repeatedly trying to express drag-style
interactions (moving a piece from one spot to another) with no way to say
so, since only click/key existed at first. `drag` takes a start box and an
end box (both normalized 0-1000, same scheme as click) and executes a real
press-move-release gesture via `input_controller.drag()`. Key actions also
support `hold_ms` for genres that need a held direction instead of a tap.

**Degenerate boxes are repaired, not rejected.** In practice the model
often returns a single point (`x1==x2, y1==y2`) instead of a real box
despite the prompt asking for one — measured as the majority of "bad
response" errors in a real session. Treating that as an error would waste
the turn (and a unit of the call budget) for what's clearly still a usable
coordinate; `llm_vision._pad_if_degenerate` instead centers a small box on
the point and continues normally.

**Memory, bounded cost.** A raw chat session grows every screenshot it's
ever seen, which gets slow and expensive over a real play session. Every
`memory_summarize_every_n_turns` turns (default 8), the agent asks Gemini
for a short plain-text summary of what it's learned (controls confirmed,
current objective, confirmed dead ends), stores it, and starts a *fresh*
chat seeded with just that summary + the current screenshot. This keeps
token growth (and therefore cost) roughly constant per turn indefinitely,
instead of growing with session length.

**This is genuinely more expensive and slower than monkey mode.** Each
Gemini call measured ~5-15 seconds round-trip in testing — real-time or
twitch-reflex action games are out of reach at that cadence; turn-based,
puzzle, adventure, and other slower-paced genres are the realistic target.
Cost scales with `--max-llm-calls` (default 100 per session — the session
stops gracefully, not silently, when the budget is hit) and
`--agent-interval` enforces a minimum gap between calls so the agent backs
off instead of hammering into free-tier rate limits (429s). If Gemini fails
5 times in a row, the session stops with `stop_reason: sustained_llm_failure`
rather than quietly reverting to random clicks and calling that "progress."

**Verified working, not theoretical.** Tested against a real Steam puzzle
game ("Lynn, The Girl Drawn On Puzzles"): starting from the main menu, agent
mode correctly identified and clicked a button (`린 이야기` / "Lynn's
Story") that the geometric vision heuristic in `vision.py` structurally
cannot detect (see that file's docstring — its brush-stroke art style has a
bounding-box fill ratio too low for any reasonable threshold), and advanced
the game to the next screen.

**Panels get probed by code, not by asking the model nicely.** A hint/help
popup in the test game has a secondary pagination chevron sitting on its
border, separate from its close/X button (i.e. the popup has more than one
page of content). The system prompt explicitly instructed checking a
newly-opened panel's edges for this before closing it — confirmed via
direct inspection that the instruction reached the model every turn, worded
two different ways — and `gemini-flash-lite-latest` still closed the panel
immediately every single time. That's a genuine capability/attention limit
of this model tier, not a prompt-wording bug, so `_probe_chevron` in
`game_agent.py` now runs `vision.detect_chevrons` (see below) *before* ever
asking the model: when a large-scale change looks like a panel opening, if
a chevron is found near the panel's edge, it gets clicked directly and the
LLM call for that turn is skipped entirely (cheaper, and sidesteps the
unreliable instruction-following instead of fighting it with more prompt
text). Verified live: the agent clicked the chevron on its own mid-session,
advanced the hint popup to a second page with new content, and reacted to
that new content on the next turn.

**`vision.detect_chevrons`** finds thin outline arrows ("<"/">") via the
same Canny-edge-contour approach as `detect_candidates`, but with a much
looser fill_ratio floor (~0.15-0.5 vs 0.6) — a real chevron measured off a
live screenshot came in at fill_ratio ~0.33, since an outlined arrow is
mostly hollow. That looser floor would reproduce `detect_candidates`'
documented false-positive problem if applied to the whole frame, so the
search is restricted to the outer ~16% margin of the frame width and to
within ~20% of vertical center (measured live: a busy decorative-art edge
produced 9 raw candidates in that margin, only 2 of which were real
chevrons; the vertical-center constraint cut it to 4). Still probabilistic,
not certain — callers should pick at most one candidate per side rather
than acting on every match.

**Change detection knows the difference between "something changed" and
"something meaningful changed," to a point.** Two frames a few seconds
apart very often diff as changed even with no real interaction — a sprite's
idle bob/pulse animation is enough (confirmed live: a character AND a
target icon the agent never touched both showed pixel diffs in a region
matching their own outlines, turn after turn, regardless of what was
clicked). The agent was observed building false confidence from this
("the symbol keeps cycling states") and treating a dead-end hypothesis as
confirmed. There's no way to reliably tell idle animation from a real
state change from a boolean diff alone, so instead of trying to filter it
out, the per-turn note says so explicitly and the stuck-cycle detector (next
section) ignores the "changed" flag for its primary check, relying on
target repetition instead.

**Stuck-cycle detection has two tiers.** Every turn's action gets reduced
to a bucketed signature (type + normalized-position, tolerant of a few
pixels of retry jitter) and logged alongside whether the screen changed.
*Primary check*: if the last `STUCK_REPEAT_COUNT` (default 2) actions are
the exact same signature, that's flagged regardless of the noisy "changed"
flag — this is what catches the idle-animation false-confidence case above.
*Exception*: this check is skipped for `key` actions, because repeating the
identical key several times in a row is the normal, correct pattern for
real-time movement genres (holding "right" to walk down a corridor) —
confirmed live against a top-down maze game, where the primary check firing
on two identical "right" presses injected an irrelevant forced click into
otherwise-correct navigation. *Secondary check*: a short cycle (2-3 distinct
targets alternating) with confirmed zero screen change across a wider
6-turn window — the unambiguous case, still applies to all action types
including `key`.

**A detected stuck cycle triggers a forced escape, not just a warning.**
Consistent with the chevron case above, prompting alone ("try something you
haven't tried") was tested and found unreliable, so a real stuck cycle
skips the LLM call for that turn and picks the next action in code, in this
priority order: (1) drag the model's own declared `controlled_piece` (see
"elements" below) to a `target_or_goal`/`empty_slot` combination that has
never been tried this session; (2) click a declared `usable_object` that
hasn't been clicked yet; (3) fall back to `vision.detect_candidates` and
click whichever on-screen candidate is farthest (bucket-distance) from
everything tried recently. Each tier is strictly more game-specific-blind
than the last — (1) and (2) are grounded in the model's own semantic
understanding of the current screen, (3) is generic. `tried_combos` is
session-long (not the short rolling window `turn_log` uses for cycle
detection), so "already tried" means for the whole session.

**The model must inventory the screen before acting, not just pick a
target.** The response schema (`AGENT_RESPONSE_SCHEMA` in `game_agent.py`,
extending the shared `llm_vision.RESPONSE_SCHEMA`) requires an `elements`
array every turn: every meaningful on-screen thing gets a label, a role
(`controlled_piece`, `target_or_goal`, `usable_object`, `obstacle`,
`empty_slot`, `ui_control`, `decorative`, or `unknown`), a position, and an
`open_sides` string for anything sitting in a box/bracket/frame shape
(which sides are visibly open vs walled — mechanically relevant for
slot/grid puzzles, since a closed side very often blocks a piece from
passing through it). This exists because prose guidance alone
("distinguish a goal marker from a movable piece") wasn't enough — measured
live, the model correctly labeled roles turn after turn once given an
explicit field to put them in, including correctly flagging a fixed symbol
as `target_or_goal` rather than something to relocate. It's not perfect:
the same live session had the model call a frame's `open_sides` "none"
(fully enclosed) when a zoomed crop of the same frame showed a real, if
subtle, gap — a small-detail miss in the same category as the chevron case,
not a schema failure. Every turn's `elements` array is logged as its own
`scene_elements` event, independent of whether the agent solves anything —
useful for QA review on its own (a per-screen inventory of what's present).
`usable_object` specifically exists because the model was only ever
considering "move the piece directly to the goal" with no representation
for "activate this switch/item as a required step first"; adding the role
plus explicit prompt guidance gave it a place to express that hypothesis.

## Limitations

- **Anti-cheat**: synthetic input (`SendInput`) and screen capture are commonly detected/blocked by kernel-level anti-cheat (EAC, BattlEye, Vanguard, etc.). This tool targets internal/authorized QA builds without such protection — it is not designed for, and makes no attempt to evade, anti-cheat on third-party live-service titles.
- **Exclusive fullscreen**: capture can't see true DirectX exclusive-fullscreen surfaces (same limitation as OBS/most capture software). Run the tested build in Borderless or Windowed mode.
- **Foreground requirement**: the target window must stay foreground/unminimized during a run; the tool re-foregrounds it automatically on loss of focus, but this isn't suited to background/multi-window unattended rigs.
- **FPS accuracy**: FPS is only reported when `--presentmon` is supplied (tagged `fps_source: presentmon` in `perf.csv`); without it, `perf.csv` reports `fps_source: unavailable` rather than an estimate, so no unreliable number is ever presented as if it were true frame timing.
- **GPU% availability**: per-process GPU Engine performance counters can be missing on some hybrid-GPU/laptop driver configurations; the tool reports `unavailable` rather than a fabricated number.
- **Vision heuristics**: the OpenCV candidate detector is tuned for 2D menu/dialog screens; it degrades to effectively-random clicking during 3D gameplay by design — that's still valid monkey-testing coverage, not a defect.
- **Replay uses absolute screen coordinates**: `replay.py` re-sends the exact `(x, y)` screen coordinates recorded during the session. If the target window has moved, resized, or is on a different monitor arrangement since the recording, replayed clicks will land in the wrong place. Replay the same machine/window layout as the original run for reliable repro.
- **LLM fallback is single-frame and stateless**: each Gemini call sees one screenshot with no memory of prior suggestions or game context, so it can loop on the same bad suggestion across separate stuck episodes; it's a best-effort nudge, not a planning agent.
- **Agent mode latency/cost**: ~5-15s per Gemini call means it's unsuitable for real-time/reflex-heavy genres, and a long session can burn through free-tier daily limits or `--max-llm-calls` faster than expected; the periodic memory-summarization step also costs a call every `memory_summarize_every_n_turns` turns, on top of one per action. Free-tier daily request quotas (`GenerateRequestsPerDayPerProjectPerModel`) reset at midnight Pacific time (confirmed against the official docs at ai.google.dev/gemini-api/docs/rate-limits) — 16:00 KST during PDT (UTC-7, roughly March-November), 17:00 KST during PST (UTC-8). A 429 with that quota name in the response body means the daily cap is spent, not a transient rate limit; retrying sooner won't help, and `game_agent.py` stops the session gracefully (`sustained_llm_failure`) after 5 consecutive failures rather than hammering a dead quota.
- **Agent mode memory is a lossy summary, not full history**: once the chat resets, only the short text summary carries forward — fine detail from turns before the last reset (exact prior coordinates, minor observations) is gone, so it can occasionally re-explore something it technically already ruled out.
- **Scope**: intended only for games you own or are authorized to QA-test.
