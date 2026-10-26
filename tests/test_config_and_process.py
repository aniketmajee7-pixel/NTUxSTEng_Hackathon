"""config.json against Appendix B of the implementation report, and the process-authenticity
rules checked statically: the agent must leave no file, log or child process behind."""
import ast
import json
from pathlib import Path
import re
import unittest

AGENT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((AGENT/'config.json').read_text())

# Report, Appendix B (Table 10): the key names and values it gives.
APPENDIX_B = {
    'browser_phase_seconds': 270, 'full_phase_seconds': 480, 'notes_phase_seconds': 90,
    'total_demo_seconds': 900, 'browser_visit_seconds': [30, 45], 'browser_find_probability': 0.3,
    'browser_max_searches': 2, 'browser_max_revisits': 2, 'browser_revisit_probability': 0.5,
    'browser_typo_probability': 0.3, 'browser_accept_consent': True, 'browser_close_popups': True,
    'keyboard_scroll_probability': 0.15, 'calc_drag_select_probability': 0.6,
    'spreadsheet_typo_scale': 1.25, 'calc_wrap_up_seconds': 20, 'default_budget_sgd': 5000,
    'carry_unused_browser_time': True,
}


class ConfigTests(unittest.TestCase):
    def test_every_setting_in_appendix_b_has_the_value_the_report_gives(self):
        for key, value in APPENDIX_B.items():
            with self.subTest(key=key):
                self.assertIn(key, CONFIG)
                self.assertEqual(CONFIG[key], value)

    def test_there_are_27_homepages_and_10_known_blocked_sites(self):
        self.assertEqual(len(CONFIG['homepages']), 27)
        self.assertEqual(len(CONFIG['browser_known_blocked']), 10)

    def test_every_homepage_has_an_https_address_and_a_search_keyword(self):
        for site in CONFIG['homepages']:
            with self.subTest(site=site['url']):
                self.assertRegex(site['url'], r'^https://[a-z0-9.-]+$')
                self.assertTrue(site['term'].strip())
        urls = [site['url'] for site in CONFIG['homepages']]
        self.assertEqual(len(set(urls)), len(urls))

    def test_the_known_blocked_sites_are_all_among_the_homepages(self):
        urls = {site['url'] for site in CONFIG['homepages']}
        self.assertLessEqual(set(CONFIG['browser_known_blocked']), urls)

    def test_the_visit_range_is_ordered_and_the_probabilities_are_probabilities(self):
        low, high = CONFIG['browser_visit_seconds']
        self.assertLess(low, high)
        for key, value in CONFIG.items():
            if key.endswith('_probability'):
                self.assertTrue(0 <= value <= 1, key)

    def test_ranges_in_the_config_are_ordered_pairs(self):
        for key, value in CONFIG.items():
            if key.endswith('_seconds') and isinstance(value, list) or key.endswith('_fraction'):
                with self.subTest(key=key):
                    self.assertEqual(len(value), 2)
                    self.assertLessEqual(value[0], value[1])

    def test_the_shortcuts_the_run_double_clicks_are_named(self):
        self.assertEqual((CONFIG['chrome_shortcut'], CONFIG['calc_shortcut'], CONFIG['notes_shortcut']),
                         ('Google Chrome', 'OpenOffice Calc', 'Notepad'))

    def test_every_key_the_code_reads_without_a_default_exists_in_the_config(self):
        """config['key'] (no default) in the agent modules must be in config.json."""
        needed = set()
        for path in AGENT.glob('*.py'):
            if path.name == 'inspect_ui.py':
                continue
            needed |= set(re.findall(r"\bconfig\['([a-z_]+)'\]", path.read_text()))
        # keys the code assigns itself (the per-run style) are not read from the file
        assigned = set(re.findall(r"\bconfig\['([a-z_]+)'\]\s*=", (AGENT/'main.py').read_text()))
        self.assertEqual(sorted(needed-assigned-set(CONFIG)), [])


AGENT_MODULES = sorted(path for path in AGENT.glob('*.py') if path.name != 'inspect_ui.py')


def tree(path):
    return ast.parse(path.read_text(), filename=str(path))


def imported(path):
    names = set()
    for node in ast.walk(tree(path)):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


class ProcessAuthenticityTests(unittest.TestCase):
    def test_the_modules_under_test_are_the_submitted_ones(self):
        self.assertEqual([p.name for p in AGENT_MODULES],
                         ['browser_action.py', 'domain.py', 'gui_support.py', 'inventory_action.py',
                          'main.py', 'notes_action.py', 'save_dialog.py'])

    def test_no_agent_module_starts_a_child_process_a_thread_or_a_logger(self):
        banned = {'subprocess', 'multiprocessing', 'threading', 'logging', 'ctypes', 'shutil', 'tempfile',
                  'webbrowser', 'socket', 'urllib.request', 'http', 'requests', 'csv', 'sqlite3', 'pickle', 'shelve'}
        for path in AGENT_MODULES:
            with self.subTest(module=path.name):
                self.assertEqual(sorted(imported(path) & banned), [])

    def test_nothing_launches_a_program_other_than_through_the_desktop(self):
        calls = {'startfile', 'system', 'popen', 'spawn', 'Popen', 'run', 'call', 'check_output',
                 'ShellExecute', 'ShellExecuteEx', 'CreateProcess'}
        for path in AGENT_MODULES:
            for node in ast.walk(tree(path)):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    owner = node.func.value.id if isinstance(node.func.value, ast.Name) else ''
                    if owner in ('os', 'subprocess', 'win32api', 'win32process', 'win32com'):
                        self.assertNotIn(node.func.attr, calls,
                                         f'{path.name}:{node.lineno} {owner}.{node.func.attr}')

    def test_no_agent_module_opens_a_file_for_writing_or_creates_one(self):
        writers = {'write_text', 'write_bytes', 'mkdir', 'makedirs', 'touch', 'mkstemp', 'mkdtemp',
                   'unlink', 'rmtree', 'copyfile', 'FileHandler', 'basicConfig'}
        os_writers = {'rename', 'replace', 'remove', 'rmdir', 'truncate', 'write'}
        for path in AGENT_MODULES:
            for node in ast.walk(tree(path)):
                if not isinstance(node, ast.Call):
                    continue
                where = f'{path.name}:{node.lineno}'
                name = (node.func.attr if isinstance(node.func, ast.Attribute)
                        else node.func.id if isinstance(node.func, ast.Name) else '')
                self.assertNotIn(name, writers, where)
                if isinstance(node.func, ast.Attribute) and getattr(node.func.value, 'id', '') == 'os':
                    self.assertNotIn(name, os_writers, where)
                if name == 'open':
                    modes = [a.value for a in node.args[1:2] if isinstance(a, ast.Constant)]
                    modes += [k.value.value for k in node.keywords
                              if k.arg == 'mode' and isinstance(k.value, ast.Constant)]
                    for mode in modes:
                        self.assertFalse(set(str(mode)) & set('wax+'), f'{where} open(..., {mode!r})')

    def test_the_only_file_the_agent_reads_for_itself_is_config_and_build_json(self):
        for path in AGENT_MODULES:
            text = path.read_text()
            for found in re.findall(r"""['"]([\w.-]+\.(?:json|csv|log|txt|ini|db|cfg))['"]""", text):
                self.assertIn(found, ('config.json', 'BUILD.json'), f'{path.name}: {found}')

    def test_main_stops_python_from_writing_bytecode_before_it_imports_an_agent_module(self):
        body = tree(AGENT/'main.py').body
        def line_of(predicate):
            return next(node.lineno for node in body if predicate(node))
        flag = line_of(lambda n: isinstance(n, ast.Assign) and any(
            isinstance(t, ast.Attribute) and t.attr == 'dont_write_bytecode' for t in n.targets))
        self.assertTrue(next(n for n in body if getattr(n, 'lineno', 0) == flag).value.value)   # = True
        # No import at module level may come before the flag, other than "import sys".
        for node in body:
            if isinstance(node, (ast.Import, ast.ImportFrom)) and node.lineno < flag:
                self.assertEqual([alias.name for alias in node.names], ['sys'])
        # The agent modules are only imported inside functions (after the flag was set at the top).
        for node in body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = {alias.name for alias in node.names} | {getattr(node, 'module', None) or ''}
                self.assertFalse(names & {'browser_action', 'inventory_action', 'notes_action',
                                          'gui_support', 'domain', 'save_dialog'}, node.lineno)

    def test_the_other_entry_modules_do_not_write_bytecode_either(self):
        # They are always imported by main.py, which has set the flag; none changes it back.
        for path in AGENT_MODULES:
            self.assertNotRegex(path.read_text(), r'dont_write_bytecode\s*=\s*False')

    def test_progress_goes_only_to_the_event_log_in_memory_and_to_stdout(self):
        for path in AGENT_MODULES:
            text = path.read_text()
            self.assertNotIn('sys.stderr', text, path.name)
            self.assertNotRegex(text, r'print\([^)]*file\s*=', path.name)


if __name__ == '__main__':
    unittest.main()
