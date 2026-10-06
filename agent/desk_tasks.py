"""Small desk tasks between the bigger pieces of work: look over Documents in File Explorer,
jot a to-do in the scratch notes.

File Explorer windows live inside explorer.exe (Win+E spawns no new process by default), so
browsing Documents adds genuine shell activity - folder views, the address bar - without any
new process lineage. Nothing is renamed, moved or deleted.

Run on the VM:  python desk_tasks.py   (then let go of the mouse)
"""
import sys
sys.dont_write_bytecode = True  # no __pycache__/*.pyc from python.exe (EID 11)

import time

import timing
from content import todo_line
from human_input import (VK_CONTROL, VK_DELETE, VK_ESCAPE, VK_LWIN, VK_RETURN, VK_SHIFT,
                         ensure_focus, find_windows_by_class, focus, foreground, hotkey, hover_in,
                         scroll, tap, type_text, wait_for_window)
from session import run_standalone

EXPLORER = "CabinetWClass"


def _new_explorer(before, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        for hwnd in find_windows_by_class(EXPLORER):
            if hwnd not in before:
                return hwnd
        time.sleep(0.25)
    raise TimeoutError("File Explorer did not open")


def look_over_documents(session):
    """Win+E, go to Documents, switch view, skim the files saved today, close the window."""
    rng = session.rng
    before = set(find_windows_by_class(EXPLORER))
    hotkey(VK_LWIN, ord("E"))
    hwnd = _new_explorer(before)
    session.explorers.append(hwnd)
    try:
        focus(hwnd)
        time.sleep(timing.read_s(6))
        hotkey(VK_CONTROL, ord("L"))                    # address bar
        time.sleep(timing.short_s(0.4))
        # the env-var path resolves on every Windows build/language, unlike a bare "Documents"
        type_text("%USERPROFILE%\\Documents", wpm=55, typo_rate=0.02, exact=True)
        time.sleep(timing.short_s(0.3))
        tap(VK_DELETE)                                  # drop an autocompleted tail
        tap(VK_RETURN)
        wait_for_window("Documents", timeout=10)
        time.sleep(timing.perceive_s())
        if rng.random() < 0.6:                          # Details / Large icons view
            ensure_focus(hwnd)
            hotkey(VK_CONTROL, VK_SHIFT, ord(rng.choice("62")))
            time.sleep(timing.think_s(0.8))
        for _ in range(rng.randint(1, 3)):
            hover_in(hwnd, rng.uniform(0.35, 0.8), rng.uniform(0.3, 0.75))
            if rng.random() < 0.5:
                scroll(rng.choice([-2, -1, 1, 2, 3]))
            time.sleep(timing.read_s(12))
    finally:
        if foreground() != hwnd:                        # e.g. a "can't find" error box
            tap(VK_ESCAPE)
            time.sleep(timing.think_s(0.5))
        focus(hwnd)
        hotkey(VK_CONTROL, ord("W"))                    # close this Explorer window
        time.sleep(timing.think_s(0.6))
        session.explorers.remove(hwnd)


def jot_todo(session):
    """Add a to-do line to the scratch notes."""
    session.note(todo_line(session.rng))


def _main(session):
    jot_todo(session)
    look_over_documents(session)
    jot_todo(session)


if __name__ == "__main__":
    run_standalone(_main, "Done.")
