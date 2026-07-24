import ctypes

import mss
import numpy as np
import win32con
import win32gui
import win32process

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32


def get_client_rect_screen(hwnd):
    """Client-area rect in screen coordinates (excludes title bar/borders), so
    captured pixels and click coordinates share the same coordinate space."""
    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    left, top = win32gui.ClientToScreen(hwnd, (left, top))
    right, bottom = win32gui.ClientToScreen(hwnd, (right, bottom))
    return left, top, right, bottom


def ensure_foreground(hwnd):
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

    if _user32.SetForegroundWindow(hwnd):
        return

    # Windows blocks SetForegroundWindow from a process that isn't already
    # foreground/responding to recent input (the "foreground lock" -- see
    # LockSetForegroundWindow docs). The standard workaround is to attach our
    # input queue to the current foreground thread's before asking, and to
    # the target thread's, then detach immediately after.
    fg_hwnd = _user32.GetForegroundWindow()
    fg_thread = win32process.GetWindowThreadProcessId(fg_hwnd)[0] if fg_hwnd else 0
    target_thread = win32process.GetWindowThreadProcessId(hwnd)[0]
    current_thread = _kernel32.GetCurrentThreadId()
    try:
        if fg_thread:
            _user32.AttachThreadInput(current_thread, fg_thread, True)
        if target_thread and target_thread != fg_thread:
            _user32.AttachThreadInput(target_thread, fg_thread, True)
        _user32.SetForegroundWindow(hwnd)
        _user32.BringWindowToTop(hwnd)
    finally:
        if fg_thread:
            _user32.AttachThreadInput(current_thread, fg_thread, False)
        if target_thread and target_thread != fg_thread:
            _user32.AttachThreadInput(target_thread, fg_thread, False)


class Capture:
    def __init__(self, hwnd):
        self.hwnd = hwnd
        self._sct = mss.mss()

    def grab(self):
        """Returns (frame_bgra_ndarray, (left, top, width, height)) in screen coords."""
        left, top, right, bottom = get_client_rect_screen(self.hwnd)
        w, h = right - left, bottom - top
        if w <= 0 or h <= 0:
            raise RuntimeError("Invalid client rect (window minimized or destroyed?)")
        region = {"left": left, "top": top, "width": w, "height": h}
        img = np.array(self._sct.grab(region))  # BGRA
        return img, (left, top, w, h)
