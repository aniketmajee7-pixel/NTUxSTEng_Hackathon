"""Test cancellation/focus logic with mock Windows APIs; never sends real input."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


def load_gui():
    fake_modules = {}
    for name in ('pyautogui', 'win32api', 'win32clipboard', 'win32con', 'win32gui',
                 'win32process', 'pywinauto'):
        fake_modules[name] = types.ModuleType(name)
    pg = fake_modules['pyautogui']
    pg.failSafeCheck = Mock()
    pg.write = Mock(); pg.press = Mock()
    fake_modules['win32con'].VK_F8 = 119
    fake_modules['win32con'].KEYEVENTF_KEYUP = 2
    fake_modules['win32api'].GetAsyncKeyState = Mock(return_value=0)
    fake_modules['win32api'].keybd_event = Mock()
    fake_modules['win32gui'].GetForegroundWindow = Mock(return_value=123)
    fake_modules['pywinauto'].Desktop = Mock()
    path = Path(__file__).resolve().parents[1]/'gui_support.py'
    spec = importlib.util.spec_from_file_location('gui_contract_under_test', path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, fake_modules):
        spec.loader.exec_module(module)
    return module, fake_modules


class GUIContractTests(unittest.TestCase):
    def setUp(self):
        self.module, self.modules = load_gui()
        self.gui = self.module.GUI.__new__(self.module.GUI)

    def test_f8_stops_before_input(self):
        self.modules['win32api'].GetAsyncKeyState.return_value = 0x8000
        with self.assertRaises(self.module.Stopped):
            self.gui.check_stop()
        self.modules['pyautogui'].write.assert_not_called()

    def test_focus_retries_only_once(self):
        self.gui.sleep = Mock(); self.gui.event = Mock()
        window = Mock(handle=123)
        self.modules['win32gui'].GetForegroundWindow.return_value = 456
        with self.assertRaises(RuntimeError):
            self.gui.focus(window)
        self.assertEqual(window.set_focus.call_count, 2)

    def test_lost_focus_prevents_typing(self):
        self.gui.focus = Mock()
        window = Mock(handle=123)
        self.modules['win32gui'].GetForegroundWindow.return_value = 456
        with self.assertRaises(RuntimeError):
            self.gui.type_text('stock', window)
        self.modules['pyautogui'].write.assert_not_called()

    def test_cleanup_releases_modifiers_without_pyautogui(self):
        self.gui.stop_event = Mock(); self.gui.sampler = Mock(); self.gui.events = Mock()
        self.gui.finish()
        api = self.modules['win32api'].keybd_event
        self.assertEqual([call.args[0] for call in api.call_args_list], [17, 18, 16, 91])
        self.gui.stop_event.set.assert_called_once()
        self.gui.events.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
