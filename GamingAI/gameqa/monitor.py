"""Independent polling thread: process alive/crash/hang detection + perf sampling.

Crash detection's authoritative source is the Windows Application Event Log
(WER Event ID 1000/1001), which is generic across any engine/game -- unlike
matching crash-dialog window titles (fragile, localization-dependent), which
is kept only as a secondary/faster non-authoritative signal.
"""
import ctypes
import json
import os
import subprocess
import threading
import time
from ctypes import wintypes

import psutil
import win32evtlog
import win32gui

try:
    import win32pdh

    HAVE_PDH = True
except ImportError:
    HAVE_PDH = False

_kernel32 = ctypes.windll.kernel32
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000
STILL_ACTIVE = 259


class Monitor(threading.Thread):
    def __init__(self, target, logger, config, on_crash=None, on_hang=None):
        super().__init__(daemon=True)
        self.target = target
        self.logger = logger
        self.config = config
        self.on_crash = on_crash
        self.on_hang = on_hang

        self._stop = threading.Event()
        self.crashed = False
        self.hung = False
        self._process = psutil.Process(target.pid)
        self._process.cpu_percent(interval=None)  # prime the internal baseline
        self._last_evt_check = time.time()

        # A handle opened while the process is still alive is required to
        # retrieve its exit code after it dies -- psutil.Process has no
        # .returncode (that's a subprocess.Popen attribute) and Windows won't
        # let a not-yet-opened handle query a process that's already gone.
        self._proc_handle = _kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, target.pid
        )

        self._presentmon_proc = None
        self._presentmon_csv = None
        if config.presentmon_path:
            self._start_presentmon()

    def stop(self):
        self._stop.set()
        if self._presentmon_proc:
            try:
                self._presentmon_proc.terminate()
            except Exception:
                pass
        if self._proc_handle:
            _kernel32.CloseHandle(self._proc_handle)
            self._proc_handle = None

    def crash_detected(self):
        return self.crashed

    def hang_detected(self):
        return self.hung

    def _get_exit_code(self):
        if not self._proc_handle:
            return None
        code = wintypes.DWORD()
        if _kernel32.GetExitCodeProcess(self._proc_handle, ctypes.byref(code)):
            return None if code.value == STILL_ACTIVE else code.value
        return None

    def _start_presentmon(self):
        try:
            exe_name = self._process.name()
        except Exception:
            return
        out_path = str(self.logger.dir / "presentmon.csv")
        try:
            self._presentmon_proc = subprocess.Popen(
                [
                    self.config.presentmon_path,
                    "--process_name", exe_name,
                    "--output_file", out_path,
                    "--multi_csv",
                    "--terminate_on_proc_exit",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._presentmon_csv = out_path
        except Exception as e:
            self.logger.log_event("error", {"source": "monitor", "message": f"PresentMon failed to start: {e}"})

    def _check_alive(self):
        try:
            return self._process.is_running() and self._process.status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return False

    def _check_hang(self):
        try:
            return bool(win32gui.IsHungAppWindow(self.target.hwnd))
        except Exception:
            return False

    def _check_wer_crash(self):
        """Scan the Application event log for a recent Event ID 1000/1001
        (Application Error / WER report) mentioning our executable."""
        hand = None
        try:
            hand = win32evtlog.OpenEventLog(None, "Application")
            flags = win32evtlog.EVENTLOG_BACKWARDS_READ | win32evtlog.EVENTLOG_SEQUENTIAL_READ
            events = win32evtlog.ReadEventLog(hand, flags, 0)
            exe_name = os.path.basename(self.target.exe or "").lower()
            for ev in events:
                if (ev.EventID & 0xFFFF) not in (1000, 1001):
                    continue
                try:
                    ts = ev.TimeGenerated.timestamp()
                except Exception:
                    ts = 0
                if ts and ts < self._last_evt_check - 10:
                    break
                strings = list(ev.StringInserts or [])
                if exe_name and any(exe_name in (s or "").lower() for s in strings):
                    return True, ev.EventID & 0xFFFF, strings
            return False, None, None
        except Exception:
            return False, None, None
        finally:
            if hand:
                try:
                    win32evtlog.CloseEventLog(hand)
                except Exception:
                    pass

    def _sample_gpu(self):
        if not HAVE_PDH:
            return "unavailable", "{}"
        try:
            _, items = win32pdh.EnumObjectItems(None, None, "GPU Engine", win32pdh.PERF_DETAIL_WIZARD)
            pid_tag = f"pid_{self.target.pid}_"
            matches = [i for i in items if pid_tag in i]
            if not matches:
                return "unavailable", "{}"

            query = win32pdh.OpenQuery()
            counters = []
            for inst in matches:
                path = win32pdh.MakeCounterPath((None, "GPU Engine", inst, None, -1, "Utilization Percentage"))
                counters.append((inst, win32pdh.AddCounter(query, path)))
            win32pdh.CollectQueryData(query)
            time.sleep(0.05)
            win32pdh.CollectQueryData(query)

            breakdown = {}
            total = 0.0
            for inst, counter in counters:
                _, val = win32pdh.GetFormattedCounterValue(counter, win32pdh.PDH_FMT_DOUBLE)
                total += val
                engtype = inst.split("engtype_")[-1] if "engtype_" in inst else inst
                breakdown[engtype] = breakdown.get(engtype, 0.0) + val
            win32pdh.CloseQuery(query)
            return round(min(total, 100.0), 2), json.dumps(breakdown)
        except Exception:
            return "unavailable", "{}"

    def _sample_fps(self):
        if self._presentmon_csv and os.path.exists(self._presentmon_csv):
            try:
                with open(self._presentmon_csv, "r") as f:
                    lines = f.readlines()
                if len(lines) > 2:
                    header = lines[0].strip().split(",")
                    last = lines[-1].strip().split(",")
                    if "MsBetweenPresents" in header:
                        idx = header.index("MsBetweenPresents")
                        ms = float(last[idx])
                        if ms > 0:
                            return round(1000.0 / ms, 1), "presentmon"
            except Exception:
                pass
        return None, "unavailable"

    def sample_perf(self):
        try:
            cpu = self._process.cpu_percent(interval=None)
            mem = self._process.memory_info()
            mem_pct = self._process.memory_percent()
        except psutil.NoSuchProcess:
            return
        gpu_pct, gpu_breakdown = self._sample_gpu()
        fps, fps_source = self._sample_fps()
        row = {
            "timestamp": time.time(),
            "elapsed_s": round(time.time() - self.logger._start_time, 2),
            "cpu_percent": cpu,
            "mem_rss_mb": round(mem.rss / (1024 * 1024), 2),
            "mem_percent": round(mem_pct, 2),
            "gpu_percent": gpu_pct,
            "gpu_engine_breakdown_json": gpu_breakdown,
            "fps": fps,
            "fps_source": fps_source,
            "window_responsive": not self._check_hang(),
        }
        self.logger.log_perf(row)

    def run(self):
        next_perf = time.time()
        while not self._stop.is_set():
            if not self._check_alive():
                self.crashed = True
                is_crash, evid, details = self._check_wer_crash()
                exit_code = self._get_exit_code()
                self.logger.log_event(
                    "crash",
                    {
                        "pid": self.target.pid,
                        "exit_code": exit_code,
                        "detection_method": "wer_event_log" if is_crash else "process_exit",
                        "event_log_id": evid,
                        "details": details,
                    },
                )
                if self.on_crash:
                    self.on_crash()
                break

            if self._check_hang():
                if not self.hung:
                    self.hung = True
                    self.logger.log_event("hang", {"detection_method": "is_hung_app_window", "duration_s": 0})
                    if self.on_hang:
                        self.on_hang()
            else:
                self.hung = False

            if time.time() >= next_perf:
                self.sample_perf()
                next_perf = time.time() + self.config.perf_interval_s

            self._last_evt_check = time.time()
            time.sleep(0.5)
