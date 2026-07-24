"""Rule-based + random exploration ("monkey testing") decision loop."""
import random
import time

import cv2
from PIL import Image
import imagehash

from . import input_controller as ic
from . import llm_vision
from . import vision


class MonkeyAgent:
    def __init__(self, target, cap, logger, config, monitor=None):
        self.target = target
        self.cap = cap
        self.logger = logger
        self.config = config
        self.monitor = monitor

        self.last_hash = None
        self.unchanged_count = 0
        self.escalation_level = 0
        self.confirmed_stuck_count = 0
        self.action_counts = {}
        self._last_screenshot_time = 0.0

    def _frame_hash(self, frame_bgra):
        img = Image.fromarray(cv2.cvtColor(frame_bgra, cv2.COLOR_BGRA2RGB))
        return imagehash.average_hash(img)

    def _maybe_screenshot(self, frame_bgra, trigger="periodic"):
        now = time.time()
        if trigger != "periodic" or now - self._last_screenshot_time >= self.config.screenshot_interval_s:
            self.logger.save_screenshot(frame_bgra, trigger=trigger)
            self._last_screenshot_time = now

    @staticmethod
    def _weighted_choice(weights: dict):
        items = list(weights.items())
        total = sum(w for _, w in items)
        if total <= 0:
            return items[0][0]
        r = random.uniform(0, total)
        upto = 0.0
        for k, w in items:
            upto += w
            if upto >= r:
                return k
        return items[-1][0]

    def _execute_escalation(self, step, rect):
        left, top, w, h = rect
        if step == "llm_suggest":
            self._try_llm_suggestion(rect)
            return
        if step == "esc":
            ic.key_press("esc")
        elif step == "tab_enter":
            ic.key_press("tab")
            time.sleep(0.1)
            ic.key_press("enter")
        elif step == "click_corner":
            ic.click(left + max(w - 15, 0), top + 15)
        elif step == "log_confirmed_stuck":
            self.confirmed_stuck_count += 1
        self.logger.log_event("input", {"action": "escalation", "step": step})

    def _try_llm_suggestion(self, rect):
        """Best-effort: ask Gemini what to do next. Any failure (no API key,
        network, malformed response) degrades to a plain 'esc' press rather
        than blocking the session -- this is an assist, not a dependency."""
        left, top, w, h = rect
        frame_bgra, _ = self.cap.grab()
        frame_bgr = cv2.cvtColor(frame_bgra, cv2.COLOR_BGRA2BGR)
        try:
            suggestion = llm_vision.suggest_action(
                frame_bgr,
                api_key=self.config.gemini_api_key,
                model=self.config.gemini_model,
            )
        except llm_vision.LLMVisionError as e:
            self.logger.log_event("error", {"source": "llm_vision", "message": str(e)})
            ic.key_press("esc")
            self.logger.log_event("input", {"action": "escalation", "step": "llm_suggest_failed_fallback_esc"})
            return

        if suggestion["action"] == "key":
            ic.key_press(suggestion["key"])
        else:
            ic.click(left + suggestion["x"], top + suggestion["y"])
        self.logger.log_event("input", {"action": "escalation", "step": "llm_suggest", "suggestion": suggestion})

    def step(self):
        frame_bgra, rect = self.cap.grab()
        left, top, w, h = rect
        self._maybe_screenshot(frame_bgra)
        frame_bgr = cv2.cvtColor(frame_bgra, cv2.COLOR_BGRA2BGR)
        if self.config.record_video:
            self.logger.write_video_frame(frame_bgr)

        frame_hash = self._frame_hash(frame_bgra)
        hash_distance = (frame_hash - self.last_hash) if self.last_hash is not None else None
        if hash_distance is not None and hash_distance <= self.config.stuck_hash_distance_threshold:
            self.unchanged_count += 1
        else:
            self.unchanged_count = 0
            self.escalation_level = 0
        self.last_hash = frame_hash

        if self.unchanged_count >= self.config.stuck_action_window:
            ladder = self.config.escalation_ladder
            step_name = ladder[min(self.escalation_level, len(ladder) - 1)]
            self.logger.log_event(
                "stuck",
                {
                    "consecutive_unchanged_frames": self.unchanged_count,
                    "hash_distance": int(hash_distance) if hash_distance is not None else 0,
                    "escalation_level": self.escalation_level,
                },
            )
            self._execute_escalation(step_name, rect)
            self.escalation_level += 1
            self.unchanged_count = 0
            return

        candidates = vision.detect_candidates(frame_bgr)
        if self.config.ocr_enabled and candidates:
            candidates = vision.rerank_with_ocr(frame_bgr, candidates)

        weights = dict(self.config.action_weights)
        if not candidates:
            fold = weights.pop("click_vision_candidate", 0)
            if fold:
                weights["random_click_in_bounds"] = weights.get("random_click_in_bounds", 0) + fold * 0.6
                weights["press_common_key"] = weights.get("press_common_key", 0) + fold * 0.4

        action_type = self._weighted_choice(weights)
        self.action_counts[action_type] = self.action_counts.get(action_type, 0) + 1
        self._dispatch(action_type, candidates, left, top, w, h)

    def _dispatch(self, action_type, candidates, left, top, w, h):
        if action_type == "click_vision_candidate" and candidates:
            scores = [c.get("score", 1.0) for c in candidates]
            chosen = random.choices(candidates, weights=scores, k=1)[0]
            x, y = left + chosen["cx"], top + chosen["cy"]
            ic.click(x, y)
            self.logger.log_event(
                "input", {"action": "mouse_click", "x": x, "y": y, "coord_space": "screen", "source": "vision_candidate"}
            )
        elif action_type == "press_common_key":
            key = random.choice(self.config.key_pool)
            ic.key_press(key)
            self.logger.log_event("input", {"action": "key_press", "key": key})
        elif action_type == "random_click_in_bounds":
            x = left + random.randint(0, max(w - 1, 0))
            y = top + random.randint(0, max(h - 1, 0))
            ic.click(x, y)
            self.logger.log_event(
                "input", {"action": "mouse_click", "x": x, "y": y, "coord_space": "screen", "source": "random"}
            )
        elif action_type == "mouse_drag":
            x1 = left + random.randint(0, max(w - 1, 0))
            y1 = top + random.randint(0, max(h - 1, 0))
            x2 = left + random.randint(0, max(w - 1, 0))
            y2 = top + random.randint(0, max(h - 1, 0))
            ic.drag(x1, y1, x2, y2)
            self.logger.log_event(
                "input", {"action": "mouse_drag", "x": x1, "y": y1, "x2": x2, "y2": y2, "coord_space": "screen"}
            )
        elif action_type == "scroll_wheel":
            amount = random.choice([-1, 1]) * random.randint(1, 3)
            ic.scroll(amount)
            self.logger.log_event("input", {"action": "scroll", "amount": amount})
