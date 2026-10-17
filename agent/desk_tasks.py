"""Small desk tasks between the bigger pieces of work: look over Documents in File Explorer,
jot a to-do in the scratch notes.

File Explorer windows live inside explorer.exe (Win+E spawns no new process by default), so
browsing Documents adds genuine shell activity - folder views, the address bar - without any
new process lineage. Nothing is renamed, moved or deleted.

Run on the VM:  python desk_tasks.py   (then let go of the mouse)
"""
import sys
sys.dont_write_bytecode = True  # no __pycache__/*.pyc from python.exe (EID 11)

import timing
from content import todo_line
from human_input import (nap, VK_CONTROL, VK_DELETE, VK_ESCAPE, VK_LWIN, VK_RETURN, VK_SHIFT,
                         ensure_focus, find_windows_by_class, focus, foreground, hotkey, hover_in,
                         scroll, tap, type_text, wait_for_new_window, wait_for_window)
from session import run_standalone

EXPLORER = "CabinetWClass"


def look_over_documents(session):
    """Win+E, go to Documents (or Downloads, once a page was saved from Chrome - its Save As
    lands there), switch view, skim the files, close the window."""
    rng = session.rng
    folder = "Downloads" if session.world.downloads and rng.random() < 0.5 else "Documents"
    before = set(find_windows_by_class(EXPLORER))
    hotkey(VK_LWIN, ord("E"))
    hwnd = wait_for_new_window("File Explorer", before, timeout=10, window_class=EXPLORER)
    session.explorers.append(hwnd)
    try:
        focus(hwnd)
        nap(timing.read_s(6))
        hotkey(VK_CONTROL, ord("L"))                    # address bar
        nap(timing.short_s(0.4))
        # the env-var path resolves on every Windows build/language, unlike a bare "Documents"
        type_text(f"%USERPROFILE%\\{folder}", speed=0.7, exact=True)
        nap(timing.short_s(0.3))
        tap(VK_DELETE)                                  # drop an autocompleted tail
        tap(VK_RETURN)
        wait_for_window(folder, timeout=10)
        nap(timing.perceive_s())
        if rng.random() < 0.6:                          # Details / Large icons view
            ensure_focus(hwnd)
            hotkey(VK_CONTROL, VK_SHIFT, ord(rng.choice("62")))
            nap(timing.think_s(0.8))
        for _ in range(rng.randint(1, 3)):
            hover_in(hwnd, rng.uniform(0.35, 0.8), rng.uniform(0.3, 0.75))
            if rng.random() < 0.5:
                scroll(rng.choice([-2, -1, 1, 2, 3]))
            nap(timing.read_s(12))
    finally:
        if foreground() != hwnd:                        # e.g. a "can't find" error box
            tap(VK_ESCAPE)
            nap(timing.think_s(0.5))
        focus(hwnd)
        hotkey(VK_CONTROL, ord("W"))                    # close this Explorer window
        nap(timing.think_s(0.6))
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
