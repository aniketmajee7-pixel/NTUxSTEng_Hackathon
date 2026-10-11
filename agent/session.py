"""Session: the long-lived apps of one working day (stdlib only).

Open-act-close every task churns Sysmon EID 1/5 and pays repeated cold starts (a 25-34k-file
AOO launch, a 5-10-process Chrome tree). A real worker instead leaves the browser, the office
suite and a scratch Notepad open and switches between them. This module owns those windows, so
tasks can ask for "the Chrome window", "a fresh Writer doc" or "jot this in my notes" without
relaunching anything. Windows are reused, so every keystroke burst is preceded by `focus()`.
It also carries the per-session WorldState, tracks the doc in progress so a failed task can
save and close it, and clears the first-run / recovery dialogs that would stall the run.
"""
import random
import time

import screen
import timing
import vision
from content import WorldState
from human_input import (VK_CONTROL, VK_DELETE, VK_END, VK_ESCAPE, VK_F4, VK_MENU, VK_RETURN,
                         VK_SHIFT, click, disable_quickedit, ensure_focus, find_window,
                         find_windows, focus, foreground, hotkey, is_window, launch, maximize,
                         move_to, tap, type_text, wait_for_new_window, wait_for_window,
                         window_title)

CHROME = "Google Chrome"
WARNING = "Privacy error"   # tab title of Chrome's certificate warning
NOTEPAD = "Notepad"
# kind -> (Run command, window title substring, native extension)
DOCS = {"writer": ("swriter", "OpenOffice Writer", ".odt"),
        "calc": ("scalc", "OpenOffice Calc", ".ods"),
        "impress": ("simpress", "OpenOffice Impress", ".odp")}


def _poll_title(hwnd, pred, timeout):
    end = time.time() + timeout
    while time.time() < end:
        if pred(window_title(hwnd)):
            return True
        time.sleep(0.25)
    return pred(window_title(hwnd))


def stamped(name):
    """`name` plus a date-time suffix, so each run saves distinct files."""
    return f"{name} {time.strftime('%d%m %H%M')}"


def save_as(name, world=None, hwnd=None):
    """Save the document `hwnd` (default: whatever is in front) under `name` via Save As.

    Works for OpenOffice and Notepad alike: Ctrl+S on an untitled document opens the system
    Save As dialog, whose filename box is focused with the old name selected.
    """
    if hwnd:
        focus(hwnd)
    hotkey(VK_CONTROL, ord("S"))
    dlg = wait_for_window("Save As")
    ensure_focus(dlg)
    time.sleep(timing.read_s(10))                       # look at the dialog
    type_text(name, wpm=50, typo_rate=0.01, exact=True)
    time.sleep(timing.short_s(0.65))
    tap(VK_RETURN)
    wait_for_window("Save As", gone=True)
    wait_for_window(name)                               # title bar switches to the saved name
    if world is not None:
        world.saved(name)


class Session:
    def __init__(self, rng=None):
        self.rng = rng or random.Random()
        self.world = WorldState(self.rng)
        self.chrome = None      # hwnd of the Chrome window
        self.tabs = 1
        self.doc = None         # hwnd of the office doc being worked on (unsaved until close_doc)
        self.notes = None       # hwnd of the scratch Notepad
        self.notes_name = None  # set once the notes have been saved
        self.recent = []        # AOO docs saved/opened this session, most recent first
        self.explorers = []     # File Explorer windows we opened
        self.dead_sites = set()  # hosts that didn't load this run: never typed again
        self.calc_grid = None   # where Calc's cells are on screen (office_loop.calibrate)

    # -- dialogs --------------------------------------------------------
    def dismiss_dialogs(self):
        """Clear the known blocking dialogs if present; harmless if none are.

        Each dialog is focused before it gets a key, so the key cannot land in a document.
        """
        for title, key, tries in (("Welcome to Apache OpenOffice", VK_RETURN, 6),
                                  ("Presentation Wizard", VK_RETURN, 5),
                                  ("Document Recovery", VK_RETURN, 3),
                                  ("Improvement Program", VK_ESCAPE, 1)):
            # wizards: Enter is "Next >" on each page and "Finish"/"Create" on the last;
            # recovery: Enter = "Start Recovery", then "Finish".
            dlg = find_window(title)
            for _ in range(tries):
                if not is_window(dlg):
                    break
                focus(dlg)
                tap(key)
                time.sleep(timing.think_s(1.0))

    # -- chrome ---------------------------------------------------------
    def ensure_chrome(self):
        """Return a live Chrome window, launching it bare (no URL in RunMRU) if needed."""
        if not is_window(self.chrome):
            self.chrome = find_window(CHROME)
        if not self.chrome:
            launch("chrome")
            self.chrome = wait_for_window(CHROME, timeout=30)
            self.tabs = 1
            screen.wait_painted(self.chrome)
            time.sleep(timing.read_s(20))               # first paint + orient
        maximize(self.chrome)
        return self.chrome

    def open_url(self, host, new_tab=False):
        """Navigate the live Chrome window. The bare host goes in the omnibox (never in the
        Run dialog); Chrome fills in https:// itself, as it does for a person.

        Returns the Chrome hwnd, or None if the site is no use today: a privacy warning with
        no way past it, or a page that never paints. Such a host is remembered and not typed
        again this run (VM: coinbase/amazon/coursera stuck on the warning, dell and
        coinmarketcap blank).
        """
        if host in self.dead_sites:
            return None
        hwnd = self.ensure_chrome()
        focus(hwnd)
        if new_tab:
            hotkey(VK_CONTROL, ord("T"))                # a new tab already focuses the omnibox
            self.tabs += 1
            time.sleep(timing.think_s(0.5))
        else:
            hotkey(VK_CONTROL, ord("L"))
            time.sleep(timing.short_s(0.45))
        ensure_focus(hwnd)
        type_text(host, wpm=60, typo_rate=0.02, exact=True)
        time.sleep(timing.short_s(0.35))
        tap(VK_DELETE)                                  # drop an inline-autocompleted tail
        time.sleep(timing.short_s(0.2))
        before = window_title(hwnd)
        tap(VK_RETURN)
        # the old page is still painted until the new one commits: wait for the title first
        if _poll_title(hwnd, lambda t: t != before, 12):
            painted = screen.wait_painted(hwnd)
        elif new_tab or WARNING in before:
            painted = False                             # nothing even started loading
        else:
            painted = screen.wait_painted(hwnd)         # same title: this site was already up
        if WARNING in window_title(hwnd):
            if not self.pass_warning(hwnd):
                self.dead_sites.add(host)
                return None
            painted = screen.wait_painted(hwnd)
        if not painted:
            time.sleep(timing.read_s(6))                # stare at the empty page, give up
            self.dead_sites.add(host)
            return None
        time.sleep(timing.perceive_s() + timing.read_s(8))
        self.accept_cookies(hwnd)
        return hwnd

    def pass_warning(self, hwnd):
        """Chrome's "Your connection is not private": Advanced, then "Proceed to <host>
        (unsafe)", as the organisers instruct. HSTS sites have no Proceed link; then read the
        explanation and leave. True if the page got past the warning.

        Found by eye, not accessibility: `vision.warning_button` finds the filled "Back to
        safety" button; "Advanced" is on its row at the left of the text column; the Proceed
        link is the last line of the expanded text. Every click waits for a hand cursor.
        """
        rng = self.rng
        time.sleep(timing.read_s(14))                   # read the warning
        rect, step = screen.content_rect(hwnd), 2
        w, h, g = screen.grab_gray(rect, step)
        btn = vision.warning_button(w, h, g)
        if not btn:
            return False
        x0, x1, row = btn
        heading = [x for x, y in vision.text_spots(w, h, g, rng=rng, sample=4000, limit=60)
                   if y < row - 8 and x < x0]
        left = rect[0] + step * (min(heading) - 6 if heading else x1 - 300)
        sy = rect[1] + step * row
        for dx in (45, 30, 65, 20, 85):                 # slide along the label until the hand
            move_to(left + dx + rng.gauss(0, 2), sy + rng.gauss(0, 1.5))
            time.sleep(timing.short_s(0.2))
            if screen.cursor_kind() == "hand":
                break
        else:
            return False
        click()
        time.sleep(timing.perceive_s() + timing.short_s(0.4))
        w, h, g = screen.grab_gray(rect, step)
        spots = vision.text_spots(w, h, g, rng=rng, sample=4000, limit=4000)
        below = sorted(((x, y) for x, y in spots if y > row + 8 and x < x1), key=lambda p: -p[1])
        for x, y in below[:8]:                          # bottom line first: that is the link
            move_to(rect[0] + step * x + rng.gauss(0, 2), rect[1] + step * y + rng.gauss(0, 1))
            time.sleep(timing.short_s(0.25))
            if screen.cursor_kind() == "hand":
                time.sleep(timing.think_s(0.6))
                click()
                if _poll_title(hwnd, lambda t: WARNING not in t, 6):
                    return True
                move_to(rect[0] + step * x + rng.gauss(0, 2), rect[1] + step * y + 3)
                if screen.cursor_kind() == "hand":     # nothing happened: click again
                    click()
                return _poll_title(hwnd, lambda t: WARNING not in t, 12)
        time.sleep(timing.read_s(25))                   # "can't visit it now" - read why, leave
        return False

    def accept_cookies(self, hwnd):
        """Click 'Accept' on a cookie-consent modal if one covers the page (e.g. Samsung):
        a person can't read or select anything until it is gone. Clicks only where the
        cursor turns into a hand. Returns True if it clicked."""
        rect = screen.content_rect(hwnd)
        step = 4
        spot = vision.consent_button(*screen.grab_gray(rect, step))
        if not spot:
            return False
        x, y = rect[0] + spot[0] * step, rect[1] + spot[1] * step
        time.sleep(timing.read_s(30))                   # skim the banner text
        move_to(x + self.rng.gauss(0, 4), y + self.rng.gauss(0, 2))
        time.sleep(timing.short_s(0.25))
        if screen.cursor_kind() != "hand":
            return False
        click()
        time.sleep(timing.think_s(0.8))
        return True

    def close_tab(self):
        if is_window(self.chrome) and self.tabs > 1:
            focus(self.chrome)
            hotkey(VK_CONTROL, ord("W"))
            self.tabs -= 1
            time.sleep(timing.think_s(0.6))

    # -- notes (Notepad) ------------------------------------------------
    def ensure_notes(self):
        """The scratch Notepad a person keeps open all day; launched once, bare `notepad`."""
        if is_window(self.notes):
            return self.notes
        before = set(find_windows(NOTEPAD))
        launch("notepad")
        self.notes = wait_for_new_window(NOTEPAD, before, timeout=15)
        focus(self.notes)
        type_text(f"{time.strftime('%d %b')} - to do / notes\n")
        return self.notes

    def note(self, text, paste=False, after=""):
        """Append a line to the notes: `text`, then (optionally) Ctrl+V, then `after`."""
        h = self.ensure_notes()
        focus(h)
        hotkey(VK_CONTROL, VK_END)
        time.sleep(timing.short_s(0.3))
        type_text("\n" + text)
        if paste:
            time.sleep(timing.short_s(0.25))
            hotkey(VK_CONTROL, ord("V"))
            time.sleep(timing.short_s(0.3))
        if after:
            type_text(after)
        time.sleep(timing.think_s(0.6))

    def save_notes(self, name):
        if not is_window(self.notes):
            return
        if self.notes_name:
            focus(self.notes)
            hotkey(VK_CONTROL, ord("S"))                # already named: plain save, no dialog
        else:
            save_as(name, self.world, self.notes)
            self.notes_name = name
        time.sleep(timing.think_s(0.6))

    # -- office ---------------------------------------------------------
    def new_doc(self, kind):
        """Ensure OpenOffice is up and bring a fresh Writer/Calc/Impress window to the front.

        Launching swriter/scalc/simpress reuses the running soffice.bin (its wrapper hands off
        over IPC), so we get a new window without a cold start. The new window is told apart
        from any older one by its handle, never just its title.
        """
        cmd, title, _ = DOCS[kind]
        before = set(find_windows(title))
        launch(cmd)
        end = time.time() + 60
        hwnd = None
        while time.time() < end and not hwnd:
            self.dismiss_dialogs()                      # wizards / recovery can come first
            try:
                hwnd = wait_for_new_window(title, before, timeout=4)
            except TimeoutError:
                pass
        if not hwnd:
            raise TimeoutError(f"{cmd} did not open a new window")
        time.sleep(timing.read_s(15))
        maximize(hwnd)
        self.doc = hwnd
        return hwnd

    def saved_doc(self, name):
        """Record a doc saved in OpenOffice (for File > Recent Documents ordering)."""
        if name in self.recent:
            self.recent.remove(name)
        self.recent.insert(0, name)

    def reopen(self, name, kind):
        """Open a doc saved earlier this session, the way a person would.

        Usually Ctrl+O and the filename; sometimes File > Recent Documents (Alt+F, U, n).
        Either way the result is verified by the window title, and a menu miss falls back to
        Ctrl+O, so a wrong guess about the menu costs seconds, not the task.
        """
        host = self.doc if is_window(self.doc) else find_window("OpenOffice")
        if not host:
            raise RuntimeError("reopen: OpenOffice is not running")
        focus(host)
        hwnd = None
        if name in self.recent and self.recent.index(name) < 9 and self.rng.random() < 0.35:
            hotkey(VK_MENU, ord("F"))
            time.sleep(timing.short_s(0.5))
            tap(ord("U"))                               # Recent Doc~uments
            time.sleep(timing.short_s(0.6))
            tap(ord("1") + self.recent.index(name))     # entries are numbered 1..9
            try:
                hwnd = wait_for_window(name, timeout=8)
            except TimeoutError:
                for _ in range(3):                      # close whatever menu is still open
                    tap(VK_ESCAPE)
                    time.sleep(0.2)
                focus(host)
        if not hwnd:
            hotkey(VK_CONTROL, ord("O"))
            dlg = wait_for_window("Open", timeout=10, exact=True)
            ensure_focus(dlg)
            time.sleep(timing.read_s(8))
            type_text(name + DOCS[kind][2], wpm=50, typo_rate=0.01, exact=True)
            time.sleep(timing.short_s(0.5))
            tap(VK_RETURN)
            hwnd = wait_for_window(name, timeout=20)
        if DOCS[kind][1] not in window_title(hwnd):
            raise RuntimeError(f"reopen: {window_title(hwnd)!r} is not a {kind} doc")
        time.sleep(timing.read_s(10))
        maximize(hwnd)
        self.doc = hwnd
        self.saved_doc(name)
        return hwnd

    def save_doc_as(self, name):
        save_as(name, self.world, self.doc)
        self.saved_doc(name)

    def close_doc(self):
        """Close the current document with Ctrl+W, keeping soffice.bin alive (Start Center)."""
        if is_window(self.doc):
            focus(self.doc)
            hotkey(VK_CONTROL, ord("W"))
            time.sleep(timing.think_s(0.8))
        self.doc = None

    def read_cell(self):
        """Copy the current Calc cell and return its displayed value (clipboard read-back)."""
        seq = screen.clipboard_seq()
        hotkey(VK_CONTROL, ord("C"))
        screen.wait_clipboard_change(seq)
        value = screen.clipboard_text().strip()
        time.sleep(timing.short_s(0.4))
        tap(VK_ESCAPE)                                  # clear the copy marquee
        return value

    def recover(self):
        """After a failed task: close stray dialogs, then save and close the doc in progress
        so no unsaved window is left to block later tasks or the final Ctrl+Q."""
        for title in ("Save As", "Confirm Save As", "Open"):
            hwnd = find_window(title, exact=(title == "Open"))
            if hwnd:
                focus(hwnd)
                tap(VK_ESCAPE)
                time.sleep(timing.think_s(0.5))
        self.dismiss_dialogs()
        if is_window(self.doc):
            focus(self.doc)
            tap(VK_ESCAPE)                              # leave any half-typed cell / selection
            time.sleep(timing.think_s(0.6))
            if window_title(self.doc).startswith("Untitled"):
                self.save_doc_as(f"Draft {time.strftime('%d%m %H%M%S')}")
            else:                                       # already named: a plain save, no dialog
                hotkey(VK_CONTROL, ord("S"))
                time.sleep(timing.think_s(1.0))
            self.close_doc()

    # -- teardown -------------------------------------------------------
    def shutdown(self):
        """Close everything cleanly at the end of the run (keyboard only, never taskkill).

        Bounded: if something refuses to close, report it and return rather than loop.
        """
        try:
            self.recover()
        except Exception as e:                          # still try to close the apps
            print(f"    shutdown: could not save the open doc ({e})", flush=True)
        for hwnd in self.explorers:
            if is_window(hwnd):
                focus(hwnd)
                hotkey(VK_CONTROL, ord("W"))
                time.sleep(timing.think_s(0.5))
        if is_window(self.notes):
            try:
                self.save_notes(stamped("Notes"))
                focus(self.notes)
                hotkey(VK_MENU, VK_F4)
                time.sleep(timing.think_s(0.6))
            except Exception as e:
                print(f"    shutdown: notes not closed ({e})", flush=True)
        for _ in range(3):
            hwnd = find_window(CHROME)
            if not hwnd:
                break
            focus(hwnd)
            hotkey(VK_CONTROL, VK_SHIFT, ord("W"))     # close the whole window, all tabs
            time.sleep(timing.think_s(0.8))
        for _ in range(3):
            hwnd = find_window("OpenOffice")
            if not hwnd:
                break
            focus(hwnd)
            hotkey(VK_CONTROL, ord("Q"))
            try:
                wait_for_window("OpenOffice", timeout=8, gone=True)
            except TimeoutError:
                fg = foreground()
                if fg and fg != hwnd:                   # a prompt we don't recognise: cancel it
                    tap(VK_ESCAPE)
                    time.sleep(timing.think_s(0.6))
        if find_window(CHROME) or find_window("OpenOffice") or is_window(self.notes):
            print("    shutdown: some windows stayed open", flush=True)


def run_standalone(task, done):
    """`__main__` for a task module: console setup, countdown, `task(session)`, shutdown."""
    disable_quickedit()
    print("Starting in 3s - let go of the mouse.", flush=True)
    time.sleep(3)
    session = Session()
    task(session)
    session.shutdown()
    print(done, flush=True)
