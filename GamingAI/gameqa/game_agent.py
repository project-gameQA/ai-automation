"""Continuous LLM-driven autonomous play loop ("agent mode").

Unlike monkey_agent.py (cheap random exploration + an occasional Gemini
nudge only when stuck), this asks Gemini to decide close to every action --
necessary because during actual gameplay the screen keeps changing from
whatever the bot does, so a "stuck" trigger rarely fires and an occasional
assist barely gets consulted. This mode is genre-agnostic (no "puzzle"
wording in the prompt) and tracks its own multi-turn memory so it can learn
controls and avoid repeating failed actions within a session, at the cost of
one Gemini round-trip per action (~5-15s measured) -- see README for the
realistic genre/latency scope this implies.
"""
import os
import re
import statistics
import time
from collections import deque

import cv2
import numpy as np
import win32gui

from . import capture as capture_mod
from . import input_controller as ic
from . import llm_vision
from . import vision

SYSTEM_PROMPT = """You are autonomously playing a video game for QA testing purposes. Your job is to make genuine progress -- advance objectives, solve challenges, defeat enemies, navigate menus, whatever is appropriate for this game's genre and current screen -- not to click randomly.

TOP PRIORITY, applies in any genre: if the screen shows any progress indicator -- a counter, checklist, percentage, score, item tally, or similar HUD element -- suggesting there are more objectives, collectibles, or goals left to find beyond whatever is currently visible, actively searching for them is your current primary objective. A goal existing somewhere in the level is not the same as it being on screen right now -- don't wait passively for a new target_or_goal to wander into view, and don't treat "nothing currently visible" as a reason to stop searching, repeat the same position, or coast on minimal effort. Systematically explore areas you haven't yet visited (see any exploration-coverage note provided each turn) with real intent to find what the indicator says is still missing, the same way you'd commit to reaching a target_or_goal that IS visible.

You don't know this specific game's controls in advance, but you already know how games in general tend to work -- use that. Prioritize, roughly in this order:
1. Read the screen first: on-screen tutorials, button prompts, key-icon overlays, and menu labels usually just tell you the control directly -- trust an explicit hint over any guess below.
2. Common conventions worth trying early, matched to what the screen looks like: WASD or arrow keys for movement; space/enter to confirm or interact; esc/back-arrow-icon/door-icon to cancel or go back; a gear icon is settings; click-to-interact is the most common menu/UI interaction, with click-and-drag common for inventory, cards, sliders, and puzzle-piece movement, and hover-then-click common in strategy/RTS-style UIs -- if the game displays several input-scheme options (e.g. "drag" vs "click-click" vs "hover-click"), that tells you directly which one it expects, so check for that before guessing. A thin chevron/arrow ("<" or ">", often just an outline, easy to miss against a busy background) is a near-universal pagination/carousel control (previous/next page, chapter, item, or hint) -- look for these at both edges of whatever you're currently looking at, not just the outer screen edge: a popup/panel/dialog/hint box has its own left/right (or top/bottom) edges, and a chevron sitting just outside or on the border of THAT panel -- separate from the panel's own close/X button -- almost always means there's more content (another hint page, another card) one tap away. Don't treat a panel's close button as your only exit option without first checking its edges for one of these.
3. Only fall back to broad experimentation once the above don't apply or haven't worked.
If the game shows a control-scheme selection/confirmation screen (e.g. choosing between "drag", "click then click", "hover then click"), identify which option is actually selected -- checkmark, filled radio button, highlighted border, not just which icons are present -- and treat that as a hard, permanent rule for this entire session: use exactly that interaction style from then on, even much later once that screen is long gone from view. Getting this wrong (e.g. dragging when the game is configured for click-then-click, which is two separate clicks -- one to select/pick up, one to place -- not a press-move-release gesture) wastes many turns repeating an interaction style the game was never listening for.
Learn from the outcome of your own prior actions in this conversation -- if an action didn't change the screen, it probably wasn't the right one; don't repeat it verbatim. Exception: if the element you targeted is small or thin (an icon, a chevron, a corner button) and nothing plausible else on screen fits what you were going for, your click may simply have missed a slightly different position than you estimated -- it's worth one retry with a deliberately wider box around the same general spot before abandoning that element entirely for a different approach.

Before choosing your action, you MUST first inventory the screen: list every distinct meaningful element currently visible in the "elements" field (not decorative background texture/scenery, but anything that looks like a piece, a slot, a target, or a control). For each, give a short label, a normalized (x,y) center position, and a role:
- "controlled_piece": the thing you move or act through (usually one, sometimes more). For this role specifically, also set "y2" to the y-coordinate of the BOTTOM edge of its sprite (where it visually touches the floor/ground), separately from "y" which stays the TOP edge of its sprite. Give the actual top and bottom pixels of the sprite, not an estimate of its middle -- this matters because a sprite with a tall hood, hat, raised arm/weapon, or antenna has its visual center well above where it actually collides with walls and floor, and reporting only a single center point silently loses that ground-contact position, which is what actually determines whether a move into a gap succeeds. Leave "y2" unset for every other role.
- "target_or_goal": a fixed destination/marker/objective -- something to bring a controlled_piece TO, not something to relocate itself. A common mistake is treating a fixed goal marker as another piece to move -- if two distinct-looking objects sit in similar-looking slots/frames, don't assume both are movable just because they look symmetric; a marker's role is usually "be reached", not "be moved". Some games render a leftover visual trace at cells the controlled_piece has already passed through (a footprint, dim afterimage, or trailing dot) -- don't mistake this kind of trailing artifact for the actual goal: a real goal was there before you approached and stays in exactly the same spot turn after turn, while a trailing mark follows behind wherever the piece has already been and tends to look visually flatter or plainer (e.g. a single dull color, no distinct icon shape) than a genuine collectible/objective. If you're not confident an object is the real goal, label it "decorative" or "unknown" rather than "target_or_goal" -- reporting a wrong position as the goal wastes turns navigating toward something that isn't really there. A maze/level may have more than one kind of target_or_goal at once: small collectibles (coins, gems, keys) AND a separate single exit/portal that completes the level -- often a distinctly animated or colored shape (a swirl, glow, gate, or door) that stands out clearly from plain wall/floor decoration. Don't assume the only goal is whatever collectible you spotted first; if you see a second, structurally different object that looks like it might be an exit, label it target_or_goal too and treat reaching it as equally valid progress, especially once nearby collectibles are exhausted or hard to reach.
- "usable_object": something in the scene that isn't the piece, the goal, or generic menu chrome, but looks like a tool/item/switch/lever/key that the puzzle or level expects you to interact with as PART of the solution -- activating, toggling, or collecting it -- possibly in addition to or before moving the controlled_piece, not as a replacement for reaching the goal. Don't assume the only path to progress is "move piece directly to goal"; if something on screen looks purpose-built (not decorative) and isn't the piece or goal itself, consider that using it might be a required step.
- "obstacle": blocks progress or should be avoided
- "empty_slot": an empty space that could receive a piece
- "ui_control": generic menu/session chrome not part of the puzzle itself (pause, settings, reset, hint/help, chevron/pagination, etc.) -- distinct from usable_object, which IS part of the puzzle's own mechanic
- "decorative": present but not interactive and not part of the puzzle/objective state
- "unknown": you can see it but genuinely can't tell its role yet
For each element that sits inside, on, or next to a box/bracket/frame shape (common in slot- or grid-based puzzles), also set "open_sides" to which side(s) of that frame are visibly OPEN (missing a wall/stroke) versus closed: e.g. "top", "bottom", "left,right", "none" (fully enclosed on all sides), or "n/a" (no frame around this element at all). This matters mechanically, not just visually: a closed side is very often a wall that blocks a piece from entering/exiting in that direction, while an open side is where a piece can actually pass through -- two frames facing each other with matching open sides (e.g. one open on the right, the neighboring one open on the left) suggests a valid path between them; a fully closed frame (open_sides "none") likely can't be entered by sliding at all and may need a different interaction (or may just be the goal marker's own display frame, not a slot). Before choosing a direction to move a piece, check whether the piece's current frame is actually open on the side facing where you want to send it -- don't assume a direction works just because the destination looks reachable in a straight line. When judging whether a gap or opening is actually passable, weigh it against the controlled_piece's own visible size on screen, not just whether there's a gap in the wall/frame line art at all -- a gap that looks barely wider than a thin wall-line but is noticeably narrower than the piece's own sprite is more likely a decorative notch than a real passage, while a gap roughly as wide as (or wider than) the piece itself is a much safer bet. Your own reasoning here is a starting guess, not a verdict -- if a direction that looked open turns out not to move you, that real outcome is more trustworthy than your visual estimate of the gap.
This inventory is not busywork -- use it, not just "what's clickable", to decide your action: identify which element is the controlled_piece, which (if any) is the target_or_goal, whether any usable_object needs to be engaged as part of the solution, and what relationship needs to hold between the piece and the goal (reach it, align with it, avoid it, defeat it, collect it -- whatever fits what's on screen) before picking a target for this turn's action. If moving the controlled_piece straight to the target_or_goal has been tried and hasn't worked, seriously consider a usable_object as the missing step rather than only retrying piece-to-goal variations. Carry forward roles you already established in earlier turns of this conversation rather than re-guessing from scratch each time, unless the screen has clearly changed in a way that invalidates them.
In a grid/maze layout, before heading toward an area, look one step further than the single cell you're about to enter: if that area is a small pocket walled in on most sides with only the one entrance you'd be coming from (a dead end), don't spend turns wandering deeper into it on the chance something's there -- turn back out the way you came and try a different branch instead, unless you can actually see a goal/coin/usable_object inside it. Recognizing a dead end from its wall shape before entering (or immediately upon seeing you're in one) is much cheaper than discovering it by exhausting every direction from inside.
(Still talking about that grid/maze case.) Once you've identified you're backtracking out of a dead end, finishing that backtrack takes priority over heading toward the goal -- don't let the goal's on-screen direction pull you back toward the same blocked direction before you've actually reached a new junction. Concretely: a single tap not moving you does NOT by itself confirm a direction is blocked -- one attempt's failure can just be a misread. Try that same direction from that same spot one more time (a bit longer hold is fine, some games need a sustained press to clear a tight spot). Only once it's failed to move you twice in a row from the same exact spot should you treat it as genuinely confirmed blocked; from then on, don't switch back to it just because it points toward the goal -- commit to a different direction that's taking you OUT of the pocket (even though it's temporarily moving you away from the goal) until you reach a cell with a genuinely different, untried opening. Only reconsider heading toward the goal once you're at a location where that direction hasn't already been ruled out.

Each turn you'll see the current screenshot and a short note on what your last action was, whether it changed the screen, and the element roles you identified last turn. Decide exactly ONE next action.

For a click, give the bounding box of the target element in normalized 0-1000 coordinates (0,0 = top-left, 1000,1000 = bottom-right), not raw pixels.
For a key press, name the key (e.g. "w", "space", "enter", "left"). If the genre needs a held direction (movement, running), set hold_ms to how long to hold it in milliseconds (up to 2000); omit hold_ms for a normal tap. In a grid/tile-based movement genre, prefer several separate short-to-moderate taps in the same direction over one very long hold: confirmed live that maxing out hold_ms (close to 2000ms) reliably overshoots past a turn -- the piece slides straight through a side-path's entrance before you can turn into it, and you only find out once you've already missed it. A held press only pays off on a long, straight, unbroken corridor with no turn to catch; anywhere a turn might be coming up, tap-and-recheck instead so you can react to what's actually ahead each time. If a note below says a move covered less distance than your fastest clean move this session, that means the move likely got cut short by a wall or edge, NOT that you should hold longer next time -- check whether your intended direction is actually open before repeating it.
For a drag (dragging a piece/card/slider from one place to another, drawing a path, some puzzle mechanics), give both a start bounding box (x1,y1,x2,y2) and an end bounding box (x3,y3,x4,y4), all in the same normalized 0-1000 coordinates. Use "drag" instead of two separate clicks whenever the interaction is a single continuous press-move-release gesture rather than two independent clicks -- check any on-screen control-scheme hint for whether this game expects drag vs click-click vs hover-click.

Respond with ONLY this JSON, no other text, no markdown code fence:
{"elements": [{"label": "<short name>", "role": "controlled_piece|target_or_goal|usable_object|obstacle|empty_slot|ui_control|decorative|unknown", "x": <int 0-1000>, "y": <int 0-1000, top edge if role is controlled_piece>, "y2": <int 0-1000, ONLY for role controlled_piece: bottom edge / ground-contact point>, "open_sides": "<e.g. 'top', 'bottom', 'left,right', 'none', or 'n/a'>"}, ...], "action": "click" or "key" or "drag", "key": "<key name, if action is key>", "hold_ms": <int ms, optional, if action is key>, "x1": <int 0-1000, if action is click or drag>, "y1": <int>, "x2": <int>, "y2": <int>, "x3": <int 0-1000, only if action is drag>, "y3": <int>, "x4": <int>, "y4": <int>, "reasoning": "<one short sentence>"}"""

MEMORY_PROMPT = """Summarize what you've learned so far in this play session, as 3-5 short plain-text bullet points (not JSON): controls confirmed to work, the current apparent objective, any actions confirmed to be dead ends, and the role of each element you've identified (e.g. "the girl = controlled_piece", "the yin-yang symbol = target_or_goal") if established. Be concise -- this replaces your turn history, so keep only what matters for continuing to play well."""

DEFAULT_KEY_HOLD_MS = 80
# SYSTEM_PROMPT tells the model hold_ms is "up to 2000" -- that was only ever
# a prompt-side request, never enforced, so a model that ignored it could
# send an arbitrarily long hold_ms and the code would sleep for exactly that
# long with no cap, silently contradicting the documented limit. Clamped in
# _execute so the prompt's stated ceiling is actually true.
MAX_KEY_HOLD_MS = 2000
# Any cardinal-direction key press longer than this gets split into a short
# probe phase + a conditional extension instead of one blind sleep. Directly
# answers a real tension between two things this project already knows are
# true: SYSTEM_PROMPT warns that a long hold overshoots straight past a
# nearby side-path entrance before you can react, while
# DIRECTION_BLOCKED_MIN_HOLD_MS needs a genuinely long hold to trust a
# no-movement reading as "blocked" rather than noise. Holding blind for the
# full target duration forces a choice between those two; probing first
# doesn't: release as soon as visible movement is detected (minimizing
# overshoot on a direction that turns out to be open) and only keep holding
# up to the full target when nothing visible happened yet (which is exactly
# the case where overshoot isn't a risk -- a blocked direction isn't going
# anywhere for the rest of the hold either).
PROBE_HOLD_MS = 125
# Fraction of frame pixels that must visibly change during a probe to count
# as "movement happened" -- deliberately generic (no scroll/grid assumption)
# so it works whether or not this game's camera pans; see _diff_regions for
# the same style of check used elsewhere for a different purpose.
# Recalibrated from the original 0.01 (1%) after it was caught live returning
# "no movement" for every single attempt in a session (25/25 false), even
# though real net progress was clearly happening between real LLM turns.
# Direct pixel-diff measurement of real before/after screenshot pairs from
# that same session showed genuine single-move displacement changes only
# ~0.096-0.102% of frame pixels (a small sprite in a 720p frame), while
# genuinely blocked attempts measured ~0.000-0.001% -- the old 1% threshold
# was roughly 10-100x too strict to ever register a real move at all. 0.0005
# sits with margin above the blocked-attempt noise ceiling and well below
# the real-movement floor measured live.
PROBE_CHANGE_FRAC = 0.0005
MAX_CONSECUTIVE_FAILURES = 5
# How many recent turns to look back over when checking for a repeating,
# ineffective action cycle -- live testing showed the agent settling into a
# 2-step loop (click element A, drag to B, click A, drag to B, ...) for 15+
# turns straight. Each individual turn technically obeys "don't repeat your
# immediately-previous action verbatim" (A and B differ from each other), so
# that per-turn instruction alone doesn't catch a longer-period cycle --
# gemini-flash-lite-latest wasn't reliably synthesizing "this 2-step pattern
# itself has already failed N times" from raw chat history alone. Detecting
# it explicitly in code and naming the exact repeated targets in the note is
# more reliable than counting on that synthesis.
STUCK_WINDOW = 6
STUCK_MAX_DISTINCT_TARGETS = 2
STUCK_REPEAT_COUNT = 2
POSITION_STUCK_WINDOW = 6
# Heuristic escapes never call the LLM, so they never get a fresh
# "elements" read -- meaning piece_pos_history (what _check_position_stuck
# uses) stays frozen at whatever it was when the stuck state was first
# detected. Confirmed live as a real bug: once triggered, the escape kept
# firing 237 turns in a row because nothing ever refreshed that frozen
# snapshot to reflect that the escapes themselves might already have broken
# the deadlock. Capping consecutive escapes and forcing a real LLM turn
# through periodically re-anchors the position history in truth.
MAX_CONSECUTIVE_HEURISTIC_ESCAPES = 4
# Cap on consecutive turns that skip the real Gemini call in favor of
# following _dfs_next_direction() directly (see the fast path in step()).
# Confirmed live as the dominant cost driver in agent mode: a real LLM turn
# got overridden by DFS exploration logic in ~75% of turns in one session
# (139/186) -- a full 5-15s round trip paid just to throw the answer away.
# Bounded (not unlimited) because a goal/coin can only ever be recognized by
# a real vision call -- DFS itself is blind to anything except open/blocked
# directions -- so a real call always follows within this many turns to
# resync ground truth and check for anything new on screen.
# Raised from 4 to 8: a real call still dominates wall-clock time (~5-15s)
# next to a free blind move (~0.7s including ACTION_SETTLE_S), so a longer
# free streak covers more physical ground per unit of wall-clock time --
# directly addresses a confirmed-live bottleneck (5.2h session, 1651 real
# turns, only 55 distinct cells visited -- mostly re-confirming already-
# known ground rather than reaching new territory). Trade-off: a goal
# sitting in view during a longer blind streak goes unnoticed for more
# turns before the next real check -- acceptable since goal sightings were
# already rare (14/1651 real turns, ~0.85%) relative to the speed gained.
DFS_SKIP_MAX_STREAK = 8
BUCKET_UNITS = 60  # normalized 0-1000 grid size for treating near-identical targets (a few px of retry jitter) as "the same" target
# How many recent "elements" reads to compare when checking whether the
# model's reported target_or_goal position is stable. A real fixed goal
# should sit in essentially the same normalized spot turn after turn (baring
# a scrolling camera, which shifts everything together, not just the goal);
# a model that's actually mislabeling a different-looking object each time
# (confirmed live: a leftover trail/footprint artifact was reported as
# "coin" at one spot, went unreported for ~15 turns once out of the recent
# labeling pattern, then reappeared at a completely different spot with a
# different label) instead scatters across many distinct buckets. This is a
# genre-agnostic reliability signal -- it never assumes anything about the
# goal's color or shape, only that a real one doesn't teleport.
GOAL_STUCK_WINDOW = 5
GOAL_MIN_DISTINCT_FOR_WARNING = 4
# Side length (px) of the persistent world canvas each captured frame gets
# pasted into, positioned using the same real pixel scroll shift
# _detect_scroll_shift already measures (see _update_world_canvas). Large
# enough to hold a big scrolling map without the character ever walking off
# the edge in a normal session; the canvas starts centered on wherever the
# character happened to be on turn 1, so this is a radius in both
# directions from there, not a one-sided extent.
WORLD_CANVAS_SIZE = 6000
# Half-width (px) of the goal template patch cropped from the world canvas
# the first time a target_or_goal is identified, and of the patch matched
# against on every later turn (see _update_goal_tracking). Small enough to
# stay specific to the goal sprite itself rather than surrounding
# floor/wall texture, large enough to survive the same few-px jitter as any
# other position read here.
GOAL_TEMPLATE_HALF_PX = 22
# How far around the goal's last known canvas position to search for it
# again each turn -- keeps the match cheap and, like
# SCROLL_SEARCH_MARGIN, makes it structurally impossible to lock onto a
# distant look-alike instead of genuine small drift/jitter at the same
# real spot.
GOAL_TEMPLATE_SEARCH_MARGIN_PX = 50
# Minimum cv2.TM_CCOEFF_NORMED score to trust a goal-template re-match.
# Below this, the goal isn't re-confirmed this turn (could be occluded by
# the character, off-screen, or genuinely collected) -- the last known
# canvas position is kept rather than discarded, since "not currently
# re-confirmed" isn't evidence the earlier confirmed sighting was wrong.
GOAL_TEMPLATE_MATCH_MIN_SCORE = 0.6
# How many consecutive turns a tracked goal can go without a fresh
# template re-match before its stabilized position is no longer trusted
# for pathing/consistency purposes (e.g. it was actually collected, or the
# view scrolled far enough that a stale lock is more likely than a real
# one still sitting there).
GOAL_TEMPLATE_MAX_STALE_TURNS = 15
# Rolling-window size for the movement-rate median used by
# _track_piece_movement, and how many samples must be collected before
# trusting that median enough to flag a "partial move" -- see that
# method's docstring for why a rolling median replaced an earlier
# running-max design.
MOVE_RATE_WINDOW = 10
# Raised from 4 -- the one-time bucket_units calibration below fires the
# moment this many samples exist, using their raw median with no baseline
# yet to compare against (MOVE_RATE_PARTIAL_FRAC's own partial-move check
# only ever activates once a median already exists, so it can't protect
# this first batch). Confirmed live as a real bug: one session's very
# first 4 samples included a move that hit a wall partway through (200ms
# held, only 20 units covered, vs a clean 125ms/40-unit sample elsewhere in
# the same session) -- with only 4 values, that one contaminated low
# reading was enough to pull the median down to CELL_SIZE_MIN's 20-unit
# floor. A larger sample count before the one-time calibration fires makes
# a single cut-short move much less likely to dominate the median.
MOVE_RATE_MIN_SAMPLES = 8
MOVE_RATE_PARTIAL_FRAC = 0.5
# BUCKET_UNITS (60) is a fixed guess about how much normalized-position
# distance corresponds to "one cell" -- fine for _bucket's original job
# (deduping near-identical UI click targets), but confirmed live as a real,
# structural problem for _pos_bucket's job (world position tracking for
# direction_known/DFS/goal-tracking): this game's own measured single-tap
# move distance clustered around 35-48 units, well under 60, so a single
# 60-unit bucket could span MORE than one real cell -- direction_known for
# "the same" bucket then silently mixed ground truth from two physically
# different spots (one genuinely open, one genuinely blocked), flipping
# back and forth as different sub-positions within that oversized bucket
# got sampled. Once enough real move-distance samples exist to calibrate a
# better estimate (see move_rate_window), self.bucket_units switches to a
# data-derived size instead of this fixed guess -- see
# CELL_SIZE_CALIBRATION_MIN_SAMPLES.
CELL_SIZE_REFERENCE_HOLD_MS = 125  # a representative "normal" hold duration to convert the calibrated rate back into a distance-per-cell estimate -- kept equal to DIRECTION_BLOCKED_MIN_HOLD_MS/PROBE_HOLD_MS, since most rate samples are collected at that hold length; drifting out of sync inflates or deflates the calibrated bucket size
CELL_SIZE_MIN = 20  # floor: never calibrate below the ~16-20 unit position-read noise ceiling, or jitter alone would scatter one real cell across many bucket keys
CELL_SIZE_MAX = 150  # ceiling: sanity bound against a wildly outlying rate sample
# How many times the model can choose click/drag without it ever being
# confirmed to move the piece before we stop trusting it to self-correct
# and start overriding the choice in code. Confirmed live as necessary,
# not hypothetical: in one real pure-keyboard session the model chose
# click or drag 12 separate times over ~120 turns, each time with fresh
# "since X and Y failed, I'll try Z" reasoning, despite the SYSTEM_PROMPT
# already saying to learn from an action's outcome and not repeat what
# didn't work -- the same "explicit instruction, ignored anyway" pattern
# already documented for the chevron-panel case elsewhere in this file.
# Bounding the attempts and then hard-overriding matches this file's
# existing precedent: don't keep re-explaining to a model that has
# already demonstrated it won't reliably self-correct here.
MAX_CLICK_DRAG_ATTEMPTS_BEFORE_BLOCK = 3
# Minimum normalized-unit displacement to accept a click/drag as having
# actually moved the controlled_piece, for ever_used_click_or_drag
# confirmation. Needs to be well above ordinary position-read noise (~3-16
# units, confirmed live from a stationary character re-measured turn to
# turn) but well below a real single-cell move (~60-300+ units in every
# real session measured so far) -- 50 sits clearly in that gap.
CLICK_DRAG_CONFIRM_MIN_DIST = 50
# Minimum world-corrected (scroll-compensated) displacement to accept a key
# press as having actually moved the controlled_piece, for direction_known /
# ever_confirmed_key_movement. Deliberately much smaller than
# CLICK_DRAG_CONFIRM_MIN_DIST: once a move is scroll-compensated (see
# camera_shift_x/y), a genuine single-cell move in a scrolling section can
# measure far smaller than the "60-300+ units" seen pre-scroll-compensation
# -- confirmed live, a real move that opened up new open_sides (none ->
# bottom,left,right) measured only ~42 units of world-corrected distance,
# well under 50, and got wrongly recorded as a confirmed-blocked direction
# because it shared CLICK_DRAG_CONFIRM_MIN_DIST's threshold. That false
# "blocked" ground truth then permanently overrode every later attempt at
# the same cell/direction, repeatedly forcing the agent away from a path
# that was actually open. Kept comfortably above the ~3-16 unit noise floor
# of a stationary character re-measured turn to turn (the same live session
# also showed two same-hold_ms=300 "right" reads from what bucketed to the
# same cell measure 31 units and 15 units respectively for what was, per
# open_sides, the same real corridor move -- read noise this close to the
# noise ceiling is why DIRECTION_BLOCKED_MIN_HOLD_MS below carries the real
# weight of telling a genuine wall apart from a noisy short-hold read, not
# this constant alone).
DIRECTION_MOVED_MIN_DIST = 20
# Minimum hold_ms before a no-movement result even COUNTS as a data point
# toward "this direction is blocked from this cell" (see
# DIRECTION_BLOCKED_MIN_FAILS below for how many such points it takes to
# actually trust it). A short tap (e.g. the 80ms default) failing to move
# the piece could just mean the tap itself was too brief, not that the
# direction is closed -- only a reasonably sustained hold failing is real
# evidence at all.
#
# This was raised from 150 to 800 for one session, as a single-hold fix for
# a real flip-flop bug (a confirmed-open "right", 300ms hold, 31-unit move,
# got immediately overwritten to BLOCKED by a same-bucket "right" reading
# only 15 units on another 300ms hold -- read noise this close to the ~16
# unit noise ceiling). 800ms did fix that, but at a steep, confirmed-live
# cost: this game's own prompt guidance (SYSTEM_PROMPT) tells the model to
# prefer short-to-moderate taps to avoid overshoot, so normal play almost
# never produces an 800ms hold -- action_override, the whole mechanism this
# ground truth exists to feed, dropped from 13-19 firings/session to 1, and
# heuristic-escape count rose 24->33 as the model was left to repeatedly
# retry its own uncorrected bad guesses. Requiring one very long hold and
# requiring enough repeated SHORT samples turned out to be two different
# ways to solve the same noise problem -- DIRECTION_BLOCKED_MIN_FAILS below
# takes the second approach (repetition, which normal short-tap play
# actually produces) so this can drop back down to something ordinary play
# reaches often, while still not trusting any single noisy sample alone.
DIRECTION_BLOCKED_MIN_HOLD_MS = 125
# How many separate qualifying (>= DIRECTION_BLOCKED_MIN_HOLD_MS) no-movement
# attempts at the same (bucket, direction) are required before trusting it
# as confirmed BLOCKED. 1 is what caused the flip-flop bug above (a single
# noisy short read was enough to overwrite a correct True); 2 means a lone
# bad reading can't overwrite established ground truth or wrongly establish
# new ground truth by itself -- it takes the same noisy result twice, which
# a real wall will keep producing and genuine noise won't. Any confirmed
# move (moved=True) immediately counts as OPEN and resets this counter, so
# it never takes more than one real success to override a run of failures.
DIRECTION_BLOCKED_MIN_FAILS = 2
# Max pixels _detect_scroll_shift will search away from a patch's origin.
# Bounds any detected shift to a plausible single-turn amount and, on a
# periodically-tiled background, makes it structurally impossible to match
# a distant-but-similar-looking tile instead of the true small shift (see
# that method's docstring for the real session this corrupted).
SCROLL_SEARCH_MARGIN = 40
# Pixel offset (in the tested direction) from the controlled_piece's own
# position to sample a "wall" color when a direction is confirmed BLOCKED
# -- close enough to reliably land on the obstacle that stopped the piece,
# not the piece's own sprite or an unrelated distant tile.
COLOR_SAMPLE_OFFSET_PX = 35
# Minimum samples needed in EACH of floor_color_samples/wall_color_samples
# before trusting color-based prediction at all. Below this, there isn't
# enough evidence yet to distinguish this game's actual floor/wall colors
# from noise (sprite edges, idle-animation shimmer, anti-aliasing).
COLOR_CALIBRATION_MIN_SAMPLES = 3
# Minimum color-space separation (Euclidean, BGR 0-255 per channel)
# required between the floor and wall centroids before trusting a
# prediction. Confirmed live as necessary: several skins today used very
# close floor/wall tones (e.g. two shades of teal), where a color-only
# classifier would be little better than a coin flip -- better to abstain
# (fall back to the existing tried-it-and-learned approach) than act on an
# unreliable color guess.
COLOR_CALIBRATION_MIN_SEPARATION = 40
# How many consecutive real turns of a near-empty "elements" report (just
# the controlled_piece, nothing else -- no target_or_goal, no usable_object,
# no obstacle) before treating it as disengaged analysis rather than a
# genuinely featureless screen. Confirmed live as a real failure mode, not
# hypothetical: once the only currently-visible coin was collected, the
# model reported a 1-element list and an unchanged (500,500) position for
# 100+ consecutive turns -- it stopped actively searching for the
# session's other, not-yet-discovered goals rather than continuing to
# explore for them. A screen this repetitive for this long without a
# single new observation is far more consistent with the model coasting on
# a placeholder than with 100 turns of a truly identical, empty view.
DEGENERATE_ANALYSIS_WARN_THRESHOLD = 3
DEGENERATE_ANALYSIS_FORCE_THRESHOLD = 6
# How many consecutive REAL turns (not heuristic escapes) can report the
# EXACT same controlled_piece position before forcing an early chat reset,
# bypassing the normal memory_summarize_every_n_turns schedule. Confirmed
# live as a distinct failure mode from the degenerate-elements one above:
# even with 56 real heuristic-escape key presses genuinely reaching the
# game in between, the model kept reporting the identical position on
# every subsequent real turn -- STUCK WARNING and DEGENERATE ANALYSIS
# WARNING text both failed to dislodge it. This looks like a within-
# conversation repetition attractor (the model anchoring on its own prior
# answer) rather than a visibility/attention problem text can fix -- a
# fresh chat session (new context, no prior "500,467" to anchor on) is a
# structurally different intervention, not just stronger wording.
STALE_POSITION_FORCE_RESET_THRESHOLD = 8
# Gap after executing an action before the *next* turn's screenshot is
# captured. Without this, the next frame is grabbed within ~0.1-0.2s of the
# click (just Python overhead) -- confirmed live: a key-icon click that
# opened a hint overlay was followed by a screenshot too early to show it,
# so the agent judged its own action a no-op. Menu/dialog fade-slide
# animations commonly run 200-500ms, so settle a bit past that.
ACTION_SETTLE_S = 0.6

# Extends the shared llm_vision.RESPONSE_SCHEMA (used as-is by monkey mode's
# single-shot stuck fallback) with a required "elements" field, specific to
# this continuous per-turn loop. Motivation, confirmed live: without an
# explicit place to externalize it, the model was demonstrably not tracking
# basic board facts turn to turn -- e.g. not treating a fixed goal marker as
# a destination distinct from the piece being moved -- even though the
# prompt already described that distinction in prose. Forcing the model to
# name a label/role/position for every element it sees, every turn, makes
# that understanding an explicit output instead of an implicit hope, and
# gives us a real per-screen inventory to log for QA review independent of
# whether the agent solves anything.
ELEMENT_ROLES = [
    "controlled_piece", "target_or_goal", "usable_object", "obstacle",
    "empty_slot", "ui_control", "decorative", "unknown",
]
AGENT_RESPONSE_SCHEMA = dict(llm_vision.RESPONSE_SCHEMA)
AGENT_RESPONSE_SCHEMA["properties"] = dict(llm_vision.RESPONSE_SCHEMA["properties"])
AGENT_RESPONSE_SCHEMA["properties"]["elements"] = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "label": {"type": "string"},
            "role": {"type": "string", "enum": ELEMENT_ROLES},
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "y2": {"type": "integer"},
            "open_sides": {"type": "string"},
        },
        "required": ["label", "role", "x", "y", "open_sides"],
    },
}
AGENT_RESPONSE_SCHEMA["required"] = list(llm_vision.RESPONSE_SCHEMA["required"]) + ["elements"]


class GameAgent:
    def __init__(self, target, cap, logger, config, monitor=None):
        self.target = target
        self.cap = cap
        self.logger = logger
        self.config = config
        self.monitor = monitor

        # available_keys: primary first (CLI flag or GEMINI_API_KEY env var,
        # mirroring llm_vision.get_client's own default), then the explicit
        # fallback (CLI flag or GEMINI_API_KEY_2), then any further
        # GEMINI_API_KEY_3, _4, ... env vars found in sequence -- lets
        # someone hand over additional keys later in a session without a
        # code change, just by setting one more env var and starting a new
        # run. Only the daily quota
        # (GenerateRequestsPerDayPerProjectPerModel) triggers a switch --
        # see the exception handler in step() -- since that's the one limit
        # backing off can never clear, unlike the per-minute one.
        primary_key = config.gemini_api_key or os.environ.get("GEMINI_API_KEY")
        fallback_key = config.gemini_api_key_fallback or os.environ.get("GEMINI_API_KEY_2")
        self.available_keys = [k for k in (primary_key, fallback_key) if k]
        extra_idx = 3
        while True:
            extra_key = os.environ.get(f"GEMINI_API_KEY_{extra_idx}")
            if not extra_key:
                break
            self.available_keys.append(extra_key)
            extra_idx += 1
        self.active_key_idx = 0
        self.client = llm_vision.get_client(self.available_keys[0] if self.available_keys else None)
        self.chat = None
        self.memory = ""
        self.turn_count = 0
        self.turns_since_reset = 0
        self.llm_call_count = 0
        self.consecutive_failures = 0
        self.last_frame_bgr = None
        self.last_action_desc = "none yet"
        self.last_call_ts = 0.0
        self.stop_reason = None
        self.turn_log = []  # rolling window of {"sig": (...), "changed": bool} for stuck-cycle detection
        self.pending_signature = None
        self.last_elements = []
        self.last_piece_pos = None  # (x, y) normalized 0-1000, controlled_piece's position last turn
        self.max_move_distance = 0  # session-long running max single-turn displacement, used only by _check_position_stuck's coarse "have you gone anywhere" gate
        self.move_rate_window = []  # rolling window of recent displacement-per-ms-held rates, self-calibrating "what a typical clean move looks like" independent of how long the key was actually held (see _track_piece_movement docstring for why this is a rolling median, not a running max)
        self.bucket_units = BUCKET_UNITS  # world-position bucket size for _pos_bucket -- starts at the fixed default, switches once to a data-derived size; see CELL_SIZE_REFERENCE_HOLD_MS
        self.bucket_units_calibrated = False
        self.move_hold_ms_acc = 0  # ms of key-hold attributable to movement since the last _track_piece_movement call; None when not attributable to a known hold duration (click/drag, or a heuristic-escape spree)
        self.move_hold_ms_direction = None  # cardinal direction the accumulated move_hold_ms_acc is attributable to; "mixed" once more than one direction contributed since the last _track_piece_movement call -- see its use in _track_piece_movement for why a mixed-direction window can't be trusted for rate calibration
        self.last_move_desc = ""
        self.piece_pos_history = []  # rolling window of recent controlled_piece positions, for net-displacement stuck detection
        self.visited_positions = set()  # session-long (not a rolling window) set of bucketed controlled_piece positions, for exploration-coverage hints -- see _visited_direction_hint and _pick_untried_direction
        self.camera_shift_x = 0.0  # cumulative background pixel shift (normalized 0-1000 units) since session start, from per-turn scroll detection -- see _detect_scroll_shift. Subtracting this from a raw reported screen position corrects it to a stable world-reference frame.
        self.camera_shift_y = 0.0
        self.direction_known = {}  # (bucket, direction) -> True (confirmed open, a move actually happened) / False (confirmed blocked, a real hold produced no movement) -- ground truth from actual attempted moves, not the model's own fallible open_sides visual judgment; see _track_piece_movement and _direction_ground_truth_hint
        self.direction_fail_counts = {}  # (bucket, direction) -> consecutive qualifying no-movement count; see DIRECTION_BLOCKED_MIN_FAILS
        self.known_route_directions = None  # cached BFS route (list of directions) from _known_route_hint, consumed by step()'s override tier
        self.canvas = None  # persistent world-canvas BGR image, lazily created on turn 1 -- see _update_world_canvas
        self.canvas_origin = None  # (x, y) px: where turn 1's frame was pasted on the canvas -- the world-position zero point
        self.canvas_shift_px = [0.0, 0.0]  # cumulative RAW pixel scroll shift (not normalized 0-1000, unlike camera_shift_x/y) -- see _update_world_canvas
        self.goal_canvas_pos = None  # (x, y) px on the canvas: stabilized target_or_goal position via template tracking -- see _update_goal_tracking
        self.goal_template = None  # small BGR patch cropped from the canvas around the goal's first sighting, re-matched every turn
        self.goal_canvas_stale_turns = 0  # consecutive turns since the goal template last re-matched confidently
        self.current_frame_bgr = None  # this turn's captured frame, stashed for color-sampling helpers -- see step()
        self.current_w = 0
        self.current_h = 0
        self.floor_color_samples = []  # BGR pixel colors sampled at positions confirmed walkable (see direction_known) -- self-calibrated per session, never hardcoded, so this works regardless of which game/skin's colors mean what
        self.wall_color_samples = []  # BGR pixel colors sampled at positions confirmed blocked
        self.pending_direction = None  # (from_bucket, direction, hold_ms) for the single most recent cardinal-direction key action, cleared (not just consumed) by anything that breaks clean attribution -- click/drag, or more than one action between measurements
        self.goal_pos_history = []  # rolling window of sets of bucketed target_or_goal positions, for identification-consistency checking
        self.goal_warning = ""
        self.consecutive_minimal_elements = 0  # session-long count of consecutive real turns reporting only the controlled_piece (no target_or_goal/usable_object/etc) -- see DEGENERATE_ANALYSIS_*_THRESHOLD
        self.consecutive_identical_position = 0  # session-long count of consecutive real turns reporting the EXACT same controlled_piece position -- see STALE_POSITION_FORCE_RESET_THRESHOLD
        self.consecutive_heuristic_escapes = 0
        self.dfs_skip_streak = 0  # consecutive turns skipped via direct DFS-follow, no real Gemini call -- see DFS_SKIP_MAX_STREAK
        self.last_has_goal = False  # goal-visibility as of the most recent REAL call; gates DFS-skip eligibility since a skip has no way to notice a new goal itself
        self.ever_used_click_or_drag = False  # session-long: has the model (not a heuristic escape) ever chosen click/drag AND had it actually move the controlled_piece? Used to skip the icon-click escape tier for pure-keyboard-movement genres
        self.pending_click_or_drag_confirm = False  # True for exactly one turn after the model chooses click/drag, until we see whether it actually moved the piece (cleared without confirming if any heuristic escape happens first, since that breaks clean attribution)
        self.failed_click_drag_attempts = 0  # session-long count of model-chosen click/drag turns not yet confirmed effective; once this hits MAX_CLICK_DRAG_ATTEMPTS_BEFORE_BLOCK, further click/drag choices get overridden in code
        self.ever_confirmed_key_movement = False  # session-long: has a "key" action (real or heuristic direction-escape) ever been confirmed to actually move the piece? Required before the click/drag block below is allowed to trigger -- see that block's comment for why
        self.tried_combos = set()  # session-long (not rolling-window) set of bucketed (src, dst) pairs already attempted
        self._start_new_chat()

    def _start_new_chat(self):
        from google.genai import types

        system_instruction = SYSTEM_PROMPT
        if self.memory:
            system_instruction += "\n\nMemory from earlier this session:\n" + self.memory
        self.chat = self.client.chats.create(
            model=self.config.gemini_model,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json",
                response_schema=AGENT_RESPONSE_SCHEMA,
                temperature=0.4,
            ),
        )
        self.turns_since_reset = 0

    def _switch_to_next_key(self):
        """Advances to the next available Gemini API key and rebuilds the
        chat session against it. Returns False if there's no next key left
        (caller falls through to the existing failure/backoff handling).

        self.chat is bound to the client instance it was created from (its
        auth travels with it, not with whatever self.client happens to
        point at later) -- reassigning self.client alone would leave the
        in-flight chat still silently using the old, exhausted key. Forcing
        a fresh chat via _start_new_chat() is required, not optional; it
        also means the new key starts a clean conversation (still seeded
        with self.memory, same as any other periodic reset), not a
        continuation of the old key's turn history.
        """
        if self.active_key_idx + 1 >= len(self.available_keys):
            return False
        self.active_key_idx += 1
        self.client = llm_vision.get_client(self.available_keys[self.active_key_idx])
        self.logger.log_event("api_key_switch", {"new_key_index": self.active_key_idx})
        self._start_new_chat()
        return True

    def _summarize_memory(self):
        # Deliberately NOT self.chat.send_message() here: the chat session's
        # config has response_mime_type="application/json" +
        # RESPONSE_SCHEMA applied to *every* message sent through it, so a
        # plain-text summary request through the chat came back forced into
        # the click/key action JSON shape instead of prose (confirmed live --
        # the "summary" was a stray action blob, not the intended bullet
        # list). Reusing the chat's history in a one-off schema-free
        # generate_content call gets a real free-text summary instead.
        #
        # Critical: chat.get_history() only covers turns since the *last*
        # reset -- it does not include the system_instruction that carried
        # the previous self.memory into this chat session. Sending only that
        # history to summarize meant any fact from before the last reset
        # (e.g. a confirmed control scheme established in turn 2) silently
        # evaporated the moment it stopped being re-mentioned in recent
        # turns -- confirmed live as a real bug, not a model-intelligence
        # limitation. Explicitly feeding the prior self.memory back in here
        # and telling the model to carry forward what's still true fixes it.
        try:
            from google.genai import types

            history = self.chat.get_history()
            memory_request = MEMORY_PROMPT
            if self.memory:
                memory_request += (
                    "\n\nYour summary from before this stretch of turns was:\n"
                    + self.memory
                    + "\n\nCarry forward anything from it that's still true -- especially a "
                    "confirmed control scheme -- even if it wasn't mentioned again in the turns "
                    "above. Don't let an established fact drop just because it's no longer recent."
                )
            response = self.client.models.generate_content(
                model=self.config.gemini_model,
                contents=list(history) + [memory_request],
                config=types.GenerateContentConfig(temperature=0.4),
            )
            self.llm_call_count += 1
            self.memory = (response.text or "").strip()
            self.logger.log_event("agent_memory_update", {"memory": self.memory})
        except Exception as e:
            self.logger.log_event(
                "error", {"source": "game_agent", "message": f"memory summarization failed: {e}"}
            )
            # keep whatever memory we already had; not fatal to the session

    def _rate_limit_wait(self):
        min_interval = self.config.agent_interval_s
        elapsed = time.time() - self.last_call_ts
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)

    def _detect_scroll_shift(self, prev_bgr, curr_bgr):
        # Confirmed live as necessary, not hypothetical: a game assumed
        # (wrongly, from an earlier session's cursory check) to have a
        # fixed, non-scrolling camera actually pans -- template-matching a
        # background patch before/after a single move found it shifted by
        # (0,-7) px on screen. Every position-bucket-based mechanism built
        # today (visited_positions, direction_known,
        # goal_pos_history) implicitly assumed normalized screen position
        # IS world position, which silently breaks the moment the camera
        # pans -- the same world cell reports different coordinates
        # depending on how much scrolling has accumulated. This detects
        # that per-turn pixel shift so callers can correct for it.
        #
        # Method: sample several fixed patches near the frame's corners
        # (margin-inset to dodge the character, which is usually centered)
        # and template-match each into the new frame, but ONLY within a
        # small search window around the patch's original location -- not
        # the whole frame. Confirmed live as essential, not a nicety: this
        # game's wall/floor art is a periodically repeating tile texture
        # (~60-90px period, confirmed by direct measurement earlier), so an
        # unconstrained whole-frame search can "confidently" (score > 0.9)
        # match a distant tile that just looks identical, not the true
        # small shift -- one real session accumulated a cumulative offset
        # of 3200 normalized units (over 3x the whole frame width) this
        # way, from false matches alone. Capping the search window to
        # SCROLL_SEARCH_MARGIN around the origin makes matching a distant,
        # merely-similar-looking tile structurally impossible: nothing
        # outside that window is even visible to matchTemplate.
        h, w = prev_bgr.shape[:2]
        patch_size = 60
        margin = 90
        candidates = [
            (margin, margin), (w - margin - patch_size, margin),
            (margin, h - margin - patch_size), (w - margin - patch_size, h - margin - patch_size),
        ]
        shifts = []
        for (px, py) in candidates:
            if px < 0 or py < 0 or px + patch_size > w or py + patch_size > h:
                continue
            patch = prev_bgr[py:py + patch_size, px:px + patch_size]
            sx0, sy0 = max(0, px - SCROLL_SEARCH_MARGIN), max(0, py - SCROLL_SEARCH_MARGIN)
            sx1 = min(w, px + patch_size + SCROLL_SEARCH_MARGIN)
            sy1 = min(h, py + patch_size + SCROLL_SEARCH_MARGIN)
            search_region = curr_bgr[sy0:sy1, sx0:sx1]
            if search_region.shape[0] < patch_size or search_region.shape[1] < patch_size:
                continue
            res = cv2.matchTemplate(search_region, patch, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(res)
            if max_val > 0.9:
                shifts.append((sx0 + max_loc[0] - px, sy0 + max_loc[1] - py))
        if len(shifts) < 2:
            return None
        dxs = sorted(s[0] for s in shifts)
        dys = sorted(s[1] for s in shifts)
        mid = len(shifts) // 2
        return (dxs[mid], dys[mid])

    def _update_world_canvas(self, frame_bgr, shift):
        # A persistent, pixel-accurate map of everywhere the camera has
        # looked this session, built the same way _detect_scroll_shift
        # already proved reliable (real image comparison) rather than the
        # model's own fallible per-turn text coordinates. Every turn's
        # frame gets pasted onto a large fixed canvas at the position
        # implied by the cumulative REAL pixel shift -- not the normalized
        # 0-1000 camera_shift_x/y (which is resolution-independent but not
        # pixel-precise enough for accurate template re-matching later in
        # _update_goal_tracking). Overwriting old canvas content with newer
        # frames is intentional and safe for a mostly-static maze: the same
        # real wall/floor pixels should look the same whenever revisited,
        # so newer data is never worse than older data at the same spot.
        h, w = frame_bgr.shape[:2]
        if self.canvas is None:
            self.canvas = np.zeros((WORLD_CANVAS_SIZE, WORLD_CANVAS_SIZE, 3), dtype=np.uint8)
            self.canvas_origin = (WORLD_CANVAS_SIZE // 2, WORLD_CANVAS_SIZE // 2)
            self.canvas_shift_px = [0.0, 0.0]
        elif shift is not None:
            self.canvas_shift_px[0] += shift[0]
            self.canvas_shift_px[1] += shift[1]
        x0 = int(self.canvas_origin[0] + self.canvas_shift_px[0])
        y0 = int(self.canvas_origin[1] + self.canvas_shift_px[1])
        cx0, cy0 = max(x0, 0), max(y0, 0)
        cx1, cy1 = min(x0 + w, WORLD_CANVAS_SIZE), min(y0 + h, WORLD_CANVAS_SIZE)
        if cx1 <= cx0 or cy1 <= cy0:
            return  # scrolled entirely off the (very generously sized) canvas -- nothing to paste this turn
        fx0, fy0 = cx0 - x0, cy0 - y0
        self.canvas[cy0:cy1, cx0:cx1] = frame_bgr[fy0:fy0 + (cy1 - cy0), fx0:fx0 + (cx1 - cx0)]

    def _canvas_pos_from_normalized(self, nx, ny, w, h):
        # Maps a normalized-0-1000 position in the CURRENT frame to
        # absolute world-canvas pixel coordinates, using this same turn's
        # cumulative pixel shift (see _update_world_canvas) -- the shared
        # conversion both _update_goal_tracking and any future canvas
        # consumer should use rather than re-deriving it ad hoc.
        px, py = nx / 1000 * w, ny / 1000 * h
        return (
            self.canvas_origin[0] + self.canvas_shift_px[0] + px,
            self.canvas_origin[1] + self.canvas_shift_px[1] + py,
        )

    def _update_goal_tracking(self, elements, w, h):
        # Stabilizes the target_or_goal's real-world position via template
        # matching against the world canvas, instead of re-trusting the
        # model's fresh per-turn text coordinate every time. Confirmed live
        # as necessary, not hypothetical: in one session, a single real
        # coin's reported y-coordinate swung by 350+ normalized units turn
        # to turn (622 -> 661 -> 361 -> 281 -> 614 -> 565) even after
        # correcting for actual camera scroll, despite the user confirming
        # only one coin existed -- the model's own read of where it was
        # kept drifting, not the coin. This still needs the model to
        # semantically identify the goal at least once (this project
        # doesn't hardcode what a "goal" looks like, to stay genre-
        # agnostic) -- it just tracks that same real patch of canvas pixels
        # afterward via image matching rather than re-asking a visibly
        # unreliable per-turn guess to hold still.
        if self.goal_canvas_pos is not None:
            gx, gy = self.goal_canvas_pos
            half, margin = GOAL_TEMPLATE_HALF_PX, GOAL_TEMPLATE_SEARCH_MARGIN_PX
            sx0, sy0 = max(int(gx - half - margin), 0), max(int(gy - half - margin), 0)
            sx1 = min(int(gx + half + margin), WORLD_CANVAS_SIZE)
            sy1 = min(int(gy + half + margin), WORLD_CANVAS_SIZE)
            th = tw = 0
            if self.goal_template is not None:
                th, tw = self.goal_template.shape[:2]
            if self.goal_template is not None and sy1 - sy0 >= th and sx1 - sx0 >= tw and th > 0 and tw > 0:
                search_region = self.canvas[sy0:sy1, sx0:sx1]
                res = cv2.matchTemplate(search_region, self.goal_template, cv2.TM_CCOEFF_NORMED)
                _, score, _, loc = cv2.minMaxLoc(res)
                if score >= GOAL_TEMPLATE_MATCH_MIN_SCORE:
                    self.goal_canvas_pos = (sx0 + loc[0] + tw / 2, sy0 + loc[1] + th / 2)
                    self.goal_canvas_stale_turns = 0
                else:
                    self.goal_canvas_stale_turns += 1
            else:
                self.goal_canvas_stale_turns += 1
        if self.goal_canvas_pos is None:
            goals = [e for e in elements if e.get("role") == "target_or_goal"]
            if goals:
                cx, cy = self._canvas_pos_from_normalized(goals[0].get("x", 0), goals[0].get("y", 0), w, h)
                half = GOAL_TEMPLATE_HALF_PX
                tx0, ty0 = max(int(cx - half), 0), max(int(cy - half), 0)
                tx1, ty1 = min(int(cx + half), WORLD_CANVAS_SIZE), min(int(cy + half), WORLD_CANVAS_SIZE)
                if tx1 - tx0 > 4 and ty1 - ty0 > 4:
                    self.goal_template = self.canvas[ty0:ty1, tx0:tx1].copy()
                    self.goal_canvas_pos = (cx, cy)
                    self.goal_canvas_stale_turns = 0

    def _stabilized_goal_world_pos(self, w, h):
        # Returns the goal's canvas-tracked position converted back into
        # this project's existing world-normalized coordinate space (same
        # convention as raw_pos - camera_shift_x/y elsewhere), so callers
        # (_track_goal_consistency, _known_route_hint) can use it as a
        # drop-in replacement for the model's raw per-turn report. None if
        # no goal is currently tracked or the track has gone stale long
        # enough that it's no longer trustworthy (see
        # GOAL_TEMPLATE_MAX_STALE_TURNS) -- callers should fall back to the
        # model's own current report in that case, not silently use a
        # possibly-outdated lock.
        if self.goal_canvas_pos is None or self.goal_canvas_stale_turns > GOAL_TEMPLATE_MAX_STALE_TURNS:
            return None
        cx, cy = self.goal_canvas_pos
        return (
            (cx - self.canvas_origin[0]) / w * 1000,
            (cy - self.canvas_origin[1]) / h * 1000,
        )

    def _diff_regions(self, prev_bgr, curr_bgr, min_area_frac=0.0003, large_change_frac=0.15):
        # Pixel-level diff instead of a perceptual hash: an 8x8 average-hash
        # comparison (the previous approach) downsamples the whole frame, so
        # a small UI element (e.g. a thin pagination chevron next to a hint
        # popup) can appear/disappear without shifting the hash enough to
        # register as "changed" at all -- confirmed live, the agent was told
        # its click "did NOT change the screen" when a real element had in
        # fact appeared. A pixel diff catches it directly, and generalizes
        # across games instead of us hand-coding which UI conventions to
        # look for.
        #
        # Returns changed-region boxes in pixel coords, or None if the
        # change is too large-scale to usefully localize. That None case
        # matters: many games dim/tint the whole screen when a modal/popup
        # opens, and that dimming alone can out-diff a genuinely new small
        # element sitting on top of busy background art (confirmed live --
        # a background area's diff measured higher than the new chevron
        # icon's own diff, because the "dim" interacts with existing
        # texture detail). Trying to box that yields one giant box covering
        # nearly the whole frame, which tells the model nothing useful.
        # When changed pixels exceed large_change_frac of the frame, skip
        # localization and let the caller fall back to a plain "look at the
        # whole image" note instead of a misleading box.
        gray1 = cv2.cvtColor(prev_bgr, cv2.COLOR_BGR2GRAY)
        gray2 = cv2.cvtColor(curr_bgr, cv2.COLOR_BGR2GRAY)
        diff = cv2.absdiff(gray1, gray2)
        _, thresh = cv2.threshold(diff, 30, 255, cv2.THRESH_BINARY)
        h, w = gray1.shape
        changed_frac = (thresh > 0).sum() / (w * h)
        if changed_frac >= large_change_frac:
            return None
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15))
        thresh = cv2.dilate(thresh, kernel, iterations=2)
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        # min_area is deliberately lower than vision.py's button-detection
        # thresholds -- this only needs to catch "something changed here",
        # not classify what it is. Note: looping idle animations (e.g. a
        # pulsing arrow) will also show up here -- left for the model to
        # judge in context rather than filtered out, since we can't
        # distinguish "decorative" from "meaningful" purely from pixel motion.
        min_area = min_area_frac * w * h
        boxes = []
        for c in contours:
            x, y, cw, ch = cv2.boundingRect(c)
            if cw * ch >= min_area:
                boxes.append((x, y, x + cw, y + ch))
        return boxes

    def _probe_chevron(self, frame_bgr, left, top, w, h):
        # vision.detect_chevrons is a probabilistic heuristic (see its
        # docstring -- loose shape thresholds mean occasional false
        # positives from decorative edge art). Pick at most one candidate
        # per side, closest to the true screen edge, rather than acting on
        # every match; prefer the right side since "next" is the more
        # common first thing to check on a freshly-opened, single-page-seen
        # panel.
        candidates = vision.detect_chevrons(frame_bgr)
        if not candidates:
            return None
        right_side = [c for c in candidates if c["cx"] >= w / 2]
        left_side = [c for c in candidates if c["cx"] < w / 2]
        if right_side:
            return max(right_side, key=lambda c: c["cx"])
        return min(left_side, key=lambda c: c["cx"])

    def _element_bucket(self, element, w, h):
        return (self._bucket(element.get("x", 0) * w // 1000, w), self._bucket(element.get("y", 0) * h // 1000, h))

    def _untried_piece_target_combos(self, w, h):
        # Grounds "try something new" in the model's OWN declared understanding
        # of the board (self.last_elements, from the required "elements" field)
        # instead of either hoping the model spontaneously thinks of a new
        # combination, or falling back to a semantically-blind generic UI
        # click. Live testing showed the model correctly labeling roles
        # (character=controlled_piece, yin-yang symbol=target_or_goal, an
        # empty frame=empty_slot) turn after turn, but still not
        # systematically trying every (piece, destination) pairing once the
        # piece moved to a new position -- it kept re-trying destinations
        # already ruled out from the new spot instead of the one destination
        # never attempted from there. tried_combos is session-long (not the
        # short rolling window turn_log uses), so "already tried" means for
        # the whole session, not just recently.
        pieces = [e for e in self.last_elements if e.get("role") == "controlled_piece"]
        targets = [e for e in self.last_elements if e.get("role") in ("target_or_goal", "empty_slot")]
        combos = []
        for p in pieces:
            p_bucket = self._element_bucket(p, w, h)
            for t in targets:
                t_bucket = self._element_bucket(t, w, h)
                if p_bucket == t_bucket:
                    continue
                combo_key = ("drag", p_bucket, t_bucket)
                if combo_key not in self.tried_combos:
                    combos.append({"piece": p, "target": t, "p_bucket": p_bucket, "t_bucket": t_bucket})
        return combos

    def _track_piece_movement(self, elements):
        # Self-calibrating alternative to measuring the maze's true grid
        # cell size in pixels: an image-based estimator (autocorrelation,
        # then contour-spacing) was tried and tested directly against real
        # screenshots -- it worked for one maze art skin (stone pillars,
        # correctly measured 64px) but returned nothing usable for two
        # others (bushes, wood crates) in the same game. Rather than ship
        # a detector known to be unreliable across skins, this instead
        # tracks the controlled_piece's own self-reported position
        # (already collected every turn) turn-to-turn, normalized by
        # hold_ms into a distance-per-ms "rate" (a 500ms tap covering 1
        # cell and a 1500ms hold covering 3 cells both reflect roughly the
        # same underlying movement speed, so comparing rate instead of raw
        # distance is meant to stop a long hold from setting an unfairly
        # high bar for a normal tap).
        #
        # Compared against the ROLLING MEDIAN of recent rates, not a
        # running max. A running-max version was tried and tested first --
        # replaying a real session's logged positions/hold_ms against it
        # showed it's still broken, just differently: a single early tap
        # that happened to land an unusually fast/clean rate permanently
        # set the ceiling, and every later legitimate move measured a
        # lower rate than that one outlier and got flagged "partial" --
        # 34 of 35 comparisons in that replay warned, worse than doing
        # nothing. The underlying problem is that ANY "best value ever
        # seen, kept forever" statistic is dominated by whichever single
        # reading happened to be the most extreme, and this game's
        # movement rate is not even consistent across different hold
        # durations to begin with (confirmed in the same replay: a 500ms
        # tap measured a HIGHER rate than a 1500ms hold). A rolling median
        # over the last MOVE_RATE_WINDOW qualifying moves is robust to
        # that -- one outlier reading (fast or slow) gets outvoted instead
        # of permanently setting the baseline, and it naturally adapts as
        # more real data comes in instead of only ever ratcheting one
        # direction. Replaying the same real session against this version
        # dropped the false-flag rate from ~30/34 comparisons to 14/34,
        # concentrated on the genuinely tiny (near-zero) displacements
        # rather than on normal single-cell moves.
        pieces = [e for e in elements if e.get("role") == "controlled_piece"]
        if not pieces:
            self.last_move_desc = ""
            self.move_hold_ms_acc = 0
            self.move_hold_ms_direction = None
            self.pending_click_or_drag_confirm = False  # can't confirm without a piece reading -- don't leave it hanging for a later, unrelated turn to resolve
            self.pending_direction = None
            return
        # Corrected to a stable world-reference frame using the
        # accumulated scroll offset (see _detect_scroll_shift) -- using
        # the raw reported screen position here would conflate genuine
        # character movement with camera panning, contaminating every
        # distance-based measurement below (hold_ms rate calibration,
        # click/drag confirmation, stuck detection) whenever scrolling and
        # real movement happen in the same turn, on top of corrupting
        # visited_positions/direction_known (all of which need a
        # stable world position, not a moving screen-relative one).
        # Prefer y2 (bottom/ground-contact edge, see AGENT_RESPONSE_SCHEMA
        # and SYSTEM_PROMPT) over y (top edge) when present -- the base is
        # the point that actually determines wall/gap collision, whereas a
        # tall sprite's top edge can sit a full cell-height above where it
        # really touches floor. Falls back to "y" for older-style readings
        # that omit y2.
        raw_pos = (pieces[0].get("x", 0), pieces[0].get("y2", pieces[0].get("y", 0)))
        pos = (raw_pos[0] - self.camera_shift_x, raw_pos[1] - self.camera_shift_y)
        hold_ms = self.move_hold_ms_acc
        if self.last_piece_pos is not None:
            dist = ((pos[0] - self.last_piece_pos[0]) ** 2 + (pos[1] - self.last_piece_pos[1]) ** 2) ** 0.5
            if dist < 1:
                self.consecutive_identical_position += 1
            else:
                self.consecutive_identical_position = 0
            if self.pending_click_or_drag_confirm:
                if dist > CLICK_DRAG_CONFIRM_MIN_DIST:
                    self.ever_used_click_or_drag = True
                    self.failed_click_drag_attempts = 0
                self.pending_click_or_drag_confirm = False
            if self.pending_direction is not None:
                # Ground truth: walls are static, so a real attempted move
                # from an exact cell either did or didn't produce movement
                # -- unlike the model's own open_sides self-report (a fresh
                # visual guess every turn), this can't be inconsistent with
                # itself, confirmed live as a real problem: the identical
                # position (489,500) got open_sides "none" on one visit and
                # "bottom,right" on a later visit in the same session --
                # the walls didn't move, only the guess did. Only record a
                # confirmed-blocked result once a reasonably long hold was
                # tried (a too-short tap failing doesn't prove the
                # direction is blocked, just that the tap was too short) AND
                # it's happened DIRECTION_BLOCKED_MIN_FAILS times in a row --
                # a single qualifying no-movement reading can still be a
                # noisy position misread (confirmed live, see that
                # constant's docstring), so one alone isn't trusted to
                # overwrite an existing True or establish a fresh False.
                from_bucket, direction, pending_hold_ms = self.pending_direction
                key = (from_bucket, direction)
                moved = dist > DIRECTION_MOVED_MIN_DIST
                if moved:
                    self.direction_known[key] = True
                    self.direction_fail_counts[key] = 0
                    self._sample_direction_color(raw_pos, direction, True)
                elif pending_hold_ms >= DIRECTION_BLOCKED_MIN_HOLD_MS:
                    fails = self.direction_fail_counts.get(key, 0) + 1
                    self.direction_fail_counts[key] = fails
                    # Only 1 fail required once self.bucket_units has been
                    # recalibrated from real measured movement (see
                    # bucket_units_calibrated) instead of DIRECTION_BLOCKED_MIN_FAILS's
                    # fixed default of 2 -- that default was a hedge against
                    # noise caused specifically by an inaccurate GUESSED
                    # bucket size aliasing two different real cells together
                    # (see BUCKET_UNITS's docstring); once the bucket size
                    # is derived from this session's own measured cell
                    # pitch instead of a guess, a single qualifying failure
                    # is trustworthy on its own, and requiring a second is
                    # just paying an extra stationary turn for safety
                    # margin that's no longer needed. Confirmed live this
                    # matters: in a 373-turn session, only 2 of 373
                    # transitions were the model re-picking an
                    # already-confirmed-blocked direction -- the rest of
                    # the session's stationary turns were mostly this
                    # structural double-confirmation cost.
                    required_fails = 1 if self.bucket_units_calibrated else DIRECTION_BLOCKED_MIN_FAILS
                    if fails >= required_fails:
                        self.direction_known[key] = False
                        self._sample_direction_color(raw_pos, direction, False)
                self.pending_direction = None
            # hold_ms is only truthy here when the intervening action(s) were
            # all key-based (see move_hold_ms_acc's invalidation to None on
            # click/drag) -- a real displacement attributable to that is
            # positive proof keyboard control works in this game. Required
            # before the click/drag hard-block below is allowed to trigger:
            # without this gate, a genuinely drag-only puzzle game where the
            # model's first 3 drags all happened to miss their target would
            # get click/drag permanently disabled and forced onto keys that
            # were never confirmed to do anything either, deadlocking the
            # rest of the session with no working input method at all.
            if hold_ms and dist > DIRECTION_MOVED_MIN_DIST:
                self.ever_confirmed_key_movement = True
            # max_move_distance (raw, separate from the rate window below)
            # still feeds _check_position_stuck's "have you gone anywhere
            # at all" gate -- that check only needs a rough sense of
            # scale, not a precise single-cell distance, so it's untouched
            # by the rate-normalization fix below (which specifically
            # targets the "hold longer" advice feedback loop, not this one).
            if dist > self.max_move_distance:
                self.max_move_distance = dist
            # Also require a single, unmixed direction across everything
            # accumulated since the last call (see move_hold_ms_direction /
            # _accumulate_move_hold): net displacement over a window that
            # mixed directions (e.g. an "up" success then a "left" success
            # before the next real read) isn't the sum of the individual
            # hops, so dist/hold_ms would misrepresent the rate of either
            # one. Confirmed live as a real, severe bug together with the
            # failed-attempt dilution this was fixed alongside: one
            # session's bucket_units calibrated all the way down to
            # CELL_SIZE_MIN's 20-unit floor even after excluding failed
            # attempts, because the DFS-direct fast path often succeeds in
            # more than one direction within a single window.
            if dist > 3 and hold_ms and self.move_hold_ms_direction != "mixed":  # ignore sub-pixel jitter; skip turns we can't attribute to a known hold duration (click/drag, or a heuristic escape spree)
                rate = dist / hold_ms
                is_partial = False
                if len(self.move_rate_window) >= MOVE_RATE_MIN_SAMPLES:
                    median_rate = statistics.median(self.move_rate_window)
                    frac = rate / median_rate if median_rate else 1.0
                    if frac < MOVE_RATE_PARTIAL_FRAC:
                        is_partial = True
                        self.last_move_desc = (
                            f"Note: your controlled_piece covered only about {round(frac * 100)}% as much "
                            "distance per millisecond held as your typical recent move -- if this is a "
                            "grid/tile-based game, that can mean this move got cut short (e.g. hit a wall or "
                            "misaligned edge partway through), not that the hold itself was too short. Don't "
                            "assume you need to hold longer next time purely from this signal -- check "
                            "whether the direction you tried is actually open before repeating it."
                        )
                    else:
                        self.last_move_desc = ""
                else:
                    self.last_move_desc = ""
                # Don't let a move already flagged as likely cut-short (see
                # is_partial above) feed back into the very median it was
                # measured against -- letting a partial move do that would
                # let a run of wall-clipped attempts drag the median down
                # turn by turn, undermining the one thing (dist/hold_ms
                # calibration) this whole rolling window exists to protect.
                # Can't protect the FIRST MOVE_RATE_MIN_SAMPLES samples this
                # way (no median exists yet to compare against) -- that's
                # what raising MOVE_RATE_MIN_SAMPLES itself is for.
                if not is_partial:
                    self.move_rate_window.append(rate)
                    self.move_rate_window = self.move_rate_window[-MOVE_RATE_WINDOW:]
                if not self.bucket_units_calibrated and len(self.move_rate_window) >= MOVE_RATE_MIN_SAMPLES:
                    # One-time recalibration from real measured movement
                    # (see CELL_SIZE_REFERENCE_HOLD_MS's docstring) instead
                    # of the fixed BUCKET_UNITS guess. Existing
                    # position-bucket-keyed ground truth (direction_known,
                    # its fail counts, visited_positions) was collected
                    # under the OLD bucket size, so a raw position that
                    # used to land in one bucket may now split across two,
                    # or vice versa -- keeping those stale keys around
                    # would silently mix data from two different
                    # granularities. Clearing them is a one-time, early-
                    # session cost (at most MOVE_RATE_MIN_SAMPLES real
                    # moves' worth of ground truth) in exchange for a
                    # permanently more accurate bucket size for the rest of
                    # what's typically a session of hundreds of turns.
                    calibrated = max(CELL_SIZE_MIN, min(statistics.median(self.move_rate_window) * CELL_SIZE_REFERENCE_HOLD_MS, CELL_SIZE_MAX))
                    self.logger.log_event(
                        "bucket_size_calibrated",
                        {"old_bucket_units": self.bucket_units, "new_bucket_units": round(calibrated, 1)},
                    )
                    self.bucket_units = calibrated
                    self.bucket_units_calibrated = True
                    self.direction_known = {}
                    self.direction_fail_counts = {}
                    self.visited_positions = set()
            else:
                self.last_move_desc = ""
        self.last_piece_pos = pos
        self.move_hold_ms_acc = 0
        self.move_hold_ms_direction = None
        self.piece_pos_history.append(pos)
        self.piece_pos_history = self.piece_pos_history[-POSITION_STUCK_WINDOW:]
        cur_bucket = self._pos_bucket(pos)
        self.visited_positions.add(cur_bucket)

    def _check_degenerate_analysis(self):
        # Confirmed live as a real failure mode, not hypothetical: once the
        # only currently-visible coin was collected, the model reported an
        # "elements" list containing only the controlled_piece (no
        # target_or_goal, no anything else) at an unchanged position for
        # 100+ consecutive real turns -- it stopped actively searching for
        # the session's other, not-yet-discovered goals rather than
        # continuing to explore for them. See DEGENERATE_ANALYSIS_*_THRESHOLD.
        if self.consecutive_minimal_elements < DEGENERATE_ANALYSIS_WARN_THRESHOLD:
            return None
        return (
            f"DEGENERATE ANALYSIS WARNING: your last {self.consecutive_minimal_elements} turns each "
            "reported almost nothing (just the controlled_piece, no target_or_goal/usable_object/"
            "obstacle/anything else), often without your position even changing. Once a previously-"
            "visible goal is gone, don't just report a minimal/empty scene and coast -- actively search "
            "the CURRENT screenshot for whatever else is there: a new area, a different collectible or "
            "exit, a path you haven't tried yet. A screen staying this featureless for this many turns "
            "in a row is far more likely a sign you're not carefully re-examining the actual current "
            "image than a sign there's genuinely nothing left to find."
        )

    def _check_position_stuck(self):
        # The turn_log-based stuck-cycle check (_check_stuck_cycle) exempts
        # "key" actions from its primary same-action-repeated rule, because
        # legitimately repeating the same movement key many times in a row
        # is normal for real-time genres -- but that exemption also blinds
        # it to genuine stuck-key-mashing, confirmed live: an agent cycled
        # through all 4 directions at the same maze corner for 15+ turns
        # (varying the action each time, so the same-action rule never
        # applied) with zero escapes firing, because the secondary
        # zero-change check also never fires -- a partial/misaligned move
        # still visibly changes pixels even when it isn't real progress.
        # This checks ground truth instead: the controlled_piece's own
        # tracked position (already collected every turn). If it hasn't
        # net-moved much over the last several turns despite repeated
        # attempts, that's stuck regardless of which specific actions were
        # tried or whether each one nudged some pixels.
        if len(self.piece_pos_history) < POSITION_STUCK_WINDOW or self.max_move_distance <= 0:
            return None
        oldest, newest = self.piece_pos_history[0], self.piece_pos_history[-1]
        net = ((newest[0] - oldest[0]) ** 2 + (newest[1] - oldest[1]) ** 2) ** 0.5
        if net < self.max_move_distance * 0.4:
            return (
                f"STUCK WARNING: your controlled_piece has net-moved only about "
                f"{round(net)} normalized units over your last {POSITION_STUCK_WINDOW} turns, versus a "
                f"largest single move this session of {round(self.max_move_distance)} -- despite trying "
                "several actions, you are not making real progress from this spot. Stop repeating actions "
                "that clearly aren't working and pick a genuinely different approach. If this is a "
                "grid/maze game: if you're in a small pocket with only one way in, that's likely a dead "
                "end -- go back out and try a completely different branch of the maze/path instead, and "
                "commit to that backtrack fully. A single tap not moving you doesn't confirm a direction is "
                "blocked -- try it once more from the same spot before concluding that. Don't let the goal's "
                "on-screen direction pull you back toward a direction that's failed twice in a row "
                "from this same spot -- once that's happened, keep moving away from this pocket via a "
                "different direction, even if it's temporarily away from "
                "the goal, until you reach a cell with a genuinely untried opening. If this ISN'T a "
                "grid/maze game, this pattern more likely means the wrong element, wrong interaction type "
                "(click vs key vs drag), or a missing usable_object step -- reconsider those instead of "
                "continuing to nudge the same stuck position."
            )
        return None

    def _visited_direction_hint(self):
        # Surfaced every turn, not just during a stuck escalation -- the
        # goal is genuine per-turn exploration awareness (what the user
        # asked for: real coverage tracking, not just reacting to whatever
        # the current screenshot shows in isolation), not only a last-
        # resort nudge once things have already gone wrong. Purely
        # position-history-based (see visited_positions / _pos_bucket), so
        # it applies to any genre with a trackable controlled_piece
        # position -- no grid/maze-specific assumption baked in. "Visited"
        # is deliberately NOT phrased as "blocked", and "not yet visited"
        # is NOT phrased as "open" -- this is a coverage signal, not a
        # wall-detection claim; conflating the two would be actively wrong
        # advice as soon as a genuine through-corridor gets revisited.
        if self.last_piece_pos is None or not self.visited_positions:
            return ""
        bx, by = self._pos_bucket(self.last_piece_pos)
        deltas = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
        parts = [
            f"{d}={'already visited' if (bx + dx, by + dy) in self.visited_positions else 'not yet visited'}"
            for d, (dx, dy) in deltas.items()
        ]
        # Also surface the broader region picture, not just the single next
        # cell: confirmed live that checking only the immediate neighbor
        # isn't enough to prevent looping inside a small, fully-explored
        # local pocket once every adjacent cell has been visited at least
        # once -- once that happens, "not yet visited" stops discriminating
        # between directions at all. A rough per-direction count of how
        # much of your visited history lies further up/down/left/right
        # gives a "which broad direction is less explored overall" signal
        # even in that situation.
        region_counts = {"up": 0, "down": 0, "left": 0, "right": 0}
        for vx, vy in self.visited_positions:
            if vy < by:
                region_counts["up"] += 1
            elif vy > by:
                region_counts["down"] += 1
            if vx < bx:
                region_counts["left"] += 1
            elif vx > bx:
                region_counts["right"] += 1
        region_parts = [f"{d}={region_counts[d]} visited cells" for d in ("up", "down", "left", "right")]
        return (
            "Exploration coverage from your own movement history this session (NOT a wall/collision "
            "reading -- 'already visited' just means you've been in roughly that area before, not that "
            "it's blocked, and 'not yet visited' doesn't guarantee it's open): " + ", ".join(parts) + ". "
            "All else being equal, prefer a 'not yet visited' direction to genuinely extend coverage rather "
            "than re-treading the same area. If every immediate direction is already visited (you're "
            "circling inside an already-explored pocket), use the broader picture instead: "
            + ", ".join(region_parts) + " -- head toward whichever broad direction has fewer visited cells "
            "overall, since that's more likely to hold unexplored territory even if the very next cell "
            "isn't new."
        )

    def _direction_ground_truth_hint(self):
        # Distinct from _visited_direction_hint above: that one is a soft
        # coverage preference derived from where the piece has BEEN;
        # this one is a hard, code-verified fact derived from what
        # actually happened when a direction was PRESSED from this exact
        # spot (see direction_known / DIRECTION_BLOCKED_MIN_HOLD_MS).
        # Deliberately told to override a fresh visual guess: confirmed
        # live that the model's own open_sides read of the identical
        # position (489,500) disagreed with itself between two visits
        # ("none" vs "bottom,right") -- the walls didn't move, so that
        # disagreement can only be a misread, not a real change. A result
        # recorded here came from an actual attempted move, which can't
        # be wrong the way a visual guess can.
        if self.last_piece_pos is None:
            return ""
        bucket = self._pos_bucket(self.last_piece_pos)
        parts = []
        for d in ("up", "down", "left", "right"):
            known = self.direction_known.get((bucket, d))
            if known is True:
                parts.append(f"{d}=confirmed OPEN (you actually moved this way from this exact spot before)")
            elif known is False:
                parts.append(f"{d}=confirmed BLOCKED (real attempts this way from this exact spot produced no movement, more than once)")
        if not parts:
            return ""
        return (
            "Ground truth from actual attempted moves at your exact current position (not a fresh visual "
            "guess): " + "; ".join(parts) + ". Trust this over your own current visual read of wall "
            "openness if they disagree -- walls don't move, so a confirmed result here stays true no "
            "matter how this screenshot looks right now. Don't re-attempt a confirmed BLOCKED direction "
            "expecting a different result -- repeated real attempts already ruled it out from this exact spot."
        )

    def _bfs_route_to_goal(self, start_bucket, goal_bucket):
        # direction_known is already, without ever being framed as one, a
        # real graph of this session's explored geometry: every True entry
        # is a confirmed edge between two adjacent cells. This queries it as
        # one via plain BFS (unweighted grid, so shortest-hop-count IS
        # shortest path) instead of leaving that connectivity implicit and
        # unused. Edges are treated as bidirectional -- if a press from A
        # confirmed A->B is open, this assumes B->A is too, the same
        # reversibility assumption _dfs_next_direction's own backtracking
        # already relies on for a cardinal-grid game; there's no cheaper way
        # to get the reverse confirmed without a wasted extra attempt.
        if start_bucket == goal_bucket:
            return []
        deltas = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
        reverse_dir = {"up": "down", "down": "up", "left": "right", "right": "left"}
        edges = {}
        for (bucket, direction), known in self.direction_known.items():
            if known is not True:
                continue
            dx, dy = deltas[direction]
            neighbor = (bucket[0] + dx, bucket[1] + dy)
            edges.setdefault(bucket, []).append((direction, neighbor))
            # Mirror edge assumes the reverse direction is equally open from
            # the neighbor's side -- geometrically reasonable, but only a
            # guess when the neighbor bucket has never been independently
            # tested from ITS side. Skip the mirror when that bucket's OWN
            # ground truth for the reverse direction explicitly disagrees
            # (recorded False) -- that's a real, separately-confirmed
            # attempt and takes priority over an assumption. Confirmed live
            # as a severe, real bug without this check: a bucket's "down"
            # was directly confirmed blocked 70+ times (direction_known
            # correctly False, matching the real maze wall), yet its
            # mirrored "up" edge from a neighboring bucket kept injecting a
            # phantom "down" route back INTO the very cell that had already
            # disproved it, so DFS recommended the same confirmed-blocked
            # direction forever regardless of what the model itself guessed
            # each turn.
            if self.direction_known.get((neighbor, reverse_dir[direction])) is not False:
                edges.setdefault(neighbor, []).append((reverse_dir[direction], bucket))
        if start_bucket not in edges:
            return None
        visited = {start_bucket}
        queue = deque([(start_bucket, [])])
        while queue:
            cur, path = queue.popleft()
            for direction, neighbor in edges.get(cur, []):
                if neighbor in visited:
                    continue
                if neighbor == goal_bucket:
                    return path + [direction]
                visited.add(neighbor)
                queue.append((neighbor, path + [direction]))
        return None

    def _known_route_hint(self):
        # Genre-agnostic detour capability, directly answering "if it can't
        # remember its movement history, at least have it build a map while
        # wandering": a model reasoning turn-by-turn off a single screenshot
        # has no way to know a working detour already exists a few turns
        # back in its own explored history when the direct line to a visible
        # target_or_goal is blocked -- this surfaces a real, ground-truth
        # verified route (via _bfs_route_to_goal) instead of leaving the
        # model to rediscover the same wall by repeatedly bumping into it.
        self.known_route_directions = None
        if self.last_piece_pos is None or not self.last_elements:
            return ""
        goals = [e for e in self.last_elements if e.get("role") == "target_or_goal"]
        if not goals:
            return ""
        start_bucket = self._pos_bucket(self.last_piece_pos)
        # Prefer the canvas-stabilized position (see _update_goal_tracking)
        # over the model's raw per-turn report -- confirmed live to be far
        # more reliable for a real, unmoving goal. Falls back to the raw
        # report only when no stabilized track exists yet (e.g. this is the
        # very first sighting, before a template has even been captured).
        stabilized = self._stabilized_goal_world_pos(self.current_w, self.current_h) if self.current_w and self.current_h else None
        if stabilized is not None:
            goal_bucket = self._pos_bucket(stabilized)
        else:
            g = goals[0]
            goal_bucket = self._pos_bucket((g.get("x", 0) - self.camera_shift_x, g.get("y", 0) - self.camera_shift_y))
        route = self._bfs_route_to_goal(start_bucket, goal_bucket)
        if not route:
            return ""
        self.known_route_directions = route
        if len(route) == 1:
            return (
                f"Known route from your own explored history: pressing '{route[0]}' is a confirmed-open "
                "move that leads toward this target_or_goal."
            )
        preview = ", ".join(route[:6])
        more = f", plus {len(route) - 6} more steps" if len(route) > 6 else ""
        return (
            f"Known route from your own explored history: a {len(route)}-step path of already-confirmed-open "
            f"moves leads to this target_or_goal -- {preview}{more}. If the direction you're about to try "
            "keeps failing, this is a verified way to actually get there, not another guess -- consider "
            "following it instead, especially if you're detouring around a wall."
        )

    def _track_goal_consistency(self, elements):
        # World-corrected for the same reason as _track_piece_movement:
        # a goal's on-screen position legitimately shifts every turn the
        # camera pans even though it hasn't moved in the world, which
        # would otherwise look identical to the actual mislabeling problem
        # this check exists to catch (see _check_goal_consistency). The
        # first reported goal uses the canvas-stabilized position when one
        # exists (see _update_goal_tracking/_stabilized_goal_world_pos) --
        # any additional goals still use their raw per-turn report, since
        # only one template is tracked at a time.
        goals = [e for e in elements if e.get("role") == "target_or_goal"]
        positions = set()
        stabilized = self._stabilized_goal_world_pos(self.current_w, self.current_h) if self.current_w and self.current_h else None
        for i, g in enumerate(goals):
            if i == 0 and stabilized is not None:
                positions.add(self._pos_bucket(stabilized))
            else:
                positions.add(self._pos_bucket((g.get("x", 0) - self.camera_shift_x, g.get("y", 0) - self.camera_shift_y)))
        self.goal_pos_history.append(positions)
        self.goal_pos_history = self.goal_pos_history[-GOAL_STUCK_WINDOW:]

    def _check_goal_consistency(self):
        # Mirrors _check_position_stuck's approach (ground-truth tracking
        # over a rolling window instead of trusting per-turn self-reports in
        # isolation), applied to identification reliability instead of
        # movement. Only counts turns where a target_or_goal was actually
        # reported -- a turn where it simply wasn't visible/mentioned isn't
        # evidence of scatter by itself.
        sightings = [p for p in self.goal_pos_history if p]
        if len(sightings) < GOAL_STUCK_WINDOW:
            return None
        all_positions = set()
        for p in sightings:
            all_positions |= p
        if len(all_positions) >= GOAL_MIN_DISTINCT_FOR_WARNING:
            coords = ", ".join(f"({x * BUCKET_UNITS},{y * BUCKET_UNITS})" for x, y in sorted(all_positions))
            return (
                "GOAL IDENTIFICATION WARNING: the position you've reported for the target_or_goal has "
                f"landed in {len(all_positions)} clearly different spots over your last "
                f"{GOAL_STUCK_WINDOW} sightings of it ({coords}), instead of staying in one consistent "
                "place. A real, fixed goal/collectible should be at essentially the same spot turn after "
                "turn (unless the whole view is panning, which would shift everything together, not just "
                "the goal) -- this much scatter strongly suggests you're latching onto a different-looking "
                "object each time (e.g. confusing it with a decorative mark, trail, or afterimage left "
                "elsewhere on the map) rather than tracking one real target. Before acting on this position "
                "again, look carefully and compare against what you actually saw a turn or two ago: is this "
                "really the same object, in the same place? If you can't be confident, label it "
                "'decorative' or 'unknown' instead of 'target_or_goal' until you find one that holds still."
            )
        return None

    def _untried_usable_objects(self, w, h):
        # Sibling to _untried_piece_target_combos, for the other kind of
        # "new thing to try": clicking a declared usable_object (a tool/
        # switch/item that's part of the puzzle mechanic, distinct from
        # plain ui_control chrome) that hasn't been clicked yet this
        # session. Added because the piece-to-target combo space alone
        # can't represent "activate this switch first" -- confirmed as a
        # real gap: the model was only ever considering moving the
        # controlled_piece directly to the goal, with no explicit slot in
        # its own reasoning for "use something else as a required step".
        untried = []
        for e in self.last_elements:
            if e.get("role") != "usable_object":
                continue
            bucket = self._element_bucket(e, w, h)
            if ("click", bucket) not in self.tried_combos:
                untried.append({"target": e, "t_bucket": bucket})
        return untried

    def _record_combo_tried(self, action, w, h, x=None, y=None, x_end=None, y_end=None):
        if action == "drag" and x is not None and x_end is not None:
            src = (self._bucket(x, w), self._bucket(y, h))
            dst = (self._bucket(x_end, w), self._bucket(y_end, h))
            self.tried_combos.add(("drag", src, dst))
        elif action == "click" and x is not None:
            self.tried_combos.add(("click", (self._bucket(x, w), self._bucket(y, h))))

    def _pick_escape_candidate(self, frame_bgr, w, h, tried_buckets):
        # Forced code-level escape from a detected stuck cycle, rather than
        # only handing the model a stronger warning and hoping it complies.
        # Precedent for why this matters more than better wording: the
        # chevron-panel case (see _probe_chevron) showed gemini-flash-lite-
        # latest ignoring an explicit, twice-strengthened instruction and
        # repeating the same wrong action anyway. vision.detect_candidates
        # is the existing generic (non-game-specific) clickable-element
        # detector already used elsewhere in this project; reusing it here
        # picks a real UI element the stuck window hasn't already tried,
        # preferring whichever is farthest (in bucket-distance) from every
        # recently-tried target so the escape is a genuinely different probe
        # rather than a near-miss retry of the same spot.
        candidates = vision.detect_candidates(frame_bgr)
        best, best_dist = None, -1
        for c in candidates:
            bucket = (self._bucket(c["cx"], w), self._bucket(c["cy"], h))
            if bucket in tried_buckets:
                continue
            min_dist = (
                min((bucket[0] - tb[0]) ** 2 + (bucket[1] - tb[1]) ** 2 for tb in tried_buckets)
                if tried_buckets else 0
            )
            if min_dist > best_dist:
                best_dist, best = min_dist, c
        return best

    def _dfs_next_direction(self):
        # Genre-agnostic exploration over position buckets (no notion of
        # "walls" -- purely position buckets and per-direction
        # tried/blocked state, exactly as scoped as the rest of this
        # position-tracking machinery: inert wherever there's no trackable
        # cardinal-direction movement). Confirmed live as necessary, not a
        # style choice: a diagnostic run with heuristic escapes fully
        # disabled (pure per-turn LLM direction choices) clustered in one
        # bucket 84% of the time -- the model's own turn-to-turn spatial
        # judgment is measurably less reliable than deterministic
        # backtracking here.
        #
        # Algorithm: if the current bucket has any direction never yet
        # attempted (per direction_known), try that. Otherwise, BFS over
        # the graph of CONFIRMED-OPEN edges (direction_known is True) to
        # find the nearest bucket that still has an untried direction, and
        # take the first step of that route.
        #
        # This replaced an earlier version that only ever retreated one
        # geometric step toward an explicit backtracking stack's immediate
        # parent once the current cell's own four directions were all
        # individually resolved. Confirmed live via an isolated,
        # zero-API-cost test against synthetic mazes (no vision/LLM
        # involved at all) that this was a real, severe bug, not just slow
        # convergence: on a trivial 5x5 maze (25 cells) it covered only
        # 3 cells even given 20,000 steps, because it could permanently
        # oscillate between two cells once BOTH had all four directions
        # resolved, never noticing that a THIRD cell reachable through one
        # of them still had an untried edge -- retreating toward "the
        # parent" is not the same question as "is there unexplored
        # territory reachable from here at all", and conflating them is
        # what got it stuck. BFS to the nearest still-untried bucket has no
        # such blind spot: it always finds the closest place still worth
        # going, via any already-confirmed route, regardless of how many
        # hops or which direction that is, and naturally handles loops
        # (not just tree-shaped mazes) since it's a real graph search
        # rather than a single linear parent chain.
        if self.last_piece_pos is None:
            return None
        directions = ("up", "down", "left", "right")
        deltas = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
        reverse_dir = {"up": "down", "down": "up", "left": "right", "right": "left"}
        cur = self._pos_bucket(self.last_piece_pos)

        def untried_directions(bucket):
            return [d for d in directions if (bucket, d) not in self.direction_known]

        untried = untried_directions(cur)
        if untried:
            # Among untried directions, prefer one the self-calibrated
            # color model predicts is open (see _predict_direction_open) --
            # a genuine "look before you leap" that reduces the bump-to-
            # discover cost this otherwise always pays for a brand new
            # cell. Still just a preference, not a requirement: if the
            # color model abstains (not enough data yet, or this skin's
            # floor/wall colors are too close to trust) or every untried
            # direction reads as "probably blocked", fall through to trying
            # the first untried one the old way -- an actual attempt is
            # still the only way to get real ground truth.
            #
            # A same-axis "this direction was blocked at several other
            # buckets along this row/column, so deprioritize it here too"
            # heuristic was tried and reverted: live data showed it
            # suppressing "right" to 22 attempts vs "up"'s 63 in one
            # session, even though right succeeded 59% of the time when
            # actually tried -- real maze walls are mostly-but-not-fully
            # solid (gaps at specific cells), so a direction being blocked
            # at OTHER buckets along the same line says nothing reliable
            # about whether it's open at THIS one, and deprioritizing it
            # anyway measurably suppressed exploration into open territory
            # instead of just saving a redundant probe.
            color_open = [d for d in untried if self._predict_direction_open(cur, d) is True]
            if color_open:
                return color_open[0]
            # No color signal either -- among untried directions, prefer
            # whichever AXIS (vertical up/down vs horizontal left/right) has
            # accumulated FEWER confirmed-open moves this session so far,
            # instead of the fixed up>down>left>right order. Confirmed live
            # as a real, separate imbalance from the reverted heuristic
            # above: the fixed order means that whenever "up" keeps
            # succeeding along a long vertical run, the agent rides it the
            # whole way without ever checking left/right at any intermediate
            # cell -- sideways only gets tried once a cell's up AND down
            # both fail -- which measurably skewed one session to 82 "up"
            # attempts vs 24 "right", even though right succeeded 71% of
            # the time when actually tried. This still only reorders untried
            # candidates -- every direction is still actually attempted
            # before being trusted, so it can't produce a wrong ground-truth
            # entry, only better balance about which axis gets checked first
            # at a fresh cell. Unlike the reverted heuristic, this makes no
            # claim about any SPECIFIC direction's odds -- it only tracks
            # overall axis balance, so it can't suppress a genuine gap the
            # way deprioritizing a specific repeatedly-blocked direction did.
            vertical_moves = sum(1 for (b, d), v in self.direction_known.items() if v is True and d in ("up", "down"))
            horizontal_moves = sum(1 for (b, d), v in self.direction_known.items() if v is True and d in ("left", "right"))
            prefer_horizontal = horizontal_moves < vertical_moves

            def axis_rank(d):
                return 0 if (d in ("left", "right")) == prefer_horizontal else 1

            return sorted(untried, key=axis_rank)[0]

        edges = {}
        for (bucket, direction), known in self.direction_known.items():
            if known is not True:
                continue
            dx, dy = deltas[direction]
            neighbor = (bucket[0] + dx, bucket[1] + dy)
            edges.setdefault(bucket, []).append((direction, neighbor))
            # Mirror edge assumes the reverse direction is equally open from
            # the neighbor's side -- geometrically reasonable, but only a
            # guess when the neighbor bucket has never been independently
            # tested from ITS side. Skip the mirror when that bucket's OWN
            # ground truth for the reverse direction explicitly disagrees
            # (recorded False) -- that's a real, separately-confirmed
            # attempt and takes priority over an assumption. Confirmed live
            # as a severe, real bug without this check: a bucket's "down"
            # was directly confirmed blocked 70+ times (direction_known
            # correctly False, matching the real maze wall), yet its
            # mirrored "up" edge from a neighboring bucket kept injecting a
            # phantom "down" route back INTO the very cell that had already
            # disproved it, so DFS recommended the same confirmed-blocked
            # direction forever regardless of what the model itself guessed
            # each turn.
            if self.direction_known.get((neighbor, reverse_dir[direction])) is not False:
                edges.setdefault(neighbor, []).append((reverse_dir[direction], bucket))
        visited = {cur}
        queue = deque([(cur, None)])
        while queue:
            node, first_step = queue.popleft()
            if node != cur and untried_directions(node):
                return first_step
            for direction, neighbor in edges.get(node, []):
                if neighbor in visited:
                    continue
                visited.add(neighbor)
                queue.append((neighbor, first_step if first_step is not None else direction))
        return None  # every bucket reachable via confirmed-open edges has been fully resolved

    def _pick_untried_direction(self):
        dfs_direction = self._dfs_next_direction()
        if dfs_direction is not None:
            return dfs_direction
        # Fallback layers below, for when the DFS stack has no opinion yet
        # (e.g. very first turns of a session, before any bucket has been
        # established). Three layers, most-trusted first:
        # 1. Never choose a direction ground-truth-confirmed BLOCKED from
        #    this exact spot (see direction_known) unless literally every
        #    direction is -- retrying a direction a real sustained press
        #    already disproved is a pure wasted turn, and this is the one
        #    signal in this method immune to vision misreads (it's the
        #    outcome of an actual attempted move, not a screenshot guess).
        # 2. Among what's left, prefer a direction whose neighboring bucket
        #    (per visited_positions) hasn't been visited yet, over just "not
        #    pressed recently" -- confirmed as a real gap: "not pressed
        #    recently" can still walk the piece straight back into a cell it
        #    already fully explored a while ago (long enough to fall out of
        #    the STUCK_WINDOW-sized recent-keys check), inside a small
        #    pocket where only 2-3 directions are physically possible.
        # 3. Recency-based fallback (the original design) when neither of
        #    the above has an opinion yet (e.g. very early in a session).
        # All of this is purely position-history-based -- genre-agnostic,
        # no grid/maze-specific assumption baked in.
        directions = ("up", "down", "left", "right")
        bucket = self._pos_bucket(self.last_piece_pos) if self.last_piece_pos is not None else None
        blocked = {d for d in directions if bucket is not None and self.direction_known.get((bucket, d)) is False}
        candidates = [d for d in directions if d not in blocked] or list(directions)

        if bucket is not None and self.visited_positions:
            deltas = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
            unvisited = [
                d for d in candidates
                if (bucket[0] + deltas[d][0], bucket[1] + deltas[d][1]) not in self.visited_positions
            ]
            if unvisited:
                recent_keys = {
                    entry["sig"][1] for entry in self.turn_log[-STUCK_WINDOW:]
                    if entry["sig"][0] == "key" and entry["sig"][1] in directions
                }
                return next((d for d in unvisited if d not in recent_keys), unvisited[0])

            # No immediate neighbor is fresh -- likely inside a small,
            # already well-trodden local pocket (every adjacent cell has
            # been visited at least once). Confirmed live as a real gap:
            # falling straight to plain recency here can cycle within the
            # same explored cluster indefinitely, since recency alone has
            # no sense of which broad direction actually leads toward
            # unexplored territory versus back into the same pocket.
            # Compare how many visited_positions fall in each cardinal
            # half-plane relative to the current bucket, and prefer the
            # direction pointing toward the region with fewer total
            # visits -- a coarse, genre-agnostic "which way is less
            # explored overall" signal, not just "is the next cell new".
            region_counts = {"up": 0, "down": 0, "left": 0, "right": 0}
            for vx, vy in self.visited_positions:
                if vy < bucket[1]:
                    region_counts["up"] += 1
                elif vy > bucket[1]:
                    region_counts["down"] += 1
                if vx < bucket[0]:
                    region_counts["left"] += 1
                elif vx > bucket[0]:
                    region_counts["right"] += 1
            min_count = min(region_counts[d] for d in candidates)
            least_explored = [d for d in candidates if region_counts[d] == min_count]
            if len(least_explored) == 1:
                return least_explored[0]
            recent_keys = {
                entry["sig"][1] for entry in self.turn_log[-STUCK_WINDOW:]
                if entry["sig"][0] == "key" and entry["sig"][1] in directions
            }
            tied = [d for d in least_explored if d not in recent_keys]
            return (tied or least_explored)[0]

        recent_keys = [
            entry["sig"][1] for entry in self.turn_log[-STUCK_WINDOW:]
            if entry["sig"][0] == "key" and entry["sig"][1] in directions
        ]
        for d in candidates:
            if d not in recent_keys:
                return d
        # everything left was tried recently too -- fall back to the least-recently-used candidate
        for d in recent_keys:
            if d in candidates:
                return d
        return candidates[0] if candidates else None

    def _bucket(self, v, span):
        if not span:
            return 0
        return round(v / span * 1000 / BUCKET_UNITS)

    def _pos_bucket(self, pos):
        # For positions already in normalized 0-1000 space (controlled_piece,
        # target_or_goal), unlike _bucket above which also converts from
        # pixel space first. Uses self.bucket_units (see its docstring),
        # not the fixed BUCKET_UNITS constant _bucket uses -- this one
        # gets recalibrated from real measured movement once enough data
        # exists, since it's tracking actual maze cell size, not deduping
        # UI click targets.
        return (round(pos[0] / self.bucket_units), round(pos[1] / self.bucket_units))

    def _sample_direction_color(self, raw_screen_pos, direction, moved):
        # Self-calibrating color model: never hardcodes which color means
        # floor vs wall (confirmed live that guessing this from a
        # screenshot is unreliable -- one skin's "obviously a wall" color
        # turned out to be the floor). Instead, every time direction_known
        # gets a new confirmed data point from a real attempted move (see
        # caller), also sample the pixel color there and file it under
        # floor or wall accordingly. Purely a byproduct of ground truth
        # already being collected -- this adds no new attempted moves, no
        # new cost, just extracts one more signal from data already in
        # hand.
        if self.current_frame_bgr is None or not self.current_w or not self.current_h:
            return
        px = int(raw_screen_pos[0] / 1000 * self.current_w)
        py = int(raw_screen_pos[1] / 1000 * self.current_h)
        if not moved:
            deltas = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
            dx, dy = deltas.get(direction, (0, 0))
            px += dx * COLOR_SAMPLE_OFFSET_PX
            py += dy * COLOR_SAMPLE_OFFSET_PX
        if not (0 <= px < self.current_w and 0 <= py < self.current_h):
            return
        color = self.current_frame_bgr[py, px]
        sample_list = self.floor_color_samples if moved else self.wall_color_samples
        sample_list.append(tuple(int(c) for c in color))
        del sample_list[:-20]  # keep only the most recent 20 -- bounded, and self-corrects if lighting/skin shifts mid-session

    def _predict_direction_open(self, bucket, direction):
        # Returns True/False if color calibration is confident enough to
        # predict, else None (abstain -- fall back to the existing "must
        # actually try it" approach). Deliberately conservative: requires
        # both a minimum sample count AND a minimum color-space separation
        # between the floor and wall centroids (see
        # COLOR_CALIBRATION_MIN_SEPARATION's docstring for why -- some
        # skins' floor/wall tones are too close for color alone to be
        # trustworthy, and a wrong prediction here would be worse than no
        # prediction, since it skips the one thing that's never wrong: an
        # actual attempted move).
        if (
            len(self.floor_color_samples) < COLOR_CALIBRATION_MIN_SAMPLES
            or len(self.wall_color_samples) < COLOR_CALIBRATION_MIN_SAMPLES
            or self.current_frame_bgr is None
            or not self.current_w or not self.current_h
        ):
            return None
        floor_centroid = [sum(c[i] for c in self.floor_color_samples) / len(self.floor_color_samples) for i in range(3)]
        wall_centroid = [sum(c[i] for c in self.wall_color_samples) / len(self.wall_color_samples) for i in range(3)]
        separation = sum((a - b) ** 2 for a, b in zip(floor_centroid, wall_centroid)) ** 0.5
        if separation < COLOR_CALIBRATION_MIN_SEPARATION:
            return None
        if self.last_piece_pos is None:
            return None
        # last_piece_pos is world-corrected; convert back to this frame's
        # raw screen position to sample the right pixels right now.
        raw_x = self.last_piece_pos[0] + self.camera_shift_x
        raw_y = self.last_piece_pos[1] + self.camera_shift_y
        deltas = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
        dx, dy = deltas.get(direction, (0, 0))
        px = int(raw_x / 1000 * self.current_w) + dx * COLOR_SAMPLE_OFFSET_PX
        py = int(raw_y / 1000 * self.current_h) + dy * COLOR_SAMPLE_OFFSET_PX
        if not (0 <= px < self.current_w and 0 <= py < self.current_h):
            return None
        color = [int(c) for c in self.current_frame_bgr[py, px]]
        dist_floor = sum((a - b) ** 2 for a, b in zip(color, floor_centroid)) ** 0.5
        dist_wall = sum((a - b) ** 2 for a, b in zip(color, wall_centroid)) ** 0.5
        return dist_floor < dist_wall

    def _action_signature(self, data, w, h):
        action = data["action"]
        if action == "click":
            return ("click", self._bucket(data["x"], w), self._bucket(data["y"], h))
        if action == "drag":
            return (
                "drag",
                self._bucket(data["x"], w), self._bucket(data["y"], h),
                self._bucket(data["x_end"], w), self._bucket(data["y_end"], h),
            )
        if action == "key":
            return ("key", data.get("key"))
        return (action,)

    def _describe_sigs(self, sigs):
        descs = []
        for sig in sigs:
            if sig[0] == "click":
                descs.append(f"click near ({sig[1] * BUCKET_UNITS},{sig[2] * BUCKET_UNITS})")
            elif sig[0] == "drag":
                descs.append(
                    f"drag from ({sig[1] * BUCKET_UNITS},{sig[2] * BUCKET_UNITS}) to "
                    f"({sig[3] * BUCKET_UNITS},{sig[4] * BUCKET_UNITS})"
                )
            elif sig[0] == "key":
                descs.append(f"key '{sig[1]}'")
            else:
                descs.append(str(sig))
        return descs

    def _check_stuck_cycle(self):
        """Returns a warning string if the recent turn_log shows a
        repeating, ineffective action cycle, else None."""
        # Primary check: the exact same single action repeated back-to-back,
        # regardless of whether the "changed" flag says each one moved
        # pixels. Confirmed live this matters: a sprite (character, a
        # yin-yang icon) can have its own idle bob/pulse animation, so two
        # frames a few seconds apart almost always diff as "changed" even
        # when the click did nothing to the actual puzzle state -- the model
        # kept crediting its own click for what was really just idle motion
        # ("the symbol keeps cycling states") and stayed confident in a
        # dead-end hypothesis for 10+ turns. Repetition of the identical
        # target is the reliable signal here, not the noisy pixel-changed
        # flag.
        if len(self.turn_log) >= STUCK_REPEAT_COUNT:
            tail = self.turn_log[-STUCK_REPEAT_COUNT:]
            sigs = {entry["sig"] for entry in tail}
            # Repeating the identical key many times in a row is the normal,
            # expected pattern for real-time movement genres (holding/
            # tapping "right" repeatedly to walk down a corridor) -- confirmed
            # live against a maze game, where this rule firing on 2 identical
            # "right" key presses injected an irrelevant forced click into
            # otherwise-correct navigation. click/drag repetition still means
            # what it meant in the puzzle-game case this rule was built for
            # (the target itself isn't moving, so re-clicking/re-dragging the
            # same spot is far more likely a dead end); only "key" gets the
            # exemption, and the secondary (zero-change-across-window) check
            # below still catches a genuinely stuck key-masher.
            if len(sigs) == 1 and next(iter(sigs))[0] != "key":
                desc = self._describe_sigs(sigs)[0]
                return (
                    f"STUCK WARNING: your last {STUCK_REPEAT_COUNT} actions were all the exact "
                    f"same thing ({desc}). Note that some sprites/icons have their own idle "
                    "animation (a subtle bob, pulse, or rotation) that changes pixels on its own "
                    "every few seconds -- the screen looking 'changed' after your click is NOT "
                    "reliable proof that the click itself did anything; don't build confidence in "
                    "a hypothesis from that alone. This exact action has not produced real "
                    "progress -- stop retrying it and pick a genuinely different target."
                )

        # Secondary check: a short cycle (e.g. alternating between 2 spots)
        # with confirmed zero pixel change across the whole window -- the
        # unambiguous case, kept from the original design.
        if len(self.turn_log) < STUCK_WINDOW:
            return None
        window = self.turn_log[-STUCK_WINDOW:]
        if any(entry["changed"] for entry in window):
            return None
        distinct = {entry["sig"] for entry in window}
        if len(distinct) > STUCK_MAX_DISTINCT_TARGETS:
            return None
        descs = self._describe_sigs(distinct)
        return (
            f"STUCK WARNING: your last {STUCK_WINDOW} actions cycled between only "
            f"{len(distinct)} target(s) ({'; '.join(descs)}) and NONE of them changed the "
            "screen at all. This hypothesis is very likely wrong -- stop retrying it. Pick a "
            "genuinely different target you have not tried in this stuck window (a different "
            "element, a different gesture type, or a UI icon you haven't touched yet)."
        )

    def _ensure_foreground(self):
        # main.py calls capture.ensure_foreground() exactly once, at session
        # startup -- fine for a short run, but confirmed live as a real bug
        # over a longer session: SendInput (what input_controller uses) is
        # OS-global, delivered to whatever window currently holds foreground
        # focus, NOT to a specific HWND. If the target loses focus partway
        # through a session (alt-tab, a popup, anything), every subsequent
        # key/click silently goes nowhere useful while mss screen capture
        # (region-based, not focus-based) keeps right on returning the
        # target's real, now-static pixels -- so the agent sees a live
        # screenshot of a game that's no longer receiving any of its input.
        # Measured live: a 60-turn session against a maze game showed the
        # player sprite in the *exact same cell* in the first and last
        # screenshot, byte-for-byte identical but for the HUD timer and a
        # sprite idle-animation flicker, despite 54 actions and reasoning
        # that confidently narrated turn after turn of movement and
        # progress that was never actually happening. Checking (cheap) and
        # re-asserting (only when needed) every turn closes that gap.
        if win32gui.GetForegroundWindow() != self.target.hwnd:
            self.logger.log_event("foreground_lost", {"hwnd": self.target.hwnd})
            capture_mod.ensure_foreground(self.target.hwnd)

    def step(self):
        """Runs one turn. Returns True to keep going, False if the agent
        decided to stop (budget exhausted or sustained failure) -- check
        self.stop_reason for why."""
        if self.config.max_llm_calls and self.llm_call_count >= self.config.max_llm_calls:
            self.stop_reason = "max_llm_calls_reached"
            return False

        self._ensure_foreground()

        frame_bgra, rect = self.cap.grab()
        left, top, w, h = rect
        frame_bgr = cv2.cvtColor(frame_bgra, cv2.COLOR_BGRA2BGR)
        # Every turn, not periodic: agent-mode turns are already expensive
        # (one Gemini call each, ~5-15s), so the tick rate is low enough that
        # saving every frame is cheap and, unlike monkey mode, high-value --
        # this is the only visual record of what the agent actually saw and
        # acted on for QA review.
        self.logger.save_screenshot(frame_bgra, trigger="agent_turn")

        # Stashed as instance state (rather than threaded as parameters
        # through every helper) so _track_piece_movement, _dfs_next_direction,
        # and the color-calibration helpers below can all sample this
        # turn's actual pixels without a wide, error-prone signature change
        # across every call site.
        self.current_frame_bgr = frame_bgr
        self.current_w = w
        self.current_h = h

        send_frame = frame_bgr
        stuck_warning = None
        degenerate_warning = None
        shift = None
        if self.last_frame_bgr is not None:
            shift = self._detect_scroll_shift(self.last_frame_bgr, frame_bgr)
            if shift is not None:
                self.camera_shift_x += shift[0] / w * 1000
                self.camera_shift_y += shift[1] / h * 1000
                self.logger.log_event(
                    "camera_scroll",
                    {
                        "shift_px": list(shift),
                        "cumulative_shift_normalized": [round(self.camera_shift_x, 1), round(self.camera_shift_y, 1)],
                    },
                )
        self._update_world_canvas(frame_bgr, shift)
        if self.last_frame_bgr is not None:
            boxes = self._diff_regions(self.last_frame_bgr, frame_bgr)
            screen_changed = boxes is None or bool(boxes)
            if self.pending_signature is not None:
                self.turn_log.append({"sig": self.pending_signature, "changed": screen_changed})
                self.turn_log = self.turn_log[-STUCK_WINDOW:]
                stuck_warning = self._check_stuck_cycle()
            position_stuck = self._check_position_stuck()
            if position_stuck and not stuck_warning:
                stuck_warning = position_stuck
            degenerate_warning = self._check_degenerate_analysis()
            # Below DEGENERATE_ANALYSIS_FORCE_THRESHOLD: text-only nudge,
            # appended to the note later without forcing an escape --
            # still worth one real LLM attempt to self-correct. At/above
            # it: fold into stuck_warning so the same escape-tier machinery
            # (which forces a real, code-level action instead of paying
            # for another likely-still-degenerate LLM call) engages, same
            # as any other stuck condition.
            if degenerate_warning and self.consecutive_minimal_elements >= DEGENERATE_ANALYSIS_FORCE_THRESHOLD:
                stuck_warning = (stuck_warning + " " + degenerate_warning) if stuck_warning else degenerate_warning
            if stuck_warning and self.consecutive_heuristic_escapes >= MAX_CONSECUTIVE_HEURISTIC_ESCAPES:
                # Force a real LLM turn to re-anchor piece_pos_history in a
                # fresh "elements" read instead of escaping blind again.
                # Clear the stale position window too, so the stuck check
                # doesn't keep firing off pre-escape-spree data for several
                # more turns while a fresh baseline rebuilds. turn_log also
                # needs clearing here, not just piece_pos_history -- it's
                # what _check_stuck_cycle (the check that actually decides
                # whether to fire another escape) reads, and it keeps
                # accumulating "unchanged" entries from the escape attempts
                # THEMSELVES (an escape trying to break a stuck state
                # usually doesn't visibly change the screen either).
                # Confirmed live as a real, session-wide cost: a session
                # logged 64 heuristic escapes against only 29 real LLM
                # turns, because each forced real turn's stuck check kept
                # seeing a window still full of the just-finished escape
                # spree's own failed attempts and immediately re-triggered
                # another one, rather than getting a genuinely fresh window
                # to judge the forced turn's own outcome by.
                self.piece_pos_history = []
                self.turn_log = []
                stuck_warning += (
                    " (Several forced escape attempts already happened automatically without asking "
                    "you -- if you're still stuck, seriously reconsider whether this is a dead end.)"
                )
            elif stuck_warning and not self.config.disable_heuristic_escapes:
                # Prefer a semantically-grounded escape over a blind icon
                # click when we have one: drag the model's own declared
                # controlled_piece to a target_or_goal/empty_slot combo it
                # has genuinely never tried this session (see
                # _untried_piece_target_combos). This uses the "elements"
                # inventory the model is already required to produce, so the
                # forced move is a real, on-topic next hypothesis rather
                # than a generic UI probe.
                # Gated behind ever_used_click_or_drag for the same reason
                # the icon-click tier below is: confirmed live, forcing a
                # mouse drag in a pure-keyboard maze game (no click/drag
                # ever used this session) is a wasted turn -- the game
                # doesn't listen to drag at all in that genre.
                combos = self._untried_piece_target_combos(w, h) if self.ever_used_click_or_drag else []
                if combos:
                    combo = combos[0]
                    p, t = combo["piece"], combo["target"]
                    px, py = p.get("x", 0) * w // 1000, p.get("y", 0) * h // 1000
                    tx, ty = t.get("x", 0) * w // 1000, t.get("y", 0) * h // 1000
                    self.last_action_desc = (
                        f"(heuristic, no LLM call -- forced escape from a detected stuck cycle) "
                        f"dragged '{p.get('label', 'piece')}' to untried destination "
                        f"'{t.get('label', 'target')}' ({t.get('role')})"
                    )
                    self.logger.log_event(
                        "heuristic_action",
                        {
                            "action": "drag",
                            "x": left + px, "y": top + py,
                            "x2": left + tx, "y2": top + ty,
                            "coord_space": "screen",
                            "reason": "stuck_cycle_escape_untried_combo",
                        },
                    )
                    ic.drag(left + px, top + py, left + tx, top + ty)
                    self.move_hold_ms_acc = None  # drag-caused displacement isn't attributable to a held-key duration
                    self.move_hold_ms_direction = None
                    self.pending_click_or_drag_confirm = False  # any intervening escape breaks clean attribution of an earlier real turn's pending click/drag -- don't let this escape's own movement get credited to it
                    self.pending_direction = None
                    self.tried_combos.add(("drag", combo["p_bucket"], combo["t_bucket"]))
                    time.sleep(ACTION_SETTLE_S)
                    self.turn_count += 1
                    self.last_frame_bgr = frame_bgr.copy()
                    self.pending_signature = ("drag", combo["p_bucket"][0], combo["p_bucket"][1], combo["t_bucket"][0], combo["t_bucket"][1])
                    self.consecutive_heuristic_escapes += 1
                    return True
                objects = self._untried_usable_objects(w, h) if self.ever_used_click_or_drag else []
                if objects:
                    obj = objects[0]["target"]
                    ox, oy = obj.get("x", 0) * w // 1000, obj.get("y", 0) * h // 1000
                    self.last_action_desc = (
                        f"(heuristic, no LLM call -- forced escape from a detected stuck cycle) "
                        f"clicked untried usable_object '{obj.get('label', 'object')}'"
                    )
                    self.logger.log_event(
                        "heuristic_action",
                        {
                            "action": "click",
                            "x": left + ox, "y": top + oy,
                            "coord_space": "screen",
                            "reason": "stuck_cycle_escape_usable_object",
                        },
                    )
                    ic.click(left + ox, top + oy)
                    self.move_hold_ms_acc = None
                    self.move_hold_ms_direction = None
                    self.pending_click_or_drag_confirm = False
                    self.pending_direction = None
                    self.tried_combos.add(("click", objects[0]["t_bucket"]))
                    time.sleep(ACTION_SETTLE_S)
                    self.turn_count += 1
                    self.last_frame_bgr = frame_bgr.copy()
                    self.pending_signature = ("click", objects[0]["t_bucket"][0], objects[0]["t_bucket"][1])
                    self.consecutive_heuristic_escapes += 1
                    return True
                # Skip the icon-click tier entirely for a session that has
                # never once used click/drag -- confirmed live as a real
                # problem for a pure-keyboard maze game: vision.detect_candidates
                # kept matching wall-texture blobs as "clickable candidates"
                # and this tier fired before the direction-escape tier ever
                # got a chance, wasting escapes on clicks that do nothing in
                # a keyboard-only game instead of trying a new direction.
                escape = None
                if self.ever_used_click_or_drag:
                    tried_buckets = set()
                    for entry in self.turn_log[-STUCK_REPEAT_COUNT:]:
                        sig = entry["sig"]
                        if sig[0] in ("click", "drag"):
                            tried_buckets.add((sig[1], sig[2]))
                            if sig[0] == "drag":
                                tried_buckets.add((sig[3], sig[4]))
                    escape = self._pick_escape_candidate(frame_bgr, w, h, tried_buckets)
                if escape is not None:
                    nx, ny = round(escape["cx"] / w * 1000), round(escape["cy"] / h * 1000)
                    self.last_action_desc = (
                        f"(heuristic, no LLM call -- forced escape from a detected stuck cycle) "
                        f"clicked an untried element at ({nx},{ny})"
                    )
                    self.logger.log_event(
                        "heuristic_action",
                        {
                            "action": "click",
                            "x": left + escape["cx"],
                            "y": top + escape["cy"],
                            "coord_space": "screen",
                            "reason": "stuck_cycle_escape",
                        },
                    )
                    ic.click(left + escape["cx"], top + escape["cy"])
                    self.move_hold_ms_acc = None
                    self.move_hold_ms_direction = None
                    self.pending_click_or_drag_confirm = False
                    self.pending_direction = None
                    time.sleep(ACTION_SETTLE_S)
                    self.turn_count += 1
                    self.last_frame_bgr = frame_bgr.copy()
                    self.pending_signature = ("click", self._bucket(escape["cx"], w), self._bucket(escape["cy"], h))
                    self.consecutive_heuristic_escapes += 1
                    return True
                # Final tier, for pure key-driven movement genres (a maze
                # with nothing clickable in view -- the tiers above all
                # need a click/drag target, which doesn't exist here).
                # Force a held press in whichever cardinal direction was
                # used least recently, since the whole point of this
                # escape is to try something genuinely different from
                # what's been failing.
                direction = self._pick_untried_direction()
                if direction is not None:
                    # Probe-and-release (see PROBE_HOLD_MS): targets
                    # DIRECTION_BLOCKED_MIN_HOLD_MS so a genuinely blocked
                    # direction still gets held long enough to be trusted as
                    # ground truth, but releases early -- minimizing
                    # overshoot -- the moment a direction that turns out to
                    # be open shows visible movement. actual_hold_ms below
                    # is what really happened and is what every downstream
                    # use (ground-truth attribution, rate calibration,
                    # logging) must key off of.
                    actual_hold_ms, probe_moved = self._hold_key_with_probe(direction, DIRECTION_BLOCKED_MIN_HOLD_MS)
                    self.last_action_desc = (
                        f"(heuristic, no LLM call -- forced escape from a detected stuck cycle) "
                        f"held '{direction}' to try a direction not recently attempted"
                    )
                    self.logger.log_event(
                        "heuristic_action",
                        {"action": "key", "key": direction, "hold_ms": actual_hold_ms, "reason": "stuck_cycle_escape_direction"},
                    )
                    # Ground-truth attribution only for the FIRST escape in a
                    # consecutive spree: escapes don't call
                    # _track_piece_movement, so last_piece_pos stays frozen at
                    # wherever the piece was BEFORE the whole spree started --
                    # a second or third escape in the same spree would
                    # wrongly attribute its own direction to that stale
                    # starting cell instead of wherever it actually fired
                    # from. Only the first one has a last_piece_pos that's
                    # still accurate.
                    #
                    # Resolved SYNCHRONOUSLY here via probe_moved (the
                    # probe's own pixel-diff movement verdict), not deferred
                    # through pending_direction the way a real LLM turn's key
                    # press is. Confirmed live as a real, previously-
                    # unnoticed bug in the deferred version: escapes fire
                    # back-to-back with no real LLM turn in between (the only
                    # thing that ever resolved pending_direction), so
                    # escape #2's turn wiped out escape #1's still-pending
                    # attribution before anything ever consumed it --
                    # heuristic escapes were silently contributing almost
                    # nothing to direction_known. That's exactly why a wall
                    # an escape had already hit repeatedly kept getting
                    # re-suggested by DFS as "untried" turn after turn.
                    if self.consecutive_heuristic_escapes == 0 and self.last_piece_pos is not None:
                        key = (self._pos_bucket(self.last_piece_pos), direction)
                        if probe_moved:
                            self.direction_known[key] = True
                            self.direction_fail_counts[key] = 0
                        elif actual_hold_ms >= DIRECTION_BLOCKED_MIN_HOLD_MS:
                            fails = self.direction_fail_counts.get(key, 0) + 1
                            self.direction_fail_counts[key] = fails
                            required_fails = 1 if self.bucket_units_calibrated else DIRECTION_BLOCKED_MIN_FAILS
                            if fails >= required_fails:
                                self.direction_known[key] = False
                    self.pending_direction = None  # this escape breaks clean attribution of any earlier still-pending key press either way
                    # Only credit hold time that actually produced movement --
                    # see the matching fix/comment in the DFS-direct fast path
                    # below for why unconditionally adding a FAILED attempt's
                    # hold_ms here corrupts move_rate_window's calibration.
                    if probe_moved:
                        self._accumulate_move_hold(direction, actual_hold_ms)
                    # Confirmed live as a real bug: a model-chosen click that did
                    # nothing was followed by four of these direction escapes, one
                    # of which (not the click) broke the piece free -- but since
                    # pending_click_or_drag_confirm was still sitting True from the
                    # click and nothing had cleared it, the NEXT real turn's big
                    # position jump got wrongly credited to that click, permanently
                    # unlocking the click-escape tiers this fix exists to gate.
                    # Any escape between a pending click/drag and the next real
                    # reading breaks clean attribution -- invalidate it here too.
                    self.pending_click_or_drag_confirm = False
                    time.sleep(ACTION_SETTLE_S)
                    self.turn_count += 1
                    self.last_frame_bgr = frame_bgr.copy()
                    self.pending_signature = ("key", direction)
                    self.consecutive_heuristic_escapes += 1
                    return True
                # No untried on-screen candidate found -- fall through to the
                # normal LLM turn below, with stuck_warning still attached to
                # the note so the model at least has the explicit signal.
            if (
                not stuck_warning
                and not self.last_has_goal
                and self.last_piece_pos is not None
                and self.dfs_skip_streak < DFS_SKIP_MAX_STREAK
                and self.ever_confirmed_key_movement
            ):
                # Fast path: skip the real Gemini call entirely when DFS
                # already has an opinion on where to explore next (see
                # DFS_SKIP_MAX_STREAK). Never engages when stuck (the escape
                # ladder above already owns that), when a goal was visible as
                # of the last real read (has_goal, set below -- DFS itself
                # can't notice a goal, only a real vision call can), or before
                # keyboard movement has ever been confirmed to do anything
                # this session (ever_confirmed_key_movement) -- without that
                # gate this would blindly probe cardinal-direction key presses
                # even in a pure click/drag genre where they do nothing.
                dfs_direction = self._dfs_next_direction()
                if dfs_direction is not None:
                    actual_hold_ms, probe_moved = self._hold_key_with_probe(dfs_direction, DIRECTION_BLOCKED_MIN_HOLD_MS)
                    bucket = self._pos_bucket(self.last_piece_pos)
                    key = (bucket, dfs_direction)
                    if probe_moved:
                        self.direction_known[key] = True
                        self.direction_fail_counts[key] = 0
                        deltas = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
                        dx, dy = deltas[dfs_direction]
                        # Advance our own position estimate by one bucket step
                        # -- the same bucket-arithmetic the BFS graph in
                        # _dfs_next_direction already trusts for edges beyond
                        # the immediately-current one, so this isn't a new
                        # category of assumption. A real call always follows
                        # within DFS_SKIP_MAX_STREAK turns to correct drift.
                        self.last_piece_pos = (
                            self.last_piece_pos[0] + dx * self.bucket_units,
                            self.last_piece_pos[1] + dy * self.bucket_units,
                        )
                    elif actual_hold_ms >= DIRECTION_BLOCKED_MIN_HOLD_MS:
                        fails = self.direction_fail_counts.get(key, 0) + 1
                        self.direction_fail_counts[key] = fails
                        required_fails = 1 if self.bucket_units_calibrated else DIRECTION_BLOCKED_MIN_FAILS
                        if fails >= required_fails:
                            self.direction_known[key] = False
                    self.last_action_desc = f"(DFS direct, no LLM call) held '{dfs_direction}' to explore an untried direction"
                    self.logger.log_event(
                        "dfs_direct_action",
                        {"action": "key", "key": dfs_direction, "hold_ms": actual_hold_ms, "moved": probe_moved},
                    )
                    self.pending_direction = None
                    self.pending_click_or_drag_confirm = False
                    # Only credit hold time that actually produced movement.
                    # Confirmed live as a real, severe bug: unconditionally
                    # adding a FAILED attempt's hold_ms here (this fast path
                    # fires far more often than the old heuristic-escape-only
                    # source of this same mistake) diluted move_rate_window's
                    # rate = net_dist / accumulated_hold_ms -- e.g. 3 failed
                    # 125ms attempts (0 distance) plus 1 real 43-unit move
                    # gave rate = 43/500 instead of the true 43/125, roughly
                    # 4x too low. One session's bucket_units calibrated all
                    # the way down to CELL_SIZE_MIN's 20-unit floor even
                    # though real single-move distances measured live were
                    # ~40-45 units -- a bucket size finer than the real cell
                    # pitch means ordinary position-read noise can flip which
                    # bucket the SAME physical spot rounds into, so a
                    # direction already confirmed blocked there keeps
                    # reappearing as "never tried" and gets retried forever.
                    # A second, separate fix travels with this one: even
                    # crediting only successful attempts isn't enough on its
                    # own if several of them land in DIFFERENT directions
                    # within the same window (very possible now that this
                    # fast path can fire up to DFS_SKIP_MAX_STREAK times
                    # before the next real read) -- net displacement over a
                    # mixed-direction window is not the sum of the
                    # individual hops, so that rate would still be wrong.
                    # _accumulate_move_hold tracks this and marks the window
                    # "mixed" once more than one direction contributes;
                    # _track_piece_movement skips calibrating from it then.
                    if probe_moved:
                        self._accumulate_move_hold(dfs_direction, actual_hold_ms)
                    # Same settle delay every other action tier in this
                    # method uses (see ACTION_SETTLE_S) -- omitting it here
                    # was a real bug: firing several of these back-to-back
                    # with no gap gave the game no time to finish the
                    # previous move's animation/cooldown before the next
                    # key_down, so rapid-fire attempts were being silently
                    # dropped/ignored by the game and probe_moved read False
                    # even on genuinely open directions. Confirmed live: all
                    # 4 cardinal directions came back "moved": false from the
                    # same spot in a row, three separate times in one
                    # session -- physically impossible in a real maze cell.
                    time.sleep(ACTION_SETTLE_S)
                    self.dfs_skip_streak += 1
                    self.turn_count += 1
                    self.last_frame_bgr = frame_bgr.copy()
                    self.pending_signature = ("key", dfs_direction)
                    return True
            if boxes is None:
                # A large-scale change usually means a panel/overlay just
                # opened. Live testing showed gemini-flash-lite-latest
                # reliably choosing to close such a panel immediately even
                # when explicitly told (twice, with increasingly direct
                # wording) to check its edges for a pagination chevron
                # first -- a model capability/attention limit, not a prompt
                # issue. Probe for one with plain CV before ever asking the
                # model: if found, click it directly and skip the LLM call
                # for this turn entirely (cheaper, and sidesteps the
                # unreliable instruction-following rather than fighting it
                # with more prompt text).
                chevron = self._probe_chevron(frame_bgr, left, top, w, h)
                if chevron is not None:
                    nx, ny = round(chevron["cx"] / w * 1000), round(chevron["cy"] / h * 1000)
                    self.last_action_desc = (
                        f"(heuristic, no LLM call) clicked a probable pagination chevron at "
                        f"({nx},{ny}) found near the edge of the newly-changed panel"
                    )
                    self.logger.log_event(
                        "heuristic_action",
                        {
                            "action": "click",
                            "x": left + chevron["cx"],
                            "y": top + chevron["cy"],
                            "coord_space": "screen",
                            "reason": "chevron_probe_after_large_scale_change",
                        },
                    )
                    ic.click(left + chevron["cx"], top + chevron["cy"])
                    self.move_hold_ms_acc = None
                    self.move_hold_ms_direction = None
                    self.pending_click_or_drag_confirm = False
                    self.pending_direction = None
                    time.sleep(ACTION_SETTLE_S)
                    self.turn_count += 1
                    self.last_frame_bgr = frame_bgr.copy()
                    self.pending_signature = ("click", self._bucket(chevron["cx"], w), self._bucket(chevron["cy"], h))
                    return True
                outcome = (
                    "changed the screen substantially (a large-scale visual change -- e.g. a "
                    "new overlay/panel opened, closed, or the scene transitioned) -- too broad "
                    "to highlight specific regions, look at the whole image for what's new or "
                    "different"
                )
            elif boxes:
                region_descs = []
                send_frame = frame_bgr.copy()
                for (x1, y1, x2, y2) in boxes:
                    cv2.rectangle(send_frame, (x1, y1), (x2, y2), (0, 0, 255), 3)
                    nx1, ny1 = round(x1 / w * 1000), round(y1 / h * 1000)
                    nx2, ny2 = round(x2 / w * 1000), round(y2 / h * 1000)
                    region_descs.append(f"({nx1},{ny1})-({nx2},{ny2})")
                outcome = (
                    "changed the screen -- new/changed content appeared in the red-boxed "
                    "region(s) highlighted below (normalized 0-1000 coords: "
                    + ", ".join(region_descs)
                    + "); note a highlighted region may just be a looping idle animation "
                    "rather than something meaningful, use judgement"
                )
            else:
                outcome = "did NOT change the screen"
        else:
            outcome = "this is the first turn"
        self.last_frame_bgr = frame_bgr.copy()

        self.turn_count += 1
        note = f"Turn {self.turn_count}. Last action: {self.last_action_desc} -- it {outcome}."
        if stuck_warning:
            note += " " + stuck_warning
        if degenerate_warning and degenerate_warning not in (stuck_warning or ""):
            note += " " + degenerate_warning
        if self.last_elements:
            def _describe_element(e):
                base = f"{e.get('label', '?')}={e.get('role', '?')}"
                return f"{base}(open:{e['open_sides']})" if e.get("open_sides") else base

            elem_summary = "; ".join(_describe_element(e) for e in self.last_elements)
            note += f" Elements you identified last turn: {elem_summary}."
            untried = self._untried_piece_target_combos(w, h)
            untried_objects = self._untried_usable_objects(w, h)
            if untried or untried_objects:
                combo_descs = [f"drag {c['piece'].get('label', 'piece')} -> {c['target'].get('label', 'target')}" for c in untried]
                combo_descs += [f"click/activate {c['target'].get('label', 'object')} (usable_object)" for c in untried_objects]
                note += (
                    " Based on those elements, you have NOT yet tried this session: "
                    + ", ".join(combo_descs)
                    + ". Prefer one of these over repeating a combination you've already tried -- "
                    "and remember a usable_object may need to be activated as part of the solution, "
                    "not just the piece moved to the goal."
                )
        if self.last_move_desc:
            note += " " + self.last_move_desc
        if self.goal_warning:
            note += " " + self.goal_warning
        ground_truth_hint = self._direction_ground_truth_hint()
        if ground_truth_hint:
            note += " " + ground_truth_hint
        visited_hint = self._visited_direction_hint()
        if visited_hint:
            note += " " + visited_hint
        route_hint = self._known_route_hint()
        if route_hint:
            note += " " + route_hint
        if self.ever_confirmed_key_movement and not self.ever_used_click_or_drag:
            # Surfaced proactively, not just after the hard-block trips
            # (see MAX_CLICK_DRAG_ATTEMPTS_BEFORE_BLOCK): confirmed live
            # that the model kept choosing "drag the piece straight to the
            # coin" as its own idea in a pure-keyboard maze, three separate
            # times before the block engaged -- since a drag is a raw
            # screen-space mouse gesture with no concept of walls, each of
            # those attempts visibly dragged the character straight through
            # wall geometry on its way to the coin's on-screen position.
            # This game has already shown its cards (keys move the piece,
            # drag never has) well before the 3-strike block is forced to
            # step in -- telling the model that directly should mean fewer
            # of those wasted, wall-ignoring drags happen in the first
            # place, not just fewer AFTER the third one.
            note += (
                " Note: keyboard directional movement has been confirmed to actually move your "
                "controlled_piece this session; click/drag has not (or hasn't been tried) -- prefer key "
                "presses for movement here rather than dragging the piece toward a target_or_goal, since a "
                "drag ignores walls entirely and won't reflect the maze's real layout even if it looks "
                "like a direct path on screen."
            )

        self._rate_limit_wait()
        try:
            from google.genai import types

            image_bytes = llm_vision.encode_frame_png(send_frame)
            response = self.chat.send_message(
                [note, types.Part.from_bytes(data=image_bytes, mime_type="image/png")]
            )
            self.last_call_ts = time.time()
            self.llm_call_count += 1
            text = response.text
        except Exception as e:
            error_text = str(e)
            # Daily quota (GenerateRequestsPerDayPerProjectPerModel) is
            # distinct from the per-minute one below: no amount of backoff
            # ever clears it before the next day, so if a second key is
            # configured, switch to it and retry immediately instead of
            # sleeping pointlessly or burning a MAX_CONSECUTIVE_FAILURES
            # slot on a wait that can't succeed.
            if "GenerateRequestsPerDayPerProjectPerModel" in error_text and self._switch_to_next_key():
                self.logger.log_event(
                    "error",
                    {
                        "source": "game_agent",
                        "message": (
                            f"Gemini call failed (daily quota exhausted on key "
                            f"{self.active_key_idx - 1}), switched to key {self.active_key_idx}: {e}"
                        ),
                    },
                )
                self.last_call_ts = time.time()
                return True  # not a real failure -- don't count it against MAX_CONSECUTIVE_FAILURES
            # A 429 rate-limit error tells us exactly how long to back off
            # (e.g. "retryDelay': '44s'") -- confirmed live this matters:
            # without honoring it, the immediate next tick (paced only by
            # the much shorter agent_interval_s) re-hits the same still-
            # exhausted per-minute window, burning through all
            # MAX_CONSECUTIVE_FAILURES retries in seconds instead of
            # actually waiting out the window, and the session gives up on
            # a limit that would have cleared on its own shortly after.
            if "RESOURCE_EXHAUSTED" in error_text or "429" in error_text:
                match = re.search(r"retryDelay['\"]?\s*:\s*['\"]?(\d+)", error_text)
                backoff_s = min(int(match.group(1)) + 5, 90) if match else 30
                self.logger.log_event(
                    "rate_limit_backoff", {"backoff_s": backoff_s, "message": error_text}
                )
                time.sleep(backoff_s)
            self.last_call_ts = time.time()
            self.logger.log_event("error", {"source": "game_agent", "message": f"Gemini call failed: {e}"})
            self.consecutive_failures += 1
            if self.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                self.stop_reason = "sustained_llm_failure"
                return False
            return True  # transient failure -- try again next tick

        self.consecutive_failures = 0

        try:
            action_data = llm_vision.parse_action_response(text, w, h)
        except llm_vision.LLMVisionError as e:
            self.logger.log_event("error", {"source": "game_agent", "message": f"bad response: {e}"})
            self.last_action_desc = "none (invalid response from model)"
            return True

        elements = action_data.get("elements") or []
        if elements:
            self.logger.log_event("scene_elements", {"elements": elements})
        if len(elements) <= 1:
            self.consecutive_minimal_elements += 1
        else:
            self.consecutive_minimal_elements = 0
        self._track_piece_movement(elements)
        self._update_goal_tracking(elements, w, h)
        stabilized_goal = self._stabilized_goal_world_pos(w, h) if w and h else None
        self.logger.log_event(
            "goal_canvas_track",
            {
                "goal_canvas_pos": list(self.goal_canvas_pos) if self.goal_canvas_pos else None,
                "stale_turns": self.goal_canvas_stale_turns,
                "stabilized_world_pos": [round(stabilized_goal[0], 1), round(stabilized_goal[1], 1)] if stabilized_goal else None,
            },
        )
        self._track_goal_consistency(elements)
        self.goal_warning = self._check_goal_consistency() or ""
        self.consecutive_heuristic_escapes = 0
        self.dfs_skip_streak = 0  # a real call just happened -- reset the skip-streak budget
        self.last_elements = elements

        if self.consecutive_identical_position >= STALE_POSITION_FORCE_RESET_THRESHOLD:
            # STUCK WARNING and DEGENERATE ANALYSIS WARNING text both
            # already had a chance to fix this and didn't -- confirmed
            # live, this can persist through 50+ real heuristic-escape key
            # presses genuinely reaching the game. A fresh chat has no
            # prior "same position again" answer to anchor on, which is a
            # structurally different intervention than more warning text
            # in the same conversation that's already producing it.
            self.logger.log_event(
                "forced_chat_reset",
                {"reason": "stale_position_repetition", "consecutive_identical_position": self.consecutive_identical_position},
            )
            self._summarize_memory()
            self._start_new_chat()
            self.consecutive_identical_position = 0

        if (
            action_data.get("action") in ("click", "drag")
            and not self.ever_used_click_or_drag
            and self.failed_click_drag_attempts >= MAX_CLICK_DRAG_ATTEMPTS_BEFORE_BLOCK
            and self.ever_confirmed_key_movement  # never block the only input method a genuinely mouse-driven game might have -- see this flag's docstring
        ):
            # Hard override, not just a stronger warning -- see
            # MAX_CLICK_DRAG_ATTEMPTS_BEFORE_BLOCK's docstring for why prose
            # alone wasn't enough here. Pick a fresh direction the same way
            # the direction-escape tier does, so the turn isn't wasted.
            original_action = action_data.get("action")
            direction = self._pick_untried_direction() or "down"
            self.logger.log_event(
                "action_override",
                {
                    "original_action": original_action,
                    "reason": "click_drag_blocked_after_repeated_failure",
                    "failed_attempts": self.failed_click_drag_attempts,
                    "override_action": "key",
                    "key": direction,
                },
            )
            action_data = {
                "action": "key",
                "key": direction,
                "hold_ms": DEFAULT_KEY_HOLD_MS,
                "reasoning": (
                    f"[overridden: {original_action} disabled after {self.failed_click_drag_attempts} "
                    f"attempts this session never moved the piece] {action_data.get('reasoning', '')}"
                ),
            }

        if (
            action_data.get("action") == "key"
            and action_data.get("key") in ("up", "down", "left", "right")
            and not self.config.disable_heuristic_escapes
        ):
            # Extends the same "code overrides a demonstrably unreliable
            # model choice" precedent as the click/drag hard-block above,
            # to raw direction picking. Two tiers:
            #
            # 1. No target_or_goal visible (pure exploration, nothing
            #    semantic to reason about): hand direction choice fully to
            #    DFS. Confirmed live: the model's own per-turn direction
            #    guesses clustered in one bucket 84% of the time here --
            #    worse than DFS backtracking.
            # 2. A target_or_goal IS visible, but the model chose a
            #    direction already GROUND-TRUTH CONFIRMED blocked from this
            #    exact spot: correct just that, don't take over full
            #    navigation intent (goal-directed movement is a real
            #    semantic judgment call this project has never tried to
            #    replace with code). Confirmed live as a real, separate gap
            #    from tier 1: a session where a coin stayed visible 58/64
            #    turns still clustered at 70% concentration, because tier 1
            #    never engaged (goal always visible) while the model kept
            #    re-choosing the SAME direction toward that coin despite it
            #    being a wall -- a wall doesn't stop being a wall just
            #    because something is visible past it.
            # 3. A target_or_goal IS visible, the model's specific choice
            #    isn't itself confirmed blocked (so tier 2 doesn't fire),
            #    but per-turn stuck detection says the straight-line
            #    approach genuinely isn't working. Only engages once stuck,
            #    same as tier 1/2's "override a demonstrated failure"
            #    precedent -- a model still making progress on its own is
            #    left alone even with a known route sitting idle, since
            #    going straight at a visible goal is often genuinely correct.
            #    Two cases: (a) a real, ground-truth detour to the goal
            #    already exists in this session's own explored history
            #    (_known_route_hint / _bfs_route_to_goal) -- follow it; (b)
            #    no such route exists yet, meaning the detour itself hasn't
            #    been explored -- fall back to DFS the same as tier 1.
            #    Confirmed live this second case matters: without it, a
            #    session spent its entire run sliding back and forth along
            #    one wall toward a visible-but-unreachable goal (16 distinct
            #    cells visited best-case elsewhere in this project, this
            #    session only 9), because tier 2 only vetoes the one
            #    known-blocked direction without ever pushing into
            #    genuinely new territory, and there was no route yet for
            #    tier 3a to recall.
            original_key = action_data.get("key")
            bucket = self._pos_bucket(self.last_piece_pos) if self.last_piece_pos is not None else None
            # self.goal_warning (see _check_goal_consistency, just computed
            # above from this turn's elements) means the reported
            # target_or_goal position has scattered across several clearly
            # different spots recently -- not a real, trustworthy
            # navigation target. Confirmed live as a real, compounding
            # cause of getting stuck: an unreliable goal position kept
            # has_goal True (so tier 1's real exploration never engaged),
            # while the model's own chosen direction kept changing turn to
            # turn (chasing wherever the "goal" currently appeared to be),
            # which meant no single (bucket, direction) pair ever repeated
            # enough times for DIRECTION_BLOCKED_MIN_FAILS to confirm it
            # blocked either -- tier 2 had nothing to correct and tier 1
            # never got a turn, so the agent sat in one spot for 35+ turns
            # with no code-side mechanism able to intervene. Treating an
            # inconsistent goal the same as no goal for this gate lets
            # real exploration take over instead.
            has_goal = (
                any(e.get("role") == "target_or_goal" for e in elements)
                and not self.goal_warning
            )
            self.last_has_goal = has_goal  # gates the DFS-skip fast path in step() -- see DFS_SKIP_MAX_STREAK
            override_key = None
            override_reason = None
            if not has_goal:
                dfs_direction = self._dfs_next_direction()
                if dfs_direction is not None and dfs_direction != original_key:
                    override_key, override_reason = dfs_direction, "exploration_direction_overridden_by_dfs"
                elif bucket is not None and self.direction_known.get((bucket, original_key)) is False:
                    # dfs_direction alone doesn't cover every case: it's
                    # None once BFS finds no reachable untried bucket at
                    # all (this cell's whole connected component, via
                    # confirmed-open edges, is fully resolved) -- but that
                    # doesn't mean the model's own choice is safe, only
                    # that DFS has nothing BETTER to affirmatively suggest.
                    # Confirmed live as a real gap: in a session stuck
                    # oscillating on one direction, roughly half of the
                    # repeated bad picks had NO action_override at all
                    # (dfs_direction was None that turn), meaning the
                    # model's own confirmed-blocked guess sailed straight
                    # through uncorrected. If the model's choice is already
                    # ground-truth confirmed blocked at this exact spot,
                    # correct it regardless of whether DFS has a positive
                    # suggestion of its own -- same fallback-to-any-
                    # confirmed-open-direction logic as the goal-visible
                    # tier below.
                    directions = ("up", "down", "left", "right")
                    open_dirs = [d for d in directions if d != original_key and self.direction_known.get((bucket, d)) is True]
                    fallback = open_dirs[0] if open_dirs else None
                    if fallback:
                        override_key, override_reason = fallback, "confirmed_blocked_direction_overridden"
            elif bucket is not None and self.direction_known.get((bucket, original_key)) is False:
                directions = ("up", "down", "left", "right")
                open_dirs = [d for d in directions if d != original_key and self.direction_known.get((bucket, d)) is True]
                fallback = open_dirs[0] if open_dirs else self._dfs_next_direction()
                if fallback and fallback != original_key:
                    override_key, override_reason = fallback, "confirmed_blocked_direction_overridden"
            elif stuck_warning:
                if self.known_route_directions and self.known_route_directions[0] != original_key:
                    override_key, override_reason = self.known_route_directions[0], "known_route_to_goal_followed"
                else:
                    # 3b: no route to recall yet -- go discover one. See the
                    # tier-3 note above.
                    dfs_direction = self._dfs_next_direction()
                    if dfs_direction is not None and dfs_direction != original_key:
                        override_key, override_reason = dfs_direction, "stuck_goal_unreachable_explore_via_dfs"
            if override_key is not None:
                self.logger.log_event(
                    "action_override",
                    {
                        "original_action": "key",
                        "original_key": original_key,
                        "reason": override_reason,
                        "override_key": override_key,
                    },
                )
                if override_reason == "exploration_direction_overridden_by_dfs":
                    override_explanation = "no target_or_goal visible, exploration direction chosen by DFS backtracking"
                elif override_reason == "known_route_to_goal_followed":
                    override_explanation = "stuck making direct progress, following a known confirmed-open route to the goal from this session's own explored history"
                elif override_reason == "stuck_goal_unreachable_explore_via_dfs":
                    override_explanation = "stuck with the goal visible but no known route to it yet, exploring new territory via DFS backtracking to find one"
                else:
                    override_explanation = "chosen direction is ground-truth confirmed blocked from this exact spot"
                action_data = {
                    "action": "key",
                    "key": override_key,
                    # At least DIRECTION_BLOCKED_MIN_HOLD_MS, not whatever
                    # the model happened to request for its own (now
                    # discarded) direction choice. Confirmed live as a real,
                    # session-wrecking bug without this: the model
                    # frequently requested short hold_ms (150ms is common),
                    # and reusing that verbatim meant a DFS-substituted
                    # direction's failure never even qualified as ONE real
                    # data point toward direction_known (which needs
                    # >=DIRECTION_BLOCKED_MIN_HOLD_MS to count at all) -- one
                    # session repeated "right" from the exact same cell 37
                    # times over 67 minutes because every single attempt
                    # used a sub-qualifying 150ms hold, so DFS kept seeing
                    # it as "never tried" forever. An override IS a
                    # deliberate ground-truth-gathering attempt, unlike an
                    # ordinary tap, so it should always be given enough
                    # hold time to actually settle the question.
                    "hold_ms": max(action_data.get("hold_ms") or DEFAULT_KEY_HOLD_MS, DIRECTION_BLOCKED_MIN_HOLD_MS),
                    "reasoning": (
                        f"[overridden: {override_explanation} instead of the model's own guess "
                        f"({original_key})] {action_data.get('reasoning', '')}"
                    ),
                }

        self._execute(action_data, left, top)
        self.pending_signature = self._action_signature(action_data, w, h)
        self._record_combo_tried(
            action_data.get("action"), w, h,
            x=action_data.get("x"), y=action_data.get("y"),
            x_end=action_data.get("x_end"), y_end=action_data.get("y_end"),
        )
        time.sleep(ACTION_SETTLE_S)

        self.turns_since_reset += 1
        if self.turns_since_reset >= self.config.memory_summarize_every_n_turns:
            self._summarize_memory()
            self._start_new_chat()

        return True

    def _accumulate_move_hold(self, direction, hold_ms):
        # Shared by every site that adds to move_hold_ms_acc for a cardinal
        # key press. Tracks whether everything accumulated since the last
        # _track_piece_movement call shares ONE direction or has become
        # "mixed" -- see _track_piece_movement's use of
        # move_hold_ms_direction for why a mixed window can't be trusted to
        # calibrate a distance-per-ms rate from.
        if self.move_hold_ms_acc is None:
            return
        self.move_hold_ms_acc += hold_ms
        if self.move_hold_ms_direction is None:
            self.move_hold_ms_direction = direction
        elif self.move_hold_ms_direction != direction:
            self.move_hold_ms_direction = "mixed"

    def _frame_changed(self, before_gray, curr_bgra):
        curr_gray = cv2.cvtColor(curr_bgra, cv2.COLOR_BGRA2GRAY)
        diff = cv2.absdiff(before_gray, curr_gray)
        _, thresh = cv2.threshold(diff, 30, 255, cv2.THRESH_BINARY)
        changed_frac = (thresh > 0).sum() / thresh.size
        # bool(...) -- a numpy comparison here returns numpy.bool_, not a
        # native Python bool. json.dumps (see logger.log_event's
        # default=str) doesn't recognize numpy.bool_ and silently falls
        # back to str(), which logs it as the STRING "True"/"False" instead
        # of a real JSON boolean. Truthiness in `if` checks still worked
        # correctly either way, so this was a log-fidelity bug, not a
        # decision-logic one -- but worth fixing for anyone reading these
        # logs (or writing analysis scripts against them) expecting real
        # booleans.
        return bool(changed_frac >= PROBE_CHANGE_FRAC)

    def _hold_key_with_probe(self, key, target_hold_ms):
        """Presses `key` for up to target_hold_ms, but releases early the
        moment a probe frame shows visible movement -- see PROBE_HOLD_MS's
        docstring for why. Returns (actual_hold_ms, moved): actual_hold_ms
        is what the caller must use (not target_hold_ms) for anything
        downstream that cares how long the key was really down
        (direction_known, move_hold_ms_acc) -- releasing early is the whole
        point, and crediting the un-held remainder would silently
        re-introduce the exact hold_ms-blind bug this project already fixed
        once before. moved is a pixel-diff-based visible-change verdict
        covering the WHOLE attempt (not just the probe window when it runs
        that far) -- a second, independent movement signal from the same
        kind of real image comparison _detect_scroll_shift already relies
        on, usable by callers (like the stuck-cycle escape tier) that can't
        wait for some future real LLM turn to resolve pending_direction
        against a fresh position reading."""
        before_bgra, _ = self.cap.grab()
        before_gray = cv2.cvtColor(before_bgra, cv2.COLOR_BGRA2GRAY)
        if target_hold_ms <= PROBE_HOLD_MS:
            ic.key_down(key)
            time.sleep(target_hold_ms / 1000.0)
            ic.key_up(key)
            after_bgra, _ = self.cap.grab()
            return target_hold_ms, self._frame_changed(before_gray, after_bgra)
        ic.key_down(key)
        time.sleep(PROBE_HOLD_MS / 1000.0)
        probe_bgra, _ = self.cap.grab()
        if self._frame_changed(before_gray, probe_bgra):
            ic.key_up(key)
            return PROBE_HOLD_MS, True
        time.sleep((target_hold_ms - PROBE_HOLD_MS) / 1000.0)
        ic.key_up(key)
        after_bgra, _ = self.cap.grab()
        return target_hold_ms, self._frame_changed(before_gray, after_bgra)

    def _execute(self, data, left, top):
        action = data["action"]
        if action in ("click", "drag"):
            # Don't set ever_used_click_or_drag here -- that would mean
            # merely ATTEMPTING a click/drag unlocks the click-based escape
            # tiers for the rest of the session, regardless of whether it
            # did anything. Confirmed live as a real bug: a model stuck in
            # a pure-keyboard maze tried "drag" exactly once out of
            # desperation (reasoning: "standard key movement is not
            # working... I will attempt [dragging]"), the drag moved the
            # controlled_piece by all of ~1 normalized unit (i.e. not at
            # all -- this game doesn't listen to drag), and that single
            # ineffective attempt still flipped the flag permanently,
            # wasting 10 later stuck-escapes on clicking wall-texture blobs
            # instead of the direction-escape tier that actually works here.
            # _track_piece_movement checks pending_click_or_drag_confirm
            # against the piece's real next-turn displacement and only
            # then confirms the flag -- an attempt has to actually work
            # before we trust this genre uses it.
            self.pending_click_or_drag_confirm = True
            self.failed_click_drag_attempts += 1  # optimistically counted as failed; _track_piece_movement resets this to 0 if the next real reading confirms it actually worked
            # A click/drag-caused position change isn't attributable to a
            # held-key duration, so the rate calibration in
            # _track_piece_movement can't compare it to anything -- mark it
            # unusable rather than let a stale hold_ms from a much earlier
            # turn get silently (and wrongly) reused for it.
            self.move_hold_ms_acc = None
            self.move_hold_ms_direction = None
            self.pending_direction = None  # breaks clean single-direction attribution for the ground-truth cache too
        if action == "click":
            x, y = left + data["x"], top + data["y"]
            ic.click(x, y)
            self.last_action_desc = f"clicked at ({data['x']},{data['y']}) -- {data.get('reasoning', '')}"
            self.logger.log_event(
                "agent_action",
                {"action": "click", "x": x, "y": y, "coord_space": "screen", "reasoning": data.get("reasoning")},
            )
        elif action == "key":
            requested_hold_ms = data.get("hold_ms") or DEFAULT_KEY_HOLD_MS
            requested_hold_ms = min(requested_hold_ms, MAX_KEY_HOLD_MS)
            key = data["key"]
            # Probe-and-release instead of a blind sleep for the requested
            # duration: see PROBE_HOLD_MS. hold_ms below is the ACTUAL time
            # held (may be less than requested_hold_ms if released early on
            # detected movement) -- everything downstream (ground-truth
            # attribution, rate calibration, logging) must use this real
            # value, not what was merely asked for. The pixel-diff "moved"
            # verdict is ignored here -- this path's ground truth is
            # resolved properly next turn via pending_direction against a
            # fresh LLM position reading (see _track_piece_movement), which
            # is the more precise signal; it's only the escape tier (which
            # has no next real turn to wait for) that needs this one directly.
            hold_ms, _probe_moved = self._hold_key_with_probe(key, requested_hold_ms)
            # Ground-truth attribution: only meaningful when this key IS a
            # cardinal direction AND we know the exact cell it was pressed
            # from AND nothing else happens before the outcome is read next
            # turn. Any other action type already clears this (see the
            # click/drag branch above); a non-directional key (e.g. space,
            # enter) also can't be attributed to a direction, so it clears
            # here too rather than leaving a stale value from an earlier
            # turn to be wrongly consumed later.
            if key in ("up", "down", "left", "right") and self.last_piece_pos is not None:
                self.pending_direction = (self._pos_bucket(self.last_piece_pos), key, hold_ms)
            else:
                self.pending_direction = None
            # A non-cardinal key (space, enter, ...) still marks the window
            # "mixed" via _accumulate_move_hold if a cardinal move also
            # contributed -- correct, since its hold time isn't part of the
            # same distance-per-ms measurement a cardinal move's is.
            self._accumulate_move_hold(key, hold_ms)
            self.last_action_desc = f"pressed '{key}' held {hold_ms}ms -- {data.get('reasoning', '')}"
            self.logger.log_event(
                "agent_action",
                {"action": "key", "key": key, "hold_ms": hold_ms, "requested_hold_ms": requested_hold_ms, "reasoning": data.get("reasoning")},
            )
        elif action == "drag":
            x1, y1 = left + data["x"], top + data["y"]
            x2, y2 = left + data["x_end"], top + data["y_end"]
            ic.drag(x1, y1, x2, y2)
            self.last_action_desc = (
                f"dragged from ({data['x']},{data['y']}) to ({data['x_end']},{data['y_end']}) -- {data.get('reasoning', '')}"
            )
            self.logger.log_event(
                "agent_action",
                {
                    "action": "drag",
                    "x": x1, "y": y1, "x2": x2, "y2": y2,
                    "coord_space": "screen",
                    "reasoning": data.get("reasoning"),
                },
            )
