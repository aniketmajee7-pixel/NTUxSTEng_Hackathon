"""Calc fallbacks with a fake GUI; no real Calc or input is used."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from domain import FINAL, Grid
from fake_sheet import FakeSheet

ENTRY = sorted(FINAL, key=lambda record: record[0])   # the order records are entered in


def load_inventory():
    pg = types.ModuleType('pyautogui')
    pg.press = Mock(); pg.FailSafeException = type('FailSafeException', (Exception,), {})
    support = types.ModuleType('gui_support')
    support.Stopped = type('Stopped', (Exception,), {})
    support.TimeUp = type('TimeUp', (support.Stopped,), {})
    spec = importlib.util.spec_from_file_location(
        'inventory_under_test', Path(__file__).resolve().parents[1]/'inventory_action.py')
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'pyautogui': pg, 'win32gui': types.ModuleType('win32gui'),
                                  'gui_support': support}):
        spec.loader.exec_module(module)
        module.Stopped = support.Stopped
    return module, pg, support


class InventoryFallbackTests(unittest.TestCase):
    def setUp(self):
        self.module, self.pg, self.support = load_inventory()
        patcher = patch.dict(sys.modules, {'gui_support': self.support, 'pyautogui': self.pg})
        patcher.start(); self.addCleanup(patcher.stop)
        self.gui = Mock(run_dir=Path('/tmp/run'))
        self.inv = self.module.InventoryAction(self.gui, {}, 5000)
        self.inv.window = Mock()
        self.inv.select = Mock()

    def test_temp_row_deleted_with_ctrl_minus_without_touching_menus(self):
        self.inv.cell_text = Mock(side_effect=['', ''])
        self.inv.menu = Mock()
        self.inv.remove_temp_row()
        keys = [c.args for c in self.gui.hotkey.call_args_list]
        self.assertIn(('shift', 'space'), keys)
        self.assertIn(('ctrl', '-'), keys)
        self.inv.menu.assert_not_called()
        self.assertTrue(any(c.kwargs.get('method') == 'ctrl_minus_entire_row'
                            for c in self.gui.event.call_args_list))

    def test_dialog_then_clear_fallbacks_when_row_survives(self):
        self.inv.cell_text = Mock(return_value='INV-TEMP')
        self.inv.delete_row_openoffice = Mock(side_effect=RuntimeError('Timed out waiting for Edit'))
        self.gui.window_handles.return_value = []
        self.inv.remove_temp_row()
        self.inv.delete_row_openoffice.assert_called_once()
        self.gui.press.assert_any_call('backspace')      # OpenOffice clears with Backspace
        self.assertTrue(any(c.args[0] == 'temp_row_cleared' for c in self.gui.event.call_args_list))

    def test_menu_timeout_in_a_step_no_longer_fails_the_run(self):
        ok = self.inv.attempt('optimal_width', Mock(side_effect=RuntimeError("Timed out waiting for ['Format']")))
        self.assertFalse(ok)
        self.assertTrue(any(c.args[0] == 'step_failed' for c in self.gui.event.call_args_list))

    def test_stop_requests_still_propagate_from_steps(self):
        with self.assertRaises(self.support.Stopped):
            self.inv.attempt('x', Mock(side_effect=self.support.Stopped('F8')))

    def table(self, records):
        return [[str(v) for v in self.inv.shown(r)] for r in records]

    def test_failed_sort_never_retypes_the_table(self):
        """The recording showed seven rows being typed again "in sorted order"."""
        self.inv.sort_dialog = Mock(side_effect=RuntimeError('Data menu did not open'))
        self.inv.table_snapshot = Mock(return_value=self.table(ENTRY))   # unchanged
        self.inv.row = Mock(); self.inv.cell = Mock(); self.inv.verify_table = Mock()
        self.assertFalse(self.inv.sort_by_item())
        self.inv.row.assert_not_called(); self.inv.cell.assert_not_called()
        self.inv.verify_table.assert_not_called()
        self.assertFalse(self.inv.sorted)
        self.assertEqual(self.inv.order, ENTRY)
        self.assertTrue(any(c.args[0] == 'sort_skipped' for c in self.gui.event.call_args_list))

    def test_confirmed_sort_updates_the_known_order_without_typing(self):
        self.inv.sort_dialog = Mock()
        self.inv.table_snapshot = Mock(return_value=self.table(FINAL))
        self.inv.row = Mock(); self.inv.cell = Mock()
        self.assertTrue(self.inv.sort_by_item())
        self.inv.row.assert_not_called(); self.inv.cell.assert_not_called()
        self.assertTrue(self.inv.sorted)
        self.assertEqual(self.inv.order, FINAL)

    def test_a_scrambled_sort_is_undone_and_only_then_checked(self):
        scrambled = self.table(list(reversed(ENTRY)))
        self.inv.sort_dialog = Mock()
        self.inv.table_snapshot = Mock(return_value=scrambled)
        self.inv.verify_table = Mock(); self.inv.row = Mock()
        self.assertFalse(self.inv.sort_by_item())
        self.gui.hotkey.assert_any_call('ctrl', 'z', window=self.inv.window)
        self.inv.verify_table.assert_called_once()
        self.inv.row.assert_not_called()

    def test_sort_keys_are_only_sent_once_the_menu_and_dialog_are_really_open(self):
        """A stray "s" or "i" must never be typed into a cell."""
        self.inv.select = Mock()
        self.gui.window_handles.return_value = [1, 2]          # no new popup after Alt+D
        with self.assertRaises(RuntimeError):
            self.inv.sort_dialog()
        self.gui.press.assert_not_called()
        # Menu opens, but "s" does not bring up the Sort dialog: stop before "i".
        self.gui.window_handles.return_value = None
        self.gui.window_handles.side_effect = [[1, 2], [1, 2, 3]]
        self.gui.wait.side_effect = RuntimeError('Timed out waiting for Sort dialog')
        with self.assertRaises(RuntimeError):
            self.inv.sort_dialog()
        self.assertEqual([c.args[0] for c in self.gui.press.call_args_list], ['s'])


if __name__ == '__main__':
    unittest.main()


class ReuseTests(unittest.TestCase):
    def setUp(self):
        self.module, self.pg, self.support = load_inventory()
        patcher = patch.dict(sys.modules, {'gui_support': self.support, 'pyautogui': self.pg})
        patcher.start(); self.addCleanup(patcher.stop)
        self.gui = Mock(run_dir=Path('/tmp/run'))
        self.inv = self.module.InventoryAction(
            self.gui, {'spreadsheet_typo_scale': 0, 'fill_down_probability': 0}, 5000)
        self.inv.window = Mock()
        self.inv.ranges_work = True              # as after a confirmed serial-number paste
        self.gui.window_handles.return_value = []
        self.log = []
        self.inv.select = Mock(side_effect=lambda a, **kw: self.log.append(('select', a)))
        self.gui.hotkey.side_effect = lambda *k, **kw: self.log.append(('key', k))
        self.gui.type_text.side_effect = lambda v, w, **kw: self.log.append(('type', str(v)))
        self.gui.press.side_effect = lambda k: self.log.append(('press', k))

    def typed(self):
        return [v for kind, v in self.log if kind == 'type']

    def test_first_serial_is_copied_down_then_only_last_digit_is_retyped(self):
        self.gui.read_clipboard.return_value = '\n'.join(['INV-001']*7)
        self.inv.fill_sku(3, 8)
        self.assertEqual(self.log[:4], [('select', 'A2'), ('key', ('ctrl', 'c')),
                                        ('select', 'A3:A8'), ('key', ('ctrl', 'v'))])
        self.assertEqual(self.log[4], ('select', 'A2:A8'))     # one look at the whole column
        self.assertTrue(self.inv.sku_pasted)
        self.log.clear()
        self.inv.row_reuse(5, ('INV-004', 'USB Hub', 'Peripherals', 4, 28, 5))
        self.assertEqual(self.log[:5], [('select', 'A5'), ('key', ('f2',)), ('press', 'backspace'),
                                        ('type', '4'), ('press', 'tab')])

    def test_unconfirmed_paste_means_serials_are_typed_in_full(self):
        """In the recording the paste landed on one cell and rows got just "2", "3"."""
        self.gui.read_clipboard.return_value = 'INV-001\n\n\n\n\nINV-001\n'
        self.inv.fill_sku(3, 8)
        self.assertFalse(self.inv.sku_pasted)
        self.log.clear()
        self.inv.row_reuse(4, ('INV-003', 'Monitor', 'Displays', 6, 220, 3))
        self.assertEqual(self.log[:3], [('select', 'A4'), ('type', 'INV-003'), ('press', 'tab')])
        self.assertNotIn(('key', ('f2',)), self.log)

    def test_when_ranges_cannot_be_selected_every_record_is_typed_in_full(self):
        """The last resort is what the VM is known to do correctly: one cell at a time,
        formulas included.  Nothing is pasted, so nothing is left half-filled."""
        self.gui.read_clipboard.return_value = 'INV-001\n'          # the "range" was one cell
        self.inv.fill_sku(3, 8)
        self.assertFalse(self.inv.ranges_work)
        self.inv.row = Mock(); self.inv.row_reuse = Mock()
        record = ('INV-002', 'Mouse', 'Accessories', 20, 18, 8)
        self.inv.reuse_row_with_retry(3, record)
        self.inv.row.assert_called_once_with(3, record)
        self.inv.row_reuse.assert_not_called()

    def test_rows_typed_in_full_are_never_pasted_over(self):
        """Pasting onto filled cells makes Calc ask whether to overwrite."""
        self.inv.full_rows = {5}
        self.inv.fill_formulas(3, 7)
        self.assertEqual([a for kind, a in self.log if kind == 'select'],
                         ['F2', 'F3:F4', 'F6:F7', 'H2', 'H3:H4', 'H6:H7'])
        self.log.clear()
        self.inv.full_rows = {8}
        self.inv.fill_formulas(8, 8)                 # that row already has both formulas
        self.assertEqual(self.log, [])

    def test_ctrl_d_is_never_used(self):
        """In OpenOffice Ctrl+D opens the Selection List; it is Fill Down only in
        LibreOffice.  In the VM a list dropped open and column H stayed empty."""
        self.inv.config['fill_down_probability'] = 1          # an old setting: now ignored
        self.inv.fill_formulas(3, 7)
        self.inv.fill_formulas(8, 8)
        keys = [k for kind, k in self.log if kind == 'key']
        self.assertNotIn(('ctrl', 'd'), keys)
        self.assertEqual(keys.count(('ctrl', 'v')), 4)

    def test_an_overwrite_question_is_answered_instead_of_left_open(self):
        self.inv.paste_into('C3')
        self.assertNotIn(('press', 'y'), self.log)
        self.gui.window_handles.return_value = [55]
        self.inv.paste_into('C4')
        self.assertEqual(self.log[-1], ('press', 'y'))

    def test_repeated_category_is_pasted_not_typed(self):
        self.inv.categories = {'Accessories': 'C2'}
        self.inv.sku_pasted = True
        self.inv.row_reuse(3, ('INV-002', 'Mouse', 'Accessories', 20, 18, 8))
        self.assertNotIn('Accessories', self.typed())
        selects = [a for kind, a in self.log if kind == 'select']
        self.assertEqual(selects, ['A3', 'C2', 'C3'])          # row start, copy source, paste target
        self.assertEqual(self.typed(), ['2', 'Mouse', '20', '18', '8'])
        self.assertEqual([k for kind, k in self.log if kind == 'key'],
                         [('f2',), ('ctrl', 'c'), ('ctrl', 'v')])

    def test_item_is_committed_before_going_to_copy_the_category(self):
        """Without the mouse the next move starts with Esc, which would throw the item away."""
        self.inv.categories = {'Accessories': 'C2'}
        self.inv.row_reuse(3, ('INV-002', 'Mouse', 'Accessories', 20, 18, 8))
        item = self.log.index(('type', 'Mouse'))
        self.assertEqual(self.log[item+1], ('press', 'tab'))
        self.assertEqual(self.log[item+2], ('select', 'C2'))

    def test_after_a_paste_the_next_cell_is_reached_with_an_arrow_or_a_click_never_tab(self):
        """A pasted cell stays marked and Tab only moves inside the marked cells.  In the
        VM the quantity and price typed after a pasted category both landed on it."""
        self.inv.categories = {'Accessories': 'C2'}
        self.inv.row_reuse(3, ('INV-002', 'Mouse', 'Accessories', 20, 18, 8))
        paste = self.log.index(('key', ('ctrl', 'v')))
        self.assertEqual(self.log[paste+1], ('press', 'right'))
        self.assertEqual(self.log[paste+2], ('type', '20'))
        # With the mouse available, the next cell is clicked instead.
        self.log.clear()
        self.inv.grid = sheet_grid()
        with patch.object(self.module.random, 'random', return_value=0.99):   # Tab everywhere else
            self.inv.row_reuse(5, ('INV-004', 'USB Hub', 'Accessories', 4, 28, 5))
        paste = self.log.index(('key', ('ctrl', 'v')))
        self.assertEqual(self.log[paste+1], ('select', 'D5'))
        self.assertEqual(self.log[paste+2], ('type', '4'))

    def test_every_row_starts_with_selecting_its_first_cell(self):
        for row, record in enumerate(ENTRY[1:6], 3):
            self.log.clear()
            self.inv.row_reuse(row, record)
            self.assertEqual(self.log[0], ('select', f'A{row}'))

    def test_formula_column_is_skipped_while_entering_a_row(self):
        self.inv.row_reuse(4, ('INV-003', 'Monitor', 'Displays', 6, 220, 3))
        self.assertFalse(any(v.startswith('=') for v in self.typed()))
        presses = [k for kind, k in self.log if kind == 'press']
        self.assertEqual(presses[-3:], ['tab', 'tab', 'enter'])   # E -> F -> G, then finish the row

    def test_new_category_is_typed_and_remembered_for_later_rows(self):
        self.inv.cell_text = Mock(return_value='INV-004')
        self.inv.row_reuse(4, ('INV-003', 'Monitor', 'Displays', 6, 220, 3))
        self.assertIn('Displays', self.typed())
        self.assertEqual(self.inv.categories['Displays'], 'C4')

    def test_formulas_are_copied_down_not_retyped(self):
        self.inv.fill_formulas(3, 7)
        pasted = [a for kind, a in self.log if kind == 'select']
        self.assertEqual(pasted, ['F2', 'F3:F7', 'H2', 'H3:H7'])
        self.assertEqual(self.typed(), [])

    def test_failed_reuse_retries_then_types_the_whole_record(self):
        self.inv.row_reuse = Mock(side_effect=RuntimeError('clipboard'))
        self.inv.row = Mock()
        record = ('INV-002', 'Mouse', 'Accessories', 20, 18, 8)
        self.inv.reuse_row_with_retry(3, record)
        # No second paste attempt: that would hit filled cells and raise an overwrite prompt.
        self.assertEqual(self.inv.row_reuse.call_count, 1)
        self.inv.row.assert_called_once_with(3, record)


class ReviewWorkTests(unittest.TestCase):
    """The filler that used to be select-and-save is now real, reversible work."""

    def setUp(self):
        self.module, self.pg, self.support = load_inventory()
        patcher = patch.dict(sys.modules, {'gui_support': self.support, 'pyautogui': self.pg})
        patcher.start(); self.addCleanup(patcher.stop)
        self.gui = Mock(run_dir=Path('/tmp/run')); self.gui.pace = 1.0
        self.inv = self.module.InventoryAction(
            self.gui, {'spreadsheet_typo_scale': 0, 'fill_down_probability': 0}, 5000)
        self.inv.window = Mock()
        self.inv.select = Mock(); self.inv.cell = Mock(); self.inv.save = Mock()

    def phase(self, rounds):
        """A phase with time for `rounds` activities (remaining is read twice per round)."""
        ticks = iter([1000]*(2*rounds))
        phase = Mock()
        type(phase).remaining = property(lambda _: next(ticks, 0))
        return phase

    def only(self, name):
        """Force review_until to pick one activity."""
        target = getattr(self.inv, name)
        return patch.object(self.module.random, 'choices', side_effect=lambda options, **_: [
            next(o for o in options if getattr(o, '__name__', '') == name)]), target

    def test_looking_around_never_saves(self):
        for name in ('walk_cells', 'check_totals'):
            self.inv.save.reset_mock()
            chooser, _ = self.only(name)
            with chooser:
                self.inv.review_until(self.phase(4))
            self.inv.save.assert_not_called()
            self.inv.cell.assert_not_called()

    def test_a_note_is_a_real_edit_and_is_saved_afterwards(self):
        chooser, _ = self.only('add_note')
        with chooser:
            self.inv.review_until(self.phase(3))
        self.assertEqual(self.inv.save.call_count, 3)
        cells = [c.args[0] for c in self.inv.cell.call_args_list]
        self.assertEqual(cells[0], 'J1')                       # header typed once
        self.assertEqual(cells.count('J1'), 1)
        self.assertTrue(all(c.startswith('J') for c in cells)) # never touches validated cells
        self.assertEqual(len(self.inv.notes), 3)

    def test_notes_never_use_the_serial_number_column_or_inv_prefix(self):
        self.assertFalse(any(n.startswith('INV-') for n in self.inv.NOTE_CHOICES))

    def test_what_if_edit_is_undone_and_confirmed(self):
        with patch.object(self.module.random, 'randint', side_effect=[4, 3]), \
             patch.object(self.module.random, 'random', return_value=0.9):
            self.inv.cell_text = Mock(return_value=str(ENTRY[2][3]))   # undo worked
            self.inv.what_if_quantity()
        self.inv.cell.assert_called_once_with('D4', ENTRY[2][3]+3)
        self.gui.hotkey.assert_any_call('ctrl', 'z', window=self.inv.window)
        self.assertEqual(self.inv.pending, {})

    def test_if_undo_did_not_take_the_original_value_is_typed_back(self):
        with patch.object(self.module.random, 'randint', side_effect=[4, 3]), \
             patch.object(self.module.random, 'random', return_value=0.9):
            self.inv.cell_text = Mock(return_value=str(ENTRY[2][3]+3))  # still the trial value
            self.inv.what_if_quantity()
        self.assertEqual(self.inv.cell.call_args_list[-1].args, ('D4', ENTRY[2][3]))
        self.assertEqual(self.inv.pending, {})

    def test_interrupted_what_if_is_restored_before_the_final_save(self):
        def explode(*a, **k):
            raise RuntimeError('focus lost')
        self.inv.pending['D4'] = ENTRY[2][3]
        self.inv.cell_text = Mock(return_value='999')
        self.inv.review_until(self.phase(0))
        self.inv.cell.assert_called_once_with('D4', ENTRY[2][3])
        self.assertEqual(self.inv.pending, {})

    def test_find_dialog_is_opened_used_and_closed(self):
        dialog = Mock()
        self.gui.window.return_value = dialog
        self.gui.window_handles.return_value = []
        self.assertFalse(self.inv.find_item())
        self.gui.hotkey.assert_called_once_with('ctrl', 'f', window=self.inv.window)
        self.assertIs(self.gui.type_text.call_args.args[1], dialog)
        self.assertEqual([c.args[0] for c in self.gui.press.call_args_list], ['enter', 'esc'])
        self.gui.focus.assert_called_with(self.inv.window)

    def test_a_failing_activity_is_contained_and_the_review_continues(self):
        self.inv.find_item = Mock(side_effect=RuntimeError('no dialog')); self.inv.find_item.__name__ = 'find_item'
        chooser, _ = self.only('find_item')
        with chooser:
            self.inv.review_until(self.phase(2))
        self.assertEqual(self.inv.find_item.call_count, 2)
        self.assertTrue(any(c.args[0] == 'step_failed' for c in self.gui.event.call_args_list))

    def test_one_new_row_gets_its_formulas_from_the_row_above(self):
        self.inv.copy_cell = Mock(); self.inv.paste_into = Mock()
        self.inv.fill_formulas(8, 8)
        self.assertEqual([c.args[0] for c in self.inv.copy_cell.call_args_list], ['F7', 'H7'])
        self.assertEqual([c.args[0] for c in self.inv.paste_into.call_args_list], ['F8', 'H8'])

    def test_failing_to_reopen_does_not_fail_a_saved_and_validated_run(self):
        self.inv.window = None
        self.inv.reopen_from_explorer = Mock(side_effect=RuntimeError('Explorer not found'))
        self.assertFalse(self.inv.attempt('reopen_workbook', self.inv.reopen_from_explorer))
        self.gui.focus.assert_not_called()      # no window to focus; must not raise


def rect(left, top, right, bottom):
    r = Mock(left=left, top=top, right=right, bottom=bottom)
    r.width.return_value = right-left; r.height.return_value = bottom-top
    return r


def cell(name, left, top, kind='DataItem', visible=True):
    c = Mock(); c.element_info.control_type = kind
    c.window_text.return_value = name
    c.rectangle.return_value = rect(left, top, left+85, top+17)
    c.is_visible.return_value = visible
    return c


class PointerSelectionTests(unittest.TestCase):
    def setUp(self):
        self.module, self.pg, self.support = load_inventory()
        self.pg.keyDown = Mock(); self.pg.keyUp = Mock()
        patcher = patch.dict(sys.modules, {'gui_support': self.support, 'pyautogui': self.pg})
        patcher.start(); self.addCleanup(patcher.stop)
        self.gui = Mock(run_dir=Path('/tmp/run'))
        self.inv = self.module.InventoryAction(self.gui, {}, 5000)
        self.inv.window = Mock()
        self.inv.window.rectangle.return_value = rect(0, 0, 1920, 1040)
        self.cells = {}
        self.inv.window.descendants.side_effect = lambda title: self.cells.get(title, [])

    def test_cell_is_clicked_instead_of_typing_its_address(self):
        b3 = cell('B3', 200, 300)
        self.cells['B3'] = [b3]
        self.inv.select('B3')
        self.gui.click.assert_called_once_with(b3, window=self.inv.window)
        self.gui.type_text.assert_not_called()         # no address typed in the Name Box
        self.gui.find.assert_not_called()

    def test_range_is_click_then_shift_click(self):
        a1, h1 = cell('A1', 40, 200), cell('H1', 640, 200)
        self.cells.update({'A1': [a1], 'H1': [h1]})
        self.inv.select('A1:H1')
        self.assertEqual([c.args[0] for c in self.gui.click.call_args_list], [a1, h1])
        self.pg.keyDown.assert_called_once_with('shift')
        self.pg.keyUp.assert_called_once_with('shift')

    def test_shift_is_released_if_the_second_click_fails(self):
        a1, h1 = cell('A1', 40, 200), cell('H1', 640, 200)
        self.cells.update({'A1': [a1], 'H1': [h1]})
        self.gui.click.side_effect = [None, RuntimeError('moved')]
        self.assertFalse(self.inv.pointer_select('A1:H1'))
        self.pg.keyUp.assert_called_once_with('shift')

    def test_name_box_and_toolbar_controls_with_the_same_name_are_never_clicked(self):
        self.cells['B3'] = [cell('B3', 60, 120, kind='Edit'), cell('B3', 60, 120, kind='ComboBox')]
        self.assertIsNone(self.inv.cell_element('B3'))

    def test_offscreen_or_implausibly_sized_elements_are_rejected(self):
        hidden = cell('B3', 200, 300, visible=False)
        outside = cell('B3', 3000, 300)
        huge = cell('B3', 200, 300); huge.rectangle.return_value = rect(0, 0, 1900, 1000)
        self.cells['B3'] = [hidden, outside, huge]
        self.assertIsNone(self.inv.cell_element('B3'))

    def test_when_calc_exposes_no_cells_it_stops_asking_and_uses_the_old_way(self):
        self.gui.find.return_value = None
        self.inv.config['calc_name_box_point'] = None
        self.assertFalse(self.inv.pointer_select('A1'))
        self.assertIsNone(self.inv.cells_exposed)          # one empty answer is not proof
        self.assertFalse(self.inv.pointer_select('A1'))
        self.assertFalse(self.inv.pointer_select('B3'))
        self.assertIs(self.inv.cells_exposed, False)
        calls = self.inv.window.descendants.call_count
        self.assertFalse(self.inv.pointer_select('C3'))
        self.assertEqual(self.inv.window.descendants.call_count, calls)   # no repeated queries
        self.assertEqual(sum(c.args[0] == 'pointer_selection_unavailable'
                             for c in self.gui.event.call_args_list), 1)

    def test_a_cell_scrolled_out_of_view_falls_back_without_disabling_the_pointer(self):
        self.cells['A1'] = [cell('A1', 40, 200)]
        self.assertTrue(self.inv.pointer_select('A1'))
        self.assertFalse(self.inv.pointer_select('J40'))     # not on screen right now
        self.assertIs(self.inv.cells_exposed, True)

    def test_setting_can_switch_pointer_selection_off(self):
        self.cells['B3'] = [cell('B3', 200, 300)]
        self.inv.config['calc_pointer_selection'] = False
        self.gui.find.return_value = None
        try:
            self.inv.select('B3')
        except Exception:
            pass
        self.gui.click.assert_not_called()


class ClipboardAndFormulaSafetyTests(unittest.TestCase):
    def setUp(self):
        self.module, self.pg, self.support = load_inventory()
        self.pg.keyDown = Mock(); self.pg.keyUp = Mock(); self.pg.size = Mock(return_value=(1920, 1080))
        patcher = patch.dict(sys.modules, {'gui_support': self.support, 'pyautogui': self.pg})
        patcher.start(); self.addCleanup(patcher.stop)
        self.gui = Mock(run_dir=Path('/tmp/run')); self.gui.pace = 1.0
        self.inv = self.module.InventoryAction(
            self.gui, {'calc_pointer_selection': False, 'fill_down_probability': 0,
                       'spreadsheet_typo_scale': 0}, 5000)
        self.inv.window = Mock()
        self.inv.window.rectangle.return_value = rect(0, 0, 1920, 1040)
        self.box = Mock(); self.box.element_info.control_type = 'ComboBox'
        self.box.is_visible.return_value = True
        self.gui.find.return_value = self.box

    def test_moving_to_the_paste_target_never_touches_the_clipboard(self):
        """The Name Box check copies the typed address; doing that between Copy and
        Paste made the paste insert the address text instead of the copied cell."""
        self.gui.read_clipboard.return_value = 'A2'
        self.inv.copy_cell('A2')
        self.gui.clear_clipboard.reset_mock(); self.gui.read_clipboard.reset_mock()
        self.gui.type_text.reset_mock(); self.gui.hotkey.reset_mock()
        self.inv.paste_into('A3:A7')
        self.gui.clear_clipboard.assert_not_called()
        self.gui.read_clipboard.assert_not_called()
        self.gui.type_text.assert_not_called()                       # no address typed
        copies = [c for c in self.gui.hotkey.call_args_list if c.args == ('ctrl', 'c')]
        self.assertEqual(copies, [])
        self.assertEqual(self.gui.hotkey.call_args_list[-1].args, ('ctrl', 'v'))

    def test_keyboard_route_reaches_the_right_cell_and_extends_the_range(self):
        keys = []
        self.gui.press.side_effect = keys.append
        self.inv.keyboard_select('C3:C5')
        self.assertEqual(keys, ['esc', 'down', 'down', 'right', 'right', 'down', 'down'])
        self.pg.keyDown.assert_called_once_with('shift')
        self.pg.keyUp.assert_called_once_with('shift')

    def test_sheet_is_clicked_for_focus_once_not_before_every_move(self):
        """Clicking the middle of the sheet before each move made the Name Box flash H20."""
        self.inv.keyboard_select('A2'); self.inv.keyboard_select('B2'); self.inv.keyboard_select('C2')
        self.assertEqual(self.gui.click_here.call_count, 1)
        self.inv.attempt('format', Mock(side_effect=RuntimeError('dialog in the way')))
        self.inv.keyboard_select('A3')
        self.assertEqual(self.gui.click_here.call_count, 2)       # focus is uncertain again

    def test_name_box_is_looked_up_once_then_reused(self):
        self.gui.read_clipboard.side_effect = ['B2', 'C4']
        self.inv.select('B2'); self.inv.select('C4')
        self.assertEqual(self.gui.find.call_count, 1)

    def table(self, records):
        return [[str(v) for v in self.inv.shown(r)] for r in records]

    def test_wrong_values_are_retyped_and_missing_formulas_are_pasted(self):
        table = self.table(ENTRY)
        table[1][7] = ''; table[2][7] = ''; table[4][7] = ''      # H3, H4, H6: as in the VM run
        table[3][0] = '4'                                          # A5: only the digit landed
        self.inv.table_snapshot = Mock(return_value=table)
        self.inv.cell = Mock(); self.inv.copy_cell = Mock(); self.inv.paste_into = Mock()
        self.inv.clear_contents = Mock()
        self.assertEqual(self.inv.verify_table(), 4)
        self.inv.cell.assert_called_once_with('A5', 'INV-004')     # a value: typed
        self.inv.copy_cell.assert_called_once_with('H2')           # a formula: copied once ...
        self.assertEqual([c.args[0] for c in self.inv.paste_into.call_args_list], ['H3:H4', 'H6'])
        self.assertEqual([c.args[0] for c in self.inv.clear_contents.call_args_list], ['H3:H4', 'H6'])
        typed = [c.args[1] for c in self.inv.cell.call_args_list]
        self.assertFalse(any(str(v).startswith('=') for v in typed))   # no formula is typed out

    def test_a_formula_that_only_looked_wrong_because_of_its_inputs_is_left_alone(self):
        broken = self.table(ENTRY)
        broken[6][3] = ''; broken[6][5] = '0'                      # D8 empty, so F8 shows 0
        self.inv.table_snapshot = Mock(side_effect=[broken, self.table(ENTRY)])
        self.inv.cell = Mock(); self.inv.repair_formulas = Mock()
        self.assertEqual(self.inv.verify_table(), 1)
        self.inv.cell.assert_called_once_with('D8', ENTRY[6][3])
        self.inv.repair_formulas.assert_not_called()
        self.assertEqual(self.inv.table_snapshot.call_count, 2)    # one more look after the fix

    def test_if_no_row_has_the_formula_one_is_typed_and_the_rest_pasted(self):
        self.inv.cell = Mock(); self.inv.copy_cell = Mock(); self.inv.paste_into = Mock()
        self.inv.clear_contents = Mock()
        self.inv.repair_formulas(5, [2, 3, 4], 4)
        self.inv.cell.assert_called_once_with('F2', '=D2*E2')
        self.inv.copy_cell.assert_called_once_with('F2')
        self.inv.paste_into.assert_called_once_with('F3:F4')

    def test_a_correct_table_is_left_alone_even_when_shown_as_currency(self):
        table = self.table(ENTRY)
        for line in table:
            line[4] = f'${float(line[4]):,.2f}'; line[5] = f'${float(line[5]):,.2f}'
        self.inv.table_snapshot = Mock(return_value=table)
        self.inv.cell = Mock()
        self.assertEqual(self.inv.verify_table(), 0)
        self.inv.cell.assert_not_called()

    def test_an_unreadable_table_is_never_answered_by_retyping_everything(self):
        self.inv.cell = Mock()
        for unreadable in ([], [['A2:H8']], [['']*8]*7):
            self.inv.table_snapshot = Mock(return_value=unreadable)
            self.assertEqual(self.inv.verify_table(), 0)
        self.inv.cell.assert_not_called()
        self.assertTrue(any(c.args[0] == 'table_check_unreliable' for c in self.gui.event.call_args_list))

    def test_delete_contents_dialog_is_confirmed_when_it_appears(self):
        self.inv.select = Mock()
        self.gui.window_handles.return_value = [77]
        self.inv.clear_contents('A9:H9')
        self.assertEqual([c.args[0] for c in self.gui.press.call_args_list], ['backspace', 'enter'])
        self.gui.press.reset_mock(); self.gui.window_handles.return_value = []
        self.inv.clear_contents('A9:H9')
        self.assertEqual([c.args[0] for c in self.gui.press.call_args_list], ['backspace'])

    def test_cell_typing_always_clears_an_autoinput_proposal(self):
        self.inv.select = Mock()
        self.inv.cell('J3', 'Fast mover', typo=True)
        self.inv.type_value('Laptop Stand', 1)
        for call in self.gui.type_text.call_args_list:
            self.assertTrue(call.kwargs['clear_inline_completion'])

    def test_find_dialog_is_closed_even_if_the_first_escape_only_shut_a_message(self):
        self.gui.window.return_value = Mock()
        self.gui.window_handles.side_effect = [[5], [5], []]
        self.inv.find_item()
        self.assertEqual([c.args[0] for c in self.gui.press.call_args_list],
                         ['enter', 'esc', 'esc', 'esc'])


def sheet_grid():
    """The grid of the VM's maximised Calc window: 12 columns of 81 px, 32 rows of 17 px."""
    return Grid([(33+81*i, 33+81*i+79) for i in range(12)],
                [(161+17*i, 161+17*i+15) for i in range(32)], (144, 158), (0, 31))


def inside(point, box):
    left, top, right, bottom = box
    return left <= point[0] <= right and top <= point[1] <= bottom


class GridPointerTests(unittest.TestCase):
    """Cells are clicked, ranges dragged or shift-clicked, and every selection is checked
    against the highlighted headers before anything is typed or pasted."""

    def setUp(self):
        self.module, self.pg, self.support = load_inventory()
        self.pg.keyDown = Mock(); self.pg.keyUp = Mock(); self.pg.size = Mock(return_value=(1280, 800))
        patcher = patch.dict(sys.modules, {'gui_support': self.support, 'pyautogui': self.pg})
        patcher.start(); self.addCleanup(patcher.stop)
        self.gui = Mock(run_dir=Path('/tmp/run')); self.gui.pace = 1.0
        self.inv = self.module.InventoryAction(self.gui, {'calc_name_box_point': None}, 5000)
        self.inv.window = Mock()
        self.inv.window.rectangle.return_value = rect(0, 0, 1280, 752)
        self.inv.grid = self.grid = sheet_grid()

    def test_a_cell_is_clicked_and_confirmed_without_keyboard_or_clipboard(self):
        self.inv.read_selection = Mock(return_value=(2, 2, 5, 5))            # C5
        self.inv.select('C5')
        (x, y), kwargs = self.gui.click_at.call_args.args, self.gui.click_at.call_args.kwargs
        self.assertTrue(inside((x, y), self.grid.box(2, 5)))
        self.assertTrue(kwargs['quick'])
        for untouched in (self.gui.type_text, self.gui.hotkey, self.gui.press,
                          self.gui.clear_clipboard, self.gui.read_clipboard, self.gui.find):
            untouched.assert_not_called()

    def test_a_range_is_dragged_from_its_first_cell_to_its_last(self):
        self.inv.read_selection = Mock(side_effect=[(5, 5, 2, 2), (0, 0, 3, 8)])   # F2, then A3:A8
        with patch.object(self.module.random, 'random', return_value=0.0):
            self.assertTrue(self.inv.grid_select('A3:A8'))
        x1, y1, x2, y2 = self.gui.drag.call_args.args
        self.assertTrue(inside((x1, y1), self.grid.box(0, 3)))
        self.assertTrue(inside((x2, y2), self.grid.box(0, 8)))
        self.gui.click_at.assert_not_called()

    def test_a_drag_never_starts_inside_an_already_selected_block(self):
        """Pressing inside a selected block and moving drags the block itself."""
        self.inv.read_selection = Mock(side_effect=[(0, 7, 2, 8), (4, 5, 2, 8)])   # A2:H8 selected
        with patch.object(self.module.random, 'random', return_value=0.0):
            self.assertTrue(self.inv.grid_select('E2:F8'))
        order = [c[0] for c in self.gui.method_calls if c[0] in ('click_at', 'drag')]
        self.assertEqual(order, ['click_at', 'drag'])          # plain click first, then the drag

    def test_a_range_can_also_be_click_then_shift_click(self):
        self.inv.read_selection = Mock(side_effect=[(0, 0, 1, 1), (5, 5, 3, 7)])
        with patch.object(self.module.random, 'random', return_value=0.99):
            self.assertTrue(self.inv.grid_select('F3:F7'))
        self.assertEqual(self.gui.click_at.call_count, 2)
        self.gui.drag.assert_not_called()
        self.pg.keyDown.assert_called_once_with('shift')
        self.pg.keyUp.assert_called_once_with('shift')

    def test_shift_is_released_if_the_second_click_fails(self):
        self.inv.read_selection = Mock(return_value=(0, 0, 1, 1))
        self.gui.click_at.side_effect = [None, RuntimeError('window moved')]
        with patch.object(self.module.random, 'random', return_value=0.99):
            self.assertFalse(self.inv.grid_select('F3:F7'))
        self.pg.keyUp.assert_called_once_with('shift')

    def test_a_wrong_selection_is_looked_at_again_before_anything_is_typed(self):
        self.inv.read_selection = Mock(side_effect=[(1, 1, 4, 4), (1, 1, 3, 3)])   # B4, then B3
        self.inv.calibrate_grid = Mock(return_value=True)
        self.assertTrue(self.inv.grid_select('B3'))
        self.inv.calibrate_grid.assert_called_once()
        self.assertEqual(self.gui.click_at.call_count, 2)
        self.assertTrue(any(c.args[0] == 'pointer_selection_mismatch' and c.kwargs['got'] == 'B4'
                            for c in self.gui.event.call_args_list))

    def test_when_the_pointer_cannot_be_confirmed_the_keyboard_route_takes_over(self):
        self.inv.read_selection = Mock(return_value=(1, 1, 4, 4))            # always B4
        self.inv.calibrate_grid = Mock(return_value=True)
        self.inv.pointer_select = Mock(return_value=False)
        self.inv.keyboard_select = Mock()
        self.gui.find.return_value = None
        self.inv.select('B3')
        self.inv.keyboard_select.assert_called_once_with('B3')

    def test_if_dragging_does_not_select_here_shift_click_is_used_from_then_on(self):
        """The two ways of selecting a range are tried in turn; the one that works stays."""
        self.inv.read_selection = Mock(side_effect=[(0, 0, 1, 1), (0, 0, 3, 3),      # drag: only A3
                                                    (0, 0, 3, 3), (0, 0, 3, 8)])     # shift+click: A3:A8
        self.inv.calibrate_grid = Mock(return_value=True)
        with patch.object(self.module.random, 'random', return_value=0.0):          # would drag
            self.assertTrue(self.inv.grid_select('A3:A8'))
        self.assertEqual(self.gui.drag.call_count, 1)
        self.pg.keyDown.assert_called_once_with('shift')
        self.assertEqual(self.inv.config['calc_drag_select_probability'], 0.0)
        self.assertTrue(any(c.args[0] == 'range_selection_method_fixed' and c.kwargs['drag'] is False
                            for c in self.gui.event.call_args_list))

    def test_a_sheet_that_is_still_painting_gets_a_second_look(self):
        blank = FakeSheet(); blank.fill(0, 0, blank.width-1, blank.height-1, (255, 255, 255))
        self.inv.grid = None
        self.gui.grab.side_effect = [blank.pixel, FakeSheet().pixel]
        self.assertTrue(self.inv.calibrate_grid())
        self.assertEqual(self.gui.grab.call_count, 2)
        self.gui.sleep.assert_called_once_with(1.0)

    def test_cells_outside_the_visible_grid_are_left_to_the_other_routes(self):
        self.inv.grid = Grid(self.grid.cols[:6], self.grid.rows[:10], (144, 158), (0, 31))
        self.assertFalse(self.inv.grid_select('J3'))
        self.assertFalse(self.inv.grid_select('A12'))
        self.gui.click_at.assert_not_called()

    def test_moving_on_is_sometimes_a_click_on_the_next_cell_instead_of_tab(self):
        self.inv.select = Mock()
        with patch.object(self.module.random, 'random', return_value=0.0):
            self.inv.advance('D4')
        self.inv.select.assert_called_once_with('D4')
        self.gui.press.assert_not_called()
        self.inv.select.reset_mock()
        with patch.object(self.module.random, 'random', return_value=0.99):
            self.inv.advance('G4', tabs=2)
        self.inv.select.assert_not_called()
        self.assertEqual([c.args[0] for c in self.gui.press.call_args_list], ['tab', 'tab'])

    def test_without_a_grid_moving_on_is_always_tab(self):
        self.inv.grid = None
        self.inv.select = Mock()
        with patch.object(self.module.random, 'random', return_value=0.0):
            self.inv.advance('D4')
        self.inv.select.assert_not_called()
        self.gui.press.assert_called_once_with('tab')

    def test_grid_is_read_from_the_screen_after_ctrl_home(self):
        sheet = FakeSheet()
        self.inv.grid = None
        self.gui.grab.return_value = sheet.pixel
        self.assertTrue(self.inv.calibrate_grid())
        self.gui.hotkey.assert_called_once_with('ctrl', 'home', window=self.inv.window)
        self.assertGreaterEqual(len(self.inv.grid.cols), 10)
        self.assertGreaterEqual(len(self.inv.grid.rows), 16)
        self.assertTrue(any(c.args[0] == 'grid_calibrated' for c in self.gui.event.call_args_list))

    def test_an_unreadable_screen_leaves_the_keyboard_route_and_says_why(self):
        self.gui.grab.return_value = None
        self.assertFalse(self.inv.calibrate_grid())
        self.assertIsNone(self.inv.grid)
        self.assertTrue(any(c.args[0] == 'grid_calibration_failed' for c in self.gui.event.call_args_list))

    def test_a_window_too_small_to_show_the_table_is_not_used(self):
        self.gui.grab.return_value = FakeSheet(columns=6, rows=9).pixel
        self.assertFalse(self.inv.calibrate_grid())
        self.assertIsNone(self.inv.grid)

    def test_selection_is_read_with_two_tiny_screen_reads(self):
        sheet = FakeSheet(); sheet.select(4, 5, 2, 8)
        self.gui.grab.return_value = sheet.pixel
        self.assertEqual(self.inv.read_selection(), (4, 5, 2, 8))
        sizes = [c.args[2:] for c in self.gui.grab.call_args_list]
        self.assertEqual([min(size) for size in sizes], [1, 1])     # one line across, one line down

    def test_header_row_is_made_bold_by_clicking_its_row_number(self):
        self.inv.select = Mock(); self.inv.ranges_work = True
        self.inv.format_table()
        x, y = self.gui.click_at.call_args.args
        self.assertTrue(0 <= x <= 31 and 161 <= y <= 176)             # the "1" at the left edge
        self.assertEqual([c.args for c in self.gui.hotkey.call_args_list],
                         [('ctrl', 'b'), ('ctrl', 'shift', '4'), ('ctrl', 'shift', '4')])
        self.assertEqual([c.args[0] for c in self.inv.select.call_args_list], ['E2:F8', 'F11:F15'])

    def test_temp_row_is_removed_by_clicking_its_row_number(self):
        self.inv.cell_text = Mock(return_value='')
        self.inv.remove_temp_row()
        x, y = self.gui.click_at.call_args.args
        top, bottom = self.grid.rows[8]
        self.assertTrue(0 <= x <= 31 and top <= y <= bottom)
        self.assertEqual([c.args for c in self.gui.hotkey.call_args_list], [('ctrl', '-')])


class SaveByClickingTests(unittest.TestCase):
    """Nobody types C:\\Users\\...\\20261010-005543-857f58\\inventory.ods into Save As."""

    def setUp(self):
        import tempfile
        self.module, self.pg, self.support = load_inventory()
        patcher = patch.dict(sys.modules, {'gui_support': self.support, 'pyautogui': self.pg})
        patcher.start(); self.addCleanup(patcher.stop)
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)/'Documents'/'HackathonDemo'
        (self.folder/'run1').mkdir(parents=True)
        self.gui = Mock(run_dir=self.folder/'run1'); self.gui.pace = 1.0
        self.inv = self.module.InventoryAction(self.gui, {}, 5000)
        self.inv.window = Mock()
        self.dialog = Mock()
        self.gui.window.return_value = self.dialog
        self.items = {('Documents', 'TreeItem'): Mock(name='Documents'),
                      ('HackathonDemo', 'ListItem'): Mock(name='HackathonDemo'),
                      ('File name', 'Edit'): Mock(name='File name')}
        self.gui.find.side_effect = lambda window, names, kind=None: next(
            (self.items[(n, kind)] for n in names if (n, kind) in self.items), None)
        self.gui.wait.side_effect = lambda predicate, label, timeout=30: predicate()
        self.gui.window_handles.return_value = []
        self.inv._zip_ready = Mock(return_value=True)

    def typed(self):
        return [c.args[0] for c in self.gui.type_text.call_args_list]

    def test_folder_is_reached_by_clicks_and_only_the_file_name_is_typed(self):
        self.inv.locate_saved = Mock(return_value=True)
        self.inv.save()
        clicks = [(c.args[0], c.kwargs.get('double', False)) for c in self.gui.click.call_args_list]
        self.assertEqual(clicks, [(self.items[('Documents', 'TreeItem')], False),
                                  (self.items[('HackathonDemo', 'ListItem')], True),
                                  (self.items[('File name', 'Edit')], False)])
        self.assertEqual(len(self.typed()), 1)
        name = self.typed()[0]
        self.assertRegex(name, r'^inventory_[a-z]{3}\d\d(_\d+)?\.ods$')
        self.assertNotIn('\\', name); self.assertNotIn('/', name); self.assertNotIn(':', name)
        self.gui.click_named.assert_called_once_with(self.dialog, ['Save'], 'Button')
        self.assertTrue(self.inv.saved_once)

    def test_if_documents_is_not_listed_the_name_alone_is_still_typed(self):
        del self.items[('Documents', 'TreeItem')]
        self.inv.locate_saved = Mock(return_value=True)
        self.inv.save()
        self.assertEqual(len(self.typed()), 1)
        self.assertNotIn('\\', self.typed()[0]); self.assertNotIn('/', self.typed()[0])

    def test_the_file_is_found_wherever_the_dialog_really_saved_it(self):
        elsewhere = self.folder.parent/self.inv.path.name       # saved one folder up
        elsewhere.write_text('x')
        self.assertTrue(self.inv.locate_saved(timeout=0))
        self.assertEqual(self.inv.path, elsewhere)

    def test_full_path_is_typed_only_as_the_last_resort(self):
        self.inv.locate_saved = Mock(return_value=False)
        target = self.folder/self.inv.path.name
        def wait(predicate, label, timeout=30):
            if 'saved' in label and 'package' not in label:
                target.write_text('x')
            return predicate()
        self.gui.wait.side_effect = wait
        self.inv.save()
        self.assertEqual(self.typed(), [target.name, str(target)])
        self.assertTrue(any(c.args[0] == 'save_by_clicks_unconfirmed' for c in self.gui.event.call_args_list))

    def test_a_taken_name_gets_a_number_instead_of_an_overwrite_prompt(self):
        first = self.inv.pick_name()
        (self.folder/first).write_text('x')
        second = self.inv.pick_name()
        self.assertEqual(second, first.replace('.ods', '_2.ods'))

    def test_later_saves_are_just_ctrl_s(self):
        self.inv.saved_once = True
        self.inv._modified = Mock(side_effect=[1.0, 2.0])
        self.inv.save()
        self.gui.hotkey.assert_called_once_with('ctrl', 's', window=self.inv.window)
        self.gui.type_text.assert_not_called()

    def test_final_save_goes_through_the_file_menu_so_it_can_be_seen(self):
        self.inv.saved_once = self.inv.unsaved = True
        self.inv._modified = Mock(side_effect=[1.0, 2.0])           # the file changes: saved
        self.gui.window_handles.side_effect = [[1], [1, 2]]         # a menu dropped open
        self.inv.save(visible=True)
        self.gui.hotkey.assert_called_once_with('alt', 'f', window=self.inv.window)
        self.assertEqual([c.args[0] for c in self.gui.press.call_args_list], ['s'])
        self.assertFalse(self.inv.unsaved)
        saved = [c for c in self.gui.event.call_args_list if c.args[0] == 'workbook_saved']
        self.assertEqual(saved[-1].kwargs['method'], 'file_menu')

    def test_no_letter_is_sent_unless_the_menu_is_seen_to_open(self):
        """An "s" sent with no menu open would be typed into the sheet."""
        self.inv.saved_once = self.inv.unsaved = True
        self.inv._modified = Mock(side_effect=[1.0, 2.0])
        self.gui.window_handles.side_effect = [[1], [1]]            # nothing new appeared
        self.inv.save(visible=True)
        self.assertEqual([c.args[0] for c in self.gui.press.call_args_list], ['esc', 'esc'])
        self.assertEqual([c.args for c in self.gui.hotkey.call_args_list], [('alt', 'f'), ('ctrl', 's')])

    def test_if_the_menu_save_did_not_write_the_file_ctrl_s_follows(self):
        self.inv.saved_once = self.inv.unsaved = True
        self.inv.wait_for_write = Mock(side_effect=[False, True])
        self.gui.window_handles.side_effect = [[1], [1, 2]]
        self.inv.save(visible=True)
        self.assertEqual([c.args for c in self.gui.hotkey.call_args_list], [('alt', 'f'), ('ctrl', 's')])
        saved = [c for c in self.gui.event.call_args_list if c.args[0] == 'workbook_saved']
        self.assertEqual(saved[-1].kwargs['method'], 'ctrl_s_after_menu')

    def test_save_changes_question_on_closing_is_answered_with_save(self):
        """Seen in the VM: a save that never reached Calc left "Do you want to save your
        changes?" on screen and the run was reported as failed."""
        self.gui.window_handles.return_value = []
        self.inv.save_if_asked()
        self.gui.press.assert_not_called()
        self.gui.window_handles.return_value = [77]
        self.inv.save_if_asked()
        self.gui.press.assert_called_once_with('enter')             # Save is the default button


class TimeCapTests(unittest.TestCase):
    """The spreadsheet phase stops at its cap and always ends with a saved, closed file."""

    def setUp(self):
        self.module, self.pg, self.support = load_inventory()
        patcher = patch.dict(sys.modules, {'gui_support': self.support, 'pyautogui': self.pg})
        patcher.start(); self.addCleanup(patcher.stop)
        self.gui = Mock(run_dir=Path('/tmp/run')); self.gui.pace = 1.0
        self.gui.time_limit = None
        self.inv = self.module.InventoryAction(self.gui, {'calc_wrap_up_seconds': 20}, 5000)
        self.window = self.inv.window = Mock()
        self.inv.save = Mock(); self.inv.restore_pending = Mock()
        self.inv.review_until = Mock()
        self.valid = {'records': 7, 'total_sgd': 3486, 'sorted_by_item': False}
        validator = patch.object(self.module, 'validate_inventory', return_value=dict(self.valid))
        self.validate = validator.start(); self.addCleanup(validator.stop)

    def test_time_up_during_the_build_still_saves_and_closes(self):
        def build():
            self.inv.step = 'rows'
            raise self.support.TimeUp('Time limit reached')
        self.inv.build = Mock(side_effect=build)
        self.validate.side_effect = ValueError('Wrong record at row 6')
        result = self.inv.run(480)
        self.inv.save.assert_called_once_with(visible=True)
        self.assertIs(self.gui.close_window.call_args.args[0], self.window)
        self.assertEqual(self.gui.close_window.call_args.kwargs['on_prompt'], self.inv.save_if_asked)
        self.assertFalse(result['complete'])
        self.assertEqual(result['cut_short_at'], 'rows')
        self.assertEqual(result['error'], 'Wrong record at row 6')
        self.assertIsNone(self.gui.time_limit)                 # lifted, so the save can run
        self.inv.review_until.assert_not_called()

    def test_everything_stops_twenty_seconds_before_the_cap(self):
        seen = {}
        self.inv.build = Mock(side_effect=lambda: seen.update(limit=self.gui.time_limit,
                                                              deadline=self.inv.phase.deadline))
        self.inv.run(480)
        self.assertAlmostEqual(seen['deadline']-seen['limit'], 20)
        self.assertAlmostEqual(self.inv.phase.deadline-self.inv.phase.started, 480)

    def test_last_save_is_the_visible_one_when_there_is_work_to_save(self):
        self.inv.build = Mock(); self.inv.saved_once = True; self.inv.unsaved = True
        self.inv.run(480)
        self.inv.save.assert_called_once_with(visible=True)

    def test_nothing_is_saved_twice_when_nothing_changed(self):
        self.inv.build = Mock(); self.inv.saved_once = True; self.inv.unsaved = False
        self.inv.run(480)
        self.inv.save.assert_not_called()
        self.gui.close_window.assert_called_once()
        self.assertTrue(any(c.args[0] == 'workbook_already_saved' for c in self.gui.event.call_args_list))

    def test_a_finished_table_is_reported_complete_with_its_path(self):
        self.inv.build = Mock()
        result = self.inv.run(480)
        self.assertTrue(result['complete'])
        self.assertEqual(result['total_sgd'], 3486)
        self.assertEqual(result['saved_path'], str(self.inv.path))
        self.assertIsNone(result['cut_short_at'])
        self.inv.review_until.assert_called_once()

    def test_a_step_that_fails_for_good_does_not_lose_the_work_done(self):
        def build():
            self.inv.step = 'summary'
            raise RuntimeError('Cannot confirm expected foreground window')
        self.inv.build = Mock(side_effect=build)
        result = self.inv.run(480)
        self.inv.save.assert_called_once()
        self.gui.close_window.assert_called_once()
        self.assertIn('summary: Cannot confirm', result['step_error'])

    def test_f8_stops_at_once_without_tidying_up(self):
        self.inv.build = Mock(side_effect=self.support.Stopped('Stopped by F8'))
        with self.assertRaises(self.support.Stopped):
            self.inv.run(480)
        self.inv.save.assert_not_called()
        self.gui.close_window.assert_not_called()
        self.assertIsNone(self.gui.time_limit)

    def test_time_up_passes_through_retry_and_carry_on_handlers(self):
        """TimeUp is a kind of stop, so a step wrapper must not swallow it and continue."""
        with self.assertRaises(self.support.TimeUp):
            self.inv.attempt('format', Mock(side_effect=self.support.TimeUp()))
        self.inv.select = Mock(side_effect=self.support.TimeUp())
        with self.assertRaises(self.support.TimeUp):
            self.inv.cell('D5', 9)
        self.inv._row = Mock(side_effect=self.support.TimeUp())
        with self.assertRaises(self.support.TimeUp):
            self.inv.row(3, ENTRY[1])
        self.inv.row_reuse = Mock(side_effect=self.support.TimeUp())
        self.inv.row = Mock(); self.inv.ranges_work = True
        with self.assertRaises(self.support.TimeUp):
            self.inv.reuse_row_with_retry(3, ENTRY[1])
        self.inv.row.assert_not_called()

    def test_if_calc_never_opened_there_is_nothing_to_save(self):
        self.inv.window = None
        self.inv.build = Mock(side_effect=self.support.TimeUp())
        result = self.inv.run(60)
        self.inv.save.assert_not_called()
        self.assertFalse(result['complete'])

    def steps(self, remaining, ranges_work=True):
        """Run build() with every step mocked; return the steps in the order they ran."""
        ran = []
        self.inv.ranges_work = ranges_work
        for name in ('calibrate_grid', 'select', 'save', 'row', 'think', 'fill_sku',
                     'reuse_row_with_retry', 'fill_formulas', 'cell', 'verify_table',
                     'remove_temp_row', 'format_table', 'sort_by_item', 'summary'):
            setattr(self.inv, name, Mock(side_effect=lambda *a, _n=name, **k: ran.append(_n)))
        self.inv.config['calc_shortcut'] = 'OpenOffice Calc'
        self.inv.grid = sheet_grid()                       # the mouse route: costs are not scaled up
        self.inv.phase = Mock(remaining=remaining)
        self.inv.build()
        return ran

    def skipped(self):
        return [c.kwargs['step'] for c in self.gui.event.call_args_list if c.args[0] == 'step_skipped']

    def test_everything_the_file_needs_is_done_and_saved_before_any_polish(self):
        ran = self.steps(remaining=400)
        for step in ('fill_sku', 'fill_formulas', 'verify_table', 'remove_temp_row',
                     'format_table', 'sort_by_item', 'summary'):
            self.assertIn(step, ran)
        summary = ran.index('summary')
        self.assertEqual(ran[summary+1], 'save')                    # complete workbook on disk
        self.assertLess(ran.index('remove_temp_row'), summary)      # a later row delete would shift the totals
        self.assertGreater(ran.index('format_table'), summary)
        self.assertGreater(ran.index('sort_by_item'), summary)
        self.assertEqual(ran[-1], 'save')
        self.assertEqual(self.skipped(), [])

    def test_short_of_time_the_temporary_row_goes_first(self):
        ran = self.steps(remaining=150)
        self.assertNotIn('remove_temp_row', ran)
        self.assertEqual(self.skipped(), ['temp_row'])
        for step in ('summary', 'format_table', 'sort_by_item'):
            self.assertIn(step, ran)

    def test_with_no_time_for_polish_the_totals_and_the_save_still_happen(self):
        ran = self.steps(remaining=25)
        self.assertEqual(self.skipped(), ['temp_row', 'format', 'sort'])
        self.assertEqual(ran[-2:], ['summary', 'save'])

    def test_a_slower_run_and_the_keyboard_route_leave_more_room(self):
        self.inv.phase = Mock(remaining=200)
        self.inv.grid = sheet_grid()
        self.assertTrue(self.inv.fits(65, after=100))               # 180 s for 165 s of work
        self.gui.pace = 1.2
        self.assertFalse(self.inv.fits(65, after=100))              # same work, 20 % slower
        self.gui.pace = 1.0; self.inv.grid = None
        self.assertFalse(self.inv.fits(65, after=100))              # without the mouse: much slower

    def test_without_range_selection_nothing_is_filled_checked_or_sorted(self):
        ran = self.steps(remaining=400, ranges_work=False)
        for step in ('fill_formulas', 'verify_table', 'sort_by_item', 'remove_temp_row'):
            self.assertNotIn(step, ran)
        self.assertEqual(ran.count('reuse_row_with_retry'), 6)      # every record still entered
        self.assertIn('summary', ran)
        self.assertEqual(self.skipped(), ['temp_row', 'sort'])

    def test_the_serial_number_paste_covers_the_row_added_later(self):
        self.steps(remaining=400)
        # fill_sku runs inside attempt(); the lambda passes rows 3..8 (INV-002 to INV-007)
        self.inv.fill_sku.assert_called_once_with(3, 8)

    def test_reopening_from_explorer_is_off_unless_asked_for(self):
        self.inv.build = Mock(); self.inv.reopen_from_explorer = Mock()
        self.inv.run(480)
        self.inv.reopen_from_explorer.assert_not_called()
        self.inv.window = Mock(); self.inv.config['reopen_after_save'] = True
        self.assertTrue(self.inv.run(480)['reopened'])


class SummaryBlockTests(unittest.TestCase):
    def setUp(self):
        self.module, self.pg, self.support = load_inventory()
        patcher = patch.dict(sys.modules, {'gui_support': self.support, 'pyautogui': self.pg})
        patcher.start(); self.addCleanup(patcher.stop)
        self.gui = Mock(run_dir=Path('/tmp/run')); self.gui.pace = 1.0
        self.inv = self.module.InventoryAction(self.gui, {'spreadsheet_typo_scale': 0}, 5000)
        self.inv.window = Mock(); self.inv.select = Mock(); self.inv.cell = Mock()
        self.inv.ranges_work = True
        self.good = ['3486', '498', '224', '1260', '5000', 'WITHIN BUDGET']

    def test_without_range_selection_two_single_cells_confirm_the_block(self):
        self.inv.ranges_work = False
        self.inv.range_values = Mock()
        self.inv.cell_text = Mock(side_effect=['3486', 'WITHIN BUDGET'])
        self.inv.summary()
        self.inv.range_values.assert_not_called()
        self.assertEqual([c.args[0] for c in self.inv.cell_text.call_args_list], ['F11', 'F16'])
        self.inv.cell.assert_not_called()
        self.inv.cell_text = Mock(side_effect=['', 'Err:509'])        # block landed elsewhere
        self.inv.summary()
        self.assertEqual([c.args[0] for c in self.inv.cell.call_args_list],
                         [f'{col}{row}' for row in range(11, 17) for col in 'EF'])

    def test_labels_then_values_are_typed_down_two_columns(self):
        self.inv.range_values = Mock(return_value=self.good)
        self.inv.summary()
        self.assertEqual([c.args[0] for c in self.inv.select.call_args_list], ['E11', 'F11'])
        typed = [str(c.args[0]) for c in self.gui.type_text.call_args_list]
        self.assertEqual(typed[0], 'Total value'); self.assertEqual(typed[6], '=SUM(F2:F8)')
        self.assertEqual(typed[-1], '=IF(F11<=F15;"WITHIN BUDGET";"OVER BUDGET")')
        self.assertEqual([c.args[0] for c in self.gui.press.call_args_list], ['enter']*12)
        self.inv.cell.assert_not_called()

    def test_if_the_block_does_not_read_back_right_each_cell_is_placed_explicitly(self):
        self.inv.range_values = Mock(return_value=['3486', '', '498'])
        self.inv.summary()
        cells = [c.args[0] for c in self.inv.cell.call_args_list]
        self.assertEqual(cells, [f'{col}{row}' for row in range(11, 17) for col in 'EF'])

    def test_currency_display_still_counts_as_right(self):
        self.inv.range_values = Mock(return_value=['$3,486.00', '$498.00', '$224.00', '$1,260.00',
                                                   '$5,000.00', 'WITHIN BUDGET'])
        self.inv.summary()
        self.inv.cell.assert_not_called()
