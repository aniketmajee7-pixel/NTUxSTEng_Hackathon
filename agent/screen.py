"""The agent's eyes: read-only Win32 perception (stdlib ctypes only).

Three cheap signals let the agent act on what is actually on screen instead of blindly:
  * a downscaled grayscale grab of a window region (GDI StretchBlt from the screen DC),
    analysed by the pure `vision.py`;
  * the live mouse-cursor shape (I-beam over text, hand over a link), the same cue a person
    uses before clicking;
  * the clipboard text and sequence number, to confirm a Ctrl+C really copied something and
    to read back a Calc cell's value.
Nothing here injects input, writes files or touches the network.
"""
import ctypes
import time
from ctypes import wintypes

import vision

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_VP = ctypes.c_void_p
for _f, _res, _args in [
    ("GetDC", _VP, [wintypes.HWND]), ("ReleaseDC", ctypes.c_int, [wintypes.HWND, _VP]),
    ("GetClipboardData", _VP, [wintypes.UINT]), ("OpenClipboard", wintypes.BOOL, [wintypes.HWND]),
    ("LoadCursorW", _VP, [_VP, _VP]), ("GetClipboardSequenceNumber", wintypes.DWORD, []),
]:
    getattr(user32, _f).restype, getattr(user32, _f).argtypes = _res, _args
for _f, _res, _args in [
    ("CreateCompatibleDC", _VP, [_VP]), ("CreateCompatibleBitmap", _VP, [_VP, ctypes.c_int, ctypes.c_int]),
    ("SelectObject", _VP, [_VP, _VP]), ("DeleteObject", wintypes.BOOL, [_VP]),
    ("DeleteDC", wintypes.BOOL, [_VP]), ("SetStretchBltMode", ctypes.c_int, [_VP, ctypes.c_int]),
    ("StretchBlt", wintypes.BOOL, [_VP] + [ctypes.c_int] * 4 + [_VP] + [ctypes.c_int] * 4 + [wintypes.DWORD]),
    ("GetDIBits", ctypes.c_int, [_VP, _VP, wintypes.UINT, wintypes.UINT, _VP, _VP, wintypes.UINT]),
]:
    getattr(gdi32, _f).restype, getattr(gdi32, _f).argtypes = _res, _args
kernel32.GlobalLock.restype, kernel32.GlobalLock.argtypes = _VP, [_VP]
kernel32.GlobalUnlock.argtypes = [_VP]

SRCCOPY, COLORONCOLOR, DIB_RGB_COLORS, CF_UNICODETEXT = 0x00CC0020, 3, 0, 13
IDC_ARROW, IDC_IBEAM, IDC_HAND = 32512, 32513, 32649


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class CURSORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("hCursor", _VP), ("ptScreenPos", wintypes.POINT)]


# ---------------------------------------------------------------- regions

def window_rect(hwnd):
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


EnumChildProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def content_rect(hwnd):
    """Screen rect of the page area: Chrome's render widget if present, else the window."""
    best = []

    def cb(child, _):
        buf = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(child, buf, 64)
        if buf.value == "Chrome_RenderWidgetHostHWND" and user32.IsWindowVisible(child):
            l, t, r, b = window_rect(child)
            best.append(((r - l) * (b - t), (l, t, r, b)))
        return True

    user32.EnumChildWindows(hwnd, EnumChildProc(cb), 0)
    if best:
        return max(best)[1]
    l, t, r, b = window_rect(hwnd)
    return l, t + (b - t) // 8, r, b                    # skip the title/tool bars


def grab_gray(rect, step=2):
    """Grayscale bytes of `rect` (screen coords) downscaled by `step`. Returns (w, h, bytes)."""
    l, t, r, b = rect
    sw, sh = max(1, r - l), max(1, b - t)
    w, h = max(1, sw // step), max(1, sh // step)
    screen = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(screen)
    bmp = gdi32.CreateCompatibleBitmap(screen, w, h)
    old = gdi32.SelectObject(mem, bmp)
    try:
        gdi32.SetStretchBltMode(mem, COLORONCOLOR)      # nearest pixel: keeps glyph edges sharp
        gdi32.StretchBlt(mem, 0, 0, w, h, screen, l, t, sw, sh, SRCCOPY)
        gdi32.SelectObject(mem, old)                    # GetDIBits needs it deselected
        bmi = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=w, biHeight=-h,
                               biPlanes=1, biBitCount=32, biCompression=0)
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bmi), DIB_RGB_COLORS)
    finally:
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(None, screen)
    raw = buf.raw
    bl, gr, rd = raw[0::4], raw[1::4], raw[2::4]
    return w, h, bytes((x + y + y + z) >> 2 for x, y, z in zip(bl, gr, rd))


def wait_painted(hwnd, timeout=12.0):
    """Wait (polling ~3x/s on a coarse 1/8 grab) until the page area is no longer blank."""
    end = time.time() + timeout
    while time.time() < end:
        if not vision.is_blank(*grab_gray(content_rect(hwnd), step=8)):
            return True
        time.sleep(0.3)
    return False


# ---------------------------------------------------------------- cursor

_CURSORS = {}


def cursor_kind():
    """'ibeam' | 'hand' | 'arrow' | 'other' for the cursor shape currently shown."""
    if not _CURSORS:
        for name, cid in (("arrow", IDC_ARROW), ("ibeam", IDC_IBEAM), ("hand", IDC_HAND)):
            _CURSORS[user32.LoadCursorW(None, ctypes.c_void_p(cid))] = name
    ci = CURSORINFO(cbSize=ctypes.sizeof(CURSORINFO))
    if not user32.GetCursorInfo(ctypes.byref(ci)):
        return "other"
    return _CURSORS.get(ci.hCursor, "other")


# ---------------------------------------------------------------- clipboard

def clipboard_seq():
    return user32.GetClipboardSequenceNumber()


def clipboard_text():
    """Current clipboard text, or '' if none / the clipboard is busy. Read-only."""
    for _ in range(5):
        if user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        return ""
    try:
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return ""
        p = kernel32.GlobalLock(h)
        if not p:
            return ""
        try:
            return ctypes.wstring_at(p)
        finally:
            kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()


def wait_clipboard_change(seq, timeout=1.5):
    """Wait until the clipboard sequence number moves past `seq`; True if it did."""
    end = time.time() + timeout
    while time.time() < end:
        if clipboard_seq() != seq:
            return True
        time.sleep(0.05)
    return False
