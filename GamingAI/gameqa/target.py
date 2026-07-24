import ctypes
import sys

import psutil
import win32gui
import win32process


def set_dpi_awareness():
    """Must be called once at process startup, before any window enumeration or
    capture, or window rects will be DPI-virtualized and silently misaligned
    with captured pixels / click coordinates on scaled displays."""
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _enum_windows():
    results = []

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        exe, name = None, None
        try:
            proc = psutil.Process(pid)
            exe = proc.exe()
            name = proc.name()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        results.append({"hwnd": hwnd, "title": title, "pid": pid, "exe": exe, "name": name})

    win32gui.EnumWindows(cb, None)
    return results


def list_windows():
    return _enum_windows()


def find_target(match: str):
    """Match against window title substring or process name (case-insensitive)."""
    match_l = match.lower()
    return [
        w
        for w in _enum_windows()
        if match_l in w["title"].lower() or (w["name"] and match_l in w["name"].lower())
    ]


class Target:
    def __init__(self, hwnd, pid, title, exe):
        self.hwnd = hwnd
        self.pid = pid
        self.title = title
        self.exe = exe


def resolve(match: str) -> Target:
    candidates = find_target(match)
    if not candidates:
        raise RuntimeError(f"No window matched '{match}'. Use `main.py list` to see available windows.")
    if len(candidates) > 1:
        print(f"Warning: {len(candidates)} windows matched '{match}'; using the first one:", file=sys.stderr)
        for c in candidates:
            marker = "->" if c is candidates[0] else "  "
            print(f"  {marker} pid={c['pid']:<7} proc={str(c['name']):<28} title={c['title']!r}", file=sys.stderr)
        print("Use a more specific --target to disambiguate.", file=sys.stderr)
    c = candidates[0]
    return Target(c["hwnd"], c["pid"], c["title"], c["exe"])
