"""Web tasks on the organisers' mock sites: browse homepages, scroll, follow links,
copy text, save a page.

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

import screen
import timing
import vision
from human_input import (Interrupted, nap, VK_CONTROL, VK_ESCAPE, VK_F5, VK_LEFT, VK_MENU,
                         VK_NEXT, VK_OEM_PLUS, VK_RETURN, click, cursor_pos, double_click, ensure_focus, find_window,
                         focus, hotkey, hover_in, move_to, scroll, tap, type_text,
                         wait_for_window, wait_title, window_title)
from session import fill_save_dialog, run_standalone

# Homepages from the organisers' list. VM recordings (teammates' desktop-demo handoff, 10 Oct): coinbase,
# amazon and coursera stay on Chrome's privacy warning (HSTS: no Proceed); dell and
# coinmarketcap paint nothing; cloud.google.com and cloudflare.com show no Proceed link.
# Those are left out. The rest may still show the warning first; `Session.open_url` goes
# through Advanced -> Proceed and drops any site that still won't load.
SITES = ["www.samsung.com", "aws.amazon.com", "www.apple.com", "apnews.com", "coingecko.com",
         "www.archive.org", "www.arstechnica.com", "www.aliexpress.com", "www.arxiv.org",
         "www.asana.com", "www.asus.com", "www.bbc.com", "www.binance.com", "www.bing.com",
         "www.bleacherreport.com", "www.booking.com", "www.cnet.com", "www.cnn.com"]
NEWSY = ["apnews.com", "www.bbc.com", "www.cnn.com", "www.arstechnica.com", "www.cnet.com",
         "coingecko.com", "www.bleacherreport.com", "aws.amazon.com"]

_STOP = {"sign", "menu", "home", "shop", "cart", "search", "more", "login", "help", "about",
         "learn", "view", "this", "that", "with", "your", "from", "have", "will", "account",
         "close", "open", "next", "back", "click", "here", "free", "today", "only", "best"}
# consent-banner / legal words: a "word off the page" that is really cookie small print
_LEGAL = {"cookie", "cookies", "privacy", "accept", "policy", "terms", "consent"}

# A word a visitor would look for on each homepage with Ctrl+F (the teammates' desktop-demo
# config.json "term" per site; the shop sites are in it too). Any other host: its own name.
FIND_TERMS = {
    "www.samsung.com": "Galaxy", "aws.amazon.com": "cloud", "www.apple.com": "Mac",
    "apnews.com": "news", "coingecko.com": "Bitcoin", "www.archive.org": "archive",
    "www.arstechnica.com": "technology", "www.aliexpress.com": "deals", "www.arxiv.org": "papers",
    "www.asana.com": "projects", "www.asus.com": "laptop", "www.bbc.com": "news",
    "www.binance.com": "crypto", "www.bing.com": "search", "www.bleacherreport.com": "sports",
    "www.booking.com": "hotels", "www.cnet.com": "reviews", "www.cnn.com": "news",
}
FIND_SHARE = 0.35                                       # of site visits that look something up


def _over_page(hwnd, rng):
    """Wheel and keys act on the page under the pointer: bring the hand over the content first."""
    l, t, r, b = screen.content_rect(hwnd)
    x, y = cursor_pos()
    if not (l + 40 <= x <= r - 40 and t + 40 <= y <= b - 40):
        hover_in(hwnd, rng.uniform(0.3, 0.7), rng.uniform(0.4, 0.8))


def _section(hwnd, rect, rng, before):
    """One sustained downward move: mostly wheel flicks of 5-9 notches, now and then Page Down
    (never Space). Returns (moved, view): `moved` is False when the picture did not change, i.e. the
    page is at its end."""
    if rng.random() < 0.15:
        ensure_focus(hwnd)                              # keys go to whatever has focus
        tap(VK_NEXT)        # never Space: it would type into an autofocused search box
    else:
        scroll(rng.randint(5, 9))
    nap(timing.short_s(0.3))                            # smooth scrolling settles
    view = screen.grab_gray(rect, 8)[2]
    return not vision.same_view(before, view), view


def read_page(hwnd, rng=random, sections=(5, 8), midway=None):
    """Browse a page the way a recording of a person did: 5-8 sustained downward moves, each
    followed by a look at the new section (about 1.1 s, a quarter of the time 3-5 s), one
    "interest" hover on some text, an early small step back up, and, once the view stops
    changing (bottom), a 1-2 step review upward. `sections=(1, 2)` is a glance: just the
    moves and looks. `midway`: a callable run once part way through (after about half the
    sections, or at the bottom if the page ends first) - e.g. a Ctrl+F lookup."""
    n = rng.randint(*sections)
    full = n >= 4
    _over_page(hwnd, rng)
    rect = screen.content_rect(hwnd)
    view = screen.grab_gray(rect, 8)[2]
    hover_at = rng.randint(1, n - 1) if full else -1    # after which section the eye is caught
    early = rng.randint(1, 2)
    pause_at = n // 2
    for i in range(n):
        moved, view = _section(hwnd, rect, rng, view)
        if not moved:
            nap(timing.short_s(0.5))                    # nothing moved: that's the end
            break
        nap(timing.review_s())
        if i == pause_at and midway:
            midway, mid = None, midway
            mid()
            view = screen.grab_gray(rect, 8)[2]         # the match scrolled the page
        if i == hover_at:
            spots = _spots(hwnd, rng)
            if spots:
                x, y = spots[0]
                move_to(x + rng.gauss(0, 2), y + rng.gauss(0, 1))
                nap(timing.dwell_s())
        elif full and i == early and rng.random() < 0.25:
            scroll(-rng.randint(1, 2))                  # "was that where I was?"
            nap(timing.short_s(0.7))
        elif rng.random() < 0.15:                       # reposition the hand over the content
            hover_in(hwnd, rng.uniform(0.3, 0.7), rng.uniform(0.4, 0.8))
    if midway:                                          # the page ended before the pause
        midway()
    if full:
        for _ in range(rng.randint(1, 2)):
            scroll(-rng.randint(3, 6))
            nap(timing.review_s())


def find_on_page(hwnd, rng, term):
    """Ctrl+F, type the site's keyword, Enter, sometimes step on to the next match or two
    (a short read after each), Esc to close the bar. A takeover mid-way still closes the
    bar before the exception goes on: the next omnibox use must not meet it."""
    try:
        ensure_focus(hwnd)
        hotkey(VK_CONTROL, ord("F"))
        nap(timing.short_s(0.6))                        # the bar slides in
        ensure_focus(hwnd)
        type_text(term, exact=True)
        nap(timing.short_s(0.35))
        tap(VK_RETURN)
        nap(timing.review_s())
        for _ in range(rng.choice((0, 0, 1, 2))):
            tap(VK_RETURN)                              # next match
            nap(timing.review_s())
        ensure_focus(hwnd)
        tap(VK_ESCAPE)
        nap(timing.short_s(0.4))
    except Exception:
        try:
            ensure_focus(hwnd)
            tap(VK_ESCAPE)
        except Exception:
            pass
        raise


def lookup_for(hwnd, rng, host):
    """For `read_page(midway=...)`: on about a third of site visits (FIND_SHARE) a function
    that looks up the site's keyword on the page, else None."""
    if rng.random() >= FIND_SHARE:
        return None
    term = FIND_TERMS.get(host) or host.split(".")[-2]  # no entry: the site's own name
    return lambda: find_on_page(hwnd, rng, term)


def page_extra(hwnd, rng):
    """At most once per site visit, p~0.12: reload with F5, or zoom in, look, and back to 100%."""
    if rng.random() >= 0.12:
        return
    ensure_focus(hwnd)
    if rng.random() < 0.5:
        tap(VK_F5)
        nap(timing.perceive_s())
        screen.wait_painted(hwnd)
        nap(timing.perceive_s())
    else:
        hotkey(VK_CONTROL, VK_OEM_PLUS)
        nap(timing.read_s(8))
        ensure_focus(hwnd)
        hotkey(VK_CONTROL, ord("0"))
        nap(timing.short_s(0.6))


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
        nap(timing.short_s(0.2))
        if screen.cursor_kind() == want:
            return x, y
        nap(timing.short_s(0.3))
    return None


def _back_to(hwnd, before):
    """Alt+Left to the page titled `before`; if that does nothing the link opened a new tab
    (no history), so close it, keeping the session's tab count true."""
    ensure_focus(hwnd)
    hotkey(VK_MENU, VK_LEFT)
    try:
        wait_for_window(before[:40], timeout=14)        # a slow Back must not become Ctrl+W
    except TimeoutError:
        if window_title(hwnd) != before:
            nap(timing.think_s(0.6))
            ensure_focus(hwnd)
            hotkey(VK_CONTROL, ord("W"))
            try:
                wait_for_window(before[:40], timeout=5)
            except TimeoutError:
                pass


def try_a_link(hwnd, rng=random):
    """Click a real link; on the mock sites that's a 404, so look at it and go back."""
    if not point_at(hwnd, "hand", rng):
        return
    before = window_title(hwnd)
    nap(timing.think_s(0.3))
    click()
    if wait_title(hwnd, lambda t: t != before, 5):
        screen.wait_painted(hwnd, timeout=5)
        nap(timing.think_s(1.5))                 # "huh, broken page"
        _back_to(hwnd, before)
        nap(timing.read_s(10))


def copy_a_word(hwnd, rng=random, reject=()):
    """Double-click a word on the page and Ctrl+C it; return the word if it is worth noting.

    Only fires where the cursor is an I-beam (selectable text, not a link), and confirms the
    copy via the clipboard sequence number, so the caller knows the clipboard holds `word`.
    `reject`: names whose parts are no use as "a word on their homepage" (the shop's own name,
    its host); cookie/legal words are always refused.
    """
    if not point_at(hwnd, "ibeam", rng):
        return None
    seq = screen.clipboard_seq()
    double_click()
    nap(timing.short_s(0.35))
    hotkey(VK_CONTROL, ord("C"))
    copied = screen.wait_clipboard_change(seq)
    word = screen.clipboard_text().strip() if copied else ""
    nap(timing.short_s(0.4))
    click()                                             # drop the selection (still on text)
    barred = _STOP | _LEGAL | {p for r in reject for p in re.split(r"[^a-z0-9]+", r.lower()) if p}
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9\-]{3,19}", word) and word.lower() not in barred:
        return word
    return None


def save_page(session, hwnd, shop):
    """Ctrl+S the page in front: a person "downloading" something from the web. Chrome's Save As
    opens in Downloads with the page title selected; type a name (in this session's naming
    style), Enter, and answer a "file exists" box like any Save As. Anything going wrong is
    swallowed: this must never take the visit down with it."""
    session.page_saved = True
    try:
        focus(hwnd)
        nap(timing.think_s(0.6))
        hotkey(VK_CONTROL, ord("S"))
        wait_for_window("Save As", timeout=10)
        name = fill_save_dialog(session.stamped(f"{shop} homepage"), session.rng, look=8)
        session.world.downloads.append(name)
        nap(timing.think_s(0.8))                        # the download bubble pops up
    except Interrupted:
        raise                                           # a takeover: let the caller recover
    except Exception as e:
        print(f"    save_page: {e}", flush=True)
        for _ in range(3):                              # a Confirm box sits on top of Save As
            dlg = find_window("Save As")
            if not dlg:
                break
            focus(dlg)
            tap(VK_ESCAPE)
            nap(timing.think_s(0.5))


def glance(session, rng=random):
    """An interruption: flip to the browser, skim a little, come back (caller refocuses)."""
    hwnd = session.ensure_chrome()
    focus(hwnd)
    nap(timing.perceive_s())
    read_page(hwnd, rng, sections=(1, 2))


def web_task(session, sites=None):
    """Browse one or two non-shopping sites (news, courses, prices) - a short ambient task."""
    rng = session.rng
    sites = sites or rng.sample(NEWSY, rng.randint(1, 2))
    for i, site in enumerate(sites):
        hwnd = session.open_url(site, new_tab=(rng.random() < 0.5))
        if hwnd is None:                                # warning with no way past / blank page
            continue
        read_page(hwnd, rng, midway=lookup_for(hwnd, rng, site))
        page_extra(hwnd, rng)
        if rng.random() < 0.6:
            try_a_link(hwnd, rng)
        if session.tabs > 3 and rng.random() < 0.6:
            session.close_tab()                         # tidy up older tabs now and then
        if i + 1 < len(sites):
            nap(timing.break_s(8, 30))


if __name__ == "__main__":
    run_standalone(lambda session: web_task(session, sites=random.sample(SITES, 3)), "Done.")
