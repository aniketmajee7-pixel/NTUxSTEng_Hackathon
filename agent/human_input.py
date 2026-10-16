"""Human-like input primitives over Win32 SendInput (stdlib only).

The one layer that injects input. It plays the device-independent plans from `timing.py` and
`mouse.py` out as real SendInput events, so the cadence validated off-VM by `judge_sim.py` is
what runs here.

  * Typing is an event timeline: key-down and key-up are scheduled independently, so a key can
    go down before the previous one releases (real rollover) and effective WPM == nominal WPM.
  * Every SendInput return value is checked: UIPI silently drops injection into a higher-
    integrity or unfocused window, and a dropped event should abort loudly.
  * Clicks are guarded against the console window (a synthetic click there toggles QuickEdit
    and suspends the process writing to stdout), and QuickEdit is disabled at start-up.
  * `focus()` brings a window forward (Alt+Tab when it is the next window, as a person would)
    and verifies it before any keys are sent.
"""
import ctypes
import time
from ctypes import wintypes

from persona import PERSONA
import mouse
import timing
from screen import class_name, poll, window_rect

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")
user32.SetProcessDPIAware()
user32.VkKeyScanW.restype = ctypes.c_short
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetKeyState.restype = ctypes.c_short
user32.WindowFromPoint.restype = wintypes.HWND
user32.WindowFromPoint.argtypes = [wintypes.POINT]
# HWND-returning calls must not be truncated to a C int on 64-bit Python
for _f in ("GetForegroundWindow", "GetAncestor", "GetWindow"):
    getattr(user32, _f).restype = wintypes.HWND
kernel32.GetConsoleWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = wintypes.LONG

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_MOVE, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0001, 0x0002, 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x0001, 0x0002, 0x0004
VK_SHIFT, VK_RETURN, VK_BACK, VK_LWIN = 0x10, 0x0D, 0x08, 0x5B
VK_TAB, VK_CONTROL, VK_HOME, VK_END = 0x09, 0x11, 0x24, 0x23
VK_MENU, VK_LEFT, VK_RIGHT, VK_UP, VK_DOWN, VK_F4 = 0x12, 0x25, 0x27, 0x26, 0x28, 0x73
VK_DELETE, VK_ESCAPE, VK_INSERT, VK_PRIOR, VK_NEXT = 0x2E, 0x1B, 0x2D, 0x21, 0x22
VK_CAPITAL, VK_OEM_PLUS, VK_F5 = 0x14, 0xBB, 0x74
MOUSEEVENTF_WHEEL, WHEEL_DELTA = 0x0800, 120
ULONG_PTR = ctypes.c_size_t

# Keys a real keyboard sends with the E0 extended prefix. Omitting it is a possible
# hook-level tell, so we set KEYEVENTF_EXTENDEDKEY for these.
_EXTENDED = {VK_LEFT, VK_RIGHT, VK_UP, VK_DOWN, VK_HOME, VK_END, VK_DELETE, VK_INSERT,
             VK_PRIOR, VK_NEXT, VK_LWIN, 0x5C, 0xA3, 0xA5}

# cached once: GetSystemMetrics on every move would be wasteful
SCREEN_W = user32.GetSystemMetrics(0)
SCREEN_H = user32.GetSystemMetrics(1)


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


_last_evt = -1e9        # perf_counter of the last injected event


def _send(inp):
    """Inject one event; raise if the OS dropped it (UIPI / wrong focus)."""
    global _last_evt
    if user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT)) != 1:
        raise OSError(f"SendInput dropped an event (err {ctypes.get_last_error()}); "
                      "target window may be higher-integrity or unfocused")
    _last_evt = time.perf_counter()


def _sleep_until(start, t):
    """Sleep to absolute deadline start+t (perf_counter), so a plan's sleeps don't drift."""
    left = t - (time.perf_counter() - start)
    if left > 0:
        time.sleep(left)


def _flight():
    """Before a tap or chord: a human release-to-press gap after whatever input came just
    before (back-to-back taps would otherwise be ~0 ms apart, which no hand does)."""
    need = timing.flight_s() - (time.perf_counter() - _last_evt)
    if need > 0:
        time.sleep(need)


def _send_mouse(**fields):
    _send(INPUT(type=INPUT_MOUSE, u=_INPUTUNION(mi=MOUSEINPUT(**fields))))


def _send_key(**fields):
    _send(INPUT(type=INPUT_KEYBOARD, u=_INPUTUNION(ki=KEYBDINPUT(**fields))))


def disable_quickedit():
    """Clear console QuickEdit so a stray click in our own window can't suspend us."""
    try:
        STD_INPUT = -10
        ENABLE_EXTENDED_FLAGS, ENABLE_QUICKEDIT = 0x0080, 0x0040
        h = kernel32.GetStdHandle(STD_INPUT)
        mode = wintypes.DWORD()
        if kernel32.GetConsoleMode(h, ctypes.byref(mode)):
            kernel32.SetConsoleMode(h, (mode.value & ~ENABLE_QUICKEDIT) | ENABLE_EXTENDED_FLAGS)
    except Exception:
        pass  # no console (e.g. run under pythonw) -> nothing to guard


# ---------- keyboard ----------

def _key(vk, up=False):
    flags = (KEYEVENTF_KEYUP if up else 0) | (KEYEVENTF_EXTENDEDKEY if vk in _EXTENDED else 0)
    _send_key(wVk=vk, wScan=user32.MapVirtualKeyW(vk, 0), dwFlags=flags)


def _unicode(c, up=False):
    _send_key(wScan=ord(c), dwFlags=KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0))


# both sides of each modifier: Win L/R, Shift L/R, Ctrl L/R, Alt L/R (right Ctrl/Alt are E0 keys)
_MODIFIERS = (VK_LWIN, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5)
_MASK = 0xE8        # an unassigned VK: tapped while Win/Alt is held, their key-up opens nothing


_caps_tapped = False


def _caps_off():
    """Caps Lock is a toggle, so a stale ON (seen before a run starts, or left by a host
    shortcut) swaps every letter's case: URLs and formulas would come out wrong. Tap it off -
    once per run, so a stale GetKeyState reading can never flip it back and forth."""
    global _caps_tapped
    if not _caps_tapped and user32.GetKeyState(VK_CAPITAL) & 1:
        _caps_tapped = True
        print("    Caps Lock was on; tapping it off", flush=True)
        _flight()
        _key(VK_CAPITAL)
        time.sleep(timing.hold_ms(key=VK_CAPITAL) / 1000.0)
        _key(VK_CAPITAL, up=True)
        time.sleep(timing.short_s(0.12))


def release_stuck():
    """Let go of any modifier held down by something other than us, and Caps Lock if it is on.

    Seen on the VM: a host shortcut (Cmd+Shift+5 on the Mac running the noVNC console) left
    Win+Shift held inside Windows, so Ctrl+S became Win+Ctrl+S (Voice access) and Ctrl+V
    became Win+Ctrl+V (Sound output). Called before every chord and typing burst; we never
    hold a modifier ourselves across those calls.
    """
    _caps_off()
    held = [vk for vk in _MODIFIERS if user32.GetAsyncKeyState(vk) & 0x8000]
    if not held:
        return
    if any(vk in (VK_LWIN, 0x5C, 0xA4, 0xA5) for vk in held):
        _key(_MASK)                    # a lone Win-up opens Start, a lone Alt-up the menu bar
        _key(_MASK, up=True)
    for vk in held:
        _key(vk, up=True)


def tap(vk):
    """Press and release one key with a human hold time."""
    release_stuck()
    _flight()
    _key(vk)
    time.sleep(timing.hold_ms(key=vk) / 1000.0)
    _key(vk, up=True)


def hotkey(*vks):
    """Chord: press in order, release in reverse. try/finally guarantees no stuck modifier."""
    release_stuck()
    _flight()
    pressed = []
    try:
        for vk in vks:
            _key(vk)
            pressed.append(vk)
            time.sleep(timing.short_s(0.065))
    finally:
        for vk in reversed(pressed):
            _key(vk, up=True)
            time.sleep(timing.short_s(0.035))


def hold_chord(mod, vk, count):
    """Hold a modifier and tap `vk` `count` times (Shift+Down x4 grows a Calc selection by four
    rows), as a hand does: the modifier stays down between the presses."""
    release_stuck()
    _flight()
    _key(mod)
    try:
        for i in range(count):
            time.sleep(timing.short_s(0.13 if i else 0.065))
            _key(vk)
            time.sleep(timing.hold_ms(key=vk) / 1000.0)
            _key(vk, up=True)
    finally:
        _key(mod, up=True)
        time.sleep(timing.short_s(0.035))


def hold_key(vk, count):
    """Hold a key so it auto-repeats `count` times (e.g. Backspace over a sentence).

    Mirrors typematic repeat: one press, ~0.5 s delay, then ~30 repeats/s, then release.
    """
    if count <= 0:
        return
    _key(vk)
    time.sleep(PERSONA.rng.gauss(0.5, 0.02))
    for _ in range(count - 1):
        _key(vk)
        time.sleep(PERSONA.rng.gauss(0.033, 0.0015))
    _key(vk, up=True)


def type_text(text, speed=None, typo_rate=None, exact=False, revise=False):
    """Type `text` from an event-timeline plan, giving real rollover and matched WPM.

    `speed` scales this burst relative to the persona's own pace (e.g. 0.75 for a filename
    read off the screen, 1.1 for a command typed from memory), keeping its live pace and
    fatigue. `typo_rate` overrides the persona's error rate. `exact` makes every typo get
    corrected - use it for URLs, filenames and cell values, which must land verbatim.
    `revise` allows the odd false start erased with Ctrl+Backspace (prose in Writer only).
    """
    saved_iki, saved_err = PERSONA.iki_ms, PERSONA.error_rate
    if speed:
        PERSONA.iki_ms /= speed
    if typo_rate is not None:
        PERSONA.error_rate = typo_rate
    try:
        strokes = timing.plan_typing(text, PERSONA, exact=exact, revise=revise)
    finally:
        PERSONA.iki_ms, PERSONA.error_rate = saved_iki, saved_err
    _run_key_timeline(strokes)


def _stroke_key(s):
    """(key id, needs_shift, needs_ctrl) for a planned stroke; see timing.schedule_keys.
    A character no key produces goes out as a Unicode packet (needs_shift None)."""
    if s.action == "back":
        return ("vk", VK_BACK), False, False
    if s.action == "ctrl_back":
        return ("vk", VK_BACK), False, True
    if s.data == "\n":
        return ("vk", VK_RETURN), False, False
    r = user32.VkKeyScanW(ord(s.data))
    if r == -1:
        return ("u", s.data), None, False
    return ("vk", r & 0xFF), bool((r >> 8) & 1), False


def _run_key_timeline(strokes):
    """Play strokes as physically consistent down/up events (see timing.schedule_keys)."""
    events = timing.schedule_keys(strokes, _stroke_key, ("vk", VK_SHIFT), ("vk", VK_CONTROL),
                                  PERSONA.rng)
    release_stuck()
    check_operator()
    down = set()
    start = time.perf_counter()
    try:
        for i, (t, up, (kind, k)) in enumerate(events):
            if i % 16 == 15:
                check_operator(down)                    # GetCursorPos: cheap, and we never move it here
            _sleep_until(start, t)
            if kind == "u":
                _unicode(k, up=up)
            else:
                _key(k, up)
                if up:
                    down.discard(k)
                else:
                    down.add(k)
    finally:
        for k in down:          # never leave a key (above all a modifier) stuck down
            try:
                _key(k, up=True)
            except OSError:
                pass


# ---------- mouse ----------

class OperatorAbort(BaseException):
    """The operator asked us to stop (mouse parked in the top-left corner). BaseException so
    per-task `except Exception` handlers don't swallow it: the agent stops for good."""


class Interrupted(Exception):
    """Someone used the mouse mid-task. We waited until they let go; the task in hand is
    abandoned (focus may have moved) and the day carries on."""


_expected = None        # where we last put the cursor; None until our first move
_TAKEOVER_PX = 40
_STILL_S = 8.0          # hand back after the mouse has been left alone this long
_CORNER_PX, _CORNER_S = 3, 2.0


def cursor_pos():
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def _wait_for_release():
    """Someone else has the mouse: keep our hands off until it has been still for _STILL_S.
    Parking it in the top-left corner for _CORNER_S is the stop button (OperatorAbort)."""
    global _expected
    last, still_since, corner_since = cursor_pos(), time.time(), None
    while True:
        time.sleep(0.25)
        p, now = cursor_pos(), time.time()
        if p[0] <= _CORNER_PX and p[1] <= _CORNER_PX:
            corner_since = corner_since or now
            if now - corner_since >= _CORNER_S:
                raise OperatorAbort("mouse parked in the top-left corner")
        else:
            corner_since = None
        if p != last:
            last, still_since = p, now
        elif corner_since is None and now - still_since >= _STILL_S:
            _expected = p
            return


def check_operator(held=None):
    """If the cursor is far from where we left it, a person is using the mouse (a judge in
    Process Explorer, a noVNC pointer): wait until they let go, then raise Interrupted so the
    task in hand is cleaned up rather than typed into whatever they focused. Under pythonw
    there is no console to Ctrl+C: the top-left corner park is the stop button. Keys in `held`
    are released first, so the person isn't shift-clicking for the whole wait."""
    if _expected is None:
        return
    x, y = cursor_pos()
    if abs(x - _expected[0]) > _TAKEOVER_PX or abs(y - _expected[1]) > _TAKEOVER_PX:
        print(f"    mouse moved by someone else to {(x, y)}; waiting until it is left alone",
              flush=True)
        for k in list(held or ()):
            _key(k, up=True)
            held.discard(k)
        _wait_for_release()
        raise Interrupted(f"mouse taken over at {(x, y)}")


def nap(seconds):
    """time.sleep that keeps an eye on the mouse (checks ~2x/s), for every pause longer than
    a motor gap: reading, thinking, breaks."""
    end = time.time() + seconds
    while True:
        check_operator()
        left = end - time.time()
        if left <= 0:
            return
        time.sleep(min(0.5, left))


def _move_abs(x, y):
    global _expected
    x = min(max(int(x), 0), SCREEN_W - 1)
    y = min(max(int(y), 0), SCREEN_H - 1)
    dx, dy = int(x * 65535 / (SCREEN_W - 1)), int(y * 65535 / (SCREEN_H - 1))
    _send_mouse(dx=dx, dy=dy, dwFlags=MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE)
    _expected = (x, y)


def move_to(x, y):
    """Move via a primary stroke + corrective sub-movements, with occasional overshoot.

    The path math lives in `mouse.plan_move` (pure, so the judge-simulator scores the same
    code); here we just play the samples out with SendInput + sleep.
    """
    check_operator()
    x0, y0 = cursor_pos()
    start, t = time.perf_counter(), 0.0
    for dt, mx, my in mouse.plan_move(x0, y0, x, y, PERSONA):
        _move_abs(mx, my)
        t += dt
        _sleep_until(start, t)


_CONSOLE_CLASSES = {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS"}  # conhost, Win11 Terminal
GA_ROOT = 2


def _at_console():
    """True if the cursor is over a console/terminal window (don't click it: QuickEdit can
    suspend us, and focus would move to the console so later keys land there)."""
    try:
        root = user32.GetAncestor(user32.WindowFromPoint(wintypes.POINT(*cursor_pos())), GA_ROOT)
        own = kernel32.GetConsoleWindow()
        return bool(root) and (root == own or class_name(root) in _CONSOLE_CLASSES)
    except Exception:
        return False


def click():
    check_operator()
    release_stuck()                                     # a held Shift would extend a selection
    if _at_console():
        return  # guard: a click here would toggle QuickEdit and suspend the agent
    _send_mouse(dwFlags=MOUSEEVENTF_LEFTDOWN)
    time.sleep(timing.click_hold_ms() / 1000.0)
    if PERSONA.rng.random() < 0.5:                      # 1 px drift between down and up
        _move_abs(*[c + PERSONA.rng.choice((-1, 1)) for c in cursor_pos()])
    _send_mouse(dwFlags=MOUSEEVENTF_LEFTUP)


def double_click():
    """Two clicks well inside the double-click time, as when selecting a word."""
    click()
    time.sleep(min(0.3, timing.short_s(0.11, 0.3)))
    click()


def scroll(notches):
    """Wheel-scroll about `notches` ticks (positive = down) as flicks: see timing.plan_scroll."""
    if not notches:
        return
    release_stuck()                                     # Shift held would scroll sideways
    check_operator()
    start, t = time.perf_counter(), 0.0
    for dt, d in timing.plan_scroll(notches, PERSONA):
        t += dt
        _sleep_until(start, t)
        step = -WHEEL_DELTA if d > 0 else WHEEL_DELTA
        _send_mouse(mouseData=ctypes.c_uint32(step).value, dwFlags=MOUSEEVENTF_WHEEL)


# ---------- windows ----------

EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def _top_windows(pred):
    """Visible top-level windows satisfying pred(hwnd), in z-order."""
    found = []

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd) and pred(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return found


def find_windows(title_part, exact=False):
    """All visible top-level windows whose title contains (or equals) title_part, in z-order."""
    want = title_part.lower()

    def match(hwnd):
        title = window_title(hwnd).lower()
        return title == want if exact else want in title

    return _top_windows(match)


def find_windows_by_class(cls):
    """Visible top-level windows of a window class (e.g. 'CabinetWClass' = File Explorer)."""
    return _top_windows(lambda hwnd: class_name(hwnd) == cls)


def find_window(title_part, exact=False):
    """Topmost visible top-level window whose title contains (or equals) title_part."""
    found = find_windows(title_part, exact)
    return found[0] if found else None


def is_window(hwnd):
    return bool(hwnd) and bool(user32.IsWindow(hwnd)) and bool(user32.IsWindowVisible(hwnd))


def foreground():
    return user32.GetForegroundWindow()


def _alt_tab_order():
    """Top-level windows in the order Alt+Tab offers them (z-order, switchable ones only)."""
    GW_OWNER, GWL_EXSTYLE, WS_EX_TOOLWINDOW, DWMWA_CLOAKED = 4, -20, 0x80, 14

    def switchable(hwnd):
        if (not user32.GetWindowTextLengthW(hwnd) or user32.GetWindow(hwnd, GW_OWNER)
                or user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW):
            return False
        cloaked = wintypes.DWORD()
        dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), 4)
        return not cloaked.value

    return _top_windows(switchable)


def _wait_foreground(hwnd, timeout):
    return bool(poll(lambda: foreground() == hwnd, timeout, 0.05))


def focus(hwnd):
    """Bring `hwnd` to the foreground before sending it keys.

    When it is the window just behind the current one (the usual browser<->editor ping-pong),
    a real Alt+Tab does it. Otherwise fall back to SetForegroundWindow, attaching to the
    foreground thread's input so Windows' foreground lock lets it through. Raises if the
    window still is not in front - typing into the wrong window is worse than stopping.
    """
    if not is_window(hwnd):
        raise RuntimeError("focus: window is gone")
    if foreground() == hwnd:
        return
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)                      # SW_RESTORE
    order = _alt_tab_order()
    if len(order) > 1 and order[0] == foreground() and order[1] == hwnd:
        hotkey(VK_MENU, VK_TAB)
        if _wait_foreground(hwnd, 1.5):
            nap(timing.perceive_s())
            return
    fg = foreground()
    me = kernel32.GetCurrentThreadId()
    them = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = bool(them) and them != me and user32.AttachThreadInput(me, them, True)
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(me, them, False)
    if not _wait_foreground(hwnd, 2.0):
        raise RuntimeError(f"focus: could not bring {window_title(hwnd)!r} to the front")
    nap(timing.perceive_s())


def maximize(hwnd):
    """Maximise a window the way a person does it from the keyboard (Win+Up), once.
    Apps reopen at their last size, so a run started with small windows stays small.
    Leaves `hwnd` in the foreground either way."""
    focus(hwnd)
    if user32.IsZoomed(hwnd):
        return
    hotkey(VK_LWIN, VK_UP)
    poll(lambda: user32.IsZoomed(hwnd), 2.0, 0.1)
    nap(timing.perceive_s())


def ensure_focus(hwnd):
    """Cheap guard before a typing burst: refocus only if something else took the foreground."""
    if foreground() != hwnd:
        focus(hwnd)


def window_title(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def wait_for_new_window(title_part, before, timeout=40, window_class=None):
    """Poll until a window matching title_part (or, if given, of `window_class`) appears that
    was not in `before` (a set)."""
    find = (lambda: find_windows_by_class(window_class)) if window_class \
        else (lambda: find_windows(title_part))
    hwnd = poll(lambda: next((h for h in find() if h not in before), None), timeout)
    if not hwnd:
        raise TimeoutError(f"no new window: {title_part!r}")
    return hwnd


def wait_title(hwnd, pred, timeout):
    """True once pred(title of hwnd) holds, checked once more at the deadline."""
    return bool(poll(lambda: pred(window_title(hwnd)), timeout, last_try=True))


def wait_for_window(title_part, timeout=20, gone=False, exact=False):
    """Poll (250 ms) until the window appears (returned), or disappears if gone=True."""
    def check():
        hwnd = find_window(title_part, exact)
        return not hwnd if gone else hwnd

    result = poll(check, timeout)
    if not result:
        raise TimeoutError(f"window {'still open' if gone else 'not found'}: {title_part!r}")
    return None if gone else result


def hover_in(hwnd, fx, fy):
    """Move to a point at fractions (fx, fy) of the window's rect, with noise."""
    l, t, r, b = window_rect(hwnd)
    x = l + (r - l) * fx + PERSONA.rng.gauss(0, 8)
    y = t + (b - t) * fy + PERSONA.rng.gauss(0, 8)
    move_to(int(x), int(y))


def click_in(hwnd, fx, fy):
    hover_in(hwnd, fx, fy)
    nap(timing.think_s(0.3))
    click()


def launch(command):
    """Open via Win+R so the parent process is explorer.exe.

    Keep the Run string bare (e.g. 'chrome', 'swriter') to avoid ClickFix-style RunMRU
    entries; type URLs in the app afterwards. A Delete before Enter clears any inline
    AutoComplete the Run box may have appended.
    """
    hotkey(VK_LWIN, ord("R"))
    run = wait_for_window("Run", timeout=5, exact=True)
    ensure_focus(run)
    time.sleep(timing.short_s(0.6))
    type_text(command, speed=1.1, typo_rate=PERSONA.error_rate * 0.5, exact=True)
    time.sleep(timing.short_s(0.33))
    tap(VK_DELETE)                                      # drop any AutoComplete tail
    time.sleep(timing.short_s(0.33))
    tap(VK_RETURN)


def idle(seconds):
    """Pause like a person for `seconds`: mostly still, with the odd small mouse nudge
    (hand resting on the mouse). Long stretches with no input at all are normal too."""
    end = time.time() + seconds
    rng = PERSONA.rng
    while True:
        left = end - time.time()
        if left <= 0:
            break
        nap(min(left, timing.think_s(12.0)))
        if end - time.time() > 2 and rng.random() < 0.35:
            x, y = cursor_pos()
            move_to(x + rng.gauss(0, 40), y + rng.gauss(0, 30))
