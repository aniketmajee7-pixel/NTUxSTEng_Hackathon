"""Windows input, cancellable pauses, state checks and resource logging."""
import csv
from collections import deque
import json
import logging
import math
import os
import random
import re
import threading
import time

import pyautogui as pg
import win32api
import win32clipboard
import win32con
import win32gui
import win32process
from pywinauto import Desktop
from domain import typing_pause, fitts_duration, pointer_path, adjacent_keys, heavy_pause


class Stopped(Exception):
    pass


class TimeUp(Stopped):
    """The phase's time limit has passed.  It is a kind of stop so that retry and
    "carry on" handlers let it through; the phase catches it and wraps up."""


# Arrows and the navigation block are "extended" keys.  pyautogui sends them without that
# flag, i.e. as numeric-keypad keys, and with NumLock on Windows then drops a held Shift:
# Shift+Down moved the cell cursor without selecting anything.
EXTENDED_KEYS = {'up': 0x26, 'down': 0x28, 'left': 0x25, 'right': 0x27, 'home': 0x24,
                 'end': 0x23, 'pageup': 0x21, 'pagedown': 0x22, 'insert': 0x2D, 'delete': 0x2E}
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, SRCCOPY = 0x0001, 0x0002, 0x00CC0020
# Virtual-key codes of the modifiers, to notice one that is held down by something else.
MODIFIERS = (('win', 0x5B, KEYEVENTF_EXTENDEDKEY), ('win', 0x5C, KEYEVENTF_EXTENDEDKEY),
             ('ctrl', 0x11, 0), ('alt', 0x12, 0), ('shift', 0x10, 0))


class GUI:
    # Run-level style multipliers (defaults keep objects built without __init__ neutral).
    wheel_speed = 1.0   # scales gaps between wheel notches
    typo_scale = 1.0    # scales how often typing mistakes happen
    pause_scale = 1.0   # scales thinking/reading pauses
    time_limit = None   # time.monotonic() value after which check_stop raises TimeUp

    def __init__(self, run_dir):
        self.run_dir = run_dir
        self.pace = random.uniform(0.85, 1.2)
        # Draw a personal style per run so two runs are not statistically identical.
        self.wheel_speed = random.uniform(0.8, 1.3)
        self.typo_scale = random.uniform(0.8, 1.4)
        self.pause_scale = random.uniform(0.85, 1.25)
        pg.FAILSAFE = True
        pg.PAUSE = 0  # fixed post-action delays are a tell; waits are explicit and random
        self.desktop = Desktop(backend='uia')
        self.events = (run_dir / 'actions.jsonl').open('a', encoding='utf-8')
        self.start = time.monotonic()
        self.stop_event = threading.Event()
        self.sampler = threading.Thread(target=self.sample_resources, daemon=True)
        self.sampler.start()

    def event(self, action, **details):
        entry = {'elapsed_seconds': round(time.monotonic()-self.start, 3),
                 'action': action, **details}
        self.events.write(json.dumps(entry)+'\n'); self.events.flush()
        logging.info('%s %s', action, details)

    def check_stop(self):
        if win32api.GetAsyncKeyState(win32con.VK_F8) & 0x8000:
            raise Stopped('Stopped by F8')
        pg.failSafeCheck()
        if self.time_limit is not None and time.monotonic() >= self.time_limit:
            raise TimeUp('Time limit reached')

    def release_stuck_modifiers(self, keep=()):
        """Let go of modifier keys that are down although the agent is not holding them.

        Seen in a recording: a host shortcut (Cmd+Shift+5 to start a screen recording)
        reached the VM console as Windows+Shift held down.  Every Ctrl+S then became
        Windows+Ctrl+S (Voice access), every Ctrl+V Windows+Ctrl+V (Sound output), and
        the run ended with unsaved work."""
        for name, code, flags in MODIFIERS:
            if name in keep:
                continue
            if win32api.GetAsyncKeyState(code) & 0x8000:
                win32api.keybd_event(code, 0, flags | KEYEVENTF_KEYUP, 0)
                self.event('stuck_key_released', key=name)

    def key_down(self, key):
        code = EXTENDED_KEYS.get(key)
        if code is None:
            pg.keyDown(key)
        else:
            win32api.keybd_event(code, 0, KEYEVENTF_EXTENDEDKEY, 0)

    def key_up(self, key):
        code = EXTENDED_KEYS.get(key)
        if code is None:
            pg.keyUp(key)
        else:
            win32api.keybd_event(code, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)

    def sleep(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.check_stop()
            time.sleep(min(0.1, max(0, end-time.monotonic())))

    def pause(self, reading=False):
        median, sigma, floor, cap = (6.0, 0.5, 3.0, 12.0) if reading else (0.67, 0.5, 0.2, 2.5)
        self.sleep(heavy_pause(median*self.pace*self.pause_scale, sigma, floor, cap))

    def wait(self, predicate, label, timeout=30, poll_interval=0.4):
        end = time.monotonic()+timeout
        last_error = None
        while time.monotonic() < end:
            self.check_stop()
            try:
                found = predicate()
                if found:
                    return found
            except (Stopped, pg.FailSafeException):
                raise
            except Exception as exc:
                last_error = exc
            self.sleep(min(poll_interval, max(0, end-time.monotonic())))
        raise RuntimeError(f'Timed out waiting for {label}: {last_error or "not ready"}')

    def window(self, pattern, timeout=30):
        def lookup():
            matches = self.window_handles(title_pattern=pattern)
            return self.desktop.window(handle=matches[0]).wrapper_object() if len(matches) == 1 else None
        return self.wait(lookup, pattern, timeout)

    def window_handles(self, title_pattern=None, class_name=None):
        """Cheap native top-level discovery before attaching a UIA wrapper."""
        matches = []
        pattern = re.compile(title_pattern) if title_pattern else None
        def inspect(handle, _):
            if not win32gui.IsWindowVisible(handle):
                return
            if pattern and not pattern.match(win32gui.GetWindowText(handle)):
                return
            if class_name and win32gui.GetClassName(handle) != class_name:
                return
            matches.append(handle)
        win32gui.EnumWindows(inspect, None)
        return matches

    def walk_controls(self, root, max_depth=5, max_nodes=200, timeout=2.0,
                      prune_types=()):
        """Bound tree work between UIA calls; a single provider call can still block."""
        queue = deque([(root, 0)])
        inspected = 0
        end = time.monotonic()+timeout
        while queue and inspected < max_nodes and time.monotonic() < end:
            self.check_stop()
            control, depth = queue.popleft()
            inspected += 1
            yield control
            if depth >= max_depth:
                continue
            try:
                if control.element_info.control_type in prune_types:
                    continue
                children = control.children()
            except (Stopped, pg.FailSafeException):
                raise
            except Exception:
                continue
            remaining = max(0, max_nodes-inspected-len(queue))
            queue.extend((child, depth+1) for child in children[:remaining])

    def focus(self, window):
        if win32gui.GetForegroundWindow() == window.handle:
            return  # Already active: skip set_focus and its 0.3 s settle delay.
        for attempt in range(2):
            self.check_stop()
            window.set_focus()
            self.sleep(0.3)
            if win32gui.GetForegroundWindow() == window.handle:
                return
            self.event('focus_retry', attempt=attempt+1)
        raise RuntimeError('Cannot confirm expected foreground window')

    def hotkey(self, *keys, window=None):
        if window is not None:
            self.focus(window)
        self.check_stop()
        self.chord(*keys); self.pause()

    def chord(self, *keys):
        """Press a shortcut the way hands do: modifiers go down first, a beat apart,
        the last key is held briefly, then everything is released in reverse."""
        down = []
        self.release_stuck_modifiers()      # a shortcut starts with every modifier up
        try:
            for key in keys[:-1]:
                self.key_down(key); down.append(key)
                time.sleep(random.uniform(0.03, 0.09))
            self.key_down(keys[-1]); down.append(keys[-1])
            time.sleep(random.uniform(0.05, 0.12))
        finally:
            for key in reversed(down):
                self.key_up(key)
                time.sleep(random.uniform(0.01, 0.05))

    def press(self, key):
        """A single non-character key (Enter, Tab, Backspace, arrows), held like any key."""
        self.check_stop()
        self.release_stuck_modifiers(keep=('shift',))   # Shift may be held on purpose (range selection)
        self.tap_key(key)

    def click_here(self):
        """Click at the current pointer position with a real press duration."""
        self.check_stop()
        pg.mouseDown(); time.sleep(random.uniform(0.05, 0.14)); pg.mouseUp()

    def tap_key(self, key, shift=False):
        """Press a key like a finger does: down, held 50-120 ms, up (never instant)."""
        if shift:
            pg.keyDown('shift')
            time.sleep(random.uniform(0.03, 0.09))
        try:
            self.key_down(key)
            try:
                time.sleep(random.uniform(0.05, 0.12))
            finally:
                self.key_up(key)
            if shift:
                time.sleep(random.uniform(0.02, 0.06))
        finally:
            if shift:
                pg.keyUp('shift')

    def type_text(self, text, window, corrected_typo=False, clear_inline_completion=False,
                  typo_kind='wrong'):
        """Type like a person; optionally make one mistake and fix it before moving on.

        typo_kind: 'wrong' (stray letter/digit), 'double' (key repeated) or
        'swap' (two letters reversed).  Formulas are never given mistakes."""
        self.focus(window)
        self.release_stuck_modifiers()
        text = str(text)
        if text.startswith('='):
            corrected_typo = False
        letters = [i for i, char in enumerate(text)
                   if char.isascii() and (char.isalpha() or char.isdigit())]
        # Mistakes cluster at the start of a word, right after a '.' (as in the manual
        # recording, where 'www.a...' became 'www.w' and was corrected).
        after_dot = [i for i in letters if i > 0 and text[i-1] == '.']
        pool_indexes = after_dot if after_dot and random.random() < 0.6 else letters
        typo_index = random.choice(pool_indexes) if corrected_typo and letters else None
        if typo_index is not None:
            char = text[typo_index]
            lower = char.isascii() and char.islower()
            nxt = text[typo_index+1] if typo_index+1 < len(text) else ''
            if typo_kind == 'double' and not lower:
                typo_kind = 'wrong'
            if typo_kind == 'swap' and not (lower and nxt.isascii() and nxt.islower()
                                            and nxt != char):
                typo_kind = 'wrong'
        def ready():
            self.check_stop()
            if win32gui.GetForegroundWindow() != window.handle:
                raise RuntimeError('Foreground window changed during typing')
        word_pace = random.uniform(0.9, 1.25)*self.pace
        shifted = {':': ';', '?': '/', '_': '-', '+': '=', '@': '2',
                   '!': '1', '#': '3', '$': '4', '%': '5', '^': '6',
                   '&': '7', '*': '8', '(': '9', ')': '0', '"': "'",
                   '<': ',', '>': '.', '|': '\\', '{': '[', '}': ']', '~': '`'}
        for i, char in enumerate(text):
            ready()
            if ord(char) > 127:
                raise RuntimeError('This demo types ASCII data only')
            if i == typo_index and typo_kind in ('wrong', 'swap'):
                if typo_kind == 'wrong':
                    neighbours = adjacent_keys(char) or ['x']   # a key beside the intended one
                    mistake = random.choice([c for c in neighbours if c != char.lower()] or neighbours)
                    backspaces = 1
                else:
                    mistake = text[i+1]+char
                    backspaces = 2
                self.sleep(random.uniform(0.1, 0.35)*self.pace)
                for letter in mistake:
                    ready(); self.tap_key(letter)
                    self.sleep(random.uniform(0.08, 0.2)*self.pace)
                self.sleep(random.uniform(0.45, 0.9)*self.pace)  # notice (manual run: ~0.5-0.75 s)
                ready()
                if clear_inline_completion:
                    self.press('delete')  # Remove a selected Chrome autocomplete suffix first.
                for _ in range(backspaces):
                    self.press('backspace')
                    if backspaces > 1:
                        self.sleep(random.uniform(0.08, 0.2)*self.pace)
                self.sleep(random.uniform(0.25, 0.55)*self.pace)  # beat before retyping
                self.event('typing_correction', character_index=i, kind=typo_kind)
            needs_shift = char.isupper() or char in shifted
            if i == 4 and text.startswith('www.'):
                self.sleep(random.uniform(0.5, 1.2)*self.pace)  # pause after 'www.' (manual: ~1 s)
            self.sleep(typing_pause(char, word_pace))
            ready()
            if needs_shift:
                self.sleep(random.uniform(0.15, 0.4)*self.pace)
                ready()
                self.tap_key(char.lower() if char.isupper() else shifted[char], shift=True)
            else:
                self.tap_key(char)
            if i == typo_index and typo_kind == 'double':
                self.sleep(random.uniform(0.04, 0.12)*self.pace)
                ready(); self.tap_key(char)  # key bounced: the letter appears twice
                self.sleep(random.uniform(0.4, 1.1)*self.pace)
                ready()
                if clear_inline_completion:
                    self.press('delete')
                self.press('backspace')
                self.event('typing_correction', character_index=i, kind='double')
            if char == ' ':
                word_pace = random.uniform(0.9, 1.25)*self.pace
            if i > 0 and random.random() < 0.035:
                self.sleep(random.uniform(0.4, 1.1)*self.pace)
        if clear_inline_completion:
            ready(); self.press('delete')  # Submit the typed homepage, without a history suffix.

    def move(self, x, y, width=40.0):
        """Reach (x, y) like a hand: Fitts-law duration, fast launch, slow approach."""
        sx, sy = pg.position()
        distance = math.hypot(x-sx, y-sy)
        duration = fitts_duration(distance, width, self.pace)
        if distance > 150:
            self.sleep(random.uniform(0.05, 0.22)*self.pace)  # reaction before a long reach
        samples = pointer_path((sx, sy), (x, y), duration)
        # An arc or overshoot towards a target near the screen edge (a desktop icon, the
        # first tab) could carry the pointer into a corner, which is pyautogui's
        # emergency-stop position and would end the run.  Keep the path on screen.
        screen_width, screen_height = pg.size()
        started = time.perf_counter()
        for t, px, py in samples:
            self.check_stop()
            wait = started+t-time.perf_counter()
            if wait > 0:
                time.sleep(wait)
            pg.moveTo(min(max(round(px), 3), screen_width-4),
                      min(max(round(py), 3), screen_height-4), _pause=False)
        pg.moveTo(round(x), round(y), _pause=False)
        corrected = samples[-1][0] > duration*1.02
        if corrected:
            self.event('pointer_settle', travel_pixels=round(distance, 1))
        self.event('pointer_route', travel_pixels=round(distance, 1),
                   duration_seconds=round(duration, 3), samples=len(samples))

    def control_point(self, control):
        """Pick within the central half of a visible control, clipped to the display."""
        if not control.is_visible() or not control.is_enabled():
            raise RuntimeError('Target control is not available')
        rect = control.rectangle()
        screen_width, screen_height = pg.size()
        left, top = max(1, rect.left), max(1, rect.top)
        right, bottom = min(screen_width-2, rect.right), min(screen_height-2, rect.bottom)
        if right-left <= 1 or bottom-top <= 1:
            raise RuntimeError('Target control has no usable visible bounds')
        return (left+(right-left)*random.uniform(0.25, 0.75),
                top+(bottom-top)*random.uniform(0.25, 0.75))

    def scroll_position(self, document):
        if document is None:
            return None
        try:
            pattern = document.iface_scroll
            if pattern.CurrentVerticallyScrollable:
                value = float(pattern.CurrentVerticalScrollPercent)
                return value if 0 <= value <= 100 else None
        except (Stopped, pg.FailSafeException):
            raise
        except Exception:
            pass
        return None

    def clear_browser_selection(self, window, document=None):
        self.focus(window)
        self.press('esc')  # Close Ctrl+F or omnibox suggestions before restoring page focus.
        cleared = False
        if document is not None:
            try:
                document.set_focus()
                selected = document.iface_text.GetSelection()
                for i in range(min(selected.Length, 8)):
                    span = selected.GetElement(i)
                    if span.GetText(1):
                        span.MoveEndpointByRange(1, span, 0)  # End -> Start: empty range.
                        span.Select()
                cleared = True
            except (Stopped, pg.FailSafeException):
                raise
            except Exception:
                # Collapse a selection when Chrome exposes no Text pattern.
                self.press('right'); self.press('esc')
        self.event('browser_selection_cleared', verified=cleared)

    WHEEL_DELTA = 120  # pyautogui passes raw units on Windows: one notch is 120.

    def scroll_content(self, window, direction, document=None,
                       fraction_range=(0.75, 1.1), notch_px=100, flick_range=(2, 4),
                       dwell_range=(1.0, 2.6), fling=False):
        """Wheel roughly one viewport as a few quick flicks of whole notches."""
        self.focus(window)
        rect = document.rectangle() if document is not None else window.rectangle()
        # The lower middle of the client avoids Chrome's tabs/address bar and scrollbars.
        outer = window.rectangle()
        if rect.width() <= 0 or rect.height() <= 0:
            rect = outer
        screen_width, screen_height = pg.size()
        left, top = max(1, rect.left, outer.left), max(1, rect.top, outer.top)
        right = min(screen_width-2, rect.left+rect.width(), outer.left+outer.width())
        bottom = min(screen_height-2, rect.top+rect.height(), outer.top+outer.height())
        if right-left <= 1 or bottom-top <= 1:
            raise RuntimeError('Browser content has no usable visible bounds')
        # Travel to the page only when the pointer is outside it; repeated
        # re-aiming between scrolls made the pointer look scripted and slow.
        x, y = pg.position()
        if not (left+10 < x < right-10 and top+10 < y < bottom-10):
            self.move(left+(right-left)*random.uniform(0.55, 0.8),
                      top+(bottom-top)*random.uniform(0.5, 0.7))
        self.check_stop()
        if win32gui.GetForegroundWindow() != window.handle:
            raise RuntimeError('Foreground window changed before scrolling')
        before = self.scroll_position(document)
        total = max(3, round(random.uniform(*fraction_range)*(bottom-top)/notch_px))
        if fling:
            # Upward return in the manual run: a whole page in under a second.
            sizes = [total]
        else:
            # Slow rolling: groups of a few notches (0.15-0.5 s apart, as measured),
            # separated by short reading holds.
            sizes, remaining = [], total
            while remaining > 0:
                size = min(remaining, random.randint(2, 5))
                sizes.append(size); remaining -= size
        for index, size in enumerate(sizes):
            for notch in range(size):
                self.check_stop()
                if win32gui.GetForegroundWindow() != window.handle:
                    raise RuntimeError('Foreground window changed during scrolling')
                pg.scroll(direction*self.WHEEL_DELTA)
                if fling:
                    self.sleep(random.uniform(0.02, 0.055))
                else:
                    gap = random.uniform(0.08, 0.2) if random.random() < 0.2 else random.uniform(0.18, 0.5)
                    self.sleep(gap*self.pace*self.wheel_speed)
            if index < len(sizes)-1:
                self.sleep(random.uniform(0.35, 1.1)*self.pace)
        low, high = dwell_range
        self.sleep(heavy_pause(math.sqrt(low*high)*self.pace*self.pause_scale, 0.5,
                               low*0.6, high*3.5))  # reading pause: skewed, sometimes long
        after = self.scroll_position(document)
        changed = None if before is None or after is None else abs(after-before) > 0.01
        self.event('wheel_input', notches=sizes, wheel_units=direction*total*self.WHEEL_DELTA,
                   before_percent=before, after_percent=after, movement_verified=changed)
        return changed

    def click_point(self, x, y, window):
        """Click a screen point inside window, e.g. a link located through the text pattern."""
        self.focus(window)
        rect = window.rectangle()
        if not (rect.left <= x < rect.right and rect.top <= y < rect.bottom):
            raise RuntimeError('Click point is outside the browser window')
        self.move(x, y, width=20)
        self.sleep(heavy_pause(0.55*self.pace, 0.45, 0.25, 2.0))
        self.check_stop()
        if win32gui.GetForegroundWindow() != window.handle:
            raise RuntimeError('Foreground window changed before clicking')
        pg.mouseDown(); time.sleep(random.uniform(0.05, 0.14)); pg.mouseUp()
        self.sleep(heavy_pause(0.65*self.pace, 0.5, 0.3, 3.0))

    def click_at(self, x, y, window, width=20, quick=False):
        """Move to a screen point inside `window` and click it.  `quick` is for dense,
        familiar targets such as spreadsheet cells, where people barely hover."""
        self.focus(window)
        rect = window.rectangle()
        if not (rect.left <= x < rect.right and rect.top <= y < rect.bottom):
            raise RuntimeError('Click point is outside the window')
        self.move(x, y, width=width)
        self.sleep(heavy_pause(0.16*self.pace, 0.45, 0.06, 0.8) if quick
                   else heavy_pause(0.55*self.pace, 0.45, 0.25, 2.0))
        self.check_stop()
        if win32gui.GetForegroundWindow() != window.handle:
            raise RuntimeError('Foreground window changed before clicking')
        pg.mouseDown(); time.sleep(random.uniform(0.05, 0.14)); pg.mouseUp()
        self.sleep(heavy_pause(0.28*self.pace, 0.5, 0.12, 1.5) if quick
                   else heavy_pause(0.65*self.pace, 0.5, 0.3, 3.0))

    def drag(self, x1, y1, x2, y2, window, width=20):
        """Press at one point, travel to another along a human path, release: the way a
        range of cells is selected with the mouse."""
        self.focus(window)
        rect = window.rectangle()
        for x, y in ((x1, y1), (x2, y2)):
            if not (rect.left <= x < rect.right and rect.top <= y < rect.bottom):
                raise RuntimeError('Drag point is outside the window')
        self.move(x1, y1, width=width)
        self.sleep(heavy_pause(0.16*self.pace, 0.45, 0.06, 0.8))
        self.check_stop()
        if win32gui.GetForegroundWindow() != window.handle:
            raise RuntimeError('Foreground window changed before dragging')
        pg.mouseDown()
        try:
            time.sleep(random.uniform(0.06, 0.15))
            self.move(x2, y2, width=width)
            time.sleep(random.uniform(0.05, 0.18))
        finally:
            pg.mouseUp()               # never leave the button held, even when stopped
        self.sleep(heavy_pause(0.3*self.pace, 0.5, 0.12, 1.5))

    def grab(self, left, top, width, height):
        """Read a rectangle of the screen (one GDI copy; nothing is sent to any window).

        Returns pixel(x, y) -> (r, g, b) in screen coordinates, or None if the screen
        cannot be read.  Used to find the spreadsheet grid, which OpenOffice does not
        expose to UI Automation."""
        try:
            import win32ui
            screen_width, screen_height = pg.size()
            left, top = max(0, int(left)), max(0, int(top))
            width = min(int(width), screen_width-left)
            height = min(int(height), screen_height-top)
            if width <= 0 or height <= 0:
                return None
            device = win32gui.GetWindowDC(0)
            source = memory = bitmap = None
            try:
                source = win32ui.CreateDCFromHandle(device)
                memory = source.CreateCompatibleDC()
                bitmap = win32ui.CreateBitmap()
                bitmap.CreateCompatibleBitmap(source, width, height)
                memory.SelectObject(bitmap)
                memory.BitBlt((0, 0), (width, height), source, (left, top), SRCCOPY)
                bits = bitmap.GetBitmapBits(True)
            finally:
                # Memory DC first (that deselects the bitmap), then the rest.  A failure
                # while tidying up must not throw away pixels that were read correctly.
                for release in ((lambda: memory.DeleteDC()) if memory is not None else None,
                                (lambda: source.DeleteDC()) if source is not None else None,
                                lambda: win32gui.ReleaseDC(0, device),
                                (lambda: win32gui.DeleteObject(bitmap.GetHandle()))
                                if bitmap is not None else None):
                    try:
                        if release is not None:
                            release()
                    except Exception:
                        pass
        except (Stopped, pg.FailSafeException):
            raise
        except Exception as exc:
            self.event('screen_read_failed', error=str(exc)[:120])
            return None
        stride = len(bits)//height
        if stride < width*4:
            self.event('screen_read_failed', error='display is not 32 bits per pixel')
            return None
        def pixel(x, y):
            x, y = int(x)-left, int(y)-top
            if not (0 <= x < width and 0 <= y < height):
                return (255, 255, 255)           # off the copied area: neither line nor highlight
            index = y*stride+x*4
            return bits[index+2], bits[index+1], bits[index]   # stored as blue, green, red
        return pixel

    def click(self, control, double=False, window=None):
        if window is not None:
            self.focus(window)
        rect = control.rectangle()
        self.move(*self.control_point(control), width=max(12, min(rect.width(), rect.height())))
        self.sleep(heavy_pause(0.55*self.pace, 0.45, 0.25, 2.0))  # hover before committing
        self.check_stop()
        # Recheck after hovering, since a menu or page may have moved the target.
        if window is not None and win32gui.GetForegroundWindow() != window.handle:
            raise RuntimeError('Foreground window changed before clicking the control')
        rect = control.rectangle()
        x, y = pg.position()
        if not control.is_visible() or not control.is_enabled():
            raise RuntimeError('Target control changed while hovering')
        if not (rect.left <= x < rect.right and rect.top <= y < rect.bottom):
            raise RuntimeError('Pointer is no longer inside the target control')
        for index in range(2 if double else 1):
            if index:
                self.sleep(random.uniform(0.07, 0.16))
            pg.mouseDown()
            time.sleep(random.uniform(0.05, 0.14))  # real button presses last tens of ms
            pg.mouseUp()
        self.sleep(heavy_pause(0.65*self.pace, 0.5, 0.3, 3.0))  # eyes follow the result

    def find(self, window, names, kind=None, bounded=False):
        def normalize(name):
            return name.casefold().replace('&', '').replace('…', '').rstrip('.:')
        names = {normalize(n) for n in names}
        kwargs = {'control_type': kind} if kind else {}
        controls = self.walk_controls(window, max_depth=7, max_nodes=250) if bounded else window.descendants(**kwargs)
        for control in controls:
            if kind and control.element_info.control_type != kind:
                continue
            name = normalize(control.window_text())
            if name in names and control.is_visible() and control.is_enabled():
                return control
        return None

    def click_named(self, window, names, kind=None):
        control = self.wait(lambda: self.find(window, names, kind), str(names), 8)
        self.click(control)
        return control

    def read_clipboard(self):
        def read():
            win32clipboard.OpenClipboard()
            try:
                if not win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                    return ''
                return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
            finally:
                win32clipboard.CloseClipboard()
        return self.wait(lambda: (True, read()), 'clipboard', 3)[1]

    def clear_clipboard(self):
        def clear():
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
            finally:
                win32clipboard.CloseClipboard()
            return True
        self.wait(clear, 'clipboard access', 3)

    def launch_shortcut(self, shortcut_name, title_pattern):
        self.event('application_launch_started', shortcut=shortcut_name)
        if self.window_handles(title_pattern=title_pattern):
            raise RuntimeError('Close existing application windows before running the demo')
        self.hotkey('win', 'd')
        self.sleep(0.6)
        def shortcut():
            for handle in self.window_handles(class_name='Progman') + self.window_handles(class_name='WorkerW'):
                shell = self.desktop.window(handle=handle).wrapper_object()
                target = self.find(shell, [shortcut_name], 'ListItem', bounded=True)
                if target:
                    return target
            return None
        icon = self.wait(shortcut, f'desktop shortcut {shortcut_name}', 10)
        self.click(icon, double=True)
        def opened():
            matches = self.window_handles(title_pattern=title_pattern)
            if len(matches) == 1:
                return self.desktop.window(handle=matches[0]).wrapper_object()
            if self.window_handles(title_pattern=r'.*Document Recovery.*'):
                # Shown after the application was killed mid-run; it blocks the new window.
                raise RuntimeError('the application is showing Document Recovery; '
                                   'close that dialog and start again')
            return None
        window = self.wait(opened, f'{shortcut_name} window', 45)
        self.focus(window)
        window.maximize()
        self.event('double_click_launch', shortcut=shortcut_name, hwnd=window.handle)
        return window

    def close_window(self, window, on_prompt=None):
        """Alt+F4, then wait for the window to go.  `on_prompt` is called while waiting,
        so the caller can answer a question the application asks before it closes."""
        self.hotkey('alt', 'f4', window=window)
        def gone():
            if not win32gui.IsWindow(window.handle):
                return True
            if on_prompt is not None:
                on_prompt()
            return False
        self.wait(gone, 'window closed', 15)

    def sample_resources(self):
        # Agent-only metrics. GetProcessTimes returns 100-nanosecond CPU units.
        handle = win32api.GetCurrentProcess()
        try:
            with (self.run_dir/'resources.csv').open('w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['elapsed_seconds', 'agent_cpu_percent_total', 'working_set_mb'])
                times = win32process.GetProcessTimes(handle)
                previous_cpu = times['KernelTime'] + times['UserTime']
                previous_wall = time.monotonic()
                while not self.stop_event.wait(5):
                    now = time.monotonic()
                    times = win32process.GetProcessTimes(handle)
                    cpu = times['KernelTime'] + times['UserTime']
                    memory = win32process.GetProcessMemoryInfo(handle)['WorkingSetSize']/1024**2
                    percent = 100*((cpu-previous_cpu)/10_000_000)/(now-previous_wall)/(os.cpu_count() or 1)
                    writer.writerow([round(now-self.start, 2), round(percent, 3), round(memory, 2)])
                    f.flush(); previous_cpu, previous_wall = cpu, now
        except Exception:
            logging.exception('Resource sampler unavailable')

    def finish(self):
        for key in ('ctrl', 'alt', 'shift', 'win'):
            # Release with Win32 API so a corner fail-safe does not prevent cleanup.
            vk = {'ctrl': 17, 'alt': 18, 'shift': 16, 'win': 91}[key]
            win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
        self.stop_event.set(); self.sampler.join(timeout=1)
        self.events.close()
