"""Generic 'clickable candidate' detection.

Heuristic, not a trained model: works reasonably on 2D menu/dialog screens,
degrades to noisy/low-value candidates during active 3D gameplay -- which is
fine, since monkey_agent.py always has non-vision fallback actions.

Known false negatives/positives observed on a real game (measured, not
theoretical -- see a real hand-painted brush-stroke menu where button pills
had fill_ratio 0.67-0.72 depending on how jagged that particular pill's
paint edge was, which is why `rectangularity` sits at 0.65 rather than a
stricter 0.7-0.8):
- A larger/more elaborately brush-stroked button (not a "selected" state --
  just a bigger default button, e.g. a menu's primary "play" action rendered
  bigger than its siblings) can have hand-painted edges frayed enough that
  its bounding-box fill_ratio drops to ~0.40-0.55, well under even the 0.65
  floor -- confirmed this isn't edge noise fixable by a morphological close
  (tested kernel sizes 3-9, fill_ratio stayed ~0.40-0.43 throughout; at
  ksize=9 the contour disappeared entirely). Lowering `rectangularity`
  further to catch it was measured directly: dropping to 0.4 added 5 new
  candidates screen-wide to catch this one button, i.e. 4 new false
  positives for 1 true positive -- a bad trade discarded in favor of
  leaning on the LLM vision fallback for this class of button instead.
- Bold multi-letter text in decorative titles/logos can incidentally pass
  the same rectangularity/aspect checks as a real button and get flagged as
  a false-positive candidate. Harmless for monkey testing (clicking dead
  text just does nothing) but not filtered out by geometry alone; --ocr's
  keyword reranking helps prefer real buttons over this but doesn't exclude
  non-button text outright.
"""
import cv2
import numpy as np

OCR_KEYWORDS = [
    "ok", "start", "continue", "play", "settings", "exit",
    "resume", "menu", "yes", "no", "retry",
]


def detect_candidates(
    frame_bgr,
    min_area_ratio=0.002,
    max_area_ratio=0.08,
    min_aspect=1.5,
    max_aspect=8.0,
    rectangularity=0.65,
    icon_min_area_ratio=0.0005,
    icon_max_area_ratio=0.01,
    icon_max_aspect=1.6,
    icon_rectangularity=0.6,
):
    """Two profiles are checked per contour:

    - "wide button" (the original profile): text/pill-shaped buttons, which
      tend to be wider than tall (min_aspect=1.5+) and reliably solid-filled.
    - "small icon": square-ish badge/icon buttons (settings gear, exit door,
      etc). These are often circular badges whose background is nearly the
      same brightness as the page behind them -- Canny finds no edge for the
      circle itself, only the glyph drawn on top, so both the area floor and
      aspect-ratio floor need to be lower than the wide-button profile (a
      near-square glyph bounding box, not a wide pill).
    """
    h, w = frame_bgr.shape[:2]
    frame_area = h * w
    if frame_area == 0:
        return []

    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    candidates = []
    for cnt in contours:
        x, y, cw, ch = cv2.boundingRect(cnt)
        bbox_area = cw * ch
        if bbox_area == 0:
            continue
        area_ratio = bbox_area / frame_area
        long_side, short_side = max(cw, ch), max(min(cw, ch), 1)
        aspect = long_side / short_side
        fill_ratio = cv2.contourArea(cnt) / bbox_area

        is_wide_button = (
            min_area_ratio <= area_ratio <= max_area_ratio
            and min_aspect <= aspect <= max_aspect
            and fill_ratio >= rectangularity
        )
        is_small_icon = (
            icon_min_area_ratio <= area_ratio <= icon_max_area_ratio
            and aspect <= icon_max_aspect
            and fill_ratio >= icon_rectangularity
        )
        if not (is_wide_button or is_small_icon):
            continue

        candidates.append(
            {"x": x, "y": y, "w": cw, "h": ch, "cx": x + cw // 2, "cy": y + ch // 2, "score": 1.0}
        )
    return candidates


def detect_chevrons(
    frame_bgr,
    edge_margin_frac=0.16,
    min_area_ratio=0.001,
    max_area_ratio=0.02,
    min_aspect=1.3,
    max_aspect=2.2,
    min_fill_ratio=0.15,
    max_fill_ratio=0.5,
    center_y_tolerance_frac=0.2,
):
    """Find pagination-style chevron ('<'/'>') candidates near the left/right
    edges of the frame.

    A real '>' chevron measured directly off a live game screenshot came out
    to fill_ratio ~0.33, aspect ~1.65 -- an outlined arrow is mostly hollow,
    so it sits at roughly half the fill_ratio floor `detect_candidates`'s
    icon profile uses (0.6). Applying a floor that low across the whole
    frame the way `detect_candidates` does would reproduce the same
    false-positive blowup documented in this module's docstring (a similar
    low-fill-ratio button measured at 0.4 added 4 false positives for 1 true
    positive screen-wide). This function stays safe at a much looser
    fill_ratio by searching only the outer `edge_margin_frac` of the frame
    width instead of the whole image -- chevrons are placed there by
    convention, and restricting the search area is what makes the loose
    shape thresholds tolerable.

    Intended for a narrow, code-only (no LLM call) use: after detecting a
    large-scale screen change (see game_agent.py's `_diff_regions` "large
    change" case, e.g. a panel just opened), probe here first. If a
    candidate exists, click it directly without spending a model call --
    live testing showed gemini-flash-lite-latest reliably choosing to close
    a hint panel immediately even when explicitly told to check its edges
    for a chevron first, so this replaces asking the model with just
    looking, for this one narrow, generic, non-game-specific pattern. Empty
    result is expected and normal on any screen that has no such control.

    `center_y_tolerance_frac` matters more than it looks: measured live on a
    real chapter-select screen with decorative bamboo/tree art running down
    both edges, the loose fill_ratio floor this function needs (see above)
    picked up 9 raw candidates in the edge margins, only 2 of which were the
    actual chevrons -- the rest were art. Real pagination controls are
    reliably placed near vertical center; the decorative false positives on
    that screen were not (they clustered near the top/bottom thirds), so
    restricting to within `center_y_tolerance_frac` of the frame's vertical
    center cut it down to 4 candidates. Callers should still treat this as a
    probabilistic hint, not a certainty -- e.g. pick at most one candidate
    per side (closest to true vertical center) rather than acting on every
    match -- since a residual false positive is cheap to recover from (one
    wasted click that the next turn's diff will show as a no-op) but acting
    on several in a row is not.
    """
    h, w = frame_bgr.shape[:2]
    frame_area = h * w
    if frame_area == 0:
        return []

    margin = int(w * edge_margin_frac)
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    candidates = []
    for cnt in contours:
        x, y, cw, ch = cv2.boundingRect(cnt)
        bbox_area = cw * ch
        if bbox_area == 0:
            continue
        cx, cy = x + cw // 2, y + ch // 2
        if not (cx <= margin or cx >= w - margin):
            continue
        if abs(cy - h / 2) > center_y_tolerance_frac * h:
            continue
        area_ratio = bbox_area / frame_area
        long_side, short_side = max(cw, ch), max(min(cw, ch), 1)
        aspect = long_side / short_side
        fill_ratio = cv2.contourArea(cnt) / bbox_area
        if not (min_area_ratio <= area_ratio <= max_area_ratio):
            continue
        if not (min_aspect <= aspect <= max_aspect):
            continue
        if not (min_fill_ratio <= fill_ratio <= max_fill_ratio):
            continue
        candidates.append(
            {"x": x, "y": y, "w": cw, "h": ch, "cx": x + cw // 2, "cy": y + ch // 2, "score": 1.0}
        )
    return candidates


def rerank_with_ocr(frame_bgr, candidates):
    try:
        import pytesseract
    except ImportError:
        return candidates

    for c in candidates:
        crop = frame_bgr[c["y"]: c["y"] + c["h"], c["x"]: c["x"] + c["w"]]
        if crop.size == 0:
            c["ocr_text"] = None
            continue
        try:
            text = pytesseract.image_to_string(crop, config="--psm 7").strip().lower()
        except Exception:
            text = ""
        c["ocr_text"] = text or None
        if text and any(k in text for k in OCR_KEYWORDS):
            c["score"] = 3.0
    return candidates
