"""Controller pilots with mocked apps: no Windows process or GUI is started."""
import builtins
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch


AGENT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((AGENT/'config.json').read_text())


class MainContractTests(unittest.TestCase):
    """main.main() with every agent module replaced by a mock: no Windows process or GUI
    is started, and (as in the real run) nothing is written to disk."""

    def run_demo(self, mode, browser_error=None, unused_seconds=None, inventory_result=None,
                 notes_result=None, notes_error=None, patches=()):
        tk = types.ModuleType('tkinter')
        tk.Tk = Mock()
        tk.messagebox = types.ModuleType('messagebox'); tk.messagebox.showinfo = Mock()
        tk.simpledialog = types.ModuleType('simpledialog'); tk.ttk = types.ModuleType('ttk')
        support = types.ModuleType('gui_support')
        support.GUI = Mock()
        support.Stopped = type('Stopped', (Exception,), {})
        pg = types.ModuleType('pyautogui')
        pg.FailSafeException = type('FailSafeException', (Exception,), {})
        browser = types.ModuleType('browser_action'); browser.BrowserAction = Mock()
        browser.BrowserAction.return_value.run.return_value = {'verified': True}
        if unused_seconds is not None:
            browser.BrowserAction.return_value.run.return_value['unused_seconds'] = unused_seconds
        browser.BrowserAction.return_value.window = None
        if browser_error:
            browser.BrowserAction.return_value.run.side_effect = browser_error
        inventory = types.ModuleType('inventory_action'); inventory.InventoryAction = Mock()
        inventory.InventoryAction.return_value.run.return_value = inventory_result or {
            'complete': True, 'total_sgd': 3486, 'records': 7, 'within_budget': True,
            'saved_path': r'C:\Users\User\Desktop\agent\inventory_oct17.ods'}
        inventory.InventoryAction.return_value.path = Path('inventory_oct17.ods')
        notes = types.ModuleType('notes_action'); notes.NotesAction = Mock()
        notes.NotesAction.return_value.run.return_value = notes_result or {'lines_typed': 6, 'end_written': True}
        if notes_error:
            notes.NotesAction.return_value.run.side_effect = notes_error
        modules = {'tkinter': tk, 'gui_support': support, 'pyautogui': pg,
                   'browser_action': browser, 'inventory_action': inventory, 'notes_action': notes}
        spec = importlib.util.spec_from_file_location('main_contract_under_test', AGENT/'main.py')
        module = importlib.util.module_from_spec(spec)
        imports, reports = [], []
        real_import_module = importlib.import_module
        def importing(name, *args, **kwargs):
            imports.append(name)
            return real_import_module(name, *args, **kwargs)
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, modules), \
             patch.object(sys, 'path', [str(AGENT)]+sys.path):
            spec.loader.exec_module(module)
            # A local namespace avoids changing os.name globally (which affects pathlib).
            module.os = types.SimpleNamespace(name='nt')
            module.launcher = Mock(return_value=[mode, 5000])
            module.preflight = Mock(return_value=([], []))  # tested separately below
            shown = module.show_result
            module.show_result = lambda report: (reports.append(report), shown(report))[1]
            for target, value in patches:
                setattr(module, target, value)
            with patch.object(module.Path, 'home', return_value=Path(directory)), \
                 patch.object(module.importlib, 'import_module', side_effect=importing), \
                 patch('builtins.__import__', side_effect=self.record_imports(imports)):
                code = module.main()
            self.assertEqual(list(Path(directory).rglob('*')), [])        # nothing was written
        support.GUI.return_value.finish.assert_called_once()
        self.result_box = (tk.messagebox.showinfo.call_args.args[1]
                           if tk.messagebox.showinfo.called else None)
        self.showinfo = tk.messagebox.showinfo
        return code, reports[0], imports, browser, inventory, notes

    @staticmethod
    def record_imports(imports):
        original = builtins.__import__
        def importing(name, *args, **kwargs):
            imports.append(name)
            return original(name, *args, **kwargs)
        return importing

    def test_browser_pilot_never_loads_inventory_or_notes_modules(self):
        code, report, imports, browser, inventory, notes = self.run_demo('Browser pilot')
        self.assertEqual(code, 0)
        self.assertNotIn('inventory_action', imports)
        self.assertNotIn('notes_action', imports)
        inventory.InventoryAction.assert_not_called()
        notes.NotesAction.assert_not_called()
        browser.BrowserAction.return_value.run.assert_called_once_with(CONFIG['pilot_phase_seconds'])
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(list(report['phases']), ['browser'])

    def test_a_full_demo_runs_browsing_spreadsheet_and_notes_in_that_order(self):
        code, report, imports, browser, inventory, notes = self.run_demo(
            'Full demo', unused_seconds=0, patches=())
        self.assertEqual(code, 0)
        self.assertEqual(list(report['phases']), ['browser', 'inventory', 'notes'])
        browser.BrowserAction.return_value.run.assert_called_once_with(CONFIG['browser_phase_seconds'])
        self.assertEqual(report['status'], 'complete')
        self.assertFalse(self.showinfo.called)                 # a complete run ends quietly

    def test_the_notes_get_the_run_report_as_lines_and_all_the_time_before_the_15_minute_mark(self):
        code, report, imports, browser, inventory, notes = self.run_demo('Full demo')
        seconds, lines = notes.NotesAction.return_value.run.call_args.args
        self.assertTrue(CONFIG['total_demo_seconds']-30 < seconds <= CONFIG['total_demo_seconds']-5)
        self.assertTrue(lines[0][1].startswith('Demo run '))
        self.assertTrue(any('inventory_oct17.ods: 7 items, total 3,486, within budget' == text
                            for _, text in lines))

    def test_full_demo_continues_inventory_after_browser_failure(self):
        code, report, imports, browser, inventory, notes = self.run_demo(
            'Full demo', RuntimeError('browser controls missing'))
        self.assertEqual(code, 1)
        self.assertIn('inventory_action', imports)
        inventory.InventoryAction.return_value.run.assert_called_once_with(CONFIG['full_phase_seconds'])
        self.assertTrue(report['phases']['inventory']['complete'])
        self.assertEqual(report['phases']['browser']['error'], 'browser controls missing')
        self.assertTrue(report['status'].startswith('partial'))
        self.assertEqual(report['windows_vm_validation'], 'pending')
        notes.NotesAction.return_value.run.assert_called_once()   # the notes are still written

    def test_unused_browsing_time_is_carried_to_the_spreadsheet(self):
        code, report, imports, browser, inventory, notes = self.run_demo('Full demo', unused_seconds=95.5)
        seconds = inventory.InventoryAction.return_value.run.call_args.args[0]
        self.assertAlmostEqual(seconds, CONFIG['full_phase_seconds']+95.5, delta=1)

    def test_without_unused_browsing_time_the_spreadsheet_gets_eight_minutes(self):
        code, report, imports, browser, inventory, notes = self.run_demo('Full demo')
        inventory.InventoryAction.return_value.run.assert_called_once_with(480)

    def test_a_complete_run_shows_no_result_box_so_the_notes_stay_visible(self):
        code, report, *_ = self.run_demo('Inventory pilot')
        self.assertEqual(code, 0)
        self.assertEqual(report['status'], 'complete')
        self.assertFalse(self.showinfo.called)

    def test_result_box_says_where_the_workbook_was_saved(self):
        self.run_demo('Inventory pilot', inventory_result={
            'complete': False, 'error': 'Wrong record at row 6',
            'saved_path': r'C:\Users\User\Desktop\agent\inventory_oct17.ods'})
        self.assertIn(r'Workbook saved: C:\Users\User\Desktop\agent\inventory_oct17.ods', self.result_box)
        self.assertIn('Status: partial: spreadsheet incomplete', self.result_box)
        self.run_demo('Browser pilot', browser_error=RuntimeError('no Chrome'))
        self.assertNotIn('Workbook saved', self.result_box)

    def test_an_unfinished_spreadsheet_is_reported_as_partial_not_complete(self):
        code, report, imports, browser, inventory, notes = self.run_demo('Full demo', inventory_result={
            'complete': False, 'error': 'Wrong record at row 6', 'cut_short_at': 'rows',
            'saved_path': r'C:\Users\User\Desktop\agent\inventory_oct17.ods'})
        self.assertEqual(code, 1)
        self.assertEqual(report['status'], 'partial: spreadsheet incomplete')
        self.assertEqual(report['error'], 'Wrong record at row 6')
        self.assertTrue(report['phases']['inventory']['saved_path'].endswith('inventory_oct17.ods'))

    def test_a_missing_saved_path_is_filled_in_from_the_agents_own_path(self):
        code, report, *_ = self.run_demo('Inventory pilot', inventory_result={'complete': True})
        self.assertEqual(report['phases']['inventory']['saved_path'], 'inventory_oct17.ods')

    def test_notes_that_typed_nothing_make_the_run_partial(self):
        code, report, *_ = self.run_demo('Full demo', notes_result={'lines_typed': 0})
        self.assertEqual(code, 1)
        self.assertEqual(report['status'], 'partial: notes not written')

    def test_a_notes_failure_does_not_lose_the_workbook_and_is_reported(self):
        code, report, imports, browser, inventory, notes = self.run_demo(
            'Full demo', notes_error=RuntimeError('Notepad would not open'))
        self.assertEqual(report['phases']['notes'], {'error': 'Notepad would not open', 'saved_path': None})
        self.assertTrue(report['phases']['inventory']['complete'])
        self.assertEqual(report['status'], 'partial: notes not written')
        self.assertEqual(code, 1)

    def test_notes_are_skipped_when_under_45_seconds_remain(self):
        code, report, imports, browser, inventory, notes = self.run_demo(
            'Full demo', patches=(('notes_seconds', Mock(return_value=44)),))
        notes.NotesAction.assert_not_called()
        self.assertEqual(report['phases']['notes']['skipped'], 'not enough time left')
        self.assertEqual(report['status'], 'partial: notes not written')

    def test_notes_are_written_with_exactly_45_seconds_left(self):
        code, report, imports, browser, inventory, notes = self.run_demo(
            'Full demo', patches=(('notes_seconds', Mock(return_value=45)),))
        notes.NotesAction.return_value.run.assert_called_once()

    def test_pilots_do_not_carry_unused_time(self):
        code, report, imports, browser, inventory, notes = self.run_demo('Browser pilot', unused_seconds=50)
        inventory.InventoryAction.assert_not_called()

    def test_inventory_pilot_never_loads_browser_module(self):
        code, report, imports, browser, inventory, notes = self.run_demo('Inventory pilot')
        self.assertEqual(code, 0)
        self.assertNotIn('browser_action', imports)
        browser.BrowserAction.assert_not_called()
        inventory.InventoryAction.return_value.run.assert_called_once_with(480)   # long enough to finish the table
        notes.NotesAction.return_value.run.assert_called_once()
        self.assertEqual(notes.NotesAction.return_value.run.call_args.args[0], CONFIG['notes_phase_seconds'])

    def test_the_modules_a_mode_needs_are_loaded_before_anything_is_typed(self):
        code, report, imports, browser, inventory, notes = self.run_demo('Full demo')
        loaded = [name for name in imports if name in ('browser_action', 'inventory_action', 'notes_action')]
        self.assertEqual(loaded[:3], ['browser_action', 'inventory_action', 'notes_action'])   # by check_files
        self.assertEqual(report['agent_files'], {'build': None, 'changed': [], 'broken': []})


class PreflightTests(unittest.TestCase):
    def load(self):
        tk = types.ModuleType('tkinter'); tk.Tk = Mock()
        tk.messagebox = types.ModuleType('messagebox'); tk.simpledialog = types.ModuleType('simpledialog')
        tk.ttk = types.ModuleType('ttk')
        spec = importlib.util.spec_from_file_location('main_preflight_under_test',
                    Path(__file__).resolve().parents[1]/'main.py')
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'tkinter': tk}):
            spec.loader.exec_module(module)
        return module, tk

    CONFIG = {'chrome_shortcut': 'Google Chrome', 'calc_shortcut': 'OpenOffice Calc'}

    def test_open_application_blocks_and_missing_shortcut_only_warns(self):
        module, _ = self.load()
        gui = Mock()
        gui.window_handles.side_effect = lambda title_pattern: [101] if 'Chrome' in title_pattern else []
        with tempfile.TemporaryDirectory() as home:
            (Path(home)/'Desktop').mkdir()
            (Path(home)/'Desktop'/'Google Chrome.lnk').write_text('')
            with patch.object(module.Path, 'home', return_value=Path(home)), \
                 patch.dict(module.os.environ, {}, clear=True):
                blockers, warnings = module.preflight(gui, self.CONFIG, 'Full demo')
        self.assertEqual(blockers, ['Google Chrome is already open. Close all of its windows.'])
        self.assertEqual(warnings, ['No desktop shortcut named "OpenOffice Calc" was found.'])

    NOTES_CONFIG = {**CONFIG, 'notes_phase_seconds': 90, 'notes_shortcut': 'Notepad'}

    def preflight_with_notes(self, mode, open_titles=(), shortcuts=()):
        module, _ = self.load()
        gui = Mock()
        gui.window_handles.side_effect = lambda title_pattern: [7] if any(
            t in title_pattern for t in open_titles) else []
        with tempfile.TemporaryDirectory() as home:
            (Path(home)/'Desktop').mkdir()
            for name in shortcuts:
                (Path(home)/'Desktop'/f'{name}.lnk').write_text('')
            with patch.object(module.Path, 'home', return_value=Path(home)), \
                 patch.dict(module.os.environ, {}, clear=True):
                return module.preflight(gui, self.NOTES_CONFIG, mode)

    def test_notepad_left_open_by_the_last_run_blocks_the_start(self):
        blockers, warnings = self.preflight_with_notes(
            'Full demo', open_titles=('Notepad',),
            shortcuts=('Google Chrome', 'OpenOffice Calc', 'Notepad'))
        self.assertEqual(len(blockers), 1)
        self.assertIn('Notepad is already open', blockers[0])
        self.assertIn('notes of the last run', blockers[0])
        self.assertEqual(warnings, [])

    def test_a_missing_notepad_shortcut_only_warns_that_win_r_is_used(self):
        blockers, warnings = self.preflight_with_notes(
            'Full demo', shortcuts=('Google Chrome', 'OpenOffice Calc'))
        self.assertEqual(blockers, [])
        self.assertEqual(len(warnings), 1)
        self.assertIn('"Notepad"', warnings[0]); self.assertIn('Win+R', warnings[0])

    def test_the_browser_pilot_does_not_look_for_notepad_or_calc(self):
        blockers, warnings = self.preflight_with_notes('Browser pilot', open_titles=('Notepad', 'Calc'),
                                                       shortcuts=('Google Chrome',))
        self.assertEqual((blockers, warnings), ([], []))

    def test_the_inventory_pilot_checks_calc_and_notepad_but_not_chrome(self):
        blockers, warnings = self.preflight_with_notes('Inventory pilot', open_titles=('Chrome',))
        self.assertEqual(blockers, [])
        self.assertEqual(len(warnings), 2)
        self.assertTrue(all('Google Chrome' not in w for w in warnings))

    def test_a_shortcut_on_the_public_or_onedrive_desktop_is_found(self):
        module, _ = self.load()
        gui = Mock(); gui.window_handles.return_value = []
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as public:
            (Path(public)/'Desktop').mkdir()
            for name in ('Google Chrome', 'OpenOffice Calc'):
                (Path(public)/'Desktop'/f'{name}.lnk').write_text('')
            with patch.object(module.Path, 'home', return_value=Path(home)), \
                 patch.dict(module.os.environ, {'PUBLIC': public}, clear=True):
                self.assertEqual(module.preflight(gui, self.CONFIG, 'Full demo'), ([], []))

    def test_pilots_only_check_the_application_they_use(self):
        module, _ = self.load()
        gui = Mock(); gui.window_handles.return_value = []
        with tempfile.TemporaryDirectory() as home, \
             patch.object(module.Path, 'home', return_value=Path(home)), \
             patch.dict(module.os.environ, {}, clear=True):
            _, browser_only = module.preflight(gui, self.CONFIG, 'Browser pilot')
            _, calc_only = module.preflight(gui, self.CONFIG, 'Inventory pilot')
        self.assertEqual(len(browser_only), 1); self.assertIn('Google Chrome', browser_only[0])
        self.assertEqual(len(calc_only), 1); self.assertIn('OpenOffice Calc', calc_only[0])

    def keyboard_state(self, caps, layout):
        module, _ = self.load()
        api = types.ModuleType('win32api')
        api.GetKeyState = Mock(return_value=1 if caps else 0)
        api.GetKeyboardLayout = Mock(return_value=layout)
        gui = Mock(); gui.window_handles.return_value = []
        with tempfile.TemporaryDirectory() as home:
            desktop = Path(home)/'Desktop'; desktop.mkdir()
            for name in ('Google Chrome', 'OpenOffice Calc'):
                (desktop/f'{name}.lnk').write_text('')
            with patch.object(module.Path, 'home', return_value=Path(home)), \
                 patch.dict(module.os.environ, {}, clear=True), \
                 patch.dict(sys.modules, {'win32api': api}):
                return module.preflight(gui, self.CONFIG, 'Full demo')

    def test_caps_lock_blocks_the_start(self):
        blockers, warnings = self.keyboard_state(caps=True, layout=0x04090409)
        self.assertEqual(len(blockers), 1); self.assertIn('Caps Lock', blockers[0])
        self.assertEqual(warnings, [])

    def test_non_us_keyboard_layout_is_a_warning(self):
        blockers, warnings = self.keyboard_state(caps=False, layout=0x08090809)   # English (UK)
        self.assertEqual(blockers, [])
        self.assertEqual(len(warnings), 1); self.assertIn('keyboard layout', warnings[0])

    def test_normal_keyboard_state_raises_nothing(self):
        self.assertEqual(self.keyboard_state(caps=False, layout=0x04090409), ([], []))

    def test_cancelling_a_blocker_stops_before_any_input(self):
        module, tk = self.load()
        module.preflight = Mock(return_value=(['Google Chrome is already open.'], []))
        tk.messagebox.askretrycancel = Mock(return_value=False)
        module.messagebox = tk.messagebox; module.tk = tk
        with self.assertRaises(module.NotStarted):
            module.confirm_preflight(Mock(), self.CONFIG, 'Full demo')

    def test_retry_rechecks_until_the_problem_is_fixed(self):
        module, tk = self.load()
        module.preflight = Mock(side_effect=[(['Calc open'], []), ([], [])])
        tk.messagebox.askretrycancel = Mock(return_value=True)
        module.messagebox = tk.messagebox; module.tk = tk
        module.confirm_preflight(Mock(), self.CONFIG, 'Full demo')
        self.assertEqual(module.preflight.call_count, 2)


class TimingAndLauncherTests(unittest.TestCase):
    def load(self):
        tk = types.ModuleType('tkinter'); tk.Tk = Mock()
        tk.StringVar = Mock(return_value=Mock(get=Mock(return_value='Full demo')))
        tk.messagebox = types.ModuleType('messagebox'); tk.messagebox.showinfo = Mock()
        tk.ttk = types.ModuleType('ttk')
        tk.ttk.Label = Mock(); tk.ttk.Radiobutton = Mock(); tk.ttk.Button = Mock()
        spec = importlib.util.spec_from_file_location('main_timing_under_test',
                    Path(__file__).resolve().parents[1]/'main.py')
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'tkinter': tk}):
            spec.loader.exec_module(module)
        return module, tk

    CONFIG = {'full_phase_seconds': 480, 'browser_phase_seconds': 270, 'pilot_phase_seconds': 120,
              'total_demo_seconds': 900, 'inventory_pilot_seconds': 480, 'default_budget_sgd': 5000,
              'notes_phase_seconds': 90, 'carry_unused_browser_time': True}

    def test_spreadsheet_gets_eight_minutes_plus_unused_browsing_up_to_what_is_left(self):
        module, _ = self.load()
        seconds = lambda elapsed, unused=0, **extra: module.inventory_seconds(
            {**self.CONFIG, **extra}, 'Full demo', elapsed, {'unused_seconds': unused})
        self.assertEqual(seconds(270), 480)                  # browsing used its 270 s: just the 480 s
        self.assertEqual(seconds(180, 90), 570)              # browsing ended 90 s early: carried over
        self.assertEqual(seconds(330, 90), 480)              # capped at 900 - 330 - 90 notes seconds
        self.assertEqual(seconds(445), 365)                  # browser closed late: 900 - 445 - 90
        self.assertEqual(seconds(880), 60)                   # never less than a minute
        self.assertEqual(seconds(180, 90, carry_unused_browser_time=False), 480)
        for elapsed in (0, 100, 180, 270, 330, 445, 600):
            self.assertLessEqual(elapsed+seconds(elapsed, 90)+90, 900)

    def test_unused_time_that_is_not_a_positive_number_is_ignored(self):
        module, _ = self.load()
        for result in (None, {}, {'unused_seconds': None}, {'unused_seconds': -20}, 'x'):
            self.assertEqual(module.inventory_seconds(self.CONFIG, 'Full demo', 100, result), 480)

    def test_inventory_pilot_gets_long_enough_to_finish_the_table(self):
        module, _ = self.load()
        self.assertEqual(module.inventory_seconds(self.CONFIG, 'Inventory pilot', 3), 480)

    def test_notes_get_everything_left_before_the_fifteen_minute_mark_less_five_seconds(self):
        module, _ = self.load()
        notes = lambda elapsed, mode='Full demo', **extra: module.notes_seconds(
            {**self.CONFIG, **extra}, mode, elapsed)
        self.assertEqual(module.NOTES_END_MARGIN, 5)
        self.assertEqual(notes(720), 175)
        self.assertEqual(notes(810), 85)
        self.assertEqual(notes(855), 40)                     # under 45: main() skips the notes
        self.assertEqual(notes(990), 0)                      # never negative
        self.assertEqual(notes(500, 'Inventory pilot'), 90)  # pilots: notes_phase_seconds
        self.assertEqual(notes(500, 'Browser pilot'), 90)
        self.assertEqual(notes(500, notes_phase_seconds=0), 0)   # switched off

    def test_shipped_config_adds_up_to_fifteen_minutes(self):
        config = json.loads((Path(__file__).resolve().parents[1]/'config.json').read_text())
        self.assertEqual(config['browser_phase_seconds'], 270)
        self.assertEqual(config['full_phase_seconds'], 480)
        self.assertEqual(config['notes_phase_seconds'], 90)
        planned = config['browser_phase_seconds']+config['full_phase_seconds']+config['notes_phase_seconds']
        self.assertEqual(config['total_demo_seconds'], 900)
        self.assertEqual(planned, 840)         # the notes take the 60 s that are left, less 5 s
        self.assertLessEqual(planned, config['total_demo_seconds'])
        self.assertTrue(config['carry_unused_browser_time'])

    def test_start_does_not_ask_for_a_budget(self):
        module, tk = self.load()
        selection = module.launcher(dict(self.CONFIG))
        start = next(c.kwargs['command'] for c in tk.ttk.Button.call_args_list
                     if c.kwargs.get('text') == 'Start')
        start()
        self.assertEqual(selection, ['Full demo', 5000.0])
        self.assertFalse(hasattr(module, 'simpledialog'))


def load_main(name='main_build_under_test'):
    tk = types.ModuleType('tkinter'); tk.Tk = Mock()
    tk.messagebox = types.ModuleType('messagebox'); tk.messagebox.showinfo = Mock()
    tk.ttk = types.ModuleType('ttk')
    spec = importlib.util.spec_from_file_location(name, AGENT/'main.py')
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'tkinter': tk}):
        spec.loader.exec_module(module)
    return module, tk


def sha(data):
    import hashlib
    return hashlib.sha256(data.replace(b'\r\n', b'\n')).hexdigest()


class BuildDifferencesTests(unittest.TestCase):
    def setUp(self):
        self.module, _ = load_main()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name)

    def build(self, files, name='agent-20261012-1930'):
        for filename, data in files.items():
            (self.folder/filename).write_bytes(data)
        (self.folder/'BUILD.json').write_text(json.dumps(
            {'name': name, 'files': {f: sha(d) for f, d in files.items()}}))
        return self.folder/'BUILD.json'

    def test_a_working_copy_without_build_json_is_not_compared(self):
        self.assertEqual(self.module.build_differences(self.folder/'BUILD.json'), (None, []))

    def test_an_unreadable_build_file_is_treated_as_a_working_copy(self):
        (self.folder/'BUILD.json').write_text('{not json')
        self.assertEqual(self.module.build_differences(self.folder/'BUILD.json'), (None, []))

    def test_files_that_match_the_build_are_not_reported(self):
        build = self.build({'main.py': b'print(1)\n', 'domain.py': b'x = 2\n'})
        self.assertEqual(self.module.build_differences(build), ('agent-20261012-1930', []))

    def test_a_changed_file_is_named(self):
        build = self.build({'main.py': b'print(1)\n', 'domain.py': b'x = 2\n'})
        (self.folder/'domain.py').write_bytes(b'x = 3\n')
        self.assertEqual(self.module.build_differences(build)[1], ['domain.py'])

    def test_a_missing_file_is_named_as_missing(self):
        build = self.build({'main.py': b'print(1)\n', 'domain.py': b'x = 2\n'})
        (self.folder/'domain.py').unlink()
        self.assertEqual(self.module.build_differences(build)[1], ['domain.py (missing)'])

    def test_changed_and_missing_files_are_listed_in_name_order(self):
        build = self.build({'b.py': b'1\n', 'a.py': b'2\n', 'c.py': b'3\n'})
        (self.folder/'c.py').write_bytes(b'changed\n'); (self.folder/'a.py').unlink()
        self.assertEqual(self.module.build_differences(build)[1], ['a.py (missing)', 'c.py'])

    def test_crlf_against_lf_counts_as_equal(self):
        build = self.build({'main.py': b'a = 1\nb = 2\n'})         # hashed with LF
        (self.folder/'main.py').write_bytes(b'a = 1\r\nb = 2\r\n')   # copied through a Windows share
        self.assertEqual(self.module.build_differences(build)[1], [])

    def test_a_real_edit_is_still_seen_through_crlf(self):
        build = self.build({'main.py': b'a = 1\nb = 2\n'})
        (self.folder/'main.py').write_bytes(b'a = 1\r\nb = 3\r\n')
        self.assertEqual(self.module.build_differences(build)[1], ['main.py'])

    def test_the_default_build_file_sits_next_to_main_py(self):
        self.assertEqual(self.module.BUILD, AGENT/'BUILD.json')


class CheckFilesTests(unittest.TestCase):
    def setUp(self):
        self.module, _ = load_main('main_check_under_test')
        self.module.build_differences = Mock(return_value=('agent-1', ['domain.py']))

    def check(self, mode, failing=()):
        loaded = []
        def importing(name):
            loaded.append(name)
            if name in failing:
                raise ImportError(f'No module named {name}')
        with patch.object(self.module.importlib, 'import_module', side_effect=importing):
            return self.module.check_files(mode), loaded

    def test_every_mode_loads_exactly_the_modules_it_will_use(self):
        self.assertEqual(self.check('Full demo')[1], ['browser_action', 'inventory_action', 'notes_action'])
        self.assertEqual(self.check('Browser pilot')[1], ['browser_action'])
        self.assertEqual(self.check('Inventory pilot')[1], ['inventory_action', 'notes_action'])

    def test_the_result_carries_the_build_and_the_changed_files(self):
        files, _ = self.check('Browser pilot')
        self.assertEqual(files, {'build': 'agent-1', 'changed': ['domain.py'], 'broken': []})

    def test_a_module_that_fails_to_load_is_listed_as_broken_and_the_rest_are_still_tried(self):
        files, loaded = self.check('Full demo', failing=('inventory_action',))
        self.assertEqual(files['broken'], ['inventory_action.py: No module named inventory_action'])
        self.assertEqual(loaded, ['browser_action', 'inventory_action', 'notes_action'])

    def test_an_unknown_mode_loads_nothing(self):
        self.assertEqual(self.check('Something else')[1], [])


class ConfirmFilesTests(unittest.TestCase):
    def setUp(self):
        self.module, self.tk = load_main('main_confirm_under_test')
        self.messagebox = self.module.messagebox = Mock()
        self.module.tk = self.tk
        self.gui = Mock()

    def test_clean_files_ask_nothing_and_log_nothing(self):
        self.module.confirm_files(self.gui, {'build': 'agent-1', 'changed': [], 'broken': []})
        self.messagebox.showerror.assert_not_called(); self.messagebox.askokcancel.assert_not_called()
        self.gui.event.assert_not_called()

    def test_a_module_that_cannot_load_stops_the_run_before_it_starts(self):
        with self.assertRaises(self.module.NotStarted):
            self.module.confirm_files(self.gui, {'build': 'agent-1', 'changed': ['domain.py'],
                                                 'broken': ['notes_action.py: boom']})
        self.messagebox.showerror.assert_called_once()
        self.messagebox.askokcancel.assert_not_called()
        self.assertIn('notes_action.py: boom', self.messagebox.showerror.call_args.args[1])

    def test_changed_files_can_be_run_anyway_when_the_operator_agrees(self):
        self.messagebox.askokcancel.return_value = True
        self.module.confirm_files(self.gui, {'build': 'agent-1', 'changed': ['domain.py'], 'broken': []})
        text = self.messagebox.askokcancel.call_args.args[1]
        self.assertIn('agent-1.zip', text); self.assertIn('domain.py', text)

    def test_changed_files_stop_the_run_when_the_operator_cancels(self):
        self.messagebox.askokcancel.return_value = False
        with self.assertRaises(self.module.NotStarted):
            self.module.confirm_files(self.gui, {'build': None, 'changed': ['domain.py'], 'broken': []})
        self.assertIn('the newest ZIP', self.messagebox.askokcancel.call_args.args[1])


class ShowResultTests(unittest.TestCase):
    def setUp(self):
        self.module, self.tk = load_main('main_result_under_test')
        self.messagebox = self.module.messagebox = Mock()
        self.module.tk = self.tk

    def test_a_complete_run_returns_zero_without_opening_anything(self):
        self.assertEqual(self.module.show_result({'status': 'complete', 'phases': {}}), 0)
        self.tk.Tk.assert_not_called(); self.messagebox.showinfo.assert_not_called()

    def test_any_other_status_shows_a_box_and_returns_one(self):
        report = {'status': 'failed', 'error': 'boom', 'elapsed_seconds': 90, 'phases': {}}
        self.assertEqual(self.module.show_result(report), 1)
        text = self.messagebox.showinfo.call_args.args[1]
        self.assertIn('Status: failed', text); self.assertIn('1.5 minutes', text); self.assertIn('boom', text)
        self.assertNotIn('Workbook saved', text)
        self.tk.Tk.return_value.destroy.assert_called_once()


if __name__ == '__main__':
    unittest.main()
