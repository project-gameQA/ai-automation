"""Gemini vision helpers shared by two call sites:

- The monkey agent's low-frequency stuck-escalation fallback (`suggest_action`
  below) -- one call only when the screen has been unchanged for 8 ticks.
- `game_agent.py`'s continuous per-turn agent loop -- one call per action.

Both need the exact same hard-won response parsing: a click *bounding box* in
normalized 0-1000 space (not raw pixels), scaled to real pixel coordinates
here. This was empirically necessary, not a style choice: measured against a
real 1920x1080 screenshot with a known button location, asking for a single
raw-pixel (x, y) point was consistently off by ~64px (missed the button
entirely -- a systematic bias across repeated calls, not random noise, so a
bounding box centered on the same raw-pixel estimate didn't help either).
Switching to normalized 0-1000 coordinates -- Google's documented
recommendation for localization on Gemini -- brought repeat measurements to
within 2-3px of the true button center. See README for the measured
before/after numbers. `parse_action_response` is the one place this lives so
both call sites share the calibration instead of drifting apart.

Note: on Gemini's free tier, prompts and images sent may be used by Google
to improve their products -- see README limitations before pointing this at
an unreleased/confidential build.

Model choice: default is `gemini-flash-lite-latest` (an alias Google keeps
pointed at its current lite-tier flash model, so it doesn't go stale the way
a dated snapshot does -- see README for the `gemini-2.5-flash-lite`
retirement this project already hit once). Free-tier daily quotas vary a lot
by model and aren't listed anywhere obvious -- measured directly against a
real key, `gemini-3.5-flash` (full, non-lite) was capped at 20 requests/day,
which `--mode agent`'s per-action calling burns through in minutes;
`gemini-flash-lite-latest` had much more free-tier headroom in the same
test. Don't assume a model's quota; a 429 with
`GenerateRequestsPerDayPerProjectPerModel-FreeTier` in the body names the
exhausted quota directly.
"""
import json
import os

PROMPT_TEMPLATE = """You are looking at a screenshot from a game window that a QA testing bot has been randomly clicking/pressing keys in for a while without any visible change on screen -- it appears stuck (e.g. on a menu, a dialog, or a loading screen with no interactive element the bot's heuristics could find).

Suggest exactly ONE next action to get the game to progress or respond. A reasonable guess is better than nothing.

Prefer, in rough order of likelihood: pressing "escape" or "enter", clicking a clearly visible button/prompt on screen, pressing a commonly-used key (arrows, space, tab).

If the action is a click, give the bounding box of the whole clickable button/element
(not a single point). Use a normalized 0-1000 coordinate system where (0,0) is the
top-left corner and (1000,1000) is the bottom-right corner of the image, regardless of
its actual pixel size -- do not use raw pixel coordinates. Make the box roughly the
size of the visible button, not the whole screen.

Respond with ONLY this JSON shape, no other text, no markdown code fence:
{{"action": "click" or "key", "key": "<key name, only if action is key>", "x1": <int 0-1000, left edge, only if action is click>, "y1": <int 0-1000, top edge>, "x2": <int 0-1000, right edge>, "y2": <int 0-1000, bottom edge>, "reasoning": "<one short sentence>"}}

x1<x2 and y1<y2, all in the 0-1000 normalized range."""

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["click", "key", "drag"]},
        "key": {"type": "string"},
        "hold_ms": {"type": "integer"},
        "x1": {"type": "integer"},
        "y1": {"type": "integer"},
        "x2": {"type": "integer"},
        "y2": {"type": "integer"},
        "x3": {"type": "integer"},
        "y3": {"type": "integer"},
        "x4": {"type": "integer"},
        "y4": {"type": "integer"},
        "reasoning": {"type": "string"},
    },
    "required": ["action", "reasoning"],
}

MAX_HOLD_MS = 2000


class LLMVisionError(Exception):
    pass


def get_client(api_key=None):
    api_key = api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise LLMVisionError("no Gemini API key (pass --gemini-api-key or set GEMINI_API_KEY)")
    try:
        from google import genai
    except ImportError as e:
        raise LLMVisionError(f"google-genai not installed ({e}); pip install google-genai")
    return genai.Client(api_key=api_key)


def encode_frame_png(frame_bgr):
    import cv2

    ok, buf = cv2.imencode(".png", frame_bgr)
    if not ok:
        raise LLMVisionError("failed to encode frame as PNG")
    return buf.tobytes()


DEGENERATE_BOX_PAD = 15  # normalized 0-1000 units


def _pad_if_degenerate(lo, hi):
    """A zero-size or reversed (lo>=hi) box is, in practice, the model giving
    a point estimate despite being asked for a box -- measured as the
    dominant "bad response" failure mode in real agent-mode sessions (most
    of the errors in one real run were exactly this). Treat it as a point
    and pad into a small usable box instead of wasting the whole turn on an
    error; a live agent-mode session can't afford to burn a turn (and a unit
    of its Gemini call budget) recovering from this."""
    if lo < hi:
        return lo, hi
    center = (lo + hi) // 2
    return max(0, center - DEGENERATE_BOX_PAD), min(1000, center + DEGENERATE_BOX_PAD)


def _scale_box(data, keys, w, h):
    """Reads a (x1,y1,x2,y2)-named box in normalized 0-1000 space from
    `data`, validates it, and overwrites those same keys in `data` with
    pixel-space values. Returns the pixel-space (x1, y1, x2, y2) tuple."""
    kx1, ky1, kx2, ky2 = keys
    nx1, ny1, nx2, ny2 = data.get(kx1), data.get(ky1), data.get(kx2), data.get(ky2)
    norm_box = (nx1, ny1, nx2, ny2)
    if not all(isinstance(v, int) for v in norm_box):
        raise LLMVisionError(f"box {keys} missing/non-integer fields: {data}")
    if not (0 <= nx1 <= 1000 and 0 <= ny1 <= 1000 and 0 <= nx2 <= 1000 and 0 <= ny2 <= 1000):
        raise LLMVisionError(f"box {keys} out of 0-1000 range: {data}")
    nx1, nx2 = _pad_if_degenerate(nx1, nx2)
    ny1, ny2 = _pad_if_degenerate(ny1, ny2)
    x1, y1, x2, y2 = nx1 * w // 1000, ny1 * h // 1000, nx2 * w // 1000, ny2 * h // 1000
    data[kx1], data[ky1], data[kx2], data[ky2] = x1, y1, x2, y2
    return x1, y1, x2, y2


def parse_action_response(text, w, h):
    """Validate + scale a raw Gemini JSON response into pixel coordinates.

    Returns a dict with the original fields plus pixel-space box coordinates
    (overwriting the raw 0-1000 values, so callers/logs never see two
    different coordinate scales mixed together):
    - click: "x1".."y2" (box) + center "x"/"y"
    - drag: "x1".."y2" (start box) + center "x"/"y", "x3".."y4" (end box) +
      center "x_end"/"y_end"
    - key: validated "key"/"hold_ms"
    Raises LLMVisionError on any malformed/out-of-range response.
    """
    if not text:
        raise LLMVisionError("empty Gemini response (possibly blocked)")
    try:
        data = json.loads(text)
    except Exception as e:
        raise LLMVisionError(f"could not parse Gemini response as JSON: {e}; raw={text!r}")

    action = data.get("action")
    if action == "click":
        x1, y1, x2, y2 = _scale_box(data, ("x1", "y1", "x2", "y2"), w, h)
        data["x"] = (x1 + x2) // 2
        data["y"] = (y1 + y2) // 2
    elif action == "drag":
        x1, y1, x2, y2 = _scale_box(data, ("x1", "y1", "x2", "y2"), w, h)
        x3, y3, x4, y4 = _scale_box(data, ("x3", "y3", "x4", "y4"), w, h)
        data["x"] = (x1 + x2) // 2
        data["y"] = (y1 + y2) // 2
        data["x_end"] = (x3 + x4) // 2
        data["y_end"] = (y3 + y4) // 2
    elif action == "key":
        if not data.get("key"):
            raise LLMVisionError(f"key action missing 'key': {data}")
        hold_ms = data.get("hold_ms")
        if hold_ms is not None:
            if not isinstance(hold_ms, int) or hold_ms < 0:
                raise LLMVisionError(f"invalid hold_ms: {data}")
            data["hold_ms"] = min(hold_ms, MAX_HOLD_MS)
    else:
        raise LLMVisionError(f"unexpected 'action' in Gemini response: {data}")

    return data


def suggest_action(frame_bgr, api_key=None, model="gemini-flash-lite-latest", timeout_s=15):
    """Single-shot stuck-fallback: returns a dict like {"action": "click"|"key",
    "key": ..., "x": ..., "y": ..., "reasoning": ...}, or raises
    LLMVisionError on any failure (missing key, import error, network/API
    failure, malformed or out-of-bounds response).
    """
    client = get_client(api_key)
    h, w = frame_bgr.shape[:2]
    image_bytes = encode_frame_png(frame_bgr)

    try:
        from google.genai import types

        response = client.models.generate_content(
            model=model,
            contents=[
                PROMPT_TEMPLATE.format(width=w, height=h),
                types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=RESPONSE_SCHEMA,
                temperature=0.4,
            ),
        )
        text = response.text
    except Exception as e:
        raise LLMVisionError(f"Gemini request failed: {e}")

    return parse_action_response(text, w, h)
