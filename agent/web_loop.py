"""Web tasks on the organisers' mock sites: browse homepages, scroll, follow links, copy text.

Only the homepages exist on the mock servers; inner links return nginx 404s, so we move
between sites via the omnibox (keeping URLs out of the Run dialog / RunMRU) and treat a 404
the way a person does - glance, then go back.

The agent looks before it clicks: `screen` grabs the page, `vision` proposes text-like spots,
and the live cursor shape confirms them - a hand means a link, an I-beam means selectable
text. So link clicks land on real links, and "copy a word off the page" copies a real word
that then shows up in the notes and the memo.

Run on the VM:  python web_loop.py   (then let go of the mouse)
"""
import sys
sys.dont_write_bytecode = True  # no __pycache__/*.pyc from python.exe (EID 11)

import random
import re
import time

import screen
import timing
import vision
from human_input import (VK_CONTROL, VK_LEFT, VK_MENU, click, double_click, ensure_focus,
                         focus, hotkey, hover_in, move_to, scroll, wait_for_window, window_title)
from session import run_standalone

# The sites the organisers say load cleanly once root_ca.crt is installed.
SITES = ["www.coinmarketcap.com", "www.coursera.org", "www.coinbase.com", "coingecko.com",
         "www.samsung.com", "aws.amazon.com", "www.amazon.com", "www.apple.com", "apnews.com",
         "www.dell.com"]
NEWSY = ["apnews.com", "www.coursera.org", "www.coinmarketcap.com", "coingecko.com",
         "www.coinbase.com", "aws.amazon.com"]

_STOP = {"sign", "menu", "home", "shop", "cart", "search", "more", "login", "help", "about",
         "learn", "view", "this", "that", "with", "your", "from", "have", "will", "account",
         "close", "open", "next", "back", "click", "here", "free", "today", "only", "best"}


def read_page(hwnd, rng=random, bursts=(2, 5)):
    """Skim the page in reading-aware bursts: a few notches at a time, the odd scroll back."""
    for _ in range(rng.randint(*bursts)):
        if rng.random() < 0.5:                          # reposition the hand over the content
            hover_in(hwnd, rng.uniform(0.3, 0.7), rng.uniform(0.4, 0.8))
        if rng.random() < 0.82:
            scroll(rng.randint(1, 4))                   # read downward
        else:
            scroll(-rng.randint(1, 3))                  # glance back up
        time.sleep(timing.read_s(rng.randint(15, 45)))


def _spots(hwnd, rng, step=2):
    rect = screen.content_rect(hwnd)
    w, h, g = screen.grab_gray(rect, step)
    pts = vision.text_spots(w, h, g, rng=rng)
    top = pts[:12]
    rng.shuffle(top)                                    # don't always go for the same spot
    return [(rect[0] + x * step, rect[1] + y * step) for x, y in top]


def point_at(hwnd, want, rng, probes=5):
    """Move over likely text until the cursor turns into `want` ('hand' or 'ibeam').

    This is how a person finds a link: move toward something, watch the pointer. Returns the
    point, or None if nothing qualified within a few glances.
    """
    for x, y in _spots(hwnd, rng)[:probes]:
        move_to(x + rng.gauss(0, 2), y + rng.gauss(0, 1))
        time.sleep(timing.short_s(0.2))
        if screen.cursor_kind() == want:
            return x, y
        time.sleep(timing.short_s(0.3))
    return None


def try_a_link(hwnd, rng=random):
    """Click a real link; on the mock sites that's a 404, so look at it and go back."""
    if not point_at(hwnd, "hand", rng):
        return False
    before = window_title(hwnd)
    time.sleep(timing.think_s(0.3))
    click()
    end = time.time() + 5
    while time.time() < end and window_title(hwnd) == before:
        time.sleep(0.25)
    if window_title(hwnd) != before:
        screen.wait_painted(hwnd, timeout=5)
        time.sleep(timing.think_s(1.5))                 # "huh, broken page"
        ensure_focus(hwnd)
        hotkey(VK_MENU, VK_LEFT)
        try:
            wait_for_window(before[:40], timeout=8)
        except TimeoutError:                            # slow back-navigation: carry on anyway
            pass
        time.sleep(timing.read_s(10))
    return True


def copy_a_word(hwnd, rng=random):
    """Double-click a word on the page and Ctrl+C it; return the word if it is worth noting.

    Only fires where the cursor is an I-beam (selectable text, not a link), and confirms the
    copy via the clipboard sequence number, so the caller knows the clipboard holds `word`.
    """
    if not point_at(hwnd, "ibeam", rng):
        return None
    seq = screen.clipboard_seq()
    double_click()
    time.sleep(timing.short_s(0.35))
    hotkey(VK_CONTROL, ord("C"))
    copied = screen.wait_clipboard_change(seq)
    word = screen.clipboard_text().strip() if copied else ""
    time.sleep(timing.short_s(0.4))
    click()                                             # drop the selection (still on text)
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9\-]{3,19}", word) and word.lower() not in _STOP:
        return word
    return None


def glance(session, rng=random):
    """An interruption: flip to the browser, skim a little, come back (caller refocuses)."""
    hwnd = session.ensure_chrome()
    focus(hwnd)
    time.sleep(timing.perceive_s())
    read_page(hwnd, rng, bursts=(1, 2))


def web_task(session, sites=None):
    """Browse one or two non-shopping sites (news, courses, prices) - a short ambient task."""
    rng = session.rng
    sites = sites or rng.sample(NEWSY, rng.randint(1, 2))
    for i, site in enumerate(sites):
        session.open_url(site, new_tab=(rng.random() < 0.5))
        hwnd = session.chrome
        read_page(hwnd, rng)
        if rng.random() < 0.6:
            try_a_link(hwnd, rng)
        if session.tabs > 3 and rng.random() < 0.6:
            session.close_tab()                         # tidy up older tabs now and then
        if i + 1 < len(sites):
            time.sleep(timing.break_s(8, 30))


if __name__ == "__main__":
    run_standalone(lambda session: web_task(session, sites=random.sample(SITES, 3)), "Done.")
