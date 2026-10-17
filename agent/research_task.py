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
from content import fresh, world_sentence
from human_input import (nap, VK_CONTROL, VK_DOWN, VK_END, VK_HOME, click_in, ensure_focus, focus,
                         hotkey, tap, type_text)
from office_loop import (bold_header, check_cell, currency_format, enter_cells, enter_row,
                         goto_cell, move_cursor, new_sheet, new_writer, parse_number, paste_down,
                         polish, read_cell, select_block, sort_dialog, type_paragraph)
from session import run_standalone
from web_loop import copy_a_word, glance, lookup_for, page_extra, read_page, save_page, try_a_link

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


BUDGETS = [1200, 1500, 1800, 2000]
MANAGERS = ["Rachel", "Kenneth", "Farah", "Hui Min", "Marcus", "Siti"]   # none is a WorldState name
BOLD_LABEL = "Recommendation:"


def pick_budget(rng, findings):
    """Chosen after the findings, so the memo has something to recommend: any budget that at
    least two models fit under, else the biggest."""
    roomy = [b for b in BUDGETS if sum(1 for f in findings if f[2] <= b) >= 2]
    return rng.choice(roomy) if roomy else max(BUDGETS)


def _and(names):
    return ", ".join(names[:-1]) + " and " + names[-1] if len(names) > 1 else names[0]


def _memo_lines(findings, budget, shops):
    """On-topic filler for the email: what a shopper would say about quotes, delivery,
    warranty and the shortlist."""
    direct = [s for s in shops if s != "AliExpress"]
    lines = ["The prices are the ones listed on each site today, so I would ask for a corporate "
             "quote before we order.",
             f"I kept the shortlist to {len(findings)} models and left anything above "
             f"{money(budget)} out of the running.",
             f"The comparison sheet uses the {money(budget)} budget in cell G1, so the Yes/No "
             f"column moves if that changes."]
    if "AliExpress" in shops:
        lines.append("Delivery is about 3 to 5 working days from the brand shops and nearer two "
                     "weeks from AliExpress.")
        if direct:
            lines.append(f"Warranty is simpler to claim on the {_and(direct)} orders, as AliExpress "
                         f"sellers would mean posting the unit back.")
    else:
        lines.append("Delivery is about 3 to 5 working days from all of the shops.")
        lines.append("Every model is listed with at least a one-year warranty.")
    return lines


def recommendation_text(session, findings, budget):
    """The memo as an email draft to finance, as a list of paragraphs."""
    w = session.world
    rng = session.rng
    manager = rng.choice(MANAGERS)
    within = sorted((f for f in findings if f[2] <= budget), key=lambda f: f[2])
    cheapest = min(findings, key=lambda f: f[2])
    shops = _and(list(dict.fromkeys(w.visited))) if w.visited else "the usual shops"
    n = len(within)
    count = f"{n} of them {'is' if n == 1 else 'are'}" if n else "None of them is"
    paras = [f"To: {manager} (Finance)\nSubject: Laptop shortlist - {time.strftime('%d %b %Y')}",
             f"Hi {manager},",
             f"I compared {len(findings)} models across {shops} against the {money(budget)} "
             f"budget. {count} within it. The cheapest overall is the {cheapest[1]} from "
             f"{cheapest[0]} at {money(cheapest[2])}."]
    extra = rng.sample(_memo_lines(findings, budget, list(dict.fromkeys(f[0] for f in findings))), 2)
    grounded = fresh(lambda: world_sentence(w, rng), set(extra))
    if grounded:
        extra.append(grounded)
    paras.append(" ".join(extra))
    if within:
        pick = within[-1]                               # most expensive that still fits = best spec
        text = (f"{BOLD_LABEL} the {pick[1]} ({pick[0]}, {money(pick[2])}). It is the "
                f"strongest option that still fits the budget.")
        if len(within) >= 2:                            # next-best that fits, never the pick itself
            backup = within[-2]
            text += (f"\nBackup option: the {backup[1]} ({backup[0]}, {money(backup[2])}), also "
                     f"within budget, in case the {pick[1]} is not available.")
        paras.append(text)
    else:
        paras.append(f"Nothing fits the {money(budget)} budget, so we either raise it or wait for "
                     f"a promotion.")
    paras.append(rng.choice([
        "Next steps: if you are happy with this, I will confirm delivery times and place the "
        "order next week.",
        "Next steps: let me know if the budget is fine and I will get a final quote from the "
        "vendor before ordering.",
        "Next steps: once you have signed off I will check delivery times and raise the "
        "purchase request."]))
    paras.append(f"Thanks,\n{w.name}")
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
    read_page(hwnd, rng, midway=lookup_for(hwnd, rng, SHOP_SITES[shop]))
    page_extra(hwnd, rng)
    if rng.random() < 0.5:
        try_a_link(hwnd, rng)
    word = copy_a_word(hwnd, rng, reject=(shop, SHOP_SITES[shop])) if rng.random() < 0.7 else None
    models = ", ".join(f"{m.replace(shop + ' ', '')} {p:,}" for s, m, p in research.findings
                       if s == shop)
    nap(timing.read_s(12))                       # "noting down" the models seen
    if word:
        session.world.terms[shop] = word
        # Chrome's double-click selection often carries a trailing space: Backspace it away
        # so it doesn't end up inside the quotes
        clip = screen.clipboard_text()
        session.note(f'{shop}: noticed "', paste=True, trim=len(clip) - len(clip.rstrip()),
                     after=f'" on their homepage. {models}')
    else:
        session.note(f"{shop}: {models}")
    if not session.page_saved and rng.random() < 0.35:
        save_page(session, hwnd, shop)                  # "keep a copy of this one"
    if session.tabs > 3 and rng.random() < 0.6:
        session.close_tab()


def _rows(session, hwnd, findings, first_row):
    """Type the shortlist rows. Usually (p~0.7, two or more rows) the Within-budget formula is
    typed in the first row only and pasted into the rest."""
    paste = len(findings) >= 2 and session.rng.random() < 0.7
    for row, (shop, model, price) in enumerate(findings, start=first_row):
        # AOO uses ';' as the function argument separator
        enter_row(session, hwnd, row, (shop, model, str(price)), f'=IF(C{row}<=$G$1;"Yes";"No")',
                  fill=paste and row > first_row)
    if paste:
        paste_down(session, hwnd, 3, first_row, first_row + 1, first_row + len(findings) - 1)


def start_sheet(session, research):
    """New comparison sheet with the shops noted so far; saved, then closed."""
    rng = session.rng
    hwnd = new_sheet(session)
    # the budget sits one empty column clear of the table, so Data > Sort sees only A:D later
    enter_cells(hwnd, "Shop", "Model", "Price (SGD)", "Within budget", "", "Budget",
                str(research.budget))
    rows = [f for f in research.findings if f[0] in session.world.visited]
    _rows(session, hwnd, rows, 2)
    if rng.random() < 0.5:
        nap(timing.read_s(20))                   # double-check against the notes
    nap(timing.read_s(15))
    name = session.save_doc_as(session.stamped("Laptop comparison"))   # may gain a suffix
    # only once it is on disk: a failed save (recovered as a Draft) must not send
    # finish_sheet after a file that doesn't exist
    research.sheet_rows = rows
    research.sheet = name
    session.close_doc()


def table_matches(text, rows, budget, by_price):
    """Does `text` - the A2:D block of the comparison sheet as read back through the clipboard
    (tab-separated, prices maybe shown as currency) - hold exactly `rows` (shop, model, price),
    each with the Yes/No its price earns against `budget`? `by_price`: and in price order
    (ties in either order). A sort that went wrong - another key, the header swept in, formulas
    pointing at the wrong row - fails this."""
    got = [line.split("\t") for line in text.replace("\r\n", "\n").strip("\n").split("\n")]
    if len(got) != len(rows) or any(len(cells) != 4 for cells in got):
        return False
    prices = [parse_number(cells[2]) for cells in got]
    if None in prices or (by_price and prices != sorted(prices)):
        return False
    have = sorted((c[0], c[1], p, c[3]) for c, p in zip(got, prices))
    want = sorted((shop, model, float(price), "Yes" if price <= budget else "No")
                  for shop, model, price in rows)
    return have == want


def sort_by_price(session, research, hwnd, rows):
    """Data > Sort by the Price column, then read the table back (A2:D, one copy). If it is
    not `rows` in price order, Ctrl+Z and keep the entry order - the table is never retyped.
    Must run before the summary rows exist, or the sort would sweep them up."""
    if not sort_dialog(session, hwnd, "P", 1, 1):
        print("    sort dialog did not open; entry order kept", flush=True)
        return
    select_block(session, hwnd, 0, 1, 4, len(rows))
    if table_matches(read_cell(), rows, research.budget, by_price=True):
        return
    print("    sort did not give the price order; undoing it", flush=True)
    nap(timing.think_s(1.2))                            # "that's not right"
    ensure_focus(hwnd)
    hotkey(VK_CONTROL, ord("Z"))
    nap(timing.think_s(0.8))
    select_block(session, hwnd, 0, 1, 4, len(rows))
    if not table_matches(read_cell(), rows, research.budget, by_price=False):
        print("    undo did not bring the table back; leaving it", flush=True)


def finish_sheet(session, research):
    """Reopen the sheet, add the remaining shops, add summary formulas, check them, save."""
    hwnd = session.reopen(research.sheet, "calc")
    rest = [f for f in research.findings if f not in research.sheet_rows]
    first = len(research.sheet_rows) + 2
    if session.calc_grid:
        nap(timing.perceive_s())
        goto_cell(session, hwnd, 0, first - 1, home=True)  # the first empty row's A cell
    else:
        click_in(hwnd, 0.3, 0.45)
        nap(timing.perceive_s())
        ensure_focus(hwnd)
        hotkey(VK_CONTROL, VK_END)                      # last used cell: G of the last data row
        nap(timing.short_s(0.3))
        tap(VK_DOWN)
        tap(VK_HOME)                                    # first empty row, column A
    _rows(session, hwnd, rest, first)
    last = first + len(rest) - 1
    rows = research.sheet_rows + rest
    # tidy the table now the data is in and before any summary row exists
    if polish(session, [lambda: bold_header(session, hwnd, 4),
                        lambda: currency_format(session, hwnd, 2, 2, last),
                        lambda: sort_by_price(session, research, hwnd, rows)]):
        goto_cell(session, hwnd, 0, last)               # the first empty row, column A
    prices = [p for _, _, p in research.findings]
    lo = min(prices)
    avg = int(Decimal(sum(prices) / len(prices)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    f_min, f_avg = f"=MIN(C2:C{last})", f"=ROUND(AVERAGE(C2:C{last});0)"
    for label, formula in (("Cheapest", f_min), ("Average", f_avg)):
        enter_cells(hwnd, label, "", formula)
    nap(timing.read_s(15))
    if session.calc_grid:                               # the MIN cell: C{last + 1}
        goto_cell(session, hwnd, 2, last)
    else:
        move_cursor(dx=2, dy=-2)
    check_cell(session, hwnd, lo, f_min, tol=0.5)
    tap(VK_DOWN)
    check_cell(session, hwnd, avg, f_avg, tol=0.5)
    nap(timing.read_s(10))
    ensure_focus(hwnd)
    hotkey(VK_CONTROL, ord("S"))                        # already .ods: saves in place, no dialog
    nap(timing.think_s(1.0))
    session.close_doc()


def bold_label_paragraph(hwnd, para):
    """A paragraph whose leading label is bold: Ctrl+B (a toggle) around the label only."""
    ensure_focus(hwnd)
    hotkey(VK_CONTROL, ord("B"))
    type_text(BOLD_LABEL, exact=True)
    hotkey(VK_CONTROL, ord("B"))
    type_text(para[len(BOLD_LABEL):], revise=True)
    type_text("\n\n")


def write_memo(session, research):
    rng = session.rng
    hwnd = new_writer(session)
    paras = recommendation_text(session, research.findings, research.budget)
    for i, para in enumerate(paras):
        if para.startswith(BOLD_LABEL):
            bold_label_paragraph(hwnd, para)
        else:
            type_paragraph(hwnd, [para], rng)           # single block: typed, not rewritten
        nap(timing.read_s(15))
        if i == 2 and rng.random() < 0.5:               # "let me just check that site again"
            glance(session, rng)
            focus(hwnd)
    session.save_doc_as(session.stamped("Email draft - laptop shortlist"))
    session.close_doc()


# ---------------------------------------------------------------- the goal

class Research:
    """The laptop-research goal as a set of steps with dependencies (see module docstring)."""

    # seconds per step, measured on the fake desktop (tools/fake_desktop.py)
    EST = {"visit": 90, "start_sheet": 115, "finish_sheet": 50, "memo": 280, "notes": 12}

    def __init__(self, session):
        rng = session.rng
        self.s = session
        self.shops = rng.sample(list(SHOP_SITES), 3)      # which 3 shops varies per run
        self.findings = pick_findings(rng, self.shops)
        self.budget = pick_budget(rng, self.findings)
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
        tried = [s for s in self.shops if self._done("visit:" + s)]
        noted = [s for s in tried if s in self.s.world.visited]  # a visit given up on isn't
        out = [("visit:" + s, lambda s=s: visit_shop(self.s, self, s))
               for s in self.shops if not self._done("visit:" + s)][:2]  # next two in shop order
        all_seen = len(tried) == len(self.shops)
        if self._done("start_sheet") and not self.sheet:
            self.done_steps.add("finish_sheet")         # the sheet was given up on: nothing to finish
        if all_seen:            # the sheet and memo cover only shops actually looked at
            self.findings = [f for f in self.findings if f[0] in noted]
            if not noted:       # nothing loaded all day: no comparison to write up
                self.done_steps |= {"start_sheet", "finish_sheet", "memo"}
        if (len(noted) >= 2 or all_seen and noted) and not self._done("start_sheet"):
            out.append(("start_sheet", lambda: start_sheet(self.s, self)))
        if all_seen and self.sheet and not self._done("finish_sheet"):
            out.append(("finish_sheet", lambda: finish_sheet(self.s, self)))
        if all_seen and self._done("start_sheet") and (self._done("finish_sheet") or not self.sheet) \
                and not self._done("memo"):
            out.append(("memo", lambda: write_memo(self.s, self)))
        if all_seen and not self._done("notes"):
            out.append(("notes", lambda: self.s.save_notes(self.s.stamped("Laptop notes"))))
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
        ready = r.ready()
        if not ready:                                   # a step was given up on: nothing left to do
            break
        name, fn = ready[0]
        t0 = time.time()
        try:
            r.run_step(name, fn)
            print(f"  {name} ({time.time() - t0:.0f}s)", flush=True)
        except Exception as e:
            print(f"  {name} failed: {e}", flush=True)
            session.recover()
        nap(timing.break_s(5, 15))


if __name__ == "__main__":
    run_standalone(research_task, "Done. Check Documents for the notes, comparison sheet and recommendation.")
