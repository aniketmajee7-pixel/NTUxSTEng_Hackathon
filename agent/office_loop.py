"""Office tasks on OpenOffice Writer/Calc/Impress: write, fill, verify, save native, close.

Each task takes the Session so the agent keeps one OpenOffice alive across tasks.

Calc results are read back through the clipboard (Ctrl+C on the cell) and a wrong value is
retyped, the way a person double-checks a total before saving.

Run on the VM:  python office_loop.py [impress]   (then let go of the mouse)
Files are saved to Documents as native .odt/.ods/.odp (no format dialog to trip over).
"""
import sys
sys.dont_write_bytecode = True  # no __pycache__/*.pyc from python.exe (EID 11)

import time

import timing
from content import sentence, world_sentence
from human_input import (VK_BACK, VK_CONTROL, VK_DELETE, VK_DOWN, VK_ESCAPE, VK_HOME, VK_MENU,
                         VK_LEFT, VK_RETURN, VK_RIGHT, VK_TAB, VK_UP, click_in, ensure_focus, hold_key,
                         hotkey, tap, type_text)
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
    """Type a value, drop any AutoInput suggestion with Delete, then advance (Tab/Enter)."""
    ensure_focus(hwnd)
    type_text(text, typo_rate=0, exact=True)
    tap(VK_DELETE)
    tap(VK_RETURN if last else VK_TAB)
    time.sleep(timing.think_s(0.35))


def new_sheet(session):
    hwnd = session.new_doc("calc")
    click_in(hwnd, 0.12, 0.25)                          # roughly cell A1
    time.sleep(timing.perceive_s())
    ensure_focus(hwnd)
    hotkey(VK_CONTROL, VK_HOME)
    return hwnd


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
    move_cursor(dx=3, dy=-1)                            # back up to the total in D{total_row}
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
