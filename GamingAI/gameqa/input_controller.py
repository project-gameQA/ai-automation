"""Synthetic keyboard/mouse input via the raw SendInput() Win32 API.

Keyboard uses hardware scan codes (KEYEVENTF_SCANCODE) rather than virtual-key
codes -- games are far more likely to respond to scan codes since that's what
a real keyboard produces. Mouse uses absolute coordinates normalized across
the full virtual screen (all monitors), matching the screen-space coordinates
produced by capture.py.

Note: this is still SendInput-based, so it carries the same anti-cheat
exposure as any other synthetic-input tool (see README limitations).
"""
import ctypes
import time
from ctypes import wintypes

user32 = ctypes.windll.user32

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_WHEEL = 0x0800
WHEEL_DELTA = 120

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", INPUT_UNION)]


# US QWERTY Set-1 scan codes (non-extended keys).
SCAN_CODES = {
    "esc": 0x01, "1": 0x02, "2": 0x03, "3": 0x04, "4": 0x05, "5": 0x06,
    "6": 0x07, "7": 0x08, "8": 0x09, "9": 0x0A, "0": 0x0B,
    "minus": 0x0C, "equals": 0x0D, "backspace": 0x0E, "tab": 0x0F,
    "q": 0x10, "w": 0x11, "e": 0x12, "r": 0x13, "t": 0x14, "y": 0x15,
    "u": 0x16, "i": 0x17, "o": 0x18, "p": 0x19,
    "leftbracket": 0x1A, "rightbracket": 0x1B, "enter": 0x1C, "leftctrl": 0x1D,
    "a": 0x1E, "s": 0x1F, "d": 0x20, "f": 0x21, "g": 0x22, "h": 0x23,
    "j": 0x24, "k": 0x25, "l": 0x26,
    "semicolon": 0x27, "quote": 0x28, "grave": 0x29, "leftshift": 0x2A, "backslash": 0x2B,
    "z": 0x2C, "x": 0x2D, "c": 0x2E, "v": 0x2F, "b": 0x30, "n": 0x31, "m": 0x32,
    "comma": 0x33, "period": 0x34, "slash": 0x35,
    "rightshift": 0x36, "multiply": 0x37, "leftalt": 0x38, "space": 0x39, "capslock": 0x3A,
    "f1": 0x3B, "f2": 0x3C, "f3": 0x3D, "f4": 0x3E, "f5": 0x3F,
    "f6": 0x40, "f7": 0x41, "f8": 0x42, "f9": 0x43, "f10": 0x44,
    "numlock": 0x45, "scrolllock": 0x46, "f11": 0x57, "f12": 0x58,
}

# Extended (E0-prefixed) keys -- need KEYEVENTF_EXTENDEDKEY set.
EXTENDED_SCAN_CODES = {
    "up": 0x48, "down": 0x50, "left": 0x4B, "right": 0x4D,
    "insert": 0x52, "delete": 0x53, "home": 0x47, "end": 0x4F,
    "pageup": 0x49, "pagedown": 0x51, "rightctrl": 0x1D, "rightalt": 0x38,
}


def _send(*inputs):
    n = len(inputs)
    arr = (INPUT * n)(*inputs)
    sent = user32.SendInput(n, arr, ctypes.sizeof(INPUT))
    if sent != n:
        raise ctypes.WinError(ctypes.get_last_error())


def _key_input(scan, flags):
    ki = KEYBDINPUT(wVk=0, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)
    return INPUT(type=INPUT_KEYBOARD, union=INPUT_UNION(ki=ki))


def _mouse_input(dx, dy, data, flags):
    mi = MOUSEINPUT(dx=dx, dy=dy, mouseData=data, dwFlags=flags, time=0, dwExtraInfo=0)
    return INPUT(type=INPUT_MOUSE, union=INPUT_UNION(mi=mi))


def _resolve_scan(key):
    key = key.lower()
    if key in EXTENDED_SCAN_CODES:
        return EXTENDED_SCAN_CODES[key], True
    if key in SCAN_CODES:
        return SCAN_CODES[key], False
    raise ValueError(f"Unknown key '{key}'")


def key_down(key):
    scan, extended = _resolve_scan(key)
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_EXTENDEDKEY if extended else 0)
    _send(_key_input(scan, flags))


def key_up(key):
    scan, extended = _resolve_scan(key)
    flags = KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP | (KEYEVENTF_EXTENDEDKEY if extended else 0)
    _send(_key_input(scan, flags))


def key_press(key, hold_s=0.05):
    key_down(key)
    time.sleep(hold_s)
    key_up(key)


def _virtual_screen_rect():
    x = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    y = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    w = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    h = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    return x, y, w, h


def move_to(x, y):
    # MOUSEEVENTF_VIRTUALDESK is required alongside ABSOLUTE for the 0-65535
    # normalized coordinates to be interpreted against the full multi-monitor
    # virtual desktop rather than just the primary display -- without it,
    # clicks land wrong on any setup with a secondary monitor to the
    # left/above the primary (negative virtual-screen origin).
    vx, vy, vw, vh = _virtual_screen_rect()
    nx = int(((x - vx) * 65535) / max(vw - 1, 1))
    ny = int(((y - vy) * 65535) / max(vh - 1, 1))
    _send(_mouse_input(nx, ny, 0, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK))


def click(x, y, button="left", hold_s=0.05):
    move_to(x, y)
    down_flag = MOUSEEVENTF_LEFTDOWN if button == "left" else MOUSEEVENTF_RIGHTDOWN
    up_flag = MOUSEEVENTF_LEFTUP if button == "left" else MOUSEEVENTF_RIGHTUP
    _send(_mouse_input(0, 0, 0, down_flag))
    time.sleep(hold_s)
    _send(_mouse_input(0, 0, 0, up_flag))


def drag(x1, y1, x2, y2, steps=10, step_delay=0.02):
    move_to(x1, y1)
    _send(_mouse_input(0, 0, 0, MOUSEEVENTF_LEFTDOWN))
    for i in range(1, steps + 1):
        ix = x1 + (x2 - x1) * i // steps
        iy = y1 + (y2 - y1) * i // steps
        move_to(ix, iy)
        time.sleep(step_delay)
    _send(_mouse_input(0, 0, 0, MOUSEEVENTF_LEFTUP))


def scroll(amount):
    """amount: positive = scroll up/forward, negative = down/back (in wheel clicks)."""
    _send(_mouse_input(0, 0, int(amount * WHEEL_DELTA), MOUSEEVENTF_WHEEL))
