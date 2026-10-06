"""Fake Windows desktop for running the agent's flows on macOS (dev tool, never ship to the VM).

Replaces ctypes.WinDLL with a small simulated desktop - Chrome, OpenOffice (Start Center,
Writer, a Calc that really evaluates the formulas we type, Impress), Notepad, File Explorer,
the Run dialog, Save As / Open dialogs, the clipboard, cursor shapes and page pixels - and
patches time so a 15-minute session runs in seconds. Then it runs an agent entry point and
checks invariants a VM run would otherwise catch the slow way:

  * no keystroke ever lands in the console or the wrong app;
  * every SendInput succeeds, no exception escapes, the run ends with no windows left open;
  * saved Calc sheets evaluate to the values the agent expected (formulas typed exactly);
  * saved files, notes, URLs visited and words copied look as intended.

It models app behaviour from documentation and best guesses, NOT the real apps: a pass here
means the agent's logic is sound, not that AOO/Chrome behave exactly like this. Still run it
on the VM.

Usage (from the repo root):
    python3 tools/fake_desktop.py [agent|research|office|web|desk|impress] [--seed N] [--quiet]
"""
import builtins
import ctypes
import os
import random
import re
import sys
import time as _time
import zlib

sys.dont_write_bytecode = True
AGENT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agent")
sys.path.insert(0, AGENT_DIR)

SW, SH = 1920, 1080
CONSOLE_CLASS = "ConsoleWindowClass"
OFFICE_APPS = {"swriter": "Writer", "scalc": "Calc", "simpress": "Impress"}

# ---------------------------------------------------------------- clock

clock = [1_000_000.0]
_time.sleep = lambda s: clock.__setitem__(0, clock[0] + max(0.0, s))
_time.time = lambda: clock[0]
_time.perf_counter = lambda: clock[0]


class Fail(BaseException):
    """BaseException so the agent's per-task `except Exception` can't swallow a check."""


# ---------------------------------------------------------------- desktop model

class Win:
    def __init__(self, title, cls, app, data=None):
        self.title, self.cls, self.app = title, cls, app
        self.buf = []           # typed text (documents, dialogs)
        self.data = data or {}  # per-app state


class Desktop:
    def __init__(self):
        self.wins = {}          # hwnd -> Win
        self.z = []             # z-order, top first
        self.next = 1000
        self.untitled = 0
        self.mods = set()
        self.cursor = (SW // 2, SH // 2)
        self.clip, self.clip_seq = "", 1
        self.disk = {}          # filename -> (kind, content)
        self.mtime = {}         # filename -> last save/open time
        self.events = []        # log of notable happenings
        self.menu = None        # open AOO menu state
        self.last_click = (0.0, (0, 0))
        self.chrome_sel = None
        self.console = self.new("C:\\agent\\python.exe", CONSOLE_CLASS, "console")

    # windows
    def new(self, title, cls, app, data=None):
        h = self.next
        self.next += 1
        self.wins[h] = Win(title, cls, app, data)
        self.z.insert(0, h)
        return h

    def dialog(self, title, app, **data):
        return self.new(title, "#32770", app, data)

    def close(self, h):
        self.wins.pop(h, None)
        if h in self.z:
            self.z.remove(h)

    def fg(self):
        return self.z[0] if self.z else None

    def raise_(self, h):
        if h in self.z:
            self.z.remove(h)
            self.z.insert(0, h)

    def find(self, pred):
        return [h for h in self.z if pred(self.wins[h])]

    def log(self, *e):
        self.events.append(e)

    # ------------------------------------------------------------ keyboard
    def key(self, vk, up):
        if up:
            self.mods.discard(vk)
            return
        if vk in (0x10, 0x11, 0x12, 0x5B):
            self.mods.add(vk)
            return
        h = self.fg()
        w = self.wins.get(h)
        ctrl, alt, shift, win = 0x11 in self.mods, 0x12 in self.mods, 0x10 in self.mods, 0x5B in self.mods
        if win and vk == ord("R"):
            self.dialog("Run", "run")
            return
        if win and vk == ord("E"):
            self.new("Home", "CabinetWClass", "explorer", {"addr": None})
            return
        if w is None:
            raise Fail(f"key {vk:#x} with no foreground window")
        if w.app == "console":
            raise Fail(f"keystroke vk={vk:#x} went to the console window")
        if alt and vk == 0x09:
            if len(self.z) > 1:
                self.raise_(self.z[1])
            return
        getattr(self, "k_" + w.app)(h, w, vk, ctrl, alt, shift)

    def ch(self, vk, shift):
        if 0x41 <= vk <= 0x5A:
            c = chr(vk).lower()
            return c.upper() if shift else c
        base = US.get(vk)
        if base is None:
            return None
        return SHIFTED.get(base, base) if shift else base

    def _edit(self, buf, vk, shift):
        """Generic edit box: chars append, Backspace pops, Enter newline; other keys ignored."""
        if vk == 0x08:
            if buf:
                buf.pop()
        elif vk == 0x0D:
            buf.append("\n")
        else:
            c = self.ch(vk, shift)
            if c is not None:
                buf.append(c)

    def _dialog_key(self, h, w, vk, shift, on_enter):
        """Dialog edit box: Enter closes it and hands the text to on_enter, Esc just closes."""
        if vk == 0x0D:
            text = "".join(w.buf)
            self.close(h)
            on_enter(text)
        elif vk == 0x1B:
            self.close(h)
        else:
            self._edit(w.buf, vk, shift)

    def _addr_key(self, d, vk, shift):
        """Open address bar: returns the typed text on Enter (closing the bar), else None."""
        if vk == 0x0D:
            text, d["addr"] = "".join(d["addr"]), None
            return text
        self._edit(d["addr"], vk, shift)

    # run dialog
    def k_run(self, h, w, vk, ctrl, alt, shift):
        self._dialog_key(h, w, vk, shift, self._run)

    def _run(self, cmd):
        self.log("run", cmd)
        if cmd == "chrome":
            self.new("New Tab - Google Chrome", "Chrome_WidgetWin_1", "chrome",
                     {"hist": ["New Tab"], "addr": None})
        elif cmd in OFFICE_APPS:
            self.open_office(OFFICE_APPS[cmd])
        elif cmd == "notepad":
            self.new("Untitled - Notepad", "Notepad", "notepad", {"name": None, "dirty": False})
        else:
            raise Fail(f"unexpected Run command {cmd!r}")

    # chrome
    def k_chrome(self, h, w, vk, ctrl, alt, shift):
        d = w.data
        if ctrl and shift and vk == ord("W"):
            self.close(h)
            return
        if ctrl and vk in (ord("L"), ord("T")):
            d["addr"] = []
            if vk == ord("T"):
                d["hist"] = ["New Tab"]
                w.title = "New Tab - Google Chrome"
            return
        if ctrl and vk == ord("W"):
            return                                      # closes a tab; window stays
        if ctrl and vk == ord("C"):
            if self.chrome_sel:
                self.set_clip(self.chrome_sel + " ")
            return
        if alt and vk == 0x25:                          # Alt+Left
            if len(d["hist"]) > 1:
                d["hist"].pop()
                w.title = d["hist"][-1] + " - Google Chrome"
            return
        if d["addr"] is not None:
            url = self._addr_key(d, vk, shift)
            if url is not None:
                self.log("nav", url)
                if not re.fullmatch(r"[a-z0-9.\-]+\.[a-z]{2,}", url):
                    raise Fail(f"bad URL typed in omnibox: {url!r}")
                d["hist"].append(url)
                w.title = f"{url} - Google Chrome"
            return
        if vk in (0x2E, 0x1B):
            return
        raise Fail(f"key {vk:#x} sent to a Chrome page with no field focused")

    # office
    def open_office(self, kind, name=None, grid=None):
        self.untitled += 1
        title = f"{name or f'Untitled {self.untitled}'} - OpenOffice {kind}"
        data = {"kind": kind, "grid": dict(grid or {}), "cur": [0, 0], "edit": None,
                "tabstart": None, "name": name, "dirty": False}
        sc = self.find(lambda w: w.app == "startcenter")
        if sc:                                          # the Start Center turns into the doc
            h = sc[0]
            w = self.wins[h]
            w.title, w.app, w.data = title, "office", data
            self.raise_(h)
        else:
            h = self.new(title, "SALFRAME", "office", data)
        self.wins[h].buf = []
        return h

    def _office_shared(self, h, w, vk, ctrl, alt):
        """Keys the Start Center and every OpenOffice window share. True if handled."""
        if self.menu:
            self.k_menu(w, vk)
        elif alt and vk == ord("F"):
            self.menu = ["file"]
        elif ctrl and vk == ord("O"):
            self.dialog("Open", "open")
        elif ctrl and vk == ord("Q"):
            if any(self.wins[x].data.get("dirty") for x in self.find(lambda x: x.app == "office")):
                raise Fail("Ctrl+Q with unsaved docs would prompt")
            for x in self.find(lambda x: x.app in ("office", "startcenter")):
                self.close(x)
        else:
            return False
        return True

    def k_office(self, h, w, vk, ctrl, alt, shift):
        d = w.data
        if self._office_shared(h, w, vk, ctrl, alt):
            return
        if alt and vk == ord("I"):
            self.menu = ["insert"]
            return
        if ctrl and vk == ord("S"):
            if d["name"] is None:
                self.dialog("Save As", "saveas", target=h)
            else:
                self.save(h)
            return
        if ctrl and vk == ord("W"):
            if d["dirty"]:
                raise Fail(f"Ctrl+W on unsaved {w.title!r} would prompt")
            w.app, w.title, w.data = "startcenter", "Apache OpenOffice", {}
            return
        if d["kind"] == "Calc":
            return self.k_calc(d, vk, ctrl, shift)
        d["dirty"] = True
        if ctrl and vk in (0x24, 0x23):
            return
        if vk in (0x09, 0x1B):                          # Impress object navigation; no-op here
            return
        self._edit(w.buf, vk, shift)

    def k_startcenter(self, h, w, vk, ctrl, alt, shift):
        if self._office_shared(h, w, vk, ctrl, alt) or vk == 0x1B:
            return
        raise Fail(f"key {vk:#x} sent to the OpenOffice Start Center")

    def k_menu(self, w, vk):
        m = self.menu
        if vk == 0x1B:
            self.menu = None
        elif m == ["file"] and vk == ord("U"):
            self.menu = ["file", "recent"]
        elif m == ["file", "recent"] and ord("1") <= vk <= ord("9"):
            self.menu = None
            names = self.recent_list()
            i = vk - ord("1")
            if i < len(names):
                self.load(names[i])
        elif m == ["insert"] and vk == 0x0D:
            self.menu = None
            w.data["dirty"] = True

    def recent_list(self):
        return [n for n, _ in sorted(self.mtime.items(), key=lambda kv: -kv[1])
                if self.disk[n][0] != "Text"]

    def k_calc(self, d, vk, ctrl, shift):
        cur = d["cur"]

        def commit():
            if d["edit"] is not None:
                d["grid"][tuple(cur)] = "".join(d["edit"])
                d["edit"] = None
                d["dirty"] = True

        if ctrl and vk == 0x24:                         # Ctrl+Home
            commit()
            cur[:] = [0, 0]
            d["tabstart"] = None
        elif ctrl and vk == 0x23:                       # Ctrl+End
            commit()
            used = [k for k, v in d["grid"].items() if v != ""]
            cur[:] = [max(c for c, _ in used), max(r for _, r in used)] if used else [0, 0]
        elif ctrl and vk == ord("C"):
            commit()
            self.set_clip(fmt(calc_eval(d["grid"], tuple(cur))) + "\r\n")
        elif vk == 0x09:
            commit()
            if d["tabstart"] is None:
                d["tabstart"] = cur[0]
            cur[0] += 1
        elif vk == 0x0D:
            commit()
            cur[1] += 1
            if d["tabstart"] is not None:
                cur[0] = d["tabstart"]
            d["tabstart"] = None
        elif vk in (0x25, 0x26, 0x27, 0x28, 0x24):     # arrows, Home
            commit()
            d["tabstart"] = None
            if vk == 0x24:
                cur[0] = 0
            else:
                dx, dy = {0x25: (-1, 0), 0x27: (1, 0), 0x26: (0, -1), 0x28: (0, 1)}[vk]
                cur[0], cur[1] = max(0, cur[0] + dx), max(0, cur[1] + dy)
        elif vk == 0x1B:
            d["edit"] = None
        elif vk == 0x2E:
            if d["edit"] is None:
                d["grid"].pop(tuple(cur), None)
        elif vk == 0x08:
            if d["edit"]:
                d["edit"].pop()
        else:
            c = self.ch(vk, shift)
            if c is None:
                raise Fail(f"Calc got unexpected key {vk:#x}")
            if d["edit"] is None:
                d["edit"] = []
            d["edit"].append(c)

    def _store(self, name, kind, content):
        self.disk[name] = (kind, content)
        self.mtime[name] = clock[0]

    def save(self, h, name=None):
        w = self.wins[h]
        d = w.data
        name = name or d["name"]
        self._store(name, d["kind"], dict(d["grid"]) if d["kind"] == "Calc" else "".join(w.buf))
        d["name"], d["dirty"] = name, False
        w.title = f"{name} - OpenOffice {d['kind']}"
        self.log("saved", name)

    def load(self, name):
        kind, content = self.disk[name]
        self.mtime[name] = clock[0]
        nh = self.open_office(kind, name=name, grid=content if kind == "Calc" else None)
        if kind != "Calc":
            self.wins[nh].buf = list(content)
        self.log("opened", name)

    # file dialogs
    def k_saveas(self, h, w, vk, ctrl, alt, shift):
        self._dialog_key(h, w, vk, shift, lambda name: self._save_as(w.data["target"], name))

    def _save_as(self, target, name):
        t = self.wins[target]
        if t.app == "notepad":
            self._store(name, "Text", "".join(t.buf))
            t.data["name"], t.data["dirty"] = name, False
            t.title = f"{name}.txt - Notepad"
            self.log("saved", name)
        else:
            self.save(target, name)
        self.raise_(target)

    def k_open(self, h, w, vk, ctrl, alt, shift):
        self._dialog_key(h, w, vk, shift, self._open)

    def _open(self, fname):
        name = re.sub(r"\.od[tsp]$", "", fname)
        if name not in self.disk:
            raise Fail(f"Open dialog: no such file {fname!r}")
        self.load(name)

    def k_notepad(self, h, w, vk, ctrl, alt, shift):
        d = w.data
        if ctrl and vk == ord("S"):
            if d["name"]:
                self._store(d["name"], "Text", "".join(w.buf))
                d["dirty"] = False
            else:
                self.dialog("Save As", "saveas", target=h)
            return
        if ctrl and vk == ord("V"):
            w.buf.extend(self.clip)
            d["dirty"] = True
            self.log("paste", self.clip)
            return
        if ctrl and vk in (0x23, 0x24):
            return
        if alt and vk == 0x73:
            if d["dirty"]:
                raise Fail("Alt+F4 on unsaved Notepad would prompt")
            self.close(h)
            return
        d["dirty"] = True
        self._edit(w.buf, vk, shift)

    def k_explorer(self, h, w, vk, ctrl, alt, shift):
        d = w.data
        if ctrl and vk == ord("W"):
            self.close(h)
            return
        if ctrl and vk == ord("L"):
            d["addr"] = []
            return
        if ctrl and shift and vk in (ord("6"), ord("2")):
            return
        if d["addr"] is not None:
            path = self._addr_key(d, vk, shift)
            if path is not None:
                w.title = "Documents" if path.lower() == "%userprofile%\\documents" else path
                self.log("explorer", w.title)
            return
        if vk == 0x2E:
            raise Fail("Delete pressed in an Explorer file list (would delete a file)")
        if vk == 0x08:
            raise Fail("Backspace in an Explorer file list (navigates up)")

    # ------------------------------------------------------------ clipboard / mouse
    def set_clip(self, text):
        self.clip = text
        self.clip_seq += 1

    def unicode_char(self, c):
        w = self.wins.get(self.fg())
        if w is None or w.app == "console":
            raise Fail("unicode char into console/no window")
        w.buf.append(c)

    def mouse(self, mi):
        flags = mi.dwFlags
        if flags & 0x8000:
            x = round(mi.dx * (SW - 1) / 65535)
            y = round(mi.dy * (SH - 1) / 65535)
            self.cursor = (x, y)
        if flags & 0x0004:
            self.on_click()

    def on_click(self):
        w = self.wins.get(self.fg())
        last_t, last_p = self.last_click
        dbl = clock[0] - last_t < 0.5 and abs(self.cursor[0] - last_p[0]) <= 4 \
            and abs(self.cursor[1] - last_p[1]) <= 4
        self.last_click = (clock[0], self.cursor)
        if w is None:
            return
        if w.app == "console":
            raise Fail("clicked the console window")
        if w.app == "chrome":
            kind = cursor_kind_at(self.cursor)
            if kind == "hand" and not dbl:
                w.data["hist"].append("404 Not Found")
                w.title = "404 Not Found - Google Chrome"
                self.log("link", self.cursor)
            elif kind == "ibeam" and dbl:
                self.chrome_sel = WORDS[(self.cursor[0] // 37 + self.cursor[1] // 23) % len(WORDS)]
            else:
                self.chrome_sel = None


US = {**{0x30 + i: str(i) for i in range(10)}, 0x20: " ", 0xBA: ";", 0xBB: "=", 0xBC: ",",
      0xBD: "-", 0xBE: ".", 0xBF: "/", 0xDE: "'", 0xDB: "[", 0xDD: "]", 0xC0: "`", 0xDC: "\\"}
SHIFTED = {"1": "!", "2": "@", "3": "#", "4": "$", "5": "%", "6": "^", "7": "&", "8": "*",
           "9": "(", "0": ")", "-": "_", "=": "+", ";": ":", "'": '"', ",": "<", ".": ">",
           "/": "?", "[": "{", "]": "}", "`": "~", "\\": "|"}
REV = {v: k for k, v in US.items()}
WORDS = ["Inspiron", "Latitude", "MacBook", "Galaxy", "Vivobook", "Bitcoin", "Courses",
         "laptops", "Shop", "Compare", "Pricing", "Deals"]

LINK_BAND = (150, 330)          # page rows (screen y) holding links
TEXT_BAND = (420, 900)          # page rows holding selectable text


def cursor_kind_at(p):
    x, y = p
    if 120 <= x <= 1700 and LINK_BAND[0] <= y < LINK_BAND[1]:
        return "hand"
    if 120 <= x <= 1700 and TEXT_BAND[0] <= y < TEXT_BAND[1]:
        return "ibeam"
    return "arrow"


def vkscan(o):
    c = chr(o)
    if c.isalpha():
        return ord(c.upper()) | (0x100 if c.isupper() else 0)
    if c in REV:
        return REV[c]
    for b, s in SHIFTED.items():
        if s == c:
            return REV[b] | 0x100
    return -1


# ---------------------------------------------------------------- tiny Calc evaluator

def calc_eval(grid, cell):
    raw = grid.get(cell, "")
    if not raw.startswith("="):
        try:
            return float(raw)
        except ValueError:
            return raw
    f = raw[1:]

    def ref(r):
        m = re.fullmatch(r"\$?([A-Z])\$?(\d+)", r)
        return (ord(m.group(1)) - 65, int(m.group(2)) - 1)

    def nums(a, b):
        (c0, r0), (c1, r1) = ref(a), ref(b)
        vals = [calc_eval(grid, (c, r)) for c in range(c0, c1 + 1) for r in range(r0, r1 + 1)]
        return [v for v in vals if isinstance(v, float)]

    m = re.fullmatch(r"([A-Z]\d+)\*([A-Z]\d+)", f)
    if m:
        return calc_eval(grid, ref(m.group(1))) * calc_eval(grid, ref(m.group(2)))
    m = re.fullmatch(r"(SUM|MIN)\(([A-Z]\d+):([A-Z]\d+)\)", f)
    if m:
        vals = nums(m.group(2), m.group(3))
        return sum(vals) if m.group(1) == "SUM" else min(vals)
    m = re.fullmatch(r"ROUND\(AVERAGE\(([A-Z]\d+):([A-Z]\d+)\);0\)", f)
    if m:
        vals = nums(m.group(1), m.group(2))
        return float(int(sum(vals) / len(vals) + 0.5))
    m = re.fullmatch(r'IF\(([A-Z]\d+)<=(\$[A-Z]\$\d+);"(\w+)";"(\w+)"\)', f)
    if m:
        return m.group(3) if calc_eval(grid, ref(m.group(1))) <= calc_eval(grid, ref(m.group(2))) \
            else m.group(4)
    return "Err:509"


def fmt(v):
    if isinstance(v, float):
        return str(int(v)) if v == int(v) else f"{v:.10g}"
    return str(v)


# ---------------------------------------------------------------- fake DLLs

class Fn:
    def __init__(self, impl):
        self.impl, self.restype, self.argtypes = impl, None, None

    def __call__(self, *a):
        return self.impl(*a) if self.impl else 1


class FakeDLL:
    def __init__(self, impls):
        object.__setattr__(self, "_impls", impls)
        object.__setattr__(self, "_fns", {})

    def __getattr__(self, n):
        if n not in self._fns:
            self._fns[n] = Fn(self._impls.get(n))
        return self._fns[n]

    def __setattr__(self, n, v):
        pass


def install(D):
    def send_input(n, pinp, size):
        inp = pinp._obj
        if inp.type == 1:
            ki = inp.u.ki
            up = bool(ki.dwFlags & 2)
            if ki.dwFlags & 4:
                if not up:
                    D.unicode_char(chr(ki.wScan))
            else:
                D.key(ki.wVk, up)
        else:
            D.mouse(inp.u.mi)
        return 1

    def enum(cb, lp):
        for h in list(D.z):
            if not cb(h, 0):
                break
        return 1

    def gettext(h, buf, n):
        buf.value = D.wins[h].title[:n - 1] if h in D.wins else ""
        return len(buf.value)

    def classname(h, buf, n):
        buf.value = D.wins[h].cls if h in D.wins else ""
        return len(buf.value)

    def setfg(h):
        D.raise_(h)
        return 1

    def rect(h, pr):
        r = pr._obj
        r.left, r.top, r.right, r.bottom = 0, 0, SW, SH
        return 1

    def cursorpos(pp):
        p = pp._obj
        p.x, p.y = D.cursor
        return 1

    user32 = dict(
        SendInput=send_input, EnumWindows=enum, EnumChildWindows=lambda h, cb, lp: 1,
        GetWindowTextLengthW=lambda h: len(D.wins[h].title) if h in D.wins else 0,
        GetWindowTextW=gettext, GetClassNameW=classname,
        IsWindowVisible=lambda h: h in D.wins, IsWindow=lambda h: h in D.wins,
        GetForegroundWindow=D.fg, SetForegroundWindow=setfg, BringWindowToTop=setfg,
        VkKeyScanW=vkscan, GetSystemMetrics=lambda i: SW if i == 0 else SH,
        GetWindow=lambda h, c: 0, GetWindowLongW=lambda h, i: 0, IsIconic=lambda h: 0,
        MapVirtualKeyW=lambda v, t: 0, GetAncestor=lambda h, f: h,
        WindowFromPoint=lambda p: D.fg(), GetWindowThreadProcessId=lambda h, p: 2,
        AttachThreadInput=lambda a, b, c: 1, GetWindowRect=rect, GetCursorPos=cursorpos,
    )
    kernel32 = dict(GetCurrentThreadId=lambda: 1, GetConsoleWindow=lambda: D.console,
                    GetStdHandle=lambda x: 1, GetConsoleMode=lambda h, m: 0)

    def windll(name, **kw):
        return FakeDLL({"user32": user32, "kernel32": kernel32}.get(name, {}))

    ctypes.WinDLL = windll
    ctypes.WINFUNCTYPE = ctypes.CFUNCTYPE

    import screen

    def grab_gray(rect, step=2):
        l, t, r, b = rect
        w, h = max(1, (r - l) // step), max(1, (b - t) // step)
        page = D.wins.get(D.fg())
        g = bytearray([245] * (w * h))
        if page and page.app == "chrome" and not page.title.startswith("New Tab"):
            rr = random.Random(zlib.crc32(page.title.encode()) & 0xFFFF)
            for band in (LINK_BAND, TEXT_BAND):
                for sy in range(band[0], band[1] - 12, 22):
                    y0 = (sy - t) // step
                    for sx in range(140, 1680, 7):
                        if rr.random() < 0.35:
                            continue
                        x0 = (sx - l) // step
                        for yy in range(y0, min(h, y0 + 9 // step + 1)):
                            if 0 <= yy < h and 0 <= x0 < w:
                                g[yy * w + x0] = 30
        return w, h, bytes(g)

    screen.grab_gray = grab_gray
    screen.cursor_kind = lambda: cursor_kind_at(D.cursor)
    screen.clipboard_seq = lambda: D.clip_seq
    screen.clipboard_text = lambda: D.clip


# ---------------------------------------------------------------- run + checks

def main():
    args = sys.argv[1:]
    which = next((a for a in args if not a.startswith("-") and not a.isdigit()), "agent")
    seed = int(args[args.index("--seed") + 1]) if "--seed" in args else 1
    quiet = "--quiet" in args
    random.seed(seed)
    D = Desktop()
    install(D)

    import persona
    persona.PERSONA.__init__(seed)
    import agent
    import desk_tasks
    import office_loop
    import research_task
    import session as S
    import web_loop

    s = S.Session(random.Random(seed))
    flows = {
        "research": lambda: research_task.research_task(s),
        "office": lambda: (office_loop.writer_task(s), office_loop.calc_task(s)),
        "impress": lambda: office_loop.impress_task(s),
        "web": lambda: web_loop.web_task(s, sites=["apnews.com", "www.dell.com"]),
        "desk": lambda: (desk_tasks.jot_todo(s), desk_tasks.look_over_documents(s)),
    }
    if which != "agent" and which not in flows:
        raise SystemExit(f"unknown flow {which!r}")

    start = clock[0]
    real_print = print
    if quiet:
        builtins.print = lambda *a, **k: None
    try:
        if which == "agent":
            agent.Session = lambda: s
            agent.main()
        else:
            flows[which]()
            s.shutdown()
    finally:
        builtins.print = real_print

    problems = []
    left = [w.title for h, w in D.wins.items() if h != D.console]
    if left:
        problems.append(f"windows left open: {left}")
    for name, (kind, content) in D.disk.items():
        if kind == "Calc":
            for cell in content:
                v = calc_eval(content, cell)
                if isinstance(v, str) and v.startswith("Err"):
                    problems.append(f"{name}: {content[cell]!r} at {cell} -> {v}")
    print(f"\n== {which} (seed {seed}): {(clock[0] - start) / 60:.1f} simulated minutes")
    for e in D.events:
        if e[0] in ("run", "nav", "saved", "opened", "explorer", "paste", "link"):
            print("  ", *e)
    if not quiet:
        for name, (kind, content) in D.disk.items():
            print(f"\n--- {name} ({kind})")
            if kind == "Calc":
                rows = max(r for _, r in content) + 1
                for r in range(rows):
                    cells = [fmt(calc_eval(content, (c, r))) if (c, r) in content else ""
                             for c in range(6)]
                    print("   " + " | ".join(cells).rstrip(" |"))
            else:
                print(content)
    if problems:
        print("\nPROBLEMS:\n  " + "\n  ".join(problems))
        sys.exit(1)
    print("\nOK: no stray keystrokes, no errors, nothing left open, all formulas evaluate")


if __name__ == "__main__":
    try:
        main()
    except Fail as e:
        print(f"\nFAIL: {e}")
        sys.exit(1)
