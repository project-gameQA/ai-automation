"""Replay a session's events.jsonl input sequence against the same target
window/coordinate space, for deterministic-ish bug reproduction."""
import json
import time

from . import input_controller as ic


def replay(events_path, speed=1.0):
    with open(events_path, "r", encoding="utf-8") as f:
        lines = [json.loads(line) for line in f if line.strip()]

    inputs = [e for e in lines if e["type"] == "input" and e["data"].get("action") != "escalation"]
    if not inputs:
        print("No replayable input events found.")
        return

    last_ts = inputs[0]["ts"]
    for e in inputs:
        delay = (e["ts"] - last_ts) / speed
        if delay > 0:
            time.sleep(min(delay, 5.0))
        last_ts = e["ts"]

        data = e["data"]
        action = data.get("action")
        try:
            if action == "mouse_click":
                ic.click(data["x"], data["y"])
            elif action == "key_press":
                ic.key_press(data["key"])
            elif action == "mouse_drag":
                ic.drag(data["x"], data["y"], data["x2"], data["y2"])
            elif action == "scroll":
                ic.scroll(data["amount"])
        except Exception as ex:
            print(f"Replay step failed ({action}): {ex}")

    print(f"Replayed {len(inputs)} input events.")
