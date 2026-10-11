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

import time

import screen
import timing
import vision
from content import sentence, world_sentence
from human_input import (VK_BACK, VK_CONTROL, VK_DELETE, VK_DOWN, VK_ESCAPE, VK_HOME, VK_MENU,
                         VK_LEFT, VK_RETURN, VK_RIGHT, VK_TAB, VK_UP, click, click_in,
                         ensure_focus, hold_key, hotkey, move_to, tap, type_text)
from session import run_standalone, stamped

ITEMS = ["Laptop", "Monitor", "Keyboard", "Docking station", "Headset", "Webcam", "Projector",
         "USB hub", "Mouse", "Standing desk", "Office chair", "Label printer"]
# retail-style price endings (.99/.95/.00) so magnitudes look human, not uniform-random
PRICES = [199.00, 249.99, 89.95, 1299.00, 349.00, 59.99, 899.00, 45.95, 129.99, 749.00, 39.95, 219.00]


def type_paragraph(hwnd, sentences, rng):
    """Type a paragraph, sometimes rewriting its last sentence before moving on.

    Monotonic left-to-right typing with only typo fixes reads as transcribed text; a person
    drafting will often hold Backspace over a sentence they don't like and try again.
    """
    ensure_focus(hwnd)
    type_text(" ".join(sentences))
    if len(sentences) > 1 and rng.random() < 0.45:
        time.sleep(timing.read_s(len(sentences[-1].split())))   # re-read it, decide to redo
        ensure_focus(hwnd)
        hold_key(VK_BACK, len(sentences[-1]))           # leaves the space before it
        time.sleep(timing.think_s(1.5))
        type_text(sentence(rng))
    type_text("\n\n")


def enter_cell(hwnd, text, last=False):
    """Type a value, drop any AutoInput suggestion with Delete, then advance (Tab/Enter).

    An empty value just skips the cell: Delete outside cell edit mode opens AOO's "Delete
    Contents" dialog, which then swallows the following keystrokes.
    """
    ensure_focus(hwnd)
    if text:
        type_text(text, typo_rate=0, exact=True)
        tap(VK_DELETE)
    tap(VK_RETURN if last else VK_TAB)
    time.sleep(timing.think_s(0.35))


def new_sheet(session):
    hwnd = session.new_doc("calc")
    click_in(hwnd, 0.12, 0.25)                          # somewhere in the sheet (B3 at 1280x800)
    time.sleep(timing.perceive_s())
    ensure_focus(hwnd)
    hotkey(VK_CONTROL, VK_HOME)
    calibrate(session, hwnd)
    return hwnd


def calibrate(session, hwnd):
    """With A1 selected, find the sheet on screen (one full grab). Cached on the session:
    every Calc window is maximised, so the geometry holds all day. Returns the grid or None,
    in which case cells are reached with the keyboard."""
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
    return session.calc_grid


def selected_cell(grid):
    """(col, row) whose column and row headers are lit right now, or None (two 1-px grabs).
    Positions only, not labels: a scrolled view would read as the clicked screen cell, so
    `goto_cell(home=True)` re-anchors the view at A1 first wherever that isn't certain."""
    l, t = grid["left"], grid["top"]
    right = l + grid["x0"] + grid["cw"] * grid["ncol"]
    _, _, _, line = screen.grab_planes((l, t + grid["hy"], right, t + grid["hy"] + 1))
    cols = vision.highlighted(line, grid["x0"], grid["cw"], grid["ncol"])
    bottom = t + grid["y0"] + grid["rh"] * grid["nrow"]
    _, _, _, side = screen.grab_planes((l + grid["sx"], t, l + grid["sx"] + 1, bottom))
    rows = vision.highlighted(side, grid["y0"], grid["rh"], grid["nrow"])
    return (cols[0], rows[0]) if len(cols) == 1 and len(rows) == 1 else None


def goto_cell(session, hwnd, col, row, home=False):
    """Put the Calc cursor on (col, row), 0-based (A1 = (0, 0)).

    With a known grid: point at the cell (somewhere inside, not its centre), click, and
    confirm from the lit headers. Otherwise, or if the click missed: Ctrl+Home + arrows.
    Never call mid-edit: clicking while a formula is being typed inserts a cell reference.
    `home`: Ctrl+Home first, so the view is known to start at A1 (a reopened document).
    Returns True if the mouse got there.
    """
    grid, rng = session.calc_grid, session.rng
    ensure_focus(hwnd)
    if grid and screen.window_rect(hwnd) != grid["rect"]:
        grid = None                                     # not where we measured it: keyboard
    if grid and home:
        hotkey(VK_CONTROL, VK_HOME)
        time.sleep(timing.short_s(0.3))
    if grid and col < grid["ncol"] - 1 and row < grid["nrow"] - 1:
        x = grid["left"] + grid["x0"] + grid["cw"] * (col + rng.uniform(0.25, 0.7))
        y = grid["top"] + grid["y0"] + grid["rh"] * (row + rng.uniform(0.3, 0.7))
        move_to(int(x), int(y))
        time.sleep(timing.think_s(0.25))
        click()
        time.sleep(timing.short_s(0.25))
        if selected_cell(grid) == (col, row):
            return True
        print(f"    click missed cell {col},{row}; using the keyboard", flush=True)
        ensure_focus(hwnd)
    hotkey(VK_CONTROL, VK_HOME)
    time.sleep(timing.short_s(0.2))
    move_cursor(col, row)
    return False


def check_cell(session, hwnd, expected, retype, tol=0.01):
    """Read the current Calc cell back via the clipboard; if it isn't `expected`, retype the
    formula once (Enter moves down, so step back up) and re-check. Returns True if it matched."""
    for attempt in range(2):
        ensure_focus(hwnd)
        got = session.read_cell()
        try:
            if abs(float(got.replace(",", "")) - expected) <= tol:
                return True
        except ValueError:
            pass
        print(f"    cell shows {got!r}, expected {expected}; "
              f"{'retyping' if attempt == 0 else 'leaving it'}", flush=True)
        if attempt == 0:
            time.sleep(timing.think_s(1.5))             # "hang on, that's not right"
            type_text(retype, typo_rate=0, exact=True)
            tap(VK_RETURN)
            tap(VK_UP)
    return False


def move_cursor(dx=0, dy=0):
    """Arrow-key the Calc cursor by (dx, dy) cells with human gaps."""
    for vk, n in ((VK_RIGHT if dx > 0 else VK_LEFT, abs(dx)), (VK_DOWN if dy > 0 else VK_UP, abs(dy))):
        for _ in range(n):
            tap(vk)
            time.sleep(timing.short_s(0.18))


def writer_task(session):
    """A short status update in Writer, grounded in what was done earlier in the day."""
    rng = session.rng
    hwnd = session.new_doc("writer")
    click_in(hwnd, rng.uniform(0.35, 0.6), rng.uniform(0.35, 0.5))
    time.sleep(timing.perceive_s())

    ensure_focus(hwnd)
    type_text(f"Update - {time.strftime('%d %b %Y')}\n\n")
    time.sleep(timing.think_s(1.2))
    for _ in range(rng.randint(1, 3)):
        sents = [sentence(rng) for _ in range(rng.randint(2, 4))]
        grounded = world_sentence(session.world, rng)
        if grounded and rng.random() < 0.6:             # tie the memo to what was done today
            sents.insert(rng.randint(1, len(sents)), grounded)
        type_paragraph(hwnd, sents, rng)
        time.sleep(timing.read_s(40))                   # re-read what was written

    session.save_doc_as(stamped("Update"))
    session.close_doc()


def calc_task(session):
    """A small equipment costing sheet, with the total read back and checked before saving."""
    rng = session.rng
    hwnd = new_sheet(session)

    rows = rng.sample(ITEMS, rng.randint(3, 6))
    for col, header in enumerate(["Item", "Qty", "Unit price", "Amount"]):
        enter_cell(hwnd, header, last=(col == 3))
    total = 0.0
    for i, item in enumerate(rows):
        r = i + 2
        qty = rng.randint(1, 20)
        price = rng.choice(PRICES)
        total += qty * price
        if session.calc_grid and rng.random() < 0.5:   # click the row's first cell
            goto_cell(session, hwnd, 0, r - 1)
        enter_cell(hwnd, item)
        enter_cell(hwnd, str(qty))
        enter_cell(hwnd, f"{price:.2f}")
        enter_cell(hwnd, f"=B{r}*C{r}", last=True)     # amount as a formula, not a typed number
        if rng.random() < 0.3:
            time.sleep(timing.read_s(20))
    total_row = len(rows) + 2
    sum_formula = f"=SUM(D2:D{total_row - 1})"
    enter_cell(hwnd, "Total")
    enter_cell(hwnd, "")
    enter_cell(hwnd, "")
    enter_cell(hwnd, sum_formula, last=True)           # cursor now at A{total_row + 1}
    time.sleep(timing.read_s(15))
    if session.calc_grid:                               # back up to the total in D{total_row}
        goto_cell(session, hwnd, 3, total_row - 1)
    else:
        move_cursor(dx=3, dy=-1)
    check_cell(session, hwnd, round(total, 2), sum_formula)
    time.sleep(timing.read_s(10))

    session.save_doc_as(stamped("Equipment"))
    session.close_doc()


def impress_task(session):
    """Two-slide summary deck in Impress. OFF by default in agent.py until verified on the VM
    (run `python office_loop.py impress`): it relies on Impress's keyboard object navigation
    (Tab selects a placeholder, Enter edits it, Esc leaves) and Alt+I > Slide."""
    rng = session.rng
    w = session.world
    hwnd = session.new_doc("impress")                   # wizard (if shown) is accepted by dismiss_dialogs
    click_in(hwnd, 0.55, 0.88)                          # bottom margin of the slide: focus, no object
    time.sleep(timing.perceive_s())

    def fill(text):
        ensure_focus(hwnd)
        tap(VK_TAB)                                     # select next placeholder
        time.sleep(timing.short_s(0.3))
        tap(VK_RETURN)                                  # start editing it
        time.sleep(timing.short_s(0.3))
        type_text(text)
        time.sleep(timing.think_s(0.6))
        tap(VK_ESCAPE)                                  # leave edit mode (object stays selected)

    fill("Laptop shortlist" if w.findings else "Weekly update")
    fill(f"{w.name} - {time.strftime('%d %b %Y')}")
    time.sleep(timing.read_s(10))
    ensure_focus(hwnd)
    tap(VK_ESCAPE)
    hotkey(VK_MENU, ord("I"))                           # Insert menu; "Slide" is the first item
    time.sleep(timing.short_s(0.5))
    tap(VK_RETURN)
    time.sleep(timing.read_s(8))
    fill("Key points")
    points = [world_sentence(w, rng) or sentence(rng) for _ in range(rng.randint(2, 3))]
    fill("\n".join(p.rstrip(".") for p in points))
    time.sleep(timing.read_s(20))

    session.save_doc_as(stamped("Summary deck"))
    session.close_doc()


def _main(session):
    if "impress" in sys.argv:
        impress_task(session)
    else:
        writer_task(session)
        time.sleep(timing.break_s(5, 15))
        calc_task(session)


if __name__ == "__main__":
    run_standalone(_main, "Done. Check Documents for the saved files.")
