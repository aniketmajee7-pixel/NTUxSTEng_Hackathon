"""A small model of OpenOffice Calc behind the GUI helper.

It lets the whole spreadsheet flow run without Windows and checks what really ends up
in the sheet: which cell every keystroke, click, drag, paste and fill lands on.  Time
is simulated: every action advances a clock by roughly what it costs on screen.

It also models what broke the recorded runs, so the same mistakes fail here first:

- Shift+arrow not selecting, and no readable screen (first recording);
- Ctrl+D is the Selection List in OpenOffice, not Fill Down (second recording: a list
  dropped open under the cell and nothing was filled);
- after a paste the target stays marked, and Tab/Enter then move only inside the marked
  cells (second recording: values typed after a paste all landed on the pasted cell)."""
import os
import re
import zipfile
from pathlib import Path
from unittest.mock import Mock

from fake_sheet import FakeSheet

LAST_COLUMN = 12


def shift_refs(text, dc, dr):
    """Move the cell references of a formula, as a relative paste or fill does."""
    if not text.startswith('='):
        return text
    return re.sub(r'([A-Z])(\d+)', lambda m: f'{chr(ord(m.group(1))+dc)}{int(m.group(2))+dr}', text)


class SimGUI:
    """The subset of gui_support.GUI that the spreadsheet code uses."""

    def __init__(self, sim, run_dir):
        self.sim, self.run_dir = sim, run_dir
        self.pace = self.pause_scale = 1.0
        self.time_limit = None
        self.desktop = Mock()

    def event(self, action, **details):
        self.sim.events.append((action, details, round(self.sim.clock, 1)))

    def check_stop(self):
        self.sim.check()

    def sleep(self, seconds):
        self.sim.tick(seconds)

    def pause(self, reading=False):
        self.sim.tick(6.0 if reading else 0.7)

    def wait(self, predicate, label, timeout=30, poll_interval=0.4):
        waited = 0.0
        while True:
            self.sim.check()
            found = predicate()
            if found:
                return found
            if waited >= timeout:
                raise RuntimeError(f'Timed out waiting for {label}: not ready')
            self.sim.tick(poll_interval); waited += poll_interval

    def window(self, pattern, timeout=30):
        return self.wait(lambda: self.sim.find_window(pattern), pattern, timeout)

    def window_handles(self, title_pattern=None, class_name=None):
        return self.sim.handles(title_pattern)

    def focus(self, window):
        pass

    def hotkey(self, *keys, window=None):
        self.sim.tick(0.9); self.sim.hotkey(tuple(keys))

    def press(self, key):
        self.sim.tick(0.1); self.sim.press(key)       # a key held for about 85 ms; pauses are separate

    def type_text(self, text, window, **options):
        # Measured in the recording: about 0.3 s for a plain letter or digit and about
        # 0.7 s for capitals and symbols (row 2, 58 characters and 8 Tabs, took 40 s).
        for char in str(text):      # the real helper checks for a stop before every key
            self.sim.tick(0.32 if char.islower() or char.isdigit() or char == ' ' else 0.7)
            self.sim.type(char, window)

    def move(self, x, y, width=40.0):
        self.sim.tick(0.5); self.sim.pointer = (x, y)

    def click_here(self):
        self.sim.tick(0.1); self.sim.click(*self.sim.pointer)

    def click_at(self, x, y, window, width=20, quick=False):
        self.sim.tick(1.2 if quick else 2.0)
        self.sim.pointer = (x, y); self.sim.click(x, y)

    def drag(self, x1, y1, x2, y2, window, width=20):
        self.sim.tick(2.2); self.sim.drag(x1, y1, x2, y2)

    def grab(self, left, top, width, height):
        return self.sim.screen()

    def find(self, window, names, kind=None, bounded=False):
        return self.sim.find_control(window, names, kind)

    def click(self, control, double=False, window=None):
        self.sim.tick(2.0); self.sim.click_control(control, double)

    def click_named(self, window, names, kind=None):
        control = self.wait(lambda: self.find(window, names, kind), str(names), 8)
        self.click(control)
        return control

    def read_clipboard(self):
        return self.sim.clip_text

    def clear_clipboard(self):
        self.sim.clip, self.sim.clip_text = None, ''

    def launch_shortcut(self, shortcut_name, title_pattern):
        self.sim.tick(8.0)
        return self.sim.window

    def close_window(self, window, on_prompt=None):
        self.sim.tick(1.5); self.sim.close()
        waited = 0.0
        while not self.sim.closed:
            if on_prompt is not None:
                on_prompt()
            if waited >= 15:
                raise RuntimeError('Timed out waiting for window closed: not ready')
            self.sim.tick(0.4); waited += 0.4


class SimCalc:
    def __init__(self, folder, time_up, grid=True, shift_arrows=True, sort='works',
                 documents_listed=True, file_menu='works', ctrl_s=True):
        # file_menu: 'works'; 'unseen' (it opens, but no new window can be detected);
        # 'dead' (S does nothing).  ctrl_s=False: the shortcut does not reach Calc.
        self.file_menu, self.ctrl_s = file_menu, ctrl_s
        self.menu_saves = 0
        self.closing = False                       # "Do you want to save your changes?"
        self.folder = Path(folder)                 # ...\Documents\HackathonDemo
        self.time_up = time_up
        self.grid_visible, self.shift_arrows, self.sort_mode = grid, shift_arrows, sort
        self.documents_listed = documents_listed
        self.cells, self.currency, self.bold_rows = {}, set(), set()
        self.anchor = self.cursor = (0, 1)
        self.block = None                          # a marked range (c1, r1, c2, r2), if any
        self.popup = False                         # the Selection List is hanging open
        self.whole_row = False
        self.editing = None
        self.tab_origin = None
        self.clip, self.clip_text = None, ''
        self.shift = False
        self.undo_stack, self.redo_stack = [], []
        self.menu = self.dialog = self.question = None
        self.sort_field = None
        self.field, self.field_selected = '', False
        self.dialog_folder = self.folder/'20261008-142443-09ab12'    # where the last run saved
        self.saved_as = None
        self.saves = 0
        self.dirty = self.closed = False
        self.errors, self.events, self.keys = [], [], []
        self.pastes = self.fills = self.clicks = self.drags = self.typed_chars = 0
        self.sheet, self.drawn = FakeSheet(), None
        self.pointer = (640, 400)
        self.clock = 0.0
        self.window = self.rectangle_mock(Mock(handle=1), 0, 0, 1280, 752)
        self.save_dialog, self.find_dialog = Mock(handle=3), Mock(handle=4)
        self.controls = {name: Mock(name=name) for name in ('documents', 'folder', 'field', 'save')}
        self.gui = SimGUI(self, self.folder/'run-now')

    @staticmethod
    def rectangle_mock(window, left, top, right, bottom):
        rect = Mock(left=left, top=top, right=right, bottom=bottom)
        rect.width.return_value, rect.height.return_value = right-left, bottom-top
        window.rectangle.return_value = rect
        return window

    # --- time ---------------------------------------------------------------------------

    def check(self):
        limit = self.gui.time_limit
        if limit is not None and self.clock >= limit:
            raise self.time_up('Time limit reached')

    def tick(self, seconds):
        self.clock += seconds
        self.check()

    # --- sheet model --------------------------------------------------------------------

    def selection(self):
        if self.whole_row:
            return 0, self.cursor[1], LAST_COLUMN, self.cursor[1]
        if self.block:
            return self.block
        return self.cursor+self.cursor

    def snapshot(self):
        self.undo_stack.append(dict(self.cells)); self.redo_stack.clear()
        self.dirty = True

    def commit(self):
        if self.editing is None:
            return
        self.snapshot()
        if self.editing == '':
            self.cells.pop(self.cursor, None)
        else:
            self.cells[self.cursor] = self.editing
        self.editing = None

    def go(self, cell, extend=False):
        self.commit()
        self.whole_row = False
        self.cursor = (max(0, min(LAST_COLUMN, cell[0])), max(1, cell[1]))
        if extend:
            (c1, r1), (c2, r2) = self.anchor, self.cursor
            self.block = (min(c1, c2), min(r1, r2), max(c1, c2), max(r1, r2))
        else:
            self.anchor, self.block = self.cursor, None

    def step_inside_block(self, by_rows):
        """Tab/Enter with a marked range: the cursor walks inside it and never leaves."""
        c1, r1, c2, r2 = self.block
        cells = ([(c, r) for r in range(r1, r2+1) for c in range(c1, c2+1)] if by_rows else
                 [(c, r) for c in range(c1, c2+1) for r in range(r1, r2+1)])
        self.commit()
        self.cursor = cells[(cells.index(self.cursor)+1) % len(cells)]

    def number(self, col, row):
        try:
            return float(self.value(col, row))
        except (TypeError, ValueError):
            return 0.0

    def value(self, col, row):
        raw = self.cells.get((col, row), '')
        if not raw.startswith('='):
            return raw
        expr = raw[1:]
        m = re.fullmatch(r'D(\d+)\*E(\d+)', expr)
        if m:
            return self.number(3, int(m.group(1)))*self.number(4, int(m.group(2)))
        m = re.fullmatch(r'IF\(D(\d+)<G(\d+);"YES";"NO"\)', expr)
        if m:
            return 'YES' if self.number(3, int(m.group(1))) < self.number(6, int(m.group(2))) else 'NO'
        m = re.fullmatch(r'(SUM|AVERAGE|MIN|MAX)\(F(\d+):F(\d+)\)', expr)
        if m:
            values = [self.number(5, row) for row in range(int(m.group(2)), int(m.group(3))+1)
                      if self.cells.get((5, row))]
            if not values:
                return 0.0
            return {'SUM': sum(values), 'AVERAGE': sum(values)/len(values),
                    'MIN': min(values), 'MAX': max(values)}[m.group(1)]
        if expr == 'IF(F11<=F15;"WITHIN BUDGET";"OVER BUDGET")':
            return 'WITHIN BUDGET' if self.number(5, 11) <= self.number(5, 15) else 'OVER BUDGET'
        return '#ERR'

    def display(self, col, row):
        value = self.value(col, row)
        try:
            number = float(value)
        except (TypeError, ValueError):
            return str(value)
        if value == '':
            return ''
        if (col, row) in self.currency:
            return f'${number:,.2f}'
        return str(int(number)) if number.is_integer() else str(round(number, 4))

    def ods(self):
        """The sheet in the shape domain.ods_cells returns: (row, column) -> (value, formula)."""
        result = {}
        for (col, row), raw in self.cells.items():
            value = self.value(col, row)
            result[(row, col+1)] = (str(value), raw if raw.startswith('=') else '')
        return result

    def table(self, last_row=8):
        return [[self.display(col, row) for col in range(8)] for row in range(2, last_row+1)]

    # --- keys ---------------------------------------------------------------------------

    def blocked(self, what):
        thing = (self.closing and 'the save-changes question' or self.question and 'the overwrite question'
                 or self.menu and 'a menu' or self.dialog)
        if thing:
            self.errors.append(f'{what} while {thing} is open')
        return bool(thing)

    def type(self, text, window):
        if window is self.save_dialog:
            if self.field_selected:
                self.field, self.field_selected = '', False
            self.field += text
            return
        if window is self.find_dialog:
            return
        if self.blocked(f'typing {text!r}'):
            return
        if self.editing is None:
            self.editing = ''
        self.editing += text
        self.typed_chars += len(text)

    def press(self, key):
        self.keys.append(key)
        if self.closing:
            self.closing = False
            if key == 'enter':                     # Save is the default button
                self.write(); self.closed = True
            elif key != 'esc':
                self.errors.append(f'key {key!r} at the save-changes question')
            return
        if self.menu == 'File':
            if key == 's':
                self.menu = None
                if self.file_menu != 'dead':
                    self.save(); self.menu_saves += 1
            elif key == 'esc':
                self.menu = None
            else:
                self.errors.append(f'key {key!r} in the File menu')
            return
        if self.question:
            pending, self.question = self.question, None
            if key == 'y':
                pending()
            elif key not in ('n', 'esc'):
                self.errors.append(f'key {key!r} at the overwrite question')
            return
        if self.menu:
            if key == 's' and self.sort_mode != 'no_dialog':
                self.menu, self.dialog, self.sort_field = None, 'Sort', 'SKU'
            elif key == 'esc':
                self.menu = None
            elif key != 's':
                self.errors.append(f'key {key!r} in the Data menu')
            return
        if self.dialog == 'Sort':
            if key == 'i':
                if self.sort_mode != 'wrong_field':
                    self.sort_field = 'Item'
            elif key == 'enter':
                self.dialog = None
                self.sort()
            elif key == 'esc':
                self.dialog = None
            else:
                self.errors.append(f'key {key!r} in the Sort dialog')
            return
        if self.dialog:
            if key == 'esc':
                self.dialog = None
            return
        if self.popup:
            self.popup = False
            if key != 'esc':
                self.errors.append(f'key {key!r} while the Selection List is open')
            return
        col, row = self.cursor
        if key in ('tab', 'enter') and self.block and not self.whole_row:
            self.step_inside_block(by_rows=key == 'tab')
        elif key == 'tab':
            origin = self.tab_origin if self.tab_origin is not None else col
            self.go((col+1, row)); self.tab_origin = origin
        elif key == 'enter':
            origin, self.tab_origin = self.tab_origin, None
            self.go((origin if origin is not None else col, row+1))
        elif key == 'esc':
            self.editing = None
        elif key == 'backspace':
            if self.editing is not None:
                self.editing = self.editing[:-1]
            else:
                self.snapshot()
                c1, r1, c2, r2 = self.selection()
                for c in range(c1, c2+1):
                    for r in range(r1, r2+1):
                        self.cells.pop((c, r), None)
        elif key in ('up', 'down', 'left', 'right'):
            dc, dr = {'up': (0, -1), 'down': (0, 1), 'left': (-1, 0), 'right': (1, 0)}[key]
            self.tab_origin = None
            self.go((col+dc, row+dr), extend=self.shift and self.shift_arrows)
        elif key == 'delete':
            if self.editing is None:
                self.errors.append('Delete outside edit mode opens the Delete Contents dialog')
        else:
            self.errors.append(f'stray key {key!r} typed into the sheet')
            self.type(key, self.window)

    def hotkey(self, keys):
        self.keys.append('+'.join(keys))
        if self.dialog == 'Save As' and keys == ('ctrl', 'a'):
            self.field_selected = True
            return
        if self.blocked(f'shortcut {"+".join(keys)}'):
            return
        if keys == ('f2',):
            self.editing = self.cells.get(self.cursor, '')
            return
        self.commit()
        c1, r1, c2, r2 = self.selection()
        if keys == ('ctrl', 'c'):
            self.clip = ([[self.cells.get((c, r), '') for c in range(c1, c2+1)]
                          for r in range(r1, r2+1)], (c1, r1))
            self.clip_text = '\n'.join('\t'.join(self.display(c, r) for c in range(c1, c2+1))
                                       for r in range(r1, r2+1))+'\n'
        elif keys == ('ctrl', 'v'):
            self.paste(c1, r1, c2, r2)
        elif keys == ('ctrl', 'd'):
            # OpenOffice: Ctrl+D is Data > Selection List.  (It is Fill Down only in
            # LibreOffice.)  Nothing is filled; a list drops open under the cell.
            self.popup = True
            self.errors.append('Ctrl+D opened the Selection List; nothing was filled')
        elif keys == ('ctrl', 'z'):
            if self.undo_stack:
                self.redo_stack.append(dict(self.cells)); self.cells = self.undo_stack.pop()
                self.dirty = True
        elif keys == ('ctrl', 'y'):
            if self.redo_stack:
                self.undo_stack.append(dict(self.cells)); self.cells = self.redo_stack.pop()
                self.dirty = True
        elif keys == ('ctrl', 'b'):
            self.bold_rows.update(range(r1, r2+1)); self.dirty = True
        elif keys == ('ctrl', 'shift', '4'):
            self.currency.update((c, r) for c in range(c1, c2+1) for r in range(r1, r2+1))
            self.dirty = True
        elif keys == ('ctrl', 'home'):
            self.tab_origin = None
            self.go((0, 1))
        elif keys == ('shift', 'space'):
            self.whole_row = True
        elif keys == ('ctrl', '-'):
            if self.whole_row:
                self.delete_row(self.cursor[1])
            else:
                self.errors.append('Ctrl+- without a whole row selected opens a dialog')
        elif keys == ('ctrl', 's'):
            if self.ctrl_s:
                self.save()
        elif keys == ('alt', 'f'):
            self.menu = 'File'
        elif keys == ('ctrl', 'shift', 's'):
            self.dialog, self.field = 'Save As', 'Untitled 1'
        elif keys == ('alt', 'd'):
            if self.sort_mode != 'no_menu':
                self.menu = 'Data'
        elif keys == ('ctrl', 'f'):
            self.dialog = 'Find & Replace'
        else:
            self.errors.append(f'unknown shortcut {"+".join(keys)}')

    def paste(self, c1, r1, c2, r2):
        if self.clip is None:
            return
        source, (oc, orow) = self.clip
        if len(source) == 1 and len(source[0]) == 1:
            targets = {(c, r): shift_refs(source[0][0], c-oc, r-orow)
                       for c in range(c1, c2+1) for r in range(r1, r2+1)}
        else:
            targets = {(c1+dc, r1+dr): shift_refs(text, c1-oc, r1-orow)
                       for dr, line in enumerate(source) for dc, text in enumerate(line)}
        def apply():
            self.snapshot(); self.pastes += 1
            for cell, text in targets.items():
                if text:
                    self.cells[cell] = text
            columns, rows = [c for c, _ in targets], [r for _, r in targets]
            self.block = (min(columns), min(rows), max(columns), max(rows))   # stays marked
        if any(self.cells.get(cell) for cell in targets):
            self.question = apply          # "You are pasting data into cells that already contain data"
        else:
            apply()

    def delete_row(self, row):
        self.snapshot()
        moved = {}
        for (c, r), text in self.cells.items():
            if r < row:
                moved[(c, r)] = text
            elif r > row:
                moved[(c, r-1)] = shift_refs(text, 0, -1)
        self.cells = moved
        self.whole_row = False

    def sort(self):
        rows = []
        row = 2
        while self.cells.get((0, row)):
            rows.append(row); row += 1
        column = next((c for c in range(8) if self.cells.get((c, 1)) == self.sort_field), 0)
        ordered = sorted(rows, key=lambda r: self.display(column, r).casefold())
        if self.sort_mode == 'scramble':
            ordered = list(reversed(rows))
        self.snapshot()
        old = dict(self.cells)
        for target, origin in zip(rows, ordered):
            for c in range(8):
                text = old.get((c, origin))
                if text:
                    self.cells[(c, target)] = shift_refs(text, 0, target-origin)
                else:
                    self.cells.pop((c, target), None)

    # --- pointer and screen -------------------------------------------------------------

    def cell_at(self, x, y):
        sheet = self.sheet
        if y < sheet.first_row_top:
            return None
        row = int((y-sheet.first_row_top)//sheet.row_height)+1
        if x <= sheet.side_right:
            return ('row', row)
        col = int((x-sheet.column_left(0))//sheet.col_width)
        return (col, row) if 0 <= col <= LAST_COLUMN else None

    def click(self, x, y):
        self.clicks += 1
        if self.blocked('a click on the sheet'):
            return
        if self.popup:
            self.popup = False             # the click only closes the list
            return
        target = self.cell_at(x, y)
        if target is None:
            return
        self.tab_origin = None
        if target[0] == 'row':
            self.go((0, target[1]))
            self.whole_row = True
        else:
            self.go(target, extend=self.shift)

    def drag(self, x1, y1, x2, y2):
        self.drags += 1
        if self.blocked('a drag on the sheet'):
            return
        self.popup = False
        start, end = self.cell_at(x1, y1), self.cell_at(x2, y2)
        c1, r1, c2, r2 = self.selection()
        if (c1, r1) != (c2, r2) and c1 <= start[0] <= c2 and r1 <= start[1] <= r2:
            self.errors.append('drag started inside a selected block: the block itself is moved')
            return
        self.tab_origin = None
        self.go(start); self.go(end, extend=True)

    def screen(self):
        if not self.grid_visible:
            return None
        c1, r1, c2, r2 = self.selection()
        if self.drawn != (c1, r1, c2, r2):
            self.sheet.select(c1, min(c2, self.sheet.columns-1), r1, r2)
            self.drawn = (c1, r1, c2, r2)
        return self.sheet.pixel

    # --- windows and dialogs ------------------------------------------------------------

    def titles(self):
        name = self.saved_as.name if self.saved_as else 'Untitled 1'
        titles = {1: f'{name} - OpenOffice Calc'}
        if self.menu and not (self.menu == 'File' and self.file_menu == 'unseen'):
            titles[2] = ''
        if self.dialog == 'Save As':
            titles[3] = 'Save As'
        if self.dialog == 'Find & Replace':
            titles[4] = 'Find & Replace'
        if self.dialog == 'Sort':
            titles[5] = 'Sort'
        if self.question or self.closing:
            titles[6] = 'OpenOffice 4.1.16'
        return titles

    def handles(self, pattern=None):
        titles = self.titles()
        return sorted(h for h, title in titles.items() if pattern is None or re.match(pattern, title))

    def find_window(self, pattern):
        found = self.handles(pattern)
        if len(found) != 1:
            return None
        return {1: self.window, 3: self.save_dialog, 4: self.find_dialog}.get(found[0], Mock(handle=found[0]))

    def find_control(self, window, names, kind):
        if window is not self.save_dialog or self.dialog != 'Save As':
            return None
        if kind == 'TreeItem' and 'Documents' in names and self.documents_listed:
            return self.controls['documents']
        if kind == 'ListItem' and self.folder.name in names and self.dialog_folder == self.folder.parent:
            return self.controls['folder']
        if kind == 'Edit' and any(name.startswith('File name') for name in names):
            return self.controls['field']
        if kind == 'Button' and 'Save' in names:
            return self.controls['save']
        return None

    def click_control(self, control, double):
        if control is self.controls['documents']:
            self.dialog_folder = self.folder.parent
        elif control is self.controls['folder'] and double:
            self.dialog_folder = self.folder
        elif control is self.controls['save']:
            self.saved_as = self.dialog_folder/self.field
            self.dialog = None
            self.save()

    def save(self):
        self.commit()
        if self.saved_as is None:
            self.errors.append('Ctrl+S before the first Save As')
            return
        if not self.dirty and self.saves:
            return                         # nothing changed: Calc does not write the file again
        self.write()

    def write(self):
        self.saved_as.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(self.saved_as, 'w') as package:
            package.writestr('content.xml', '<sheet/>')
        self.saves += 1
        os.utime(self.saved_as, (1_700_000_000+self.saves, 1_700_000_000+self.saves))
        self.dirty = False

    def close(self):
        self.commit()
        if self.dirty:
            self.closing = True            # Calc asks before closing; nothing is lost yet
        else:
            self.closed = True
