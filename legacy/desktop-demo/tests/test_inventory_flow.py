"""The whole spreadsheet phase, run against a model of Calc (tests/sim_calc.py).

Unlike the contract tests, nothing inside InventoryAction is mocked here: every click,
drag, key and paste goes to the model, and the real validate_inventory then checks the
resulting sheet.  This is where a wrong Tab count or a paste on the wrong cell shows."""
import importlib.util
from pathlib import Path
import random
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import domain
from domain import FINAL, Phase
from sim_calc import SimCalc

ENTRY = sorted(FINAL, key=lambda record: record[0])


def expected_table(records):
    return [[str(v) for v in (r[0], r[1], r[2], r[3], r[4], r[3]*r[4], r[5],
                              'YES' if r[3] < r[5] else 'NO')] for r in records]


def plain(table):
    """Displayed values without currency formatting: '$1,260.00' -> '1260'."""
    def value(text):
        if text.startswith('$'):
            number = float(text[1:].replace(',', ''))
            return str(int(number)) if number.is_integer() else str(number)
        return text
    return [[value(text) for text in line] for line in table]


class SpreadsheetFlowTests(unittest.TestCase):
    def run_flow(self, seed=0, seconds=480, config=None, **sim_options):
        pg = types.ModuleType('pyautogui')
        pg.FailSafeException = type('FailSafeException', (Exception,), {})
        support = types.ModuleType('gui_support')
        support.Stopped = type('Stopped', (Exception,), {})
        support.TimeUp = type('TimeUp', (support.Stopped,), {})
        spec = importlib.util.spec_from_file_location(
            'inventory_flow_under_test', Path(__file__).resolve().parents[1]/'inventory_action.py')
        module = importlib.util.module_from_spec(spec)
        modules = {'pyautogui': pg, 'win32gui': types.ModuleType('win32gui'), 'gui_support': support}
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        folder = Path(temp.name)/'Documents'/'HackathonDemo'
        (folder/'run-now').mkdir(parents=True)
        sim = SimCalc(folder, support.TimeUp, **sim_options)
        pg.keyDown = lambda key: setattr(sim, 'shift', True) if key == 'shift' else None
        pg.keyUp = lambda key: setattr(sim, 'shift', False) if key == 'shift' else None
        pg.size = lambda: (1280, 800)
        settings = {'calc_shortcut': 'OpenOffice Calc', 'spreadsheet_typo_scale': 0,
                    'calc_wrap_up_seconds': 20, **(config or {})}
        random.seed(seed)
        with patch.dict(sys.modules, modules):
            spec.loader.exec_module(module)
            inv = module.InventoryAction(sim.gui, settings, 5000)
            import time as real_time
            clock = types.SimpleNamespace(monotonic=lambda: sim.clock, time=real_time.time,
                                          localtime=real_time.localtime)
            with patch.object(module, 'Phase', side_effect=lambda s: Phase(s, clock=lambda: sim.clock)), \
                 patch.object(module, 'time', clock), \
                 patch.object(domain, 'ods_cells', side_effect=lambda path: sim.ods()), \
                 patch.object(module.Path, 'home', return_value=Path(temp.name)):
                result = inv.run(seconds)
        return sim, inv, result

    def events(self, sim, name):
        return [details for action, details, _ in sim.events if action == name]

    def when(self, sim, name):
        return next(clock for action, _, clock in sim.events if action == name)

    # --- the normal run -----------------------------------------------------------------

    def test_full_phase_builds_a_valid_sorted_workbook_with_mouse_and_copy_paste(self):
        for seed in range(25):
            sim, inv, result = self.run_flow(seed)
            self.assertEqual(sim.errors, [], seed)
            self.assertTrue(result['complete'], (seed, result))
            self.assertTrue(result['sorted_by_item'], seed)
            self.assertEqual(plain(sim.table()), expected_table(FINAL), seed)
            self.assertTrue(sim.closed and not sim.dirty, seed)
            self.assertTrue(result['pointer_selection'] and result['copy_paste_used'], seed)
            self.assertGreaterEqual(sim.pastes+sim.fills, 4, seed)      # serials, categories, formulas
            self.assertGreater(sim.clicks+sim.drags, 40, seed)          # the mouse does the travelling
            self.assertEqual(sim.bold_rows, {1}, seed)
            self.assertLessEqual(sim.clock, 480, seed)                  # saved and closed inside the cap
            self.assertEqual(self.events(sim, 'cell_repaired'), [], seed)   # right the first time
            self.assertNotIn('ctrl+d', sim.keys, seed)

    def test_table_is_built_with_time_to_spare_before_the_cap(self):
        finished = []
        for seed in range(25):
            sim, _, _ = self.run_flow(seed)
            finished.append(self.when(sim, 'summary_added'))
        self.assertLess(max(finished), 420, finished)       # modelled seconds; the cap is 480

    def test_serial_numbers_are_pasted_once_and_only_their_last_digit_is_typed(self):
        sim, inv, _ = self.run_flow(3)
        self.assertEqual(self.events(sim, 'column_filled')[0]['confirmed'], True)
        methods = [e['method'] for e in self.events(sim, 'sku_edited')]
        self.assertEqual(methods, ['paste_then_edit_last_digit']*6)
        self.assertEqual(sim.keys.count('f2'), 6)

    def test_repeated_category_is_copied_and_formulas_are_never_typed_after_row_two(self):
        sim, inv, _ = self.run_flow(5)
        self.assertEqual(len(self.events(sim, 'category_pasted')), 4)   # Accessories x4 after row 2
        rows = [e for e in self.events(sim, 'inventory_row_added')]
        self.assertEqual([e['method'] for e in rows if e['sku'].startswith('INV-00')],
                         ['typed']+['reuse']*6)
        self.assertEqual(self.events(sim, 'table_checked')[0]['repaired'], 0)

    def test_save_as_types_a_bare_file_name_into_the_clicked_folder(self):
        sim, inv, result = self.run_flow(1)
        self.assertRegex(sim.field, r'^inventory_[a-z]{3}\d\d\.ods$')
        self.assertEqual(sim.saved_as.parent, sim.folder)               # ...\Documents\HackathonDemo
        self.assertEqual(result['saved_path'], str(sim.saved_as))
        self.assertEqual(self.events(sim, 'save_folder_browsed')[0]['clicks'], 2)

    def test_file_is_still_found_when_documents_is_not_in_the_dialog(self):
        sim, inv, result = self.run_flow(2, documents_listed=False)
        self.assertTrue(result['complete'])
        self.assertEqual(sim.saved_as.parent.name, '20261008-142443-09ab12')   # where the dialog was
        self.assertEqual(result['saved_path'], str(sim.saved_as))
        self.assertNotIn('\\', sim.field); self.assertNotIn('/', sim.field)

    # --- what the second recording showed -------------------------------------------------

    def test_values_typed_after_a_pasted_category_land_in_their_own_cells(self):
        """In the VM run row 8 ended as: Laptop Stand | 32 | (empty) | (empty) | 3."""
        for seed in range(25):
            sim, inv, _ = self.run_flow(seed, seconds=330)       # as far as the table itself
            for row, record in enumerate(ENTRY, 2):
                if sim.cells.get((2, row)):
                    self.assertEqual(sim.cells.get((2, row)), record[2], (seed, row))   # category kept

    def test_both_formula_columns_are_filled_by_pasting(self):
        sim, inv, _ = self.run_flow(2)
        filled = self.events(sim, 'formulas_filled')
        self.assertEqual([(e['column'], e['method']) for e in filled],
                         [('F', 'paste'), ('H', 'paste')]*2)          # rows 3-7, then row 8
        for row in range(3, 9):
            self.assertTrue(sim.cells[(7, row)].startswith('=IF(D'), row)

    def test_the_run_ends_with_a_save_that_can_be_seen(self):
        sim, inv, result = self.run_flow(1)
        last = self.events(sim, 'workbook_saved')[-1]
        self.assertEqual(last['method'], 'file_menu')
        self.assertEqual(sim.menu_saves, 1)
        self.assertEqual(sim.keys[-2:], ['alt+f', 's'])               # File, Save; then the window closes
        self.assertTrue(sim.closed and not sim.dirty)

    def test_if_the_file_menu_cannot_be_used_ctrl_s_saves_and_nothing_is_typed_into_the_sheet(self):
        for mode, method in (('unseen', 'ctrl_s'), ('dead', 'ctrl_s_after_menu')):
            sim, inv, result = self.run_flow(1, file_menu=mode)
            self.assertEqual(sim.errors, [], mode)
            self.assertEqual(self.events(sim, 'workbook_saved')[-1]['method'], method, mode)
            self.assertTrue(result['complete'] and sim.closed and not sim.dirty, mode)

    def test_if_no_save_reaches_calc_its_question_on_closing_is_answered_with_save(self):
        """The first Full demo ended on "Do you want to save your changes?" and was failed."""
        sim, inv, result = self.run_flow(1, file_menu='dead', ctrl_s=False)
        self.assertEqual(sim.errors, [])
        self.assertEqual(len(self.events(sim, 'save_confirmed_on_close')), 1)
        self.assertTrue(sim.closed and not sim.dirty)
        self.assertTrue(result['complete'], result)

    # --- sorting can fail in several ways; none of them retypes the table ----------------

    def test_a_sort_that_cannot_be_done_or_confirmed_leaves_a_valid_unsorted_table(self):
        for mode in ('no_menu', 'no_dialog', 'wrong_field', 'scramble'):
            typed = []
            for sort in ('works', mode):
                sim, inv, result = self.run_flow(7, sort=sort)
                self.assertEqual(sim.errors, [], mode)
                self.assertTrue(result['complete'], (mode, result))
                typed.append(self.when(sim, 'summary_added'))
            self.assertFalse(result['sorted_by_item'], mode)
            self.assertEqual(plain(sim.table()), expected_table(ENTRY), mode)
            self.assertEqual(len(self.events(sim, 'sort_skipped')), 1, mode)
            self.assertEqual(self.events(sim, 'cell_repaired'), [], mode)      # nothing retyped
            self.assertLess(typed[1]-typed[0], 25, mode)                       # and no minutes lost

    # --- fallbacks ----------------------------------------------------------------------

    def test_without_a_readable_screen_the_keyboard_route_builds_the_same_table(self):
        sim, inv, result = self.run_flow(4, seconds=900, grid=False)
        self.assertEqual(sim.errors, [])
        self.assertTrue(result['complete'], result)
        self.assertFalse(result['pointer_selection'])
        self.assertTrue(result['copy_paste_used'])              # Shift+arrow ranges work again
        self.assertEqual(plain(sim.table()), expected_table(FINAL))
        self.assertEqual(self.events(sim, 'cell_repaired'), [])
        self.assertEqual(len(self.events(sim, 'grid_calibration_failed')), 2)   # at launch, and once more after the first save

    def test_the_recorded_vm_behaviour_ends_in_a_valid_table_typed_in_full(self):
        """No readable screen and Shift+arrow not selecting: what the recording showed."""
        sim, inv, result = self.run_flow(6, seconds=900, grid=False, shift_arrows=False)
        self.assertEqual(sim.errors, [])
        self.assertTrue(result['complete'], result)
        self.assertFalse(result['copy_paste_used'])
        self.assertEqual(plain(sim.table()), expected_table(ENTRY))
        self.assertEqual(self.events(sim, 'column_filled')[0]['confirmed'], False)
        self.assertEqual(self.events(sim, 'cell_repaired'), [])          # no repair loop
        self.assertEqual([e['step'] for e in self.events(sim, 'step_skipped')], ['temp_row', 'sort'])

    # --- the time cap -------------------------------------------------------------------

    def test_whenever_the_time_runs_out_the_file_is_saved_and_closed_inside_the_cap(self):
        for seconds in range(125, 480, 9):          # any moment after the first save
            sim, inv, result = self.run_flow(seconds, seconds=seconds)
            label = (seconds, result.get('cut_short_at'), sim.errors)
            self.assertEqual(sim.errors, [], label)
            self.assertTrue(sim.closed, label)
            self.assertFalse(sim.dirty, label)
            self.assertIsNotNone(sim.saved_as, label)
            self.assertLessEqual(sim.clock, seconds, label)
            self.assertEqual(result['saved_path'], str(sim.saved_as), label)
            self.assertIsNone(inv.gui.time_limit, label)

    def test_a_run_cut_short_says_so_and_never_claims_a_finished_table(self):
        sim, inv, result = self.run_flow(8, seconds=150)
        self.assertFalse(result['complete'])
        self.assertEqual(result['cut_short_at'], 'rows')
        self.assertIn('error', result)

    def test_a_slow_run_drops_the_optional_steps_but_still_finishes_the_table(self):
        """With less time the temporary row goes first; the totals and the save stay."""
        sim, inv, result = self.run_flow(9, seconds=380)
        self.assertEqual(sim.errors, [])
        skipped = [e['step'] for e in self.events(sim, 'step_skipped')]
        self.assertIn('temp_row', skipped)
        self.assertTrue(result['complete'], result)
        self.assertLessEqual(sim.clock, 380)

    def test_totals_are_saved_before_formatting_and_sorting_start(self):
        sim, inv, result = self.run_flow(10)
        order = [action for action, _, _ in sim.events
                 if action in ('summary_added', 'table_formatted', 'inventory_sorted')]
        self.assertEqual(order, ['summary_added', 'table_formatted', 'inventory_sorted'])
        saves = [clock for action, _, clock in sim.events if action == 'workbook_saved']
        self.assertTrue(any(self.when(sim, 'summary_added') <= clock <= self.when(sim, 'table_formatted')
                            for clock in saves))

    def test_a_run_cut_during_the_polish_is_still_a_complete_workbook(self):
        for seconds in (400, 410, 420, 430):
            sim, inv, result = self.run_flow(11, seconds=seconds)
            self.assertTrue(result['complete'], (seconds, result))
            self.assertEqual(sim.errors, [])

    def test_each_fallback_route_finishes_the_table_inside_eight_minutes(self):
        """Modelled at the typing speed measured in the recording."""
        for seed in range(6):
            sim, inv, result = self.run_flow(seed, grid=False)          # no screen read
            self.assertTrue(result['complete'], (seed, result))
            self.assertLessEqual(sim.clock, 480)

    def test_what_if_edits_are_always_put_back(self):
        for seed in range(12):
            sim, inv, result = self.run_flow(seed, seconds=700)        # plenty of review time
            self.assertTrue(result['complete'], (seed, result))        # validation sees original values
            self.assertEqual(inv.pending, {}, seed)


if __name__ == '__main__':
    unittest.main()
