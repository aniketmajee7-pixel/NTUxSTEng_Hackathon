"""tools/make_zip.py builds a ZIP whose BUILD.json main.build_differences() accepts."""
import importlib.util
import json
from datetime import datetime
from pathlib import Path
import sys
import tempfile
import types
import unittest
import zipfile
from unittest.mock import Mock, patch

AGENT = Path(__file__).resolve().parents[1]
TOOL = AGENT/'tools'/'make_zip.py'


def load(path, name, modules=None):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules or {}):
        spec.loader.exec_module(module)
    return module


def load_main():
    tk = types.ModuleType('tkinter'); tk.Tk = Mock()
    tk.messagebox = types.ModuleType('messagebox'); tk.ttk = types.ModuleType('ttk')
    return load(AGENT/'main.py', 'main_zip_under_test', {'tkinter': tk})


class MakeZipTests(unittest.TestCase):
    NOW = datetime(2026, 10, 12, 19, 30)

    def setUp(self):
        self.tool, self.main = load(TOOL, 'make_zip_under_test'), load_main()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.work = Path(self.directory.name)

    def build(self, source=AGENT, name='out.zip'):
        return self.tool.make_zip(self.work/name, source=source, now=self.NOW)

    def extract(self, archive, folder='unzipped'):
        target = self.work/folder
        with zipfile.ZipFile(archive) as z:
            z.extractall(target)
        return target

    def fake_source(self):
        """A source tree with the files that must stay out, and CRLF files."""
        source = self.work/'src'
        (source/'tests').mkdir(parents=True); (source/'__pycache__').mkdir()
        (source/'main.py').write_bytes(b'import sys\r\nprint(1)\r\n')
        (source/'domain.py').write_text('X = 1\n')
        (source/'config.json').write_text('{"a": 1}\n')
        for name in ('requirements.txt', 'create_shortcut.ps1', 'README.md'):
            (source/name).write_text('x\n')
        (source/'tests'/'test_x.py').write_text('pass\n')
        (source/'__pycache__'/'main.cpython-313.pyc').write_bytes(b'\0')
        (source/'notes.txt').write_text('not shipped\n')
        return source

    def test_the_zip_holds_the_agent_files_at_the_top_level_and_nothing_else(self):
        archive = self.build()
        with zipfile.ZipFile(archive) as z:
            names = z.namelist()
        expected = {p.name for p in AGENT.glob('*.py')} | {
            'config.json', 'requirements.txt', 'create_shortcut.ps1', 'README.md', 'BUILD.json'}
        self.assertEqual(set(names), expected)
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(all('/' not in name for name in names))

    def test_tests_pycache_and_stray_files_are_left_out(self):
        source = self.fake_source()
        with zipfile.ZipFile(self.build(source)) as z:
            names = z.namelist()
        self.assertEqual(sorted(names), ['BUILD.json', 'README.md', 'config.json', 'create_shortcut.ps1',
                                         'domain.py', 'main.py', 'requirements.txt'])
        self.assertFalse(any('test' in n or 'pycache' in n or n.endswith('.pyc') for n in names))

    def test_the_real_zip_has_no_tests_and_no_bytecode(self):
        with zipfile.ZipFile(self.build()) as z:
            names = z.namelist()
        self.assertFalse([n for n in names if n.startswith('test') or 'pycache' in n or n.endswith('.pyc')])
        self.assertIn('fake_dialog.py', [p.name for p in (AGENT/'tests').glob('*.py')])
        self.assertNotIn('fake_dialog.py', names)

    def test_build_json_has_a_dated_name_and_a_hash_for_each_python_file_and_the_config(self):
        source = self.fake_source()
        manifest = json.loads(zipfile.ZipFile(self.build(source)).read('BUILD.json'))
        self.assertEqual(set(manifest), {'name', 'files'})
        self.assertEqual(manifest['name'], 'agent-20261012-1930')
        self.assertEqual(sorted(manifest['files']), ['config.json', 'domain.py', 'main.py'])
        for digest in manifest['files'].values():
            self.assertRegex(digest, r'^[0-9a-f]{64}$')

    def test_hashes_are_of_the_bytes_with_crlf_turned_into_lf(self):
        import hashlib
        source = self.fake_source()                                  # main.py is written with CRLF
        manifest = json.loads(zipfile.ZipFile(self.build(source)).read('BUILD.json'))
        self.assertEqual(manifest['files']['main.py'], hashlib.sha256(b'import sys\nprint(1)\n').hexdigest())
        self.assertEqual(manifest['files']['domain.py'], hashlib.sha256(b'X = 1\n').hexdigest())

    def test_the_default_name_is_the_build_name_in_the_current_directory(self):
        source = self.fake_source()
        with patch.object(self.tool.Path, 'cwd', return_value=self.work):
            out = self.tool.make_zip(source=source, now=self.NOW)
        self.assertEqual(out, self.work/'agent-20261012-1930.zip')
        self.assertTrue(out.is_file())

    def test_main_takes_an_optional_output_path_and_prints_a_summary(self):
        target = self.work/'chosen.zip'
        with patch.object(self.tool, 'SOURCE', AGENT), patch('builtins.print') as shown:
            self.assertEqual(self.tool.main([str(target)]), 0)
        self.assertTrue(zipfile.is_zipfile(target))
        self.assertIn('chosen.zip', shown.call_args.args[0])
        with patch('builtins.print'):
            self.assertEqual(self.tool.main(['a.zip', 'b.zip']), 2)

    def test_an_empty_source_folder_is_an_error(self):
        empty = self.work/'empty'; empty.mkdir()
        with self.assertRaises(SystemExit):
            self.tool.make_zip(self.work/'x.zip', source=empty)

    def test_an_unpacked_zip_matches_its_own_build_json(self):
        folder = self.extract(self.build())
        name, changed = self.main.build_differences(folder/'BUILD.json')
        self.assertEqual((name, changed), ('agent-20261012-1930', []))

    def test_main_py_in_the_zip_is_the_one_in_the_source(self):
        folder = self.extract(self.build())
        self.assertEqual((folder/'main.py').read_bytes(), (AGENT/'main.py').read_bytes())

    def test_an_edited_file_is_reported_after_unpacking(self):
        folder = self.extract(self.build())
        with open(folder/'domain.py', 'a') as handle:
            handle.write('\n# edited on the VM\n')
        self.assertEqual(self.main.build_differences(folder/'BUILD.json')[1], ['domain.py'])

    def test_an_edited_config_is_reported_but_the_readme_is_not_checked(self):
        folder = self.extract(self.build())
        (folder/'README.md').write_text('changed\n')
        self.assertEqual(self.main.build_differences(folder/'BUILD.json')[1], [])
        config = json.loads((folder/'config.json').read_text()); config['browser_phase_seconds'] = 300
        (folder/'config.json').write_text(json.dumps(config))
        self.assertEqual(self.main.build_differences(folder/'BUILD.json')[1], ['config.json'])

    def test_a_missing_file_and_crlf_copies_after_unpacking(self):
        folder = self.extract(self.build())
        (folder/'notes_action.py').unlink()
        crlf = (folder/'gui_support.py').read_bytes().replace(b'\n', b'\r\n')
        (folder/'gui_support.py').write_bytes(crlf)                  # a copy through a Windows share
        self.assertEqual(self.main.build_differences(folder/'BUILD.json')[1], ['notes_action.py (missing)'])

    def test_check_files_sees_the_build_next_to_main_py(self):
        folder = self.extract(self.build())
        self.main.BUILD = folder/'BUILD.json'
        with patch.object(self.main.importlib, 'import_module'):
            files = self.main.check_files('Browser pilot')
        self.assertEqual(files, {'build': 'agent-20261012-1930', 'changed': [], 'broken': []})


if __name__ == '__main__':
    unittest.main()
