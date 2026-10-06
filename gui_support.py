"""Windows input, cancellable pauses, state checks and resource logging."""
import csv
import json
import logging
import os
import random
import threading
import time

import pyautogui as pg
import win32api
import win32clipboard
import win32con
import win32gui
import win32process
from pywinauto import Desktop


class Stopped(Exception):
    pass


class GUI:
    def __init__(self, run_dir):
        self.run_dir = run_dir
        self.pace = random.uniform(0.85, 1.2)
        pg.FAILSAFE = True
        pg.PAUSE = 0.06
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

    def sleep(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.check_stop()
            time.sleep(min(0.1, max(0, end-time.monotonic())))

    def pause(self, reading=False):
        base = random.lognormvariate(1.8 if reading else -0.4, 0.5)
        self.sleep(min(18 if reading else 2.5, max(2 if reading else 0.2, base*self.pace)))

    def wait(self, predicate, label, timeout=30):
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
            self.sleep(0.4)
        raise RuntimeError(f'Timed out waiting for {label}: {last_error or "not ready"}')

    def window(self, pattern, timeout=30):
        def lookup():
            matches = self.desktop.windows(title_re=pattern, visible_only=True)
            return matches[0] if len(matches) == 1 else None
        return self.wait(lookup, pattern, timeout)

    def focus(self, window):
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
        pg.hotkey(*keys); self.pause()

    def type_text(self, text, window, corrected_typo=False):
        self.focus(window)
        for i, char in enumerate(str(text)):
            self.check_stop()
            if win32gui.GetForegroundWindow() != window.handle:
                raise RuntimeError('Foreground window changed during typing')
            if ord(char) > 127:
                raise RuntimeError('This demo types ASCII data only')
            if corrected_typo and i == 2 and char.isalpha():
                pg.write('x'); self.sleep(random.uniform(0.15, 0.4)); pg.press('backspace')
            pg.write(char)
            self.sleep(random.uniform(0.035, 0.12)*self.pace)
            if char == ' ':
                self.sleep(random.uniform(0.1, 0.4)*self.pace)

    def move(self, x, y):
        sx, sy = pg.position()
        offset = random.uniform(-35, 35)
        cx, cy = (sx+x)/2+offset, (sy+y)/2-offset
        steps = random.randint(12, 20)
        duration = random.uniform(0.35, 0.85)*self.pace
        for i in range(1, steps+1):
            self.check_stop()
            t = i/steps
            pg.moveTo((1-t)**2*sx+2*(1-t)*t*cx+t*t*x,
                      (1-t)**2*sy+2*(1-t)*t*cy+t*t*y)
            self.sleep(duration/steps)

    def click(self, control, double=False):
        if not control.is_visible() or not control.is_enabled():
            raise RuntimeError('Target control is not available')
        rect = control.rectangle()
        if rect.width() <= 0 or rect.height() <= 0:
            raise RuntimeError('Target control has no visible bounds')
        self.move(rect.left+rect.width()/2, rect.top+rect.height()/2)
        pg.doubleClick(interval=random.uniform(0.09, 0.16)) if double else pg.click()
        self.pause()

    def find(self, window, names, kind=None):
        def normalize(name):
            return name.casefold().replace('&', '').replace('…', '').rstrip('.:')
        names = {normalize(n) for n in names}
        kwargs = {'control_type': kind} if kind else {}
        for control in window.descendants(**kwargs):
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
        if self.desktop.windows(title_re=title_pattern, visible_only=True):
            raise RuntimeError('Close existing application windows before running the demo')
        self.hotkey('win', 'd')
        self.sleep(0.6)
        def shortcut():
            for shell in self.desktop.windows(class_name='Progman') + self.desktop.windows(class_name='WorkerW'):
                target = self.find(shell, [shortcut_name], 'ListItem')
                if target:
                    return target
            return None
        icon = self.wait(shortcut, f'desktop shortcut {shortcut_name}', 10)
        self.click(icon, double=True)
        window = self.window(title_pattern, 45)
        self.focus(window)
        window.maximize()
        self.event('double_click_launch', shortcut=shortcut_name, hwnd=window.handle)
        return window

    def close_window(self, window):
        self.hotkey('alt', 'f4', window=window)
        self.wait(lambda: not win32gui.IsWindow(window.handle), 'window closed', 15)

    def sample_resources(self):
        # Agent-only metrics. GetProcessTimes returns 100-nanosecond CPU units.
        handle = win32api.GetCurrentProcess()
        try:
            with (self.run_dir/'resources.csv').open('w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['elapsed_seconds', 'agent_cpu_percent_total', 'working_set_mb'])
                previous_cpu = sum(win32process.GetProcessTimes(handle)[key] for key in ('KernelTime', 'UserTime'))
                previous_wall = time.monotonic()
                while not self.stop_event.wait(5):
                    now = time.monotonic()
                    cpu = sum(win32process.GetProcessTimes(handle)[key] for key in ('KernelTime', 'UserTime'))
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
