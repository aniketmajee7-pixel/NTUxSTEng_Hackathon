"""Office tasks on OpenOffice Writer/Calc/Impress: write, fill, verify, save native, close.

Each task takes the Session so the agent keeps one OpenOffice alive across tasks.

Calc results are read back through the clipboard (Ctrl+C on the cell) and a wrong value is
retyped, the way a person double-checks a total before saving. Cells are often reached with
the mouse: `calibrate` finds the sheet on screen (AOO exposes no cells to UI Automation), and
`goto_cell` clicks a cell and checks its headers lit up, falling back to the keyboard.

Run on the VM:  python office_loop.py [impress]   (then let go of the mouse)
Files are saved to Documents as native .odt/.ods/.odp (no format dialog to trip over).
"""
import sys
sys.dont_write_bytecode = True  # no __pycache__/*.pyc from python.exe (EID 11)

import re
import time

import screen
import timing
import vision
from content import fresh, sentence, world_sentence
from human_input import (nap, VK_BACK, VK_CONTROL, VK_DELETE, VK_DOWN, VK_ESCAPE, VK_HOME, VK_MENU,
                         VK_LEFT, VK_RETURN, VK_RIGHT, VK_SHIFT, VK_TAB, VK_UP, click, click_in,
                         ensure_focus, find_window, focus, hold_chord, hold_key, hotkey, move_to, tap,
                         type_text, wait_for_window)
from session import run_standalone

# plausible SGD unit prices per item, with retail-style endings (.99/.95/.00): a laptop is
# never 39.95 and a mouse never 1299
ITEM_PRICES = {
    "Laptop": [1149.00, 1199.00, 1299.00], "Monitor": [219.00, 249.99, 289.00],
    "Keyboard": [49.95, 79.00, 119.00], "Docking station": [129.99, 189.00, 229.00],
    "Headset": [59.99, 89.95, 149.00], "Webcam": [45.95, 69.99, 99.00],
    "Projector": [449.00, 599.00, 749.00], "USB hub": [19.95, 29.99, 39.95],
    "Mouse": [24.95, 39.95, 59.99], "Standing desk": [349.00, 449.00, 549.00],
    "Office chair": [199.00, 249.00, 329.00], "Label printer": [89.00, 129.99, 159.00],
}
ITEMS = list(ITEM_PRICES)


def type_paragraph(hwnd, sentences, rng, seen=None):
    """Type a paragraph, sometimes rewriting its last sentence before moving on (`seen`: the
    document's sentences so far, so the rewrite doesn't repeat one).

    Monotonic left-to-right typing with only typo fixes reads as transcribed text; a person
    drafting will often hold Backspace over a sentence they don't like and try again.
    """
    ensure_focus(hwnd)
    type_text(" ".join(sentences), revise=True)
    if len(sentences) > 1 and rng.random() < 0.45:
        nap(timing.read_s(len(sentences[-1].split())))   # re-read it, decide to redo
        ensure_focus(hwnd)
        hold_key(VK_BACK, len(sentences[-1]))           # leaves the space before it
        nap(timing.think_s(1.5))
        type_text(fresh(lambda: sentence(rng), seen if seen is not None else set()), revise=True)
    type_text("\n\n")


def enter_cell(hwnd, text, last=False):
    """Type a value, drop any AutoInput suggestion with Delete, then advance (Tab/Enter).

    An empty value just skips the cell: Delete outside cell edit mode opens AOO's "Delete
    Contents" dialog, which then swallows the following keystrokes.
    """
    ensure_focus(hwnd)
    if text:
        # numbers and formulas may get typos (fixed before Tab); text cells stay clean, since
        # Backspace there would eat AutoInput's selected completion rather than our letter
        plain = not (text[0] == "=" or text[0].isdigit())
        type_text(text, typo_rate=0 if plain else None, exact=True)
        tap(VK_DELETE)
    tap(VK_RETURN if last else VK_TAB)
    nap(timing.think_s(0.35))


def enter_cells(hwnd, *values):
    """Type a run of cells along a row; Enter after the last returns to the row's first column."""
    for i, v in enumerate(values):
        enter_cell(hwnd, v, last=(i == len(values) - 1))


def enter_row(session, hwnd, row, values, formula, fill=False):
    """Type one data row (`row`: 1-based) of text/number `values` plus a trailing `formula`,
    after clicking its first cell half the time. `fill`: the formula will be pasted down
    later, so this row stops at its last value."""
    if session.calc_grid and session.rng.random() < 0.5:
        goto_cell(session, hwnd, 0, row - 1)
    if fill:
        enter_cells(hwnd, *values)
    else:
        enter_cells(hwnd, *values, formula)



def new_writer(session):
    hwnd = session.new_doc("writer")
    click_in(hwnd, session.rng.uniform(0.35, 0.6), session.rng.uniform(0.35, 0.5))
    nap(timing.perceive_s())
    return hwnd


def new_sheet(session):
    hwnd = session.new_doc("calc")
    click_in(hwnd, 0.12, 0.25)                          # somewhere in the sheet (B3 at 1280x800)
    nap(timing.perceive_s())
    ensure_focus(hwnd)
    hotkey(VK_CONTROL, VK_HOME)
    calibrate(session, hwnd)
    return hwnd


def calibrate(session, hwnd):
    """With A1 selected, find the sheet on screen (one full grab). Cached on the session:
    every Calc window is maximised, so the geometry holds all day. Without a grid
    (`session.calc_grid` stays None) cells are reached with the keyboard."""
    if session.calc_grid is None:
        rect = screen.window_rect(hwnd)
        grid = vision.calc_grid(*screen.grab_planes(rect))
        if grid:
            grid["left"], grid["top"], grid["rect"] = rect[0], rect[1], rect
            session.calc_grid = grid
            print(f"    Calc grid found: {grid['cw']}x{grid['rh']} px cells, "
                  f"{grid['ncol']}x{grid['nrow']} visible", flush=True)
        else:
            print("    Calc grid not found; cells by keyboard", flush=True)


def lit_headers(grid):
    """([column indices], [row indices]) whose headers are lit right now (two 1-px grabs).
    One cell selected: one of each; a range or a whole row: several."""
    l, t = grid["left"], grid["top"]
    right = l + grid["x0"] + grid["cw"] * grid["ncol"]
    _, _, _, line = screen.grab_planes((l, t + grid["hy"], right, t + grid["hy"] + 1))
    cols = vision.highlighted(line, grid["x0"], grid["cw"], grid["ncol"])
    bottom = t + grid["y0"] + grid["rh"] * grid["nrow"]
    _, _, _, side = screen.grab_planes((l + grid["sx"], t, l + grid["sx"] + 1, bottom))
    return cols, vision.highlighted(side, grid["y0"], grid["rh"], grid["nrow"])


def selected_cell(grid):
    """(col, row) whose column and row headers are lit right now, or None. Positions only,
    not labels: a scrolled view would read as the clicked screen cell, so
    `goto_cell(home=True)` re-anchors the view at A1 first wherever that isn't certain."""
    cols, rows = lit_headers(grid)
    return (cols[0], rows[0]) if len(cols) == 1 and len(rows) == 1 else None


def goto_cell(session, hwnd, col, row, home=False):
    """Put the Calc cursor on (col, row), 0-based (A1 = (0, 0)).

    With a known grid: point at the cell (somewhere inside, not its centre), click, and
    confirm from the lit headers. Otherwise, or if the click missed: Ctrl+Home + arrows.
    Never call mid-edit: clicking while a formula is being typed inserts a cell reference.
    `home`: Ctrl+Home first, so the view is known to start at A1 (a reopened document).
    """
    grid, rng = session.calc_grid, session.rng
    ensure_focus(hwnd)
    if grid and screen.window_rect(hwnd) != grid["rect"]:
        grid = None                                     # not where we measured it: keyboard
    if grid and home:
        hotkey(VK_CONTROL, VK_HOME)
        nap(timing.short_s(0.3))
    if grid and col < grid["ncol"] - 1 and row < grid["nrow"] - 1:
        x = grid["left"] + grid["x0"] + grid["cw"] * (col + rng.uniform(0.25, 0.7))
        y = grid["top"] + grid["y0"] + grid["rh"] * (row + rng.uniform(0.38, 0.62))   # rows are thin: aim mid-height
        move_to(int(x), int(y))
        nap(timing.think_s(0.25))
        click()
        nap(timing.short_s(0.25))
        if selected_cell(grid) == (col, row):
            return
        print(f"    click missed cell {col},{row}; using the keyboard", flush=True)
        ensure_focus(hwnd)
    hotkey(VK_CONTROL, VK_HOME)
    nap(timing.short_s(0.2))
    move_cursor(col, row)


def read_cell():
    """Copy the current Calc cell and return its displayed value (clipboard read-back)."""
    seq = screen.clipboard_seq()
    hotkey(VK_CONTROL, ord("C"))
    screen.wait_clipboard_change(seq)
    value = screen.clipboard_text().strip()
    nap(timing.short_s(0.4))
    tap(VK_ESCAPE)                                  # clear the copy marquee
    return value


# a number as Calc displays it: plain, or currency such as $1,260.00, S$1,260.00, -$5.00, ($5.00)
_SHOWN = re.compile(r"\(?-?(?:[A-Z]{0,3}\$|[A-Z]{3}\s)?-?(\d[\d,]*(?:\.\d+)?|\.\d+)\)?")


def parse_number(text):
    """The number a Calc cell shows (`text` as read back by Ctrl+C), or None if it isn't one
    - "Yes", "Err:509" and "#VALUE!" are not numbers, whatever digits they hold."""
    text = text.strip()
    m = _SHOWN.fullmatch(text)
    if not m:
        return None
    value = float(m.group(1).replace(",", ""))
    return -value if text.startswith("(") or "-" in text else value


def check_cell(session, hwnd, expected, retype, tol=0.01):
    """Read the current Calc cell back via the clipboard; if it isn't `expected`, retype the
    formula once (Enter moves down, so step back up) and re-check. Returns True if it matched."""
    for attempt in range(2):
        ensure_focus(hwnd)
        got = read_cell()
        value = parse_number(got)
        if value is not None and abs(value - expected) <= tol:
            return True
        print(f"    cell shows {got!r}, expected {expected}; "
              f"{'retyping' if attempt == 0 else 'leaving it'}", flush=True)
        if attempt == 0:
            nap(timing.think_s(1.5))             # "hang on, that's not right"
            type_text(retype, typo_rate=0, exact=True)
            tap(VK_RETURN)
            tap(VK_UP)
    return False


def paste_down(session, hwnd, col, src_row, first, last):
    """Copy the formula typed in (col, src_row) and paste it into the empty cells of the rows
    first..last below it (col 0-based, rows 1-based sheet rows); relative references adjust
    themselves, as with fill. Like a person: back to the cell, Ctrl+C, the first target cell,
    Shift+Down over the rest, Ctrl+V, then on to column A of the row below the data.

    VM facts: the pasted range stays marked, so Tab/Enter only move inside it - hence the
    final `goto_cell` (a click, or Ctrl+Home + arrows), never Tab/Enter. Ctrl+D is not Fill
    Down in AOO. Pasting onto non-empty cells raises an overwrite prompt: targets must be
    empty."""
    goto_cell(session, hwnd, col, src_row - 1)
    ensure_focus(hwnd)
    hotkey(VK_CONTROL, ord("C"))
    nap(timing.short_s(0.4))
    goto_cell(session, hwnd, col, first - 1)
    if last > first:
        ensure_focus(hwnd)
        hold_chord(VK_SHIFT, VK_DOWN, last - first)
        nap(timing.short_s(0.3))
    ensure_focus(hwnd)
    hotkey(VK_CONTROL, ord("V"))
    nap(timing.think_s(0.6))
    goto_cell(session, hwnd, 0, last)


def select_block(session, hwnd, col, row, ncols, nrows):
    """Select ncols x nrows cells from (col, row), 0-based: go to the first cell, then
    Shift+Down and Shift+Right (extended arrows; the range grows from the cursor cell)."""
    goto_cell(session, hwnd, col, row)
    ensure_focus(hwnd)
    if nrows > 1:
        hold_chord(VK_SHIFT, VK_DOWN, nrows - 1)
    if ncols > 1:
        hold_chord(VK_SHIFT, VK_RIGHT, ncols - 1)
    nap(timing.short_s(0.3))


def bold_header(session, hwnd, ncols):
    """Bold the header row. Half the time, when the grid is known, a click on row header 1
    (the whole row; the lit headers confirm it); otherwise A1 and Shift+Right over the
    `ncols` header cells. Then Ctrl+B."""
    grid, rng = session.calc_grid, session.rng
    whole = False
    if grid and screen.window_rect(hwnd) == grid["rect"] and rng.random() < 0.5:
        x = grid["left"] + grid["sx"] + rng.uniform(2, max(3, grid["x0"] - grid["sx"] - 5))
        y = grid["top"] + grid["y0"] + grid["rh"] * rng.uniform(0.3, 0.7)
        ensure_focus(hwnd)
        move_to(int(x), int(y))
        nap(timing.think_s(0.25))
        click()
        nap(timing.short_s(0.25))
        cols, rows = lit_headers(grid)
        whole = rows == [0] and len(cols) >= grid["ncol"] - 1
        if not whole:
            print("    row header click missed; selecting by keyboard", flush=True)
    if not whole:
        select_block(session, hwnd, 0, 0, ncols, 1)
    ensure_focus(hwnd)
    hotkey(VK_CONTROL, ord("B"))
    nap(timing.think_s(0.6))


def currency_format(session, hwnd, col, first, last):
    """Currency format (Ctrl+Shift+4) on the data cells of column `col` (0-based), sheet rows
    first..last. Cells then read back as "$1,260.00" (see `parse_number`)."""
    select_block(session, hwnd, col, first - 1, 1, last - first + 1)
    ensure_focus(hwnd)
    hotkey(VK_CONTROL, VK_SHIFT, ord("4"))
    nap(timing.think_s(0.6))


def sort_dialog(session, hwnd, letter, col, row):
    """Sort the block around cell (col, row) by the column whose header starts with `letter`:
    Alt+D, S opens the Sort dialog (waited for by its exact title), the "Sort key 1" list has
    focus so the letter picks the header, Enter, wait for the dialog to close. Needs a table
    with a header row and no neighbouring data. VM: Alt+D, S, letter, Enter worked. Returns
    False if the dialog never appeared (menu closed again) or would not close (cancelled)."""
    goto_cell(session, hwnd, col, row)
    ensure_focus(hwnd)
    hotkey(VK_MENU, ord("D"))
    nap(timing.short_s(0.5))
    tap(ord("S"))
    try:
        dlg = wait_for_window("Sort", timeout=5, exact=True)
    except TimeoutError:
        for _ in range(2):                              # close the menu; nothing may stay typed
            tap(VK_ESCAPE)
            nap(timing.short_s(0.3))
        return False
    ensure_focus(dlg)
    nap(timing.read_s(8))                               # look at the dialog
    tap(ord(letter.upper()))
    nap(timing.think_s(0.6))
    tap(VK_RETURN)
    try:
        wait_for_window("Sort", timeout=8, gone=True, exact=True)
    except TimeoutError:                                # still open: cancel it, don't block the run
        dlg = find_window("Sort", exact=True)
        if dlg:
            focus(dlg)
            tap(VK_ESCAPE)
            nap(timing.think_s(0.6))
        return False
    return True


POLISH = 0.6        # each finishing touch (bold header, currency, sort) is done this often


def polish(session, steps):
    """A person who has just typed a table often tidies it, in no fixed order and not always:
    run each of `steps` (callables) with probability POLISH, shuffled. True if any ran."""
    rng = session.rng
    chosen = [st for st in steps if rng.random() < POLISH]
    rng.shuffle(chosen)
    for st in chosen:
        st()
    return bool(chosen)


def move_cursor(dx=0, dy=0):
    """Arrow-key the Calc cursor by (dx, dy) cells with human gaps."""
    for vk, n in ((VK_RIGHT if dx > 0 else VK_LEFT, abs(dx)), (VK_DOWN if dy > 0 else VK_UP, abs(dy))):
        for _ in range(n):
            tap(vk)
            nap(timing.short_s(0.18))


def writer_task(session):
    """A short status update in Writer, grounded in what was done earlier in the day."""
    rng = session.rng
    hwnd = new_writer(session)

    ensure_focus(hwnd)
    type_text(f"Update - {time.strftime('%d %b %Y')}\n\n")
    nap(timing.think_s(1.2))
    seen = set()                                        # no sentence twice in one document
    for _ in range(rng.randint(1, 3)):
        sents = [fresh(lambda: sentence(rng), seen) for _ in range(rng.randint(2, 4))]
        grounded = fresh(lambda: world_sentence(session.world, rng), seen) \
            if rng.random() < 0.6 else None             # tie the memo to what was done today
        if grounded:
            sents.insert(rng.randint(1, len(sents)), grounded)
        type_paragraph(hwnd, sents, rng, seen)
        nap(timing.read_s(40))                   # re-read what was written

    session.save_doc_as(session.stamped("Update"))
    session.close_doc()


def calc_task(session):
    """A small equipment costing sheet, with the total read back and checked before saving."""
    rng = session.rng
    hwnd = new_sheet(session)

    rows = rng.sample(ITEMS, rng.randint(3, 6))
    enter_cells(hwnd, "Item", "Qty", "Unit price", "Amount")
    total = 0.0
    paste = rng.random() < 0.7        # type the amount formula once, paste it into the other rows
    for i, item in enumerate(rows):
        r = i + 2
        price = rng.choice(ITEM_PRICES[item])
        qty = rng.randint(1, 20 if price < 100 else 8)
        total += qty * price
        # amount as a formula, not a typed number
        enter_row(session, hwnd, r, (item, str(qty), f"{price:.2f}"), f"=B{r}*C{r}", fill=paste and i > 0)
        if rng.random() < 0.3:
            nap(timing.read_s(20))
    if paste:
        paste_down(session, hwnd, 3, 2, 3, len(rows) + 1)   # ends at A{total_row}, below the data
    total_row = len(rows) + 2
    if polish(session, [lambda: bold_header(session, hwnd, 4),
                        lambda: currency_format(session, hwnd, 2, 2, len(rows) + 1)]):
        goto_cell(session, hwnd, 0, total_row - 1)      # back to the first empty row
    sum_formula = f"=SUM(D2:D{total_row - 1})"
    enter_cells(hwnd, "Total", "", "", sum_formula)    # cursor now at A{total_row + 1}
    nap(timing.read_s(15))
    if session.calc_grid:                               # back up to the total in D{total_row}
        goto_cell(session, hwnd, 3, total_row - 1)
    else:
        move_cursor(dx=3, dy=-1)
    check_cell(session, hwnd, round(total, 2), sum_formula)
    nap(timing.read_s(10))

    session.save_doc_as(session.stamped("Equipment"))
    session.close_doc()


def impress_task(session):
    """Two-slide summary deck in Impress. OFF by default in agent.py until verified on the VM
    (run `python office_loop.py impress`): it relies on Impress's keyboard object navigation
    (Tab selects a placeholder, Enter edits it, Esc leaves) and Alt+I > Slide."""
    rng = session.rng
    w = session.world
    hwnd = session.new_doc("impress")                   # wizard (if shown) is accepted by dismiss_dialogs
    click_in(hwnd, 0.55, 0.88)                          # bottom margin of the slide: focus, no object
    nap(timing.perceive_s())

    def fill(text):
        ensure_focus(hwnd)
        tap(VK_TAB)                                     # select next placeholder
        nap(timing.short_s(0.3))
        tap(VK_RETURN)                                  # start editing it
        nap(timing.short_s(0.3))
        type_text(text)
        nap(timing.think_s(0.6))
        tap(VK_ESCAPE)                                  # leave edit mode (object stays selected)

    fill("Laptop shortlist" if w.findings else "Weekly update")
    fill(f"{w.name} - {time.strftime('%d %b %Y')}")
    nap(timing.read_s(10))
    ensure_focus(hwnd)
    tap(VK_ESCAPE)
    hotkey(VK_MENU, ord("I"))                           # Insert menu; "Slide" is the first item
    nap(timing.short_s(0.5))
    tap(VK_RETURN)
    nap(timing.read_s(8))
    fill("Key points")
    points = [world_sentence(w, rng) or sentence(rng) for _ in range(rng.randint(2, 3))]
    fill("\n".join(p.rstrip(".") for p in points))
    nap(timing.read_s(20))

    session.save_doc_as(session.stamped("Summary deck"))
    session.close_doc()


def _main(session):
    if "impress" in sys.argv:
        impress_task(session)
    else:
        writer_task(session)
        nap(timing.break_s(5, 15))
        calc_task(session)


if __name__ == "__main__":
    run_standalone(_main, "Done. Check Documents for the saved files.")
