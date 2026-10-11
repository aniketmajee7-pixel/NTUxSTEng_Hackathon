"""Laptop-research goal: browse shops, keep notes, build and later finish a Calc comparison,
then write a recommendation memo.

The goal is split into steps (`Research.ready()`), so the workday planner in `agent.py` can
interleave them with other work - visit two shops, start the sheet, go read the news, visit
the rest, reopen the sheet and finish it - instead of replaying one fixed storyline (which is
itself a fingerprint). Steps carry real dependencies: no sheet before two shops are noted, no
memo before the sheet is finished.

The mock shop sites serve homepages only, so models and prices come from a small local
catalogue; what IS real is the browsing trail, a word actually copied off each homepage into
the notes, and the Calc totals read back via the clipboard before the memo quotes them.

Run on the VM:  python research_task.py   (then let go of the mouse)
"""
import sys
sys.dont_write_bytecode = True  # no __pycache__/*.pyc from python.exe (EID 11)

import time
from decimal import ROUND_HALF_UP, Decimal

import screen
import timing
from content import sentence, world_sentence
from human_input import VK_CONTROL, VK_DOWN, VK_END, VK_HOME, click_in, ensure_focus, focus, hotkey, tap
from office_loop import check_cell, enter_cell, goto_cell, move_cursor, new_sheet, type_paragraph
from session import run_standalone, stamped
from web_loop import copy_a_word, glance, read_page, try_a_link

# Approximate SGD list prices, used as what the "researcher" noted on each site.
CATALOGUE = {
    "Asus": [("ASUS Zenbook 14 OLED", 1598), ("ASUS Vivobook S 15", 1298),
             ("ASUS ProArt PX13", 2898), ("ASUS Vivobook 16", 999)],
    "Apple": [("MacBook Air 13 M4", 1499), ("MacBook Air 15 M4", 1799), ("MacBook Pro 14 M5", 2399)],
    "Samsung": [("Galaxy Book5 Pro 14", 2098), ("Galaxy Book5 360", 1698), ("Galaxy Book4 Edge", 1898)],
    "AliExpress": [("Lenovo IdeaPad Slim 5", 1049), ("Acer Swift Go 14", 1199),
                   ("HP Pavilion 15", 999), ("Chuwi CoreBook X", 549)],
}
# dell.com and amazon.com don't load on the VM (see web_loop.SITES)
SHOP_SITES = {"Asus": "www.asus.com", "Apple": "www.apple.com", "Samsung": "www.samsung.com",
              "AliExpress": "www.aliexpress.com"}


def money(value):
    return f"S${value:,}"


def pick_findings(rng, shops=None):
    """A different shortlist each run: 1-2 models per shop, prices nudged."""
    findings = []
    for shop, models in CATALOGUE.items():
        if shops is not None and shop not in shops:
            continue
        for model, price in rng.sample(models, 2 if rng.random() < 0.75 else 1):
            findings.append((shop, model, price + rng.choice([0, 0, -50, -100, 50])))
    return findings


def recommendation_text(session, findings, budget):
    w = session.world
    rng = session.rng
    within = sorted((f for f in findings if f[2] <= budget), key=lambda f: f[2])
    cheapest = min(findings, key=lambda f: f[2])
    shops = ", ".join(w.visited) if w.visited else "the usual shops"
    paras = [f"Laptop shortlist - {time.strftime('%d %b %Y')}",
             f"Budget: {money(budget)}. I compared {len(findings)} models across {shops}. "
             f"{len(within)} of them are within budget. The cheapest overall is the {cheapest[1]} "
             f"from {cheapest[0]} at {money(cheapest[2])}."]
    voice = [sentence(rng)]                             # a line in the worker's own voice
    grounded = world_sentence(w, rng)
    if grounded:
        voice.append(grounded)
    paras.append(" ".join(voice))
    if within:
        pick = within[-1]                               # most expensive that still fits = best spec
        paras.append(f"Recommendation: the {pick[1]} ({pick[0]}, {money(pick[2])}). It is the "
                     f"strongest option that still fits the budget.\n"
                     f"Backup option: the {within[0][1]} at {money(within[0][2])} if we want to "
                     f"save more.")
    else:
        paras.append("Nothing fits the budget, so we either raise it or wait for a promotion.")
    paras.append("Next steps: confirm the budget with finance and check delivery times before "
                 "ordering.")
    return paras


# ---------------------------------------------------------------- steps

def visit_shop(session, research, shop):
    """Browse one shop's homepage, maybe follow a link, copy a word into the notes."""
    rng = session.rng
    new_tab = bool(session.world.visited) and rng.random() < 0.5
    hwnd = session.open_url(SHOP_SITES[shop], new_tab=new_tab)
    if hwnd is None:                                    # site down today: note it, drop its models
        rest = [f for f in research.findings if f[0] != shop]
        if rest:
            research.findings = rest
            session.world.findings = [f for f in session.world.findings if f[0] != shop]
        session.note(f"{shop}: site wouldn't load, skip")
        return
    session.world.visit(shop)
    read_page(hwnd, rng, bursts=(2, 4))
    if rng.random() < 0.5:
        try_a_link(hwnd, rng)
    word = copy_a_word(hwnd, rng) if rng.random() < 0.7 else None
    models = ", ".join(f"{m.replace(shop + ' ', '')} {p:,}" for s, m, p in research.findings
                       if s == shop)
    time.sleep(timing.read_s(12))                       # "noting down" the models seen
    if word:
        session.world.terms[shop] = word
        # Chrome's double-click selection often carries a trailing space; don't double it
        sep = "" if screen.clipboard_text().endswith((" ", "\t")) else " "
        session.note(f"{shop}: ", paste=True, after=f"{sep}is all over their homepage. {models}")
    else:
        session.note(f"{shop}: {models}")
    if session.tabs > 3 and rng.random() < 0.6:
        session.close_tab()


def _rows(session, hwnd, findings, first_row):
    for row, (shop, model, price) in enumerate(findings, start=first_row):
        if session.calc_grid and session.rng.random() < 0.5:   # click the row's first cell
            goto_cell(session, hwnd, 0, row - 1)
        for v in (shop, model, str(price)):
            enter_cell(hwnd, v)
        # AOO uses ';' as the function argument separator
        enter_cell(hwnd, f'=IF(C{row}<=$F$1;"Yes";"No")', last=True)


def start_sheet(session, research):
    """New comparison sheet with the shops noted so far; saved, then closed."""
    rng = session.rng
    hwnd = new_sheet(session)
    for i, v in enumerate(("Shop", "Model", "Price (SGD)", "Within budget", "Budget",
                           str(research.budget))):
        enter_cell(hwnd, v, last=(i == 5))
    rows = [f for f in research.findings if f[0] in session.world.visited]
    _rows(session, hwnd, rows, 2)
    if rng.random() < 0.5:
        time.sleep(timing.read_s(20))                   # double-check against the notes
    name = stamped("Laptop comparison")
    time.sleep(timing.read_s(15))
    session.save_doc_as(name)
    # only once it is on disk: a failed save (recovered as a Draft) must not send
    # finish_sheet after a file that doesn't exist
    research.sheet_rows = rows
    research.sheet = name
    session.close_doc()


def finish_sheet(session, research):
    """Reopen the sheet, add the remaining shops, add summary formulas, check them, save."""
    hwnd = session.reopen(research.sheet, "calc")
    rest = [f for f in research.findings if f not in research.sheet_rows]
    first = len(research.sheet_rows) + 2
    if session.calc_grid:
        time.sleep(timing.perceive_s())
        goto_cell(session, hwnd, 0, first - 1, home=True)  # the first empty row's A cell
    else:
        click_in(hwnd, 0.3, 0.45)
        time.sleep(timing.perceive_s())
        ensure_focus(hwnd)
        hotkey(VK_CONTROL, VK_END)                      # last used cell: F of the last data row
        time.sleep(timing.short_s(0.3))
        tap(VK_DOWN)
        tap(VK_HOME)                                    # first empty row, column A
    _rows(session, hwnd, rest, first)
    last = first + len(rest) - 1
    prices = [p for _, _, p in research.findings]
    lo = min(prices)
    avg = int(Decimal(sum(prices) / len(prices)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    f_min, f_avg = f"=MIN(C2:C{last})", f"=ROUND(AVERAGE(C2:C{last});0)"
    for label, formula in (("Cheapest", f_min), ("Average", f_avg)):
        enter_cell(hwnd, label)
        enter_cell(hwnd, "")
        enter_cell(hwnd, formula, last=True)
    time.sleep(timing.read_s(15))
    if session.calc_grid:                               # the MIN cell: C{last + 1}
        goto_cell(session, hwnd, 2, last)
    else:
        move_cursor(dx=2, dy=-2)
    check_cell(session, hwnd, lo, f_min, tol=0.5)
    tap(VK_DOWN)
    check_cell(session, hwnd, avg, f_avg, tol=0.5)
    time.sleep(timing.read_s(10))
    ensure_focus(hwnd)
    hotkey(VK_CONTROL, ord("S"))                        # already .ods: saves in place, no dialog
    time.sleep(timing.think_s(1.0))
    session.close_doc()


def write_memo(session, research):
    rng = session.rng
    hwnd = session.new_doc("writer")
    click_in(hwnd, rng.uniform(0.35, 0.6), rng.uniform(0.35, 0.5))
    time.sleep(timing.perceive_s())
    paras = recommendation_text(session, research.findings, research.budget)
    for i, para in enumerate(paras):
        type_paragraph(hwnd, [para], rng)               # single block: typed, not rewritten
        time.sleep(timing.read_s(15))
        if i == 1 and rng.random() < 0.5:               # "let me just check that site again"
            glance(session, rng)
            focus(hwnd)
    session.save_doc_as(stamped("Laptop recommendation"))
    session.close_doc()


# ---------------------------------------------------------------- the goal

class Research:
    """The laptop-research goal as a set of steps with dependencies (see module docstring)."""

    # seconds per step, measured on the fake desktop (tools/fake_desktop.py)
    EST = {"visit": 90, "start_sheet": 140, "finish_sheet": 70, "memo": 190, "notes": 15}

    def __init__(self, session):
        rng = session.rng
        self.s = session
        self.budget = rng.choice([1200, 1500, 1800, 2000])
        self.shops = rng.sample(list(SHOP_SITES), 3)      # which 3 shops varies per run
        self.findings = pick_findings(rng, self.shops)
        session.world.budget = self.budget
        for f in self.findings:
            session.world.note(*f)
        self.sheet = None
        self.sheet_rows = []
        self.done_steps = set()
        self.attempts = {}

    def _done(self, name):
        return name in self.done_steps or self.attempts.get(name, 0) >= 2   # give up after 2 tries

    def ready(self):
        """Steps whose dependencies are met: [(name, fn)]."""
        visited = [s for s in self.shops if self._done("visit:" + s)]
        out = [("visit:" + s, lambda s=s: visit_shop(self.s, self, s))
               for s in self.shops if not self._done("visit:" + s)][:2]  # next two in shop order
        if len(visited) >= 2 and not self._done("start_sheet"):
            out.append(("start_sheet", lambda: start_sheet(self.s, self)))
        all_seen = len(visited) == len(self.shops)
        if all_seen and self.sheet and not self._done("finish_sheet"):
            out.append(("finish_sheet", lambda: finish_sheet(self.s, self)))
        if all_seen and self._done("start_sheet") and (self._done("finish_sheet") or not self.sheet) \
                and not self._done("memo"):
            out.append(("memo", lambda: write_memo(self.s, self)))
        if all_seen and not self._done("notes"):
            out.append(("notes", lambda: self.s.save_notes(stamped("Laptop notes"))))
        return out

    def remaining_est(self):
        names = ["visit:" + s for s in self.shops] + ["start_sheet", "finish_sheet", "memo", "notes"]
        return sum(self.EST[n.split(":")[0]] for n in names if not self._done(n))

    def done(self):
        return not self.ready() and self.remaining_est() == 0

    def run_step(self, name, fn):
        self.attempts[name] = self.attempts.get(name, 0) + 1
        fn()
        self.done_steps.add(name)


def research_task(session):
    """Standalone: run the whole goal in a natural order with short breaks between steps."""
    r = Research(session)
    while not r.done():
        name, fn = r.ready()[0]
        t0 = time.time()
        try:
            r.run_step(name, fn)
            print(f"  {name} ({time.time() - t0:.0f}s)", flush=True)
        except Exception as e:
            print(f"  {name} failed: {e}", flush=True)
            session.recover()
        time.sleep(timing.break_s(5, 15))


if __name__ == "__main__":
    run_standalone(research_task, "Done. Check Documents for the notes, comparison sheet and recommendation.")
