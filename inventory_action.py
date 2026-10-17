from pathlib import Path
import random
import re
import time

import pyautogui as pg
import win32gui

from domain import (HEADERS, INITIAL, FINAL, Phase, validate_inventory, inventory_range,
                    heavy_pause, find_grid, address_of)
from save_dialog import browse_to, confirm_replace, elsewhere, file_name_field, folder_shown, free_name

MONTHS = ('jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec')
EXTRA = ('INV-007', 'Laptop Stand', 'Accessories', 7, 32, 3)
# The finished records in the order they are entered (by SKU); FINAL is the same set by Item.
ENTRY = sorted(FINAL, key=lambda record: record[0])
# Rough seconds a step takes, to decide whether an optional step still fits before the cap.
COST = {'format': 12, 'sort': 25, 'summary': 115}
# Seconds each part of the essential work took in the VM pilot (10:05 PM recording).  At a
# checkpoint the run's own speed so far projects when the workbook will be done.
SCHEDULE = (('launch', 10), ('headers', 40), ('first_save', 22), ('row_2', 50),
            ('rows', 118), ('formulas', 20), ('extra_row', 34), ('check', 10),
            ('summary', 110))
AGENT_FOLDER = Path(__file__).resolve().parent     # where main.py is: Desktop\agent


class InventoryAction:
    def __init__(self, gui, config, budget):
        self.gui, self.config, self.budget = gui, config, budget
        # The workbook goes into the agent folder (Desktop\agent).
        self.folder = Path(config.get('save_folder') or AGENT_FOLDER)
        self.path = self.folder/self.pick_name()
        self.window = None
        self.saved_once = False
        self.categories = {}  # category -> cell that already holds it
        self.pending = {}     # cell -> value to put back if a what-if edit is interrupted
        self.cells_exposed = None  # None = not checked yet; False = Calc exposes no cells
        self.cell_checks = 0       # early lookups that found nothing (the window may still be loading)
        self.name_box = None       # cached Name Box control, when Calc exposes one
        self.grid = None           # sheet geometry read from the screen (domain.Grid)
        self.sku_pasted = False    # column A already holds copies of the first serial number
        self.ranges_work = False   # a multi-cell selection was made and confirmed in this run
        self.full_rows = set()     # rows typed in full, formulas included
        self.order = list(ENTRY)   # records in the order they sit in the sheet
        self.sorted = False
        self.phase = None
        self.step = 'start'        # where the run is, for the report when time runs out
        self.sheet_click_needed = True   # keyboard route: focus may not be in the sheet
        self.unsaved = False       # something changed in the sheet since the last save
        self.clipboard_category = None   # the category the clipboard still holds, if any
        self.hurry = False         # behind schedule: shorter pauses, no optional steps
        self.rate = None           # measured seconds per planned second (see checkpoint)

    def pick_name(self):
        """A file name a person would type, e.g. inventory_oct10.ods; numbered if taken."""
        now = time.localtime()
        return free_name((self.folder, self.folder.parent),
                         f'inventory_{MONTHS[now.tm_mon-1]}{now.tm_mday:02d}', '.ods')

    # --- Time --------------------------------------------------------------------------

    def fits(self, cost, after=0):
        """Whether an optional step of `cost` seconds still fits before the cap, leaving
        `after` seconds for the essential work that follows it.  Never when behind."""
        if self.phase is None:
            return True
        if self.hurry:
            return False
        reserve = self.config.get('calc_wrap_up_seconds', 20)
        pace = self.gui.pace if isinstance(self.gui.pace, (int, float)) else 1.0
        # Everything takes longer for a slower run, and much longer without the mouse.
        # Once a checkpoint has measured this run's real speed, that speed is used.
        scale = self.rate if self.rate is not None else pace*(1.0 if self.grid is not None else 1.6)
        return self.phase.remaining-reserve-10 >= (cost+after)*scale     # 10 s to spare

    def think(self):
        """A glance at the source list between records (skipped on the slower keyboard
        route and when behind schedule, where the time is needed for the work itself)."""
        g = self.gui
        if self.grid is not None and not self.hurry:
            g.sleep(heavy_pause(1.8*g.pace, 0.5, 0.8, 6.0))

    def checkpoint(self, done):
        """After an essential part: project the finish from this run's own speed so far.
        While the workbook would not be complete before the cap, hurry: shorter pauses
        (pace down to 0.75) and no optional steps.  Once back on time the optional steps
        may run again (the faster pace stays).  The Full demo in the 11:04 PM recording
        ran about a minute behind the pilot and its totals did not fit."""
        started, deadline = (getattr(self.phase, 'started', None),
                             getattr(self.phase, 'deadline', None))
        if not all(isinstance(t, (int, float)) for t in (started, deadline)):
            return
        g = self.gui
        names = [name for name, _ in SCHEDULE]
        index = names.index(done)+1
        planned_done = sum(cost for _, cost in SCHEDULE[:index])
        planned_left = sum(cost for _, cost in SCHEDULE[index:])
        spent = time.monotonic()-started
        rate = self.rate = max(0.6, spent/planned_done)
        projected = spent+rate*planned_left
        limit = deadline-started-self.config.get('calc_wrap_up_seconds', 20)
        g.event('calc_schedule', after=done, spent=round(spent, 1), rate=round(rate, 2),
                projected_finish=round(projected, 1), limit=round(limit, 1))
        late = projected > limit-10                    # less than ten seconds to spare
        if late:
            pace = g.pace if isinstance(g.pace, (int, float)) else 1.0
            faster = max(0.75, pace*0.9)
            if faster < pace or not self.hurry:
                g.pace = faster
                g.event('calc_hurry', after=done, projected_finish=round(projected, 1),
                        limit=round(limit, 1), pace=round(faster, 3))
        elif self.hurry:
            g.event('calc_back_on_time', after=done, projected_finish=round(projected, 1))
        self.hurry = late

    # --- Finding and selecting cells ----------------------------------------------------

    def calibrate_grid(self):
        """Read where the columns and rows are from the screen (see domain.find_grid).

        Presses Ctrl+Home so that A1 is the selected cell.  Without a grid the keyboard
        route is used, so a failure here costs speed, not correctness."""
        from gui_support import Stopped
        g = self.gui
        self.grid, reason = None, 'screen could not be read'
        try:
            g.hotkey('ctrl', 'home', window=self.window)
            rect = self.window.rectangle()
            screen_width, screen_height = pg.size()
            left, top = max(0, rect.left), max(0, rect.top)
            right, bottom = min(screen_width, rect.right)-1, min(screen_height, rect.bottom)-1
            for look in range(2):                 # the sheet may still be painting
                pixel = g.grab(left, top, right-left+1, bottom-top+1)
                if pixel is None:
                    break
                grid = find_grid(pixel, left, top, right, bottom)
                if grid is None:
                    reason = 'no sheet with A1 selected was recognised'
                elif len(grid.cols) < 10 or len(grid.rows) < 16:
                    reason = f'only {len(grid.cols)} columns and {len(grid.rows)} rows are visible'
                else:
                    self.grid = grid
                    break
                if not look:
                    g.sleep(1.0)
        except (Stopped, pg.FailSafeException):
            raise
        except Exception as exc:
            reason = str(exc)[:120]
        if self.grid is None:
            g.event('grid_calibration_failed', reason=reason)
            return False
        (left, right), (top, bottom) = self.grid.cols[0], self.grid.rows[0]
        g.event('grid_calibrated', columns=len(self.grid.cols), rows=len(self.grid.rows),
                first_cell=[left, top, right, bottom])
        return True

    def read_selection(self):
        """The selected range, read from the highlighted headers (two tiny screen reads)."""
        grid, g = self.grid, self.gui
        first, last = grid.cols[0][0], grid.cols[-1][1]
        top, bottom = grid.rows[0][0], grid.rows[-1][1]
        header = g.grab(first, grid.header_y, last-first+1, 1)
        side = g.grab(grid.side_x, top, 1, bottom-top+1)
        if header is None or side is None:
            return None
        return grid.selection(header, side)

    def grid_select(self, address):
        """Select a cell or range with the mouse and confirm it from the highlighted
        headers.  Returns False when this cannot be done; the clipboard is never touched."""
        if self.grid is None:
            return False
        from gui_support import Stopped
        g = self.gui
        x1, y1, x2, y2 = inventory_range(address)
        if x2 >= len(self.grid.cols) or y2 >= len(self.grid.rows):
            return False
        wanted = (x1, x2, y1+1, y2+1)
        dragged = []
        for attempt in range(2):
            got = None
            try:
                start = self.grid.point(x1, y1+1)
                if (x1, y1) == (x2, y2):
                    g.click_at(*start, window=self.window, quick=True)
                else:
                    end = self.grid.point(x2, y2+1)
                    # Second try: the other way of selecting a range.
                    drag = (not dragged[0] if dragged else
                            random.random() < self.config.get('calc_drag_select_probability', 0.6))
                    dragged.append(drag)
                    current = self.read_selection()
                    single = (current is not None and current[0] == current[1]
                              and current[2] == current[3])
                    if not drag or not single:
                        # Pressing inside an already selected block would drag the block
                        # itself, so a plain click on the first cell comes first.
                        g.click_at(*start, window=self.window, quick=True)
                    if drag:
                        g.drag(*start, *end, window=self.window)
                    else:
                        pg.keyDown('shift')               # shift+click extends the selection
                        try:
                            g.click_at(*end, window=self.window, quick=True)
                        finally:
                            pg.keyUp('shift')
                got = self.read_selection()
            except (Stopped, pg.FailSafeException):
                raise
            except Exception as exc:
                g.event('pointer_selection_failed', range=address, error=str(exc)[:120])
                return False
            if got == wanted:
                if len(dragged) == 2:
                    # One way works here and the other does not: stay with the one that works.
                    self.config['calc_drag_select_probability'] = 1.0 if dragged[1] else 0.0
                    g.event('range_selection_method_fixed', drag=dragged[1])
                g.event('cell_selected', range=address, method='pointer')
                return True
            g.event('pointer_selection_mismatch', wanted=address,
                    got=address_of(got) if got else None)
            if attempt or not self.calibrate_grid():      # the view may have moved: look again
                break
        return False

    def cell_element(self, name):
        """The on-screen element for one cell, when Calc exposes its cells to UI Automation.

        Looked up by exact name with a single query; never guessed from coordinates,
        so a click can only land on the cell that was asked for."""
        if self.cells_exposed is False:
            return None
        from gui_support import Stopped
        window_rect = self.window.rectangle()
        for title in (name, f'Cell {name}'):
            try:
                found = self.window.descendants(title=title)
            except (Stopped, pg.FailSafeException):
                raise
            except Exception:
                continue
            if not isinstance(found, (list, tuple)):
                continue
            for element in found:
                try:
                    if element.element_info.control_type in ('Edit', 'ComboBox', 'Button', 'MenuItem'):
                        continue                      # the Name Box or a toolbar control, not a cell
                    rect = element.rectangle()
                    inside = (window_rect.left <= rect.left and rect.right <= window_rect.right
                              and window_rect.top <= rect.top and rect.bottom <= window_rect.bottom)
                    if (element.is_visible() and inside
                            and 15 <= rect.width() <= 700 and 8 <= rect.height() <= 80):
                        self.cells_exposed = True
                        return element
                except (Stopped, pg.FailSafeException):
                    raise
                except Exception:
                    continue
        if self.cells_exposed is None:
            # Right after launch the accessibility tree may not be ready, so allow a few
            # empty answers before concluding that Calc exposes no cells at all.
            self.cell_checks += 1
            if self.cell_checks >= 3:
                self.cells_exposed = False
                self.gui.event('pointer_selection_unavailable',
                               reason='Calc does not expose cells to UI Automation')
        return None

    def pointer_select(self, address):
        """Select a cell (or range) by clicking its UI Automation element.  Returns False
        when Calc exposes no such elements (OpenOffice 4.1 does not)."""
        from gui_support import Stopped
        g = self.gui
        first, _, last = address.partition(':')
        try:
            start = self.cell_element(first)
            end = self.cell_element(last) if last else None
            if start is None or (last and end is None):
                return False
            g.click(start, window=self.window)
            if end is not None:
                pg.keyDown('shift')               # shift+click extends the selection
                try:
                    g.click(end, window=self.window)
                finally:
                    pg.keyUp('shift')
        except (Stopped, pg.FailSafeException):
            raise
        except Exception as exc:
            g.event('pointer_selection_failed', range=address, error=str(exc)[:120])
            return False
        g.event('cell_selected', range=address, method='pointer')
        return True

    def find_name_box(self):
        """The Name Box control, looked up once (a full tree search on every selection
        was wasted work)."""
        if self.name_box is not None:
            try:
                if self.name_box.is_visible():
                    return self.name_box
            except Exception:
                pass
            self.name_box = None
        box = self.gui.find(self.window, ['Name Box', 'Name box', 'Sheet Area', 'Name'])
        # "Sheet Area" is not a name box unless it is an Edit/ComboBox.
        if box and box.element_info.control_type in ('Edit', 'ComboBox'):
            self.name_box = box
        return self.name_box

    def keyboard_select(self, address):
        """Reach a cell with Ctrl+Home and the arrow keys.  Touches neither the Name Box
        nor the clipboard."""
        g = self.gui
        x1, y1, x2, y2 = inventory_range(address)
        if self.sheet_click_needed:
            # Put the focus in the sheet (after launch, a dialog or a failed step).  Doing
            # this before every move made the Name Box flash "H20" all through the recording.
            rect = self.window.rectangle()
            g.move(rect.left+rect.width()*0.5, rect.top+rect.height()*0.65)
            g.click_here()
            self.sheet_click_needed = False
        g.press('esc')
        g.hotkey('ctrl', 'home', window=self.window)
        for key, count in (('down', y1), ('right', x1)):
            for _ in range(count):
                g.press(key)
        try:
            pg.keyDown('shift')
            for key, count in (('down', y2-y1), ('right', x2-x1)):
                for _ in range(count):
                    g.press(key)
        finally:
            pg.keyUp('shift')
        g.sleep(random.uniform(0.15, 0.4)); g.event('keyboard_range_selected', range=address)

    def select(self, address, keep_clipboard=False):
        """Select a cell or range: with the mouse when the grid is known, else through
        the Name Box when Calc exposes one, else with the keyboard.

        `keep_clipboard=True` is for the step between Copy and Paste: the Name Box route
        checks itself by copying the typed address, which would replace the copied cell
        and make the paste insert the address text instead."""
        g = self.gui
        g.focus(self.window)
        if self.config.get('calc_pointer_selection', True) and (
                self.grid_select(address) or self.pointer_select(address)):
            return
        if keep_clipboard:
            self.keyboard_select(address)
            return
        box = self.find_name_box()
        if not box:
            self.keyboard_select(address)
            return
        g.click(box)
        g.hotkey('ctrl', 'a')
        g.type_text(address, self.window)
        self.clipboard_category = None
        g.clear_clipboard(); g.hotkey('ctrl', 'c')
        if g.read_clipboard().strip().upper() != address.upper():
            self.gui.press('esc')
            raise RuntimeError('Cannot verify the Name Box; stopping to avoid editing the wrong cell')
        self.gui.press('enter'); g.pause()

    # --- Typing ---------------------------------------------------------------------------

    def cell(self, address, value, typo=False):
        # The write is idempotent: refocusing/reselecting once cannot add a duplicate row.
        from gui_support import Stopped
        for attempt in range(2):
            try:
                self.select(address)
                # Calc's AutoInput proposes existing entries as you type.  Delete removes
                # a proposal before a correction or Enter, so it can never be accepted.
                self.unsaved = True
                self.gui.type_text(value, self.window, corrected_typo=typo,
                                   clear_inline_completion=True)
                self.gui.press('enter'); self.gui.pause()
                self.gui.event('cell_edited', cell=address, value=str(value))
                return
            except (Stopped, pg.FailSafeException):
                raise
            except Exception:
                if attempt:
                    raise
                self.gui.event('cell_retry', cell=address)
                self.gui.focus(self.window); self.gui.press('esc')

    def column(self, start, values, typo_chance=0.0):
        """Type values down a column: click the first cell, then Enter moves down."""
        g = self.gui
        self.select(start)
        self.unsaved = True
        for value in values:
            g.type_text(value, self.window, corrected_typo=random.random() < typo_chance,
                        clear_inline_completion=True)
            g.press('enter'); g.pause()

    def row(self, row_number, record):
        from gui_support import Stopped
        for attempt in range(2):
            try:
                return self._row(row_number, record)
            except (Stopped, pg.FailSafeException):
                raise
            except Exception:
                if attempt:
                    raise
                self.gui.event('row_retry', row=row_number)
                self.gui.focus(self.window); self.gui.press('esc')

    def type_value(self, value, index):
        """Type one cell value. Typed text and plain numbers occasionally get a slip that
        is fixed before Tab/Enter; formulas (indices 5 and 7) never do."""
        chance = {0: 0.04, 1: 0.3, 2: 0.15, 3: 0.1, 4: 0.1, 6: 0.08}.get(index, 0)
        chance *= self.config.get('spreadsheet_typo_scale', 1.0)
        self.unsaved = True
        self.gui.type_text(value, self.window, corrected_typo=random.random() < chance,
                           clear_inline_completion=True,   # drop any AutoInput proposal
                           typo_kind=random.choices(
                               ('wrong', 'double', 'swap'),
                               weights=self.config.get('typo_kind_weights', (1, 0, 0)))[0])

    def advance(self, next_address, tabs=1):
        """Move on after typing a cell: usually Tab, sometimes a click on the next cell
        (clicking another cell also commits what was typed)."""
        g = self.gui
        if (self.grid is not None
                and random.random() < self.config.get('calc_click_next_cell_probability', 0.3)):
            self.select(next_address)
            return
        for _ in range(tabs):
            g.press('tab')
        g.pause()

    def leave_pasted_cell(self, next_address):
        """Go to the next cell after a paste.

        After Ctrl+V the pasted cells stay marked, and with a marked range Tab and Enter
        only move inside it.  For a single pasted cell that means "stay": in the VM the
        quantity and the price typed after a pasted category both landed on the category.
        A click on another cell, or an arrow key, leaves the marked range."""
        g = self.gui
        if self.grid is not None:
            self.select(next_address)
        else:
            g.press('right'); g.pause()

    def _row(self, row_number, record):
        """Type every cell of a record (used for the first row and as the fallback)."""
        sku, item, category, qty, price, reorder = record
        values = [sku, item, category, qty, price, f'=D{row_number}*E{row_number}',
                  reorder, f'=IF(D{row_number}<G{row_number};"YES";"NO")']
        self.select(f'A{row_number}')
        for index, value in enumerate(values):
            self.type_value(value, index)
            self.gui.press('tab' if index < 7 else 'enter'); self.gui.pause()
        self.categories.setdefault(category, f'C{row_number}')
        self.full_rows.add(row_number)
        self.gui.event('inventory_row_added', row=row_number, sku=sku, method='typed')

    # --- Reuse existing cells instead of retyping repeated content -------------------

    def copy_cell(self, address):
        self.select(address)
        self.clipboard_category = None
        self.gui.hotkey('ctrl', 'c', window=self.window)

    def paste_into(self, address):
        g = self.gui
        self.select(address, keep_clipboard=True)   # the copied cell must survive the move
        self.unsaved = True
        g.hotkey('ctrl', 'v', window=self.window)
        # Pasting onto filled cells makes Calc ask whether to overwrite.  The flow avoids
        # that, but a question left open would swallow everything typed after it.
        if g.window_handles(title_pattern=r'^OpenOffice( Calc| \d).*'):
            g.press('y'); g.pause()
            g.event('overwrite_confirmed', range=address)

    def fill_sku(self, first_row, last_row):
        """Copy the first serial number down the column; each row then only needs its
        last digit changed.  One look at the column confirms the paste."""
        self.copy_cell('A2')
        self.paste_into(f'A{first_row}:A{last_row}')
        values = self.range_values(f'A2:A{last_row}')
        # This is also the proof that a range can be selected, copied and pasted at all.
        # In the recording it could not, and every later "fill" silently hit one cell.
        self.sku_pasted = self.ranges_work = values == ['INV-001']*(last_row-1)
        self.gui.event('column_filled', column='A', rows=[first_row, last_row], source='A2',
                       confirmed=self.sku_pasted)

    @staticmethod
    def runs(rows):
        """Consecutive rows grouped: [3, 4, 6] -> [(3, 4), (6, 6)]."""
        groups = []
        for row in sorted(rows):
            if groups and row == groups[-1][1]+1:
                groups[-1] = (groups[-1][0], row)
            else:
                groups.append((row, row))
        return groups

    def paste_down(self, column, source_row, rows):
        """Copy one cell of a column and paste it into other rows of the same column."""
        self.copy_cell(f'{column}{source_row}')
        for first, last in self.runs(rows):
            target = f'{column}{first}'
            self.paste_into(target if first == last else f'{target}:{column}{last}')

    def fill_formulas(self, first_row, last_row):
        """Bring both formulas down from the row above by copy and paste; the relative
        references adjust.  verify_table() checks the result.

        Ctrl+D is not used: in OpenOffice it opens the Selection List (it is Fill Down
        only in LibreOffice).  In the VM a list dropped open and nothing was filled."""
        # A row that was typed in full already has its formulas; pasting onto it would
        # raise the overwrite question, so only the other rows are pasted into.
        rows = [row for row in range(first_row, last_row+1) if row not in self.full_rows]
        if not rows:
            return
        for column in ('F', 'H'):
            self.paste_down(column, first_row-1, rows)
            self.gui.event('formulas_filled', column=column, rows=[rows[0], rows[-1]], method='paste')

    def row_reuse(self, row_number, record):
        """Enter a record after row 2 exists.  The row starts with a click on its first
        cell; the serial number is the pasted one with its last digit changed, a category
        that already exists is copied, and the formula columns are filled afterwards."""
        g = self.gui
        sku, item, category, qty, price, reorder = record
        r = row_number
        self.select(f'A{r}')
        self.unsaved = True
        if self.sku_pasted:
            g.hotkey('f2', window=self.window)          # edit in place, cursor at the end
            g.press('backspace')
            g.type_text(sku[-1], self.window)
            method = 'paste_then_edit_last_digit'
        else:
            self.type_value(sku, 0)
            method = 'typed'
        g.press('tab'); g.pause()
        g.event('sku_edited', cell=f'A{r}', value=sku, method=method)
        self.type_value(item, 1)
        g.press('tab'); g.pause()                       # commits the item before anything else
        source = self.categories.get(category)
        if source:
            # Same category: paste, don't retype.  It is copied from the nearest cell that
            # has it, and not copied again while the clipboard still holds it.
            copied = self.clipboard_category != category
            if copied:
                self.copy_cell(source)
                self.clipboard_category = category
            self.paste_into(f'C{r}')
            self.categories[category] = f'C{r}'
            g.event('category_pasted', row=r, category=category, source=source, copied=copied)
            self.leave_pasted_cell(f'D{r}')
        else:
            self.type_value(category, 2)
            self.categories[category] = f'C{r}'
            self.advance(f'D{r}')
        self.type_value(qty, 3); self.advance(f'E{r}')
        self.type_value(price, 4); self.advance(f'G{r}', tabs=2)   # F is a formula, filled later
        self.type_value(reorder, 6); g.press('enter'); g.pause()
        g.event('inventory_row_added', row=r, sku=sku, method='reuse')

    def reuse_row_with_retry(self, row_number, record):
        from gui_support import Stopped
        if not self.ranges_work:
            # Ranges cannot be selected here, so nothing was pasted and nothing can be
            # filled later: type the record in full, formulas included.
            return self.row(row_number, record)
        try:
            return self.row_reuse(row_number, record)
        except (Stopped, pg.FailSafeException):
            raise
        except Exception as exc:
            self.gui.event('row_reuse_failed', row=row_number, error=str(exc)[:160])
            for _ in range(2):
                self.gui.press('esc'); self.gui.sleep(0.25)   # leave edit mode / close a prompt
            self.gui.focus(self.window)
            # Pasting again would land on cells that are now filled and raise Calc's
            # "overwrite?" prompt, so the fallback types the whole record instead.
            self.row(row_number, record)

    # --- Checking the table with one look -------------------------------------------------

    def cell_text(self, address):
        """Read a cell through the clipboard (OpenOffice exposes little through UIA)."""
        g = self.gui
        self.select(address)
        self.clipboard_category = None
        g.clear_clipboard(); g.hotkey('ctrl', 'c')
        return g.read_clipboard().strip()

    def range_values(self, address):
        """Displayed values of a one-column range, top to bottom, through one copy."""
        g = self.gui
        self.select(address)
        self.clipboard_category = None
        g.clear_clipboard(); g.hotkey('ctrl', 'c')
        return [line.strip() for line in g.read_clipboard().splitlines()]

    def table_snapshot(self, last_row=8):
        """Displayed values of A2:H{last_row}, row by row, through a single copy."""
        g = self.gui
        self.select(f'A2:H{last_row}')
        self.clipboard_category = None
        g.clear_clipboard(); g.hotkey('ctrl', 'c')
        return [[value.strip() for value in line.split('\t')]
                for line in g.read_clipboard().splitlines()]

    @staticmethod
    def shown(record):
        """What a correct row displays in columns A to H."""
        sku, item, category, qty, price, reorder = record
        return [sku, item, category, qty, price, qty*price, reorder,
                'YES' if qty < reorder else 'NO']

    @staticmethod
    def same(text, expected):
        if isinstance(expected, str):
            return text == expected
        try:    # numbers may be shown as currency: $1,260.00
            return abs(float(re.sub(r'[^0-9.\-]', '', text))-float(expected)) < 0.005
        except ValueError:
            return False

    def mismatches(self, table, records):
        """(row, column index) of every cell that does not show what it should."""
        wrong = []
        for offset, record in enumerate(records):
            line = table[offset] if offset < len(table) else []
            for col, expected in enumerate(self.shown(record)):
                if not self.same(line[col] if col < len(line) else '', expected):
                    wrong.append((offset+2, col))
        return wrong

    def verify_table(self, records=None):
        """Compare the whole table with what it should hold, using one copy, and put
        right only the cells that differ.  Returns how many cells were put right.

        Values are retyped; a formula is copied from a row where it is right and pasted,
        which is what a person does and takes seconds instead of a quarter of a minute
        per cell.  If most of the table looks wrong, the reading itself is doubted and
        nothing is touched: the saved file is still validated at the end."""
        g = self.gui
        records = records or self.order
        last_row = 1+len(records)
        wrong = self.mismatches(self.table_snapshot(last_row), records)
        if len(wrong) > 4*len(records):
            g.event('table_check_unreliable', differing_cells=len(wrong))
            return 0
        repaired = 0
        values = [(row, col) for row, col in wrong if col not in (5, 7)]
        for row, col in values:
            record = records[row-2]
            self.cell(f'{chr(65+col)}{row}', record[5] if col == 6 else record[col])
            g.event('cell_repaired', cell=f'{chr(65+col)}{row}', method='typed')
        repaired += len(values)
        if values and len(values) < len(wrong):
            # A formula cell may only have looked wrong because of the value beside it.
            wrong = self.mismatches(self.table_snapshot(last_row), records)
        for col in (5, 7):
            rows = [row for row, c in wrong if c == col]
            if rows:
                self.repair_formulas(col, rows, last_row)
                repaired += len(rows)
        g.event('table_checked', rows=len(records), repaired=repaired)
        return repaired

    def repair_formulas(self, col, rows, last_row):
        """Put a formula back into the given rows of column F (5) or H (7)."""
        g = self.gui
        column = chr(65+col)
        formula = (lambda row: f'=D{row}*E{row}') if col == 5 else (
            lambda row: f'=IF(D{row}<G{row};"YES";"NO")')
        good = [row for row in range(2, last_row+1) if row not in rows]
        if not good:                                   # nowhere to copy from: type one
            self.cell(f'{column}{rows[0]}', formula(rows[0]))
            good, rows = [rows[0]], rows[1:]
        for first, last in self.runs(rows):
            # Empty the cells first, so the paste cannot raise the overwrite question.
            self.clear_contents(f'{column}{first}' if first == last else f'{column}{first}:{column}{last}')
        if rows:
            self.paste_down(column, good[0], rows)
        g.event('cell_repaired', cell=f'{column}{rows[0] if rows else good[0]}',
                rows=rows, method='formula_pasted')

    # --- Menus and saving ------------------------------------------------------------------

    def open_save_dialog(self):
        g = self.gui
        for attempt in range(2):
            g.hotkey('ctrl', 'shift', 's', window=self.window)
            try:
                dialog = g.window(r'^Save As$', 15)
                file_name_field(g, dialog)
                return dialog
            except RuntimeError as exc:
                if attempt:
                    raise
                g.event('save_dialog_retry', error=str(exc)[:120])
                g.press('esc'); g.sleep(1.0)

    def locate_saved(self, timeout=12, since=None):
        """The dialog was driven by clicks, so check on disk where the file really went.
        Only a file written by this save counts (modified at `since` or later): in the
        7:55 PM run an inventory_oct17.ods left in Documents\\HackathonDemo by an earlier
        run was taken for the new one."""
        g = self.gui
        name = self.path.name
        def fresh(path):
            try:
                return path.exists() and (since is None or path.stat().st_mtime >= since-2)
            except OSError:
                return False
        # Documents\HackathonDemo: where the dialog opened in earlier runs.
        places = [self.folder, self.folder.parent, Path.home()/'Documents'/'HackathonDemo',
                  Path.home()/'Desktop', Path.home()/'Documents', Path.home()]
        end = time.monotonic()+timeout
        while True:
            found = [place/name for place in places if fresh(place/name)]
            found += [path for path in self.folder.glob('*/'+name) if fresh(path)]   # an earlier run's folder
            if found:
                self.path = found[0]
                return True
            if time.monotonic() >= end:
                return False
            g.sleep(0.4)

    def save_as(self):
        """First save: reach the folder with the mouse and type only the file name.

        In the 7:55 PM run the clicks did not get there: the dialog stayed in This PC, Save
        put the workbook there (Windows keeps such files in AppData), an older copy was
        replaced, and an old file of the same name elsewhere was taken for it.  So Save is
        never pressed while the dialog plainly shows another folder: the full path is typed
        instead, a replace question there is answered No, and only a file written by this
        save counts as found."""
        from gui_support import Stopped
        g = self.gui
        dialog = self.open_save_dialog()
        try:
            browse_to(g, dialog, self.folder)
        except (Stopped, pg.FailSafeException):
            raise
        except Exception as exc:
            g.event('save_folder_browse_failed', error=str(exc)[:120])
        typed_path = elsewhere(dialog, self.folder)
        if typed_path:
            g.event('save_folder_typed', shown=folder_shown(dialog)[:80])
            self.path = self.folder/self.path.name
        started = time.time()
        g.click(file_name_field(g, dialog)); g.hotkey('ctrl', 'a')
        g.type_text(str(self.path) if typed_path else self.path.name, dialog)
        g.click_named(dialog, ['Save'], 'Button')
        if typed_path:
            confirm_replace(g)                       # the agent's own folder
            g.wait(lambda: self.path.exists(), 'new ODS file saved', 30)
        elif confirm_replace(g, replace=False):
            # The name was free in the agent folder, so the dialog was somewhere else.  It
            # is still open after the No: type the whole path there.
            self.save_by_path(dialog)
        elif not self.locate_saved(since=started):
            # Last resort, the old way: type the full path.
            g.event('save_by_clicks_unconfirmed', name=self.path.name)
            for _ in range(2):
                g.press('esc'); g.sleep(0.4)
            self.save_by_path(self.open_save_dialog())
        self.window = g.window(r'.*inventory.*Calc.*', 15)
        self.saved_once = True
        self.sheet_click_needed = True

    def save_by_path(self, dialog):
        """Type the workbook's full path into the dialog and save it there."""
        g = self.gui
        self.path = self.folder/self.path.name
        g.click(file_name_field(g, dialog)); g.hotkey('ctrl', 'a')
        g.type_text(str(self.path), dialog)
        g.click_named(dialog, ['Save'], 'Button')
        confirm_replace(g)
        g.wait(lambda: self.path.exists(), 'new ODS file saved', 30)

    def save_from_menu(self):
        """File > Save through the menu (Alt+F, then S): the same save as Ctrl+S, but one
        that can be seen on screen.  Returns False if the menu did not visibly open; a
        letter is never sent unless it did."""
        g = self.gui
        before = set(g.window_handles())
        g.hotkey('alt', 'f', window=self.window)
        g.sleep(random.uniform(0.4, 0.7))
        if set(g.window_handles()) == before:
            for _ in range(2):
                g.press('esc'); g.sleep(0.2)             # menu bar may still have the focus
            return False
        g.sleep(random.uniform(0.3, 0.8))                # find Save in the list
        g.press('s')
        return True

    def wait_for_write(self, before, seconds=4):
        """True once the file on disk has changed since `before`."""
        end = time.monotonic()+seconds
        while self._modified() == before:
            if time.monotonic() >= end:
                return False
            self.gui.sleep(0.3)
        return True

    def save(self, visible=False):
        """Save the workbook.  `visible=True` (the last save of the run) goes through the
        File menu so the save can be seen, and falls back to Ctrl+S."""
        g = self.gui
        if self.saved_once:
            before = self._modified()
            method = 'ctrl_s'
            if visible and self.config.get('final_save_from_menu', True) and self.save_from_menu():
                method = 'file_menu'
                if not self.wait_for_write(before):
                    for _ in range(2):
                        g.press('esc'); g.sleep(0.2)     # whatever the menu left open
                    method = 'ctrl_s_after_menu'
            if method != 'file_menu':
                g.hotkey('ctrl', 's', window=self.window)
                if self.unsaved:
                    self.wait_for_write(before)          # let the write finish before moving on
        else:
            self.save_as()
            method = 'save_as'
        # Saving must produce a readable ODS; a format prompt is not bypassed blindly.
        g.wait(self._zip_ready, 'saved ODS package', 20)
        self.unsaved = False
        g.event('workbook_saved', path=str(self.path), method=method)

    def save_if_asked(self):
        """Calc asks "Do you want to save your changes?" when it is closed with unsaved
        work.  Save is the default button, so Enter saves and lets the window close."""
        g = self.gui
        if g.window_handles(title_pattern=r'^OpenOffice( Calc| \d).*'):
            g.press('enter')
            g.event('save_confirmed_on_close')
            g.sleep(1.0)

    def _modified(self):
        try:
            return self.path.stat().st_mtime
        except OSError:
            return None

    def _zip_ready(self):
        import zipfile
        try:
            with zipfile.ZipFile(self.path) as z:
                return 'content.xml' in z.namelist()
        except (OSError, zipfile.BadZipFile):
            return False

    def attempt(self, name, action):
        """Run a non-essential UI step; on failure log it, close stray popups, move on."""
        from gui_support import Stopped
        try:
            action()
            return True
        except (Stopped, pg.FailSafeException):
            raise
        except Exception as exc:
            self.gui.event('step_failed', step=name, error=str(exc)[:200])
            self.sheet_click_needed = True
            for _ in range(3):
                self.gui.press('esc'); self.gui.sleep(0.25)
            if self.window is not None:
                self.gui.focus(self.window)
            return False

    # --- Rows, formatting, sorting ---------------------------------------------------------

    def clear_contents(self, address):
        """Empty a range.  OpenOffice clears with Backspace and opens a "Delete Contents"
        dialog on Delete (LibreOffice is the other way round), so handle the dialog."""
        g = self.gui
        self.select(address)
        self.unsaved = True
        g.press('backspace'); g.sleep(0.6)
        if g.window_handles(title_pattern=r'^Delete Contents$'):
            g.press('enter'); g.sleep(0.4)
        g.pause()

    def format_table(self):
        """Bold header row; currency for the money columns and the totals."""
        g = self.gui
        self.unsaved = True
        if self.grid is not None:
            g.click_at(*self.grid.row_header_point(1), window=self.window, quick=True)  # whole row 1
        elif self.ranges_work:
            self.select('A1:H1')
        else:
            self.select('A1')
            g.hotkey('shift', 'space', window=self.window)      # the whole row, without a range
        g.hotkey('ctrl', 'b', window=self.window)
        if self.ranges_work:
            for block in ('E2:F8', 'F11:F15'):
                self.select(block); g.hotkey('ctrl', 'shift', '4', window=self.window)
        g.event('table_formatted', currency=self.ranges_work)

    def sort_dialog(self):
        """Data > Sort with the keyboard.  OpenOffice's menus are not exposed to UI
        Automation, so they cannot be clicked by name; Alt+D, S is how the keyboard
        reaches them.  Every step is checked so a stray letter can never land in a cell."""
        g = self.gui
        self.select('B2')                                # a cell in the Item column
        before = set(g.window_handles())
        g.hotkey('alt', 'd', window=self.window)
        g.sleep(random.uniform(0.4, 0.7))
        if set(g.window_handles()) == before:
            raise RuntimeError('Data menu did not open')
        g.press('s')
        g.wait(lambda: g.window_handles(title_pattern=r'^Sort$'), 'Sort dialog', 5)
        g.pause()
        g.press('i'); g.pause()                          # "Sort by" has the focus: picks Item
        self.unsaved = True
        g.press('enter')
        g.wait(lambda: not g.window_handles(title_pattern=r'^Sort$'), 'Sort dialog to close', 5)
        g.focus(self.window)

    def sort_by_item(self):
        """Sort by Item, then look at the table.  The table is never retyped to "sort"
        it: if the sort cannot be confirmed, the entry order is kept and reported."""
        g = self.gui
        self.attempt('sort_dialog', self.sort_dialog)
        by_item = sorted(self.order, key=lambda record: record[1])
        table = self.table_snapshot(1+len(self.order))
        if not self.mismatches(table, by_item):
            self.order, self.sorted = by_item, True
            g.event('inventory_sorted', column='Item', direction='ascending', method='keyboard_menu')
            return True
        if self.mismatches(table, self.order):
            # Something changed, but not into the intended order: take it back.
            g.hotkey('ctrl', 'z', window=self.window)
            self.verify_table()
        g.event('sort_skipped', reason='sort could not be confirmed; entry order kept')
        return False

    def summary(self):
        """The totals block: labels down column E, then values down column F (Enter
        moves down), then one look to confirm they landed where they should."""
        g = self.gui
        rows = [('Total value', '=SUM(F2:F8)'),
                ('Average value', '=AVERAGE(F2:F8)'),
                ('Lowest value', '=MIN(F2:F8)'),
                ('Highest value', '=MAX(F2:F8)'),
                ('Budget', self.budget),
                ('Result', '=IF(F11<=F15;"WITHIN BUDGET";"OVER BUDGET")')]
        scale = self.config.get('spreadsheet_typo_scale', 1.0)
        self.column('E11', [label for label, _ in rows], typo_chance=0.15*scale)
        self.column('F11', [value for _, value in rows])
        stocks = [record[3]*record[4] for record in FINAL]
        expected = [sum(stocks), sum(stocks)/len(stocks), min(stocks), max(stocks), self.budget,
                    'WITHIN BUDGET' if sum(stocks) <= self.budget else 'OVER BUDGET']
        if self.ranges_work:
            shown = self.range_values('F11:F16')
            right = len(shown) == len(expected) and all(
                self.same(text, value) for text, value in zip(shown, expected))
        else:
            # No range can be copied here: the first and the last cell tell enough (the
            # last one depends on the total and on the budget being in their places).
            shown = [self.cell_text('F11'), self.cell_text('F16')]
            right = self.same(shown[0], expected[0]) and self.same(shown[1], expected[5])
        if not right:
            # Enter did not walk down the column as expected: place each cell explicitly.
            g.event('summary_retyped', shown=shown[:6])
            for offset, (label, value) in enumerate(rows):
                self.cell(f'E{11+offset}', label); self.cell(f'F{11+offset}', value)
        g.event('summary_added', checked=right)

    # --- Review work that fills the remaining time (replaces select-and-save filler) ---

    def walk_cells(self):
        """Look over the table: click a few cells, sometimes step on with the arrow keys."""
        g = self.gui
        for _ in range(random.randint(2, 4)):
            self.select(random.choice('ABCDEFGH')+str(random.randint(2, 8)))
            g.sleep(heavy_pause(0.9*g.pace, 0.5, 0.3, 4.0))
            if random.random() < 0.4:
                for _ in range(random.randint(1, 3)):
                    g.press(random.choice(('right', 'right', 'down', 'down', 'left', 'up')))
                    g.sleep(heavy_pause(0.4*g.pace, 0.5, 0.15, 2.5))
        g.event('inventory_review', activity='walk_cells')
        return False

    def check_totals(self):
        """Look down the summary block."""
        g = self.gui
        self.select('F11')
        for _ in range(random.randint(1, 5)):
            g.sleep(heavy_pause(0.9*g.pace, 0.5, 0.3, 4.0))
            g.press('down')
        g.event('inventory_review', activity='check_totals')
        return False

    def what_if_quantity(self):
        """Try a different quantity, look at the total, then undo it (sometimes redo/undo)."""
        g = self.gui
        row = random.randint(2, 8)
        address, original = f'D{row}', self.order[row-2][3]
        self.pending[address] = original
        self.cell(address, original+random.randint(1, 6))
        self.select('F11'); g.pause(reading=True)
        g.hotkey('ctrl', 'z', window=self.window)
        if random.random() < 0.3:
            g.hotkey('ctrl', 'y', window=self.window); g.pause()
            g.hotkey('ctrl', 'z', window=self.window)
        self.restore_pending()
        g.event('inventory_review', activity='what_if_quantity', cell=address)
        return False

    def restore_pending(self):
        """Make sure every what-if edit is really back to its original value."""
        for address, original in list(self.pending.items()):
            try:
                same = float(self.cell_text(address).replace(',', '')) == float(original)
            except ValueError:
                same = False
            if not same:
                self.cell(address, original)
                self.gui.event('what_if_restored_by_typing', cell=address)
            del self.pending[address]

    def find_item(self):
        """Look an item up with Find & Replace (Ctrl+F), then close the dialog."""
        g = self.gui
        g.hotkey('ctrl', 'f', window=self.window)
        dialog = g.window(r'.*Find.*Replace.*', 8)
        g.type_text(random.choice(FINAL)[1], dialog)
        g.press('enter'); g.pause(reading=True)
        for _ in range(4):
            # First Esc may only close a "not found" message; the dialog floats above the
            # sheet, so it must really be gone before any cell is clicked.
            g.press('esc'); g.sleep(0.4)
            if not g.window_handles(title_pattern=r'.*Find.*Replace.*'):
                break
        g.focus(self.window)
        self.sheet_click_needed = True
        g.event('inventory_review', activity='find_item')
        return False

    def review_until(self, phase):
        """Keep doing believable spreadsheet work until the time is nearly up: look over
        the cells and the totals, try a quantity and put it back, find an item.  Nothing is
        typed into the sheet that was not asked for (no notes column)."""
        g = self.gui
        self.step = 'review'
        if self.hurry:
            g.event('step_skipped', step='review', reason='behind schedule')
            return
        reserve = self.config.get('calc_wrap_up_seconds', 20)
        activities = ((self.walk_cells, 3), (self.check_totals, 2), (self.what_if_quantity, 2),
                      (self.find_item, 1))
        while phase.remaining-reserve > 12:
            activity = random.choices([a for a, _ in activities],
                                      weights=[w for _, w in activities])[0]
            if not self.attempt(activity.__name__, activity):
                self.attempt('restore_pending', self.restore_pending)
            if phase.remaining-reserve > 12:
                g.pause(reading=True)
        self.restore_pending()

    def reopen_from_explorer(self):
        """Reopen the saved file from File Explorer by clicking through the folders."""
        g = self.gui
        g.hotkey('win', 'e')
        def foreground_explorer():
            handle = win32gui.GetForegroundWindow()
            if win32gui.GetClassName(handle) == 'CabinetWClass':
                return g.desktop.window(handle=handle).wrapper_object()
            return None
        explorer = g.wait(foreground_explorer, 'foreground File Explorer', 20)
        name = self.path.name
        find_file = lambda: g.find(explorer, [name, name.rsplit('.', 1)[0]], 'ListItem')
        clicked = self.path.parent == self.folder and browse_to(g, explorer, self.folder) == 2
        if not clicked or find_file() is None:
            g.hotkey('ctrl', 'l', window=explorer)
            g.type_text(str(self.path.parent), explorer); g.press('enter')
        file_icon = g.wait(find_file, 'saved workbook icon', 15)
        g.click(file_icon, double=True)
        self.window = g.window(r'.*inventory.*Calc.*', 30)
        g.focus(self.window)

    # --- The phase -------------------------------------------------------------------------

    def build(self):
        """Create the workbook.  Essential steps always run; optional ones (format, sort)
        are skipped when they no longer fit before the time cap."""
        g = self.gui
        self.step = 'launch'
        self.window = g.launch_shortcut(self.config['calc_shortcut'], r'.*Calc.*')
        self.calibrate_grid()
        self.step = 'headers'
        self.select('A1')
        for i, header in enumerate(HEADERS):
            g.type_text(header, self.window); g.press('tab' if i < 7 else 'enter')
        self.step = 'first_save'
        self.save()
        self.checkpoint('first_save')
        if self.grid is None:
            self.calibrate_grid()                     # one more look now that Calc has settled
        self.step = 'rows'
        self.row(2, ENTRY[0])                         # the first record is typed in full
        self.checkpoint('row_2')
        self.think()
        last = 1+len(INITIAL)                         # row 7; one more record is added below it
        self.attempt('fill_sku', lambda: self.fill_sku(3, last+1))
        for row, record in enumerate(INITIAL[1:], 3):
            self.reuse_row_with_retry(row, record)
            if row == 5:
                self.save()
            self.think()
        self.checkpoint('rows')
        self.step = 'formulas'
        if self.ranges_work:
            self.attempt('fill_formulas', lambda: self.fill_formulas(3, last))
        self.step = 'edits'
        self.cell('D5', 9); self.cell('E4', 210)      # corrections to two earlier entries
        self.checkpoint('formulas')
        self.step = 'extra_row'
        self.reuse_row_with_retry(last+1, EXTRA)
        if self.ranges_work:
            self.attempt('fill_formulas_extra', lambda: self.fill_formulas(last+1, last+1))
            self.step = 'check'
            self.verify_table()
        self.save()
        self.checkpoint('check')
        self.step = 'summary'
        self.summary()
        # File > Save, so the save can be seen.  Whatever is done after this (format,
        # sort, a what-if) is saved the same way at the very end, by wrap_up.
        self.save(visible=True)
        self.hurry = False          # the workbook is complete: the polish runs if it fits
        # From here on the workbook is complete and saved; the rest is polish, done only
        # if it fits before the cap.
        polished = False
        if self.fits(COST['format']):
            self.step = 'format'
            polished = self.attempt('format', self.format_table)
        else:
            g.event('step_skipped', step='format', reason='not enough time left')
        if not self.ranges_work:
            g.event('step_skipped', step='sort', reason='the result could not be checked')
        elif self.fits(COST['sort']):
            self.step = 'sort'
            polished = self.sort_by_item() or polished
        else:
            g.event('step_skipped', step='sort', reason='not enough time left')
        if polished:
            g.event('polish_done', saved_at='end')        # wrap_up saves it with File > Save

    def wrap_up(self, phase, cut_short=None, failure=None):
        """Save, close and check the file.  Runs after the time limit has been lifted, so
        it always completes: the run ends with a saved workbook whatever happened before."""
        g = self.gui
        result = {'complete': False, 'cut_short_at': cut_short, 'saved_path': None}
        if self.window is None:
            result['error'] = failure or 'Calc was not opened in time'
            g.event('inventory_phase_complete', elapsed=time.monotonic()-phase.started, validation=result)
            return result
        if cut_short or failure:
            for _ in range(2):
                g.press('esc'); g.sleep(0.3)          # leave a half-typed cell or an open dialog
        self.attempt('restore_pending', self.restore_pending)
        if self.unsaved or not self.saved_once:
            self.save(visible=True)
        else:
            g.event('workbook_already_saved', path=str(self.path))
        # Should a save not have reached Calc, it asks on closing, and that is answered.
        g.close_window(self.window, on_prompt=self.save_if_asked)
        self.window = None
        try:
            result.update(validate_inventory(self.path, self.budget), complete=True)
        except Exception as exc:                      # an unfinished table is reported, not hidden
            result['error'] = str(exc)
        if failure:
            result['step_error'] = failure
        result.update(saved_path=str(self.path), sorted_in_sheet=self.sorted, pointer_selection=self.grid is not None,
                      copy_paste_used=self.ranges_work)
        if self.config.get('reopen_after_save', False):
            result['reopened'] = self.attempt('reopen_workbook', self.reopen_from_explorer)
        g.event('inventory_phase_complete', elapsed=time.monotonic()-phase.started, validation=result)
        return result

    def run(self, seconds):
        from gui_support import Stopped, TimeUp
        g = self.gui
        phase = self.phase = Phase(seconds)
        pace = g.pace
        if isinstance(pace, (int, float)) and not isinstance(pace, bool):
            # The pace drifts site by site while browsing (up to x1.35); the spreadsheet
            # starts again at a working pace.
            g.pace = min(1.0, max(0.85, pace))
            if g.pace != pace:
                g.event('pace_reset', before=round(pace, 3), after=round(g.pace, 3))
        # Everything stops this long before the cap, so saving and closing fit inside it.
        g.time_limit = phase.deadline-self.config.get('calc_wrap_up_seconds', 20)
        cut_short = failure = None
        try:
            self.build()
            self.review_until(phase)
        except TimeUp:
            cut_short = self.step
            g.event('inventory_time_up', step=self.step)
        except (Stopped, pg.FailSafeException):
            raise                                     # F8 or the corner: stop, do not tidy up
        except Exception as exc:
            # A step failed for good.  The work so far is still saved and closed.
            failure = f'{self.step}: {exc}'
            g.event('inventory_step_failed', step=self.step, error=str(exc)[:200])
        finally:
            g.time_limit = None
        return self.wrap_up(phase, cut_short, failure)
