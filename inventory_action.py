import random
import time

import pyautogui as pg
import win32gui

from domain import HEADERS, INITIAL, Phase, validate_inventory


class InventoryAction:
    def __init__(self, gui, config, budget):
        self.gui, self.config, self.budget = gui, config, budget
        self.path = gui.run_dir/'inventory.ods'
        self.window = None
        self.saved_once = False

    def select(self, address):
        g = self.gui
        g.focus(self.window)
        box = g.find(self.window, ['Name Box', 'Name box', 'Sheet Area', 'Name'])
        # "Sheet Area" is not a name box unless it is an Edit/ComboBox.
        if box and box.element_info.control_type not in ('Edit', 'ComboBox'):
            box = None
        if box:
            g.click(box)
        elif self.config.get('calc_name_box_point'):
            if self.config.get('calibration_resolution') != list(pg.size()):
                raise RuntimeError('Resolution changed since Name Box calibration; recalibrate')
            x, y = self.config['calc_name_box_point']
            rect = self.window.rectangle()
            if not (rect.left < x < rect.right and rect.top < y < rect.bottom):
                raise RuntimeError('Configured name-box coordinates lie outside Calc')
            g.move(x, y); pg.click()
        else:
            raise RuntimeError('Calc Name Box is not accessible. Use Calibrate Name Box from the launcher, then rerun the inventory pilot.')
        g.hotkey('ctrl', 'a')
        g.type_text(address, self.window)
        g.clear_clipboard(); g.hotkey('ctrl', 'c')
        if g.read_clipboard().strip().upper() != address.upper():
            pg.press('esc')
            raise RuntimeError('Cannot verify the Name Box; stopping to avoid editing the wrong cell')
        pg.press('enter'); g.pause()

    def cell(self, address, value, typo=False):
        # The write is idempotent: refocusing/reselecting once cannot add a duplicate row.
        from gui_support import Stopped
        for attempt in range(2):
            try:
                self.select(address)
                self.gui.type_text(value, self.window, corrected_typo=typo)
                pg.press('enter'); self.gui.pause()
                self.gui.event('cell_edited', cell=address, value=str(value))
                return
            except (Stopped, pg.FailSafeException):
                raise
            except Exception:
                if attempt:
                    raise
                self.gui.event('cell_retry', cell=address)
                self.gui.focus(self.window); pg.press('esc')

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
                self.gui.focus(self.window); pg.press('esc')

    def _row(self, row_number, record):
        sku, item, category, qty, price, reorder = record
        values = [sku, item, category, qty, price, f'=D{row_number}*E{row_number}',
                  reorder, f'=IF(D{row_number}<G{row_number};"YES";"NO")']
        self.select(f'A{row_number}')
        for index, value in enumerate(values):
            self.gui.type_text(value, self.window,
                               corrected_typo=(index == 1 and random.random() < 0.3))
            pg.press('tab' if index < 7 else 'enter'); self.gui.pause()
        self.gui.event('inventory_row_added', row=row_number, sku=sku)

    def menu(self, *names):
        g = self.gui
        g.focus(self.window)
        for i, name in enumerate(names):
            # Menu popups may live outside the Calc window in the UIA tree.
            if i == 0:
                g.click_named(self.window, [name], 'MenuItem')
            else:
                def find_menu():
                    found = g.find(self.window, [name], 'MenuItem')
                    if found:
                        return found
                    for popup in g.desktop.windows(visible_only=True):
                        found = g.find(popup, [name], 'MenuItem')
                        if found:
                            return found
                    return None
                g.click(g.wait(find_menu, f'menu {name}', 8))

    def save(self):
        g = self.gui
        if self.saved_once:
            g.hotkey('ctrl', 's', window=self.window)
        else:
            g.hotkey('ctrl', 'shift', 's', window=self.window)
            dialog = g.window(r'^Save As$', 15)
            edit = g.find(dialog, ['File name', 'File name:'], 'Edit')
            if not edit:
                # The standard Windows Save As edit identifier.
                candidates = dialog.descendants(control_type='Edit')
                edit = next((c for c in candidates if c.element_info.automation_id == '1001'), None)
            if not edit:
                raise RuntimeError('Cannot identify Save As File name field')
            g.click(edit); g.hotkey('ctrl', 'a'); g.type_text(str(self.path), dialog)
            g.click_named(dialog, ['Save'], 'Button')
            g.wait(lambda: self.path.exists(), 'new ODS file saved', 30)
            self.window = g.window(r'.*inventory.*Calc.*', 15)
            self.saved_once = True
        # Saving must produce a readable ODS; a format prompt is not bypassed blindly.
        g.wait(self._zip_ready, 'saved ODS package', 20)
        g.event('workbook_saved', path=str(self.path))

    def _zip_ready(self):
        import zipfile
        try:
            with zipfile.ZipFile(self.path) as z:
                return 'content.xml' in z.namelist()
        except (OSError, zipfile.BadZipFile):
            return False

    def format_and_sort(self):
        g = self.gui
        self.select('A1:H1'); g.hotkey('ctrl', 'b', window=self.window)
        self.select('A1:H8')
        self.menu('Format', 'Column', 'Optimal Width...')
        dialog = g.window(r'.*Optimal.*Width.*', 10)
        g.click_named(dialog, ['OK'], 'Button')
        self.select('E2:F8'); g.hotkey('ctrl', 'shift', '4', window=self.window)
        self.select('A1:H8')
        self.menu('Data', 'Sort...')
        dialog = g.window(r'^Sort$', 10)
        tab = g.find(dialog, ['Options'], 'TabItem')
        if not tab:
            raise RuntimeError('Sort Options tab missing')
        g.click(tab)
        header = g.find(dialog, ['Range contains column labels'], 'CheckBox')
        if not header:
            raise RuntimeError('Cannot confirm Sort header setting')
        if header.get_toggle_state() == 0:
            g.click(header)
        g.click_named(dialog, ['Sort Criteria'], 'TabItem')
        combos = [c for c in dialog.descendants(control_type='ComboBox') if c.is_visible()]
        if not combos:
            raise RuntimeError('Sort field selector not accessible')
        field = sorted(combos, key=lambda c: c.rectangle().top)[0]
        g.click(field)
        # Open dropdown and select a labelled Item entry, not a guessed index.
        g.hotkey('alt', 'down')
        choice = None
        for popup in g.desktop.windows(visible_only=True):
            choice = g.find(popup, ['Item'], 'ListItem')
            if choice:
                break
        if not choice:
            raise RuntimeError('Sort-by-Item choice missing')
        g.click(choice)
        ascending = g.find(dialog, ['Ascending'], 'RadioButton')
        if not ascending:
            raise RuntimeError('Ascending sort control missing')
        g.click(ascending); g.click_named(dialog, ['OK'], 'Button')
        g.event('inventory_sorted', column='Item', direction='ascending')

    def run(self, seconds):
        g = self.gui
        phase = Phase(seconds)
        self.window = g.launch_shortcut(self.config['calc_shortcut'], r'.*Calc.*')
        self.select('A1')
        for i, header in enumerate(HEADERS):
            g.type_text(header, self.window); pg.press('tab' if i < 7 else 'enter')
        self.save()
        for row, record in enumerate(INITIAL, 2):
            self.row(row, record)
            if row in (4, 7):
                self.save()
            g.pause(reading=True)
        self.cell('D5', 9); self.cell('E4', 210)
        self.row(8, ('INV-007', 'Laptop Stand', 'Accessories', 7, 32, 3))
        self.row(9, ('INV-TEMP', 'Temporary Item', 'Test', 1, 1, 1))
        self.select('A9:H9')
        self.menu('Sheet', 'Delete Rows') if g.find(self.window, ['Sheet'], 'MenuItem') else self.delete_row_openoffice()
        self.save()
        self.format_and_sort()
        summaries = [(11, 'Total stock value', '=SUM(F2:F8)'),
                     (12, 'Average item value', '=AVERAGE(F2:F8)'),
                     (13, 'Minimum item value', '=MIN(F2:F8)'),
                     (14, 'Maximum item value', '=MAX(F2:F8)'),
                     (15, 'Budget SGD', self.budget),
                     (16, 'Budget result', '=IF(F11<=F15;"WITHIN BUDGET";"OVER BUDGET")')]
        for row, label, value in summaries:
            self.cell(f'E{row}', label); self.cell(f'F{row}', value)
        self.select('F11:F15'); g.hotkey('ctrl', 'shift', '4', window=self.window)
        self.save()
        # Productive review until the deadline; data edits are reversible and saved.
        while not phase.expired:
            self.select(random.choice(['A2:H4', 'A5:H8', 'E11:F16', 'D2:F8']))
            g.pause(reading=True)
            g.event('inventory_review')
            self.save()
        self.save()
        g.close_window(self.window)
        self.window = None
        result = validate_inventory(self.path, self.budget)
        # Reopen by double-clicking the saved file in Explorer, through the shell.
        g.hotkey('win', 'e')
        def foreground_explorer():
            handle = win32gui.GetForegroundWindow()
            if win32gui.GetClassName(handle) == 'CabinetWClass':
                return g.desktop.window(handle=handle).wrapper_object()
            return None
        explorer = g.wait(foreground_explorer, 'foreground File Explorer', 20)
        g.hotkey('ctrl', 'l', window=explorer); g.type_text(str(g.run_dir), explorer); pg.press('enter')
        file_icon = g.wait(lambda: g.find(explorer, ['inventory.ods', 'inventory'], 'ListItem'), 'saved workbook icon', 15)
        g.click(file_icon, double=True)
        self.window = g.window(r'.*inventory.*Calc.*', 30)
        g.focus(self.window); self.select('E11:F16')
        g.event('inventory_phase_complete', elapsed=time.monotonic()-phase.started, validation=result)
        return result

    def delete_row_openoffice(self):
        g = self.gui
        self.menu('Edit', 'Delete Cells...')
        dialog = g.window(r'.*Delete Cells.*', 10)
        g.click_named(dialog, ['Delete entire row(s)', 'Delete entire row'], 'RadioButton')
        g.click_named(dialog, ['OK'], 'Button')
