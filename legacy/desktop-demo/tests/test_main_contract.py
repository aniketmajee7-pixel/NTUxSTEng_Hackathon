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


class MainContractTests(unittest.TestCase):
    def run_demo(self, mode, browser_error=None, unused_seconds=None, inventory_result=None):
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
        inventory.InventoryAction.return_value.run.return_value = inventory_result or {'total_sgd': 3486}
        inventory.InventoryAction.return_value.path = Path('inventory_oct10.ods')
        modules = {'tkinter': tk, 'gui_support': support, 'pyautogui': pg,
                   'browser_action': browser, 'inventory_action': inventory}
        spec = importlib.util.spec_from_file_location('main_contract_under_test',
                    Path(__file__).resolve().parents[1]/'main.py')
        module = importlib.util.module_from_spec(spec)
        imports = []
        original_import = builtins.__import__
        def importing(name, *args, **kwargs):
            imports.append(name)
            return original_import(name, *args, **kwargs)
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, modules):
            spec.loader.exec_module(module)
            # A local namespace avoids changing os.name globally (which affects pathlib).
            module.os = types.SimpleNamespace(name='nt')
            module.launcher = Mock(return_value=[mode, 5000])
            module.preflight = Mock(return_value=([], []))  # tested separately below
            with patch.object(module.Path, 'home', return_value=Path(directory)), \
                 patch.object(module.logging, 'basicConfig'), \
                 patch.object(module.logging, 'exception'), \
                 patch('builtins.__import__', side_effect=importing):
                code = module.main()
            reports = list(Path(directory).rglob('report.json'))
            self.assertEqual(len(reports), 1)
            report = json.loads(reports[0].read_text())
        support.GUI.return_value.finish.assert_called_once()
        self.result_box = tk.messagebox.showinfo.call_args.args[1]
        return code, report, imports, browser, inventory

    def test_browser_pilot_never_loads_inventory_module(self):
        code, report, imports, browser, inventory = self.run_demo('Browser pilot')
        self.assertEqual(code, 0)
        self.assertNotIn('inventory_action', imports)
        inventory.InventoryAction.assert_not_called()
        browser.BrowserAction.return_value.run.assert_called_once_with(120)
        stages = report['startup_stages']
        self.assertEqual([s['stage'] for s in stages],
                         ['gui_dependencies_imported', 'gui_initialized', 'browser_module_imported'])
        self.assertTrue(all(s['agent_cpu_seconds'] >= 0 for s in stages))

    def test_full_demo_continues_inventory_after_browser_failure(self):
        code, report, imports, browser, inventory = self.run_demo('Full demo', RuntimeError('browser controls missing'))
        self.assertEqual(code, 1)
        self.assertIn('inventory_action', imports)
        inventory.InventoryAction.return_value.run.assert_called_once_with(480)
        self.assertEqual(report['phases']['inventory']['total_sgd'], 3486)
        self.assertEqual(report['phases']['browser']['error'], 'browser controls missing')
        self.assertTrue(report['status'].startswith('partial'))
        self.assertEqual(report['windows_vm_validation'], 'pending')

    def test_spreadsheet_is_capped_at_eight_minutes_even_if_browsing_ended_early(self):
        code, report, imports, browser, inventory = self.run_demo('Full demo', unused_seconds=95.5)
        inventory.InventoryAction.return_value.run.assert_called_once_with(480)

    def test_result_box_says_where_the_workbook_was_saved(self):
        self.run_demo('Inventory pilot', inventory_result={
            'complete': True, 'total_sgd': 3486,
            'saved_path': 'C:/Users/User/Documents/HackathonDemo/inventory_oct10.ods'})
        self.assertIn('Workbook saved: C:/Users/User/Documents/HackathonDemo/inventory_oct10.ods',
                      self.result_box)
        self.run_demo('Browser pilot')
        self.assertNotIn('Workbook saved', self.result_box)

    def test_an_unfinished_spreadsheet_is_reported_as_partial_not_complete(self):
        code, report, imports, browser, inventory = self.run_demo('Full demo', inventory_result={
            'complete': False, 'error': 'Wrong record at row 6', 'cut_short_at': 'rows',
            'saved_path': 'C:/Users/User/Documents/HackathonDemo/inventory_oct10.ods'})
        self.assertEqual(code, 1)
        self.assertEqual(report['status'], 'partial: spreadsheet incomplete')
        self.assertEqual(report['error'], 'Wrong record at row 6')
        self.assertTrue(report['phases']['inventory']['saved_path'].endswith('inventory_oct10.ods'))

    def test_pilots_do_not_carry_unused_time(self):
        code, report, imports, browser, inventory = self.run_demo('Browser pilot', unused_seconds=50)
        inventory.InventoryAction.assert_not_called()

    def test_inventory_pilot_never_loads_browser_module(self):
        code, report, imports, browser, inventory = self.run_demo('Inventory pilot')
        self.assertEqual(code, 0)
        self.assertNotIn('browser_action', imports)
        browser.BrowserAction.assert_not_called()
        inventory.InventoryAction.return_value.run.assert_called_once_with(480)   # long enough to finish the table


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


if __name__ == '__main__':
    unittest.main()


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

    CONFIG = {'full_phase_seconds': 480, 'browser_phase_seconds': 420, 'pilot_phase_seconds': 120,
              'total_demo_seconds': 900, 'inventory_pilot_seconds': 480, 'default_budget_sgd': 5000}

    def test_spreadsheet_gets_eight_minutes_or_what_is_left_of_fifteen(self):
        module, _ = self.load()
        seconds = lambda elapsed, **extra: module.inventory_seconds(
            {**self.CONFIG, **extra}, 'Full demo', elapsed, {'unused_seconds': 90})
        self.assertEqual(seconds(330), 480)            # browsing ended early: still capped at 8 min
        self.assertEqual(seconds(420), 480)            # the planned hand-over at 7:00
        self.assertEqual(seconds(445), 455)            # browser closed late: the end stays at 15:00
        self.assertEqual(seconds(330, carry_unused_browser_time=True), 570)
        self.assertEqual(seconds(445, carry_unused_browser_time=True), 455)
        for elapsed in (0, 100, 330, 420, 445, 600):
            self.assertLessEqual(elapsed+seconds(elapsed), 900)

    def test_inventory_pilot_gets_long_enough_to_finish_the_table(self):
        module, _ = self.load()
        self.assertEqual(module.inventory_seconds(self.CONFIG, 'Inventory pilot', 3), 480)

    def test_shipped_config_adds_up_to_fifteen_minutes(self):
        config = json.loads((Path(__file__).resolve().parents[1]/'config.json').read_text())
        self.assertEqual(config['browser_phase_seconds'], 420)
        self.assertEqual(config['full_phase_seconds'], 480)
        self.assertEqual(config['browser_phase_seconds']+config['full_phase_seconds'],
                         config['total_demo_seconds'])
        self.assertFalse(config['carry_unused_browser_time'])

    def test_start_does_not_ask_for_a_budget(self):
        module, tk = self.load()
        selection = module.launcher(dict(self.CONFIG))
        start = next(c.kwargs['command'] for c in tk.ttk.Button.call_args_list
                     if c.kwargs.get('text') == 'Start')
        start()
        self.assertEqual(selection, ['Full demo', 5000.0])
        self.assertFalse(hasattr(module, 'simpledialog'))
