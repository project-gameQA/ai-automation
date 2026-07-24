import csv
import json
import time
from pathlib import Path


class SessionLogger:
    """Per-run session logger: events.jsonl, screenshots/, perf.csv, summary.json."""

    def __init__(self, base_dir, session_name):
        self.dir = Path(base_dir) / session_name
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "screenshots").mkdir(exist_ok=True)

        self._events_path = self.dir / "events.jsonl"
        self._events_file = open(self._events_path, "a", encoding="utf-8")

        self._perf_path = self.dir / "perf.csv"
        perf_existed = self._perf_path.exists() and self._perf_path.stat().st_size > 0
        self._perf_file = open(self._perf_path, "a", newline="", encoding="utf-8")
        self._perf_writer = csv.writer(self._perf_file)
        self._perf_header_written = perf_existed

        self._seq = 0
        self._start_time = time.time()
        self._video_writer = None
        self._video_size = None
        self._video_fps = 5

    def log_event(self, event_type, data=None):
        self._seq += 1
        entry = {"ts": time.time(), "seq": self._seq, "type": event_type, "data": data or {}}
        self._events_file.write(json.dumps(entry, default=str) + "\n")
        self._events_file.flush()
        return entry

    def save_screenshot(self, frame_bgra, trigger="periodic", frame_hash=None):
        import cv2

        idx = self._seq
        rel_path = f"screenshots/{idx:06d}.png"
        # cv2.imwrite silently fails (returns False, no exception) for paths
        # containing non-ASCII characters on Windows -- its PNG encoder uses
        # ANSI fopen under the hood. Session directories are named after the
        # (often non-English) window title, so this isn't an edge case here.
        # Encoding in memory and writing the bytes via a normal Python file
        # handle sidesteps it, since Python's own I/O handles Unicode paths
        # correctly on Windows.
        ok, buf = cv2.imencode(".png", frame_bgra)
        if ok:
            with open(self.dir / rel_path, "wb") as f:
                f.write(buf.tobytes())
        else:
            self.log_event("error", {"source": "logger", "message": "cv2.imencode failed for screenshot"})
        self.log_event(
            "screenshot",
            {"path": rel_path, "trigger": trigger, "frame_hash": str(frame_hash) if frame_hash else None},
        )
        return rel_path

    def write_video_frame(self, frame_bgr):
        """Appends a frame to video.mp4, opening the writer lazily on the first
        frame. This is a *timelapse* of captured frames encoded at the
        configured video_fps, not a real-time recording -- since frames are
        only captured at action_interval_s cadence, encoding at real-time fps
        would produce a mostly-frozen video. Use the `ts` field in
        events.jsonl to reconstruct real elapsed time between frames if
        needed. Frames with a different size than the first (e.g. the target
        window was resized mid-session) are dropped with a logged warning
        rather than crashing the writer.
        """
        import cv2

        h, w = frame_bgr.shape[:2]
        if self._video_writer is None:
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            self._video_writer = cv2.VideoWriter(str(self.dir / "video.mp4"), fourcc, self._video_fps, (w, h))
            self._video_size = (w, h)
        if (w, h) != self._video_size:
            self.log_event("error", {"source": "logger", "message": f"video frame size {(w, h)} != {self._video_size}, dropped"})
            return
        self._video_writer.write(frame_bgr)

    def set_video_fps(self, fps):
        self._video_fps = fps

    def log_perf(self, row: dict):
        if not self._perf_header_written:
            self._perf_writer.writerow(list(row.keys()))
            self._perf_header_written = True
        self._perf_writer.writerow(list(row.values()))
        self._perf_file.flush()

    def write_summary(self, summary: dict):
        with open(self.dir / "summary.json", "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, default=str)

    def close(self):
        self._events_file.close()
        self._perf_file.close()
        if self._video_writer is not None:
            self._video_writer.release()
