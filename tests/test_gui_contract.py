"""Test cancellation/focus logic with mock Windows APIs; never sends real input."""
import importlib.util
import random
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


MODIFIERS = ('shift', 'ctrl', 'alt', 'win')


def load_gui():
    fake_modules = {}
    for name in ('pyautogui', 'win32api', 'win32clipboard', 'win32con', 'win32gui',
                 'win32process', 'pywinauto'):
        fake_modules[name] = types.ModuleType(name)
    pg = fake_modules['pyautogui']
    pg.FailSafeException = type('FailSafeException', (Exception,), {})
    pg.failSafeCheck = Mock()
    pg.write = Mock(); pg.press = Mock()
    # Keys are sent as keyDown/keyUp.  Record characters in `pg.write` and named keys
    # (backspace, delete, enter ...) in `pg.press`; modifiers are ignored.
    def key_down(key):
        if key in MODIFIERS:
            return None
        return pg.press(key) if len(key) > 1 else pg.write(key)
    pg.key_down_default = key_down
    pg.keyDown = Mock(side_effect=key_down)
    pg.keyUp = Mock()
    pg.size = Mock(return_value=(1920, 1080))
    pg.position = Mock(return_value=(500, 500))
    pg.moveTo = Mock(); pg.mouseDown = Mock(); pg.mouseUp = Mock()
    pg.scroll = Mock(); pg.click = Mock(); pg.doubleClick = Mock()
    fake_modules['win32con'].VK_F8 = 119
    fake_modules['win32con'].KEYEVENTF_KEYUP = 2
    fake_modules['win32api'].GetAsyncKeyState = Mock(return_value=0)
    # Arrows and the navigation block are sent through win32api with the extended-key
    # flag (see gui_support.EXTENDED_KEYS); record them like any other named key.
    extended = {0x26: 'up', 0x28: 'down', 0x25: 'left', 0x27: 'right', 0x24: 'home',
                0x23: 'end', 0x21: 'pageup', 0x22: 'pagedown', 0x2D: 'insert', 0x2E: 'delete'}
    def keybd_event(code, scan, flags, extra):
        if code in extended and not flags & 2:
            pg.press(extended[code])
    fake_modules['win32api'].keybd_event = Mock(side_effect=keybd_event)
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
        self.gui.pace = 1.0
        sleeper = patch.object(self.module.time, 'sleep')  # key holds are real sleeps
        self.slept = sleeper.start(); self.addCleanup(sleeper.stop)

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
        """finish() has no sampler thread or log to close any more: it only lets go of the
        modifier keys, through win32api so a corner fail-safe cannot stop it."""
        self.gui.finish()
        api = self.modules['win32api'].keybd_event
        self.assertEqual([call.args[0] for call in api.call_args_list], [17, 18, 16, 91])
        self.assertTrue(all(call.args[2] == 2 for call in api.call_args_list))   # key-up events
        self.modules['pyautogui'].keyUp.assert_not_called()

    def test_shifted_characters_release_shift(self):
        self.gui.focus = Mock(); self.gui.sleep = Mock()
        self.gui.type_text('A:', Mock(handle=123))
        pg = self.modules['pyautogui']
        self.assertEqual([c.args[0] for c in pg.write.call_args_list], ['a', ';'])
        self.assertEqual([c.args[0] for c in pg.keyDown.call_args_list], ['shift', 'a', 'shift', ';'])
        self.assertEqual([c.args[0] for c in pg.keyUp.call_args_list], ['a', 'shift', ';', 'shift'])

    def test_corrected_typo_preserves_final_text(self):
        self.gui.focus = Mock(); self.gui.sleep = Mock(); self.gui.event = Mock()
        pg = self.modules['pyautogui']
        # Replay emitted input to prove the typo is removed before submission.
        keys = []
        pg.write.side_effect = lambda value: keys.extend(value)
        pg.press.side_effect = lambda key: keys.pop() if key == 'backspace' else None
        self.gui.type_text('apple.com', Mock(handle=123), corrected_typo=True)
        self.assertEqual(''.join(keys), 'apple.com')
        pg.press.assert_called_once_with('backspace')

    def replay(self, text, kind, **kwargs):
        self.gui.focus = Mock(); self.gui.sleep = Mock(); self.gui.event = Mock()
        pg = self.modules['pyautogui']
        keys = []; pg.write.reset_mock(); pg.press.reset_mock()
        pg.write.side_effect = lambda value: keys.extend(value)
        pg.press.side_effect = lambda key: keys.pop() if key == 'backspace' else None
        self.gui.type_text(text, Mock(handle=123), corrected_typo=True, typo_kind=kind, **kwargs)
        return ''.join(keys), self.gui.event.call_args_list

    def test_every_typo_kind_is_corrected_before_finishing(self):
        for kind in ('wrong', 'double', 'swap'):
            for seed in range(40):
                random.seed(seed)
                final, events = self.replay('laptop stand 210', kind)
                self.assertEqual(final, 'laptop stand 210', (kind, seed))
                self.assertTrue(any(c.args[0] == 'typing_correction' for c in events), (kind, seed))

    def test_wrong_key_is_beside_the_intended_one_then_fixed_after_a_pause(self):
        from domain import adjacent_keys
        for seed in range(30):
            random.seed(seed)
            waits = []
            final, events = self.replay('aws', 'wrong')
            self.gui.sleep.side_effect = waits.append
            written = [c.args[0] for c in self.modules['pyautogui'].write.call_args_list]
            self.assertEqual(final, 'aws')
            self.assertEqual(len(written), 4)       # three letters plus one stray key
            # Whichever position was hit, the stray key neighbours the letter that follows it.
            stray_at = next(i for i in range(3) if written[i] != 'aws'[i])
            self.assertIn(written[stray_at], adjacent_keys('aws'[stray_at]))

    def test_keys_are_held_not_instant_and_released_in_order(self):
        self.gui.focus = Mock(); self.gui.sleep = Mock()
        pg = self.modules['pyautogui']
        order = []
        pg.keyDown.side_effect = lambda k: order.append(('down', k))
        pg.keyUp.side_effect = lambda k: order.append(('up', k))
        self.slept.reset_mock()
        self.gui.type_text('ab', Mock(handle=123))
        self.assertEqual(order, [('down', 'a'), ('up', 'a'), ('down', 'b'), ('up', 'b')])
        holds = [c.args[0] for c in self.slept.call_args_list]
        self.assertEqual(len(holds), 2)
        self.assertTrue(all(0.05 <= h <= 0.12 for h in holds), holds)
        pg.keyDown.side_effect = pg.key_down_default
        pg.keyUp.side_effect = None

    def test_shift_is_down_before_and_up_after_the_shifted_key(self):
        self.gui.focus = Mock(); self.gui.sleep = Mock()
        pg = self.modules['pyautogui']
        order = []
        pg.keyDown.side_effect = lambda k: order.append(('down', k))
        pg.keyUp.side_effect = lambda k: order.append(('up', k))
        self.gui.type_text('A', Mock(handle=123))
        self.assertEqual(order, [('down', 'shift'), ('down', 'a'), ('up', 'a'), ('up', 'shift')])
        pg.keyDown.side_effect = pg.key_down_default
        pg.keyUp.side_effect = None

    def test_run_level_style_defaults_are_neutral_and_drawn_per_run(self):
        self.assertEqual((self.module.GUI.wheel_speed, self.module.GUI.typo_scale,
                          self.module.GUI.pause_scale), (1.0, 1.0, 1.0))
        import random as r
        r.seed(3)
        pairs = set()
        for _ in range(5):
            with patch.object(self.module, 'Desktop'):
                g = self.module.GUI()
            pairs.add((round(g.wheel_speed, 3), round(g.typo_scale, 3)))
            self.assertTrue(0.8 <= g.wheel_speed <= 1.3 and 0.6 <= g.typo_scale <= 1.5)
        self.assertGreater(len(pairs), 1)

    def record_keys(self):
        pg = self.modules['pyautogui']
        order = []
        pg.keyDown.side_effect = lambda k: order.append(('down', k))
        pg.keyUp.side_effect = lambda k: order.append(('up', k))
        self.addCleanup(lambda: (setattr(pg.keyDown, 'side_effect', pg.key_down_default),
                                 setattr(pg.keyUp, 'side_effect', None)))
        return order

    def test_shortcut_holds_modifiers_then_releases_in_reverse(self):
        self.gui.focus = Mock(); self.gui.pause = Mock()
        order = self.record_keys()
        self.slept.reset_mock()
        self.gui.hotkey('ctrl', 'shift', 's')
        self.assertEqual(order, [('down', 'ctrl'), ('down', 'shift'), ('down', 's'),
                                 ('up', 's'), ('up', 'shift'), ('up', 'ctrl')])
        self.assertTrue(all(c.args[0] > 0 for c in self.slept.call_args_list))
        self.assertGreaterEqual(self.slept.call_count, 3)   # never an instant chord

    def test_shortcut_releases_keys_even_if_a_key_fails(self):
        pg = self.modules['pyautogui']
        order = self.record_keys()
        def down(key):
            order.append(('down', key))
            if key == 's':
                raise RuntimeError('input blocked')
        pg.keyDown.side_effect = down
        with self.assertRaises(RuntimeError):
            self.gui.chord('ctrl', 's')
        self.assertIn(('up', 'ctrl'), order)

    def test_named_keys_are_held_like_typed_characters(self):
        order = self.record_keys()
        self.slept.reset_mock()
        self.gui.press('enter')
        self.assertEqual(order, [('down', 'enter'), ('up', 'enter')])
        self.assertTrue(0.05 <= self.slept.call_args.args[0] <= 0.12)

    def test_f8_stops_a_named_key_before_it_is_sent(self):
        self.modules['win32api'].GetAsyncKeyState.return_value = 0x8000
        order = self.record_keys()
        with self.assertRaises(self.module.Stopped):
            self.gui.press('enter')
        self.assertEqual(order, [])
        self.modules['win32api'].GetAsyncKeyState.return_value = 0

    def test_www_prefix_is_followed_by_a_thinking_pause(self):
        self.gui.focus = Mock(); self.gui.event = Mock()
        waits = []
        self.gui.sleep = Mock(side_effect=waits.append)
        self.gui.type_text('www.apple.com', Mock(handle=123))
        # one pause of 0.5-1.2 s (plus the ordinary key delays) sits before the 5th character
        self.assertTrue(any(0.5 <= w <= 1.25 for w in waits))

    def test_digits_get_a_wrong_digit_not_a_letter(self):
        for seed in range(30):
            random.seed(seed)
            self.replay('2025', 'wrong')
            written = ''.join(c.args[0] for c in self.modules['pyautogui'].write.call_args_list)
            self.assertTrue(written.isdigit(), written)

    def test_formulas_never_receive_mistakes(self):
        for kind in ('wrong', 'double', 'swap'):
            final, events = self.replay('=SUM(F2:F8)', kind)
            self.assertFalse(any(c.args[0] == 'typing_correction' for c in events))
            self.modules['pyautogui'].press.assert_not_called()

    def test_swap_uses_two_backspaces_and_falls_back_when_not_swappable(self):
        random.seed(1)
        self.replay('abcd', 'swap')
        self.assertEqual(self.modules['pyautogui'].press.call_count, 2)
        random.seed(1)
        self.replay('a1', 'swap')  # no adjacent lowercase pair at a digit: becomes 'wrong'
        self.assertLessEqual(self.modules['pyautogui'].press.call_count, 2)

    def test_focus_loss_during_pause_prevents_next_character(self):
        self.gui.focus = Mock(); self.gui.event = Mock()
        api = self.modules['win32gui'].GetForegroundWindow
        self.gui.sleep = Mock(side_effect=lambda _: setattr(api, 'return_value', 456))
        with self.assertRaises(RuntimeError):
            self.gui.type_text('apple', Mock(handle=123))
        self.modules['pyautogui'].write.assert_not_called()

    def test_url_typo_correction_clears_inline_history_completion(self):
        self.gui.focus = Mock(); self.gui.sleep = Mock(); self.gui.event = Mock()
        pg = self.modules['pyautogui']
        state = {'text': '', 'suffix': ''}
        def write(value):
            state['suffix'] = ''
            state['text'] += value
            # Model Chrome's selected history suffix after both wrong and final text.
            if state['text'] in ('x', 'apple.com'):
                state['suffix'] = '/shop'
        def press(key):
            if state['suffix']:
                state['suffix'] = ''
            elif key == 'backspace':
                state['text'] = state['text'][:-1]
        pg.write.side_effect = write; pg.press.side_effect = press
        with patch.object(self.module.random, 'choice', side_effect=[0, 'x']):
            self.gui.type_text('apple.com', Mock(handle=123), corrected_typo=True,
                               clear_inline_completion=True)
        self.assertEqual(state, {'text': 'apple.com', 'suffix': ''})

    def test_scroll_target_is_clipped_to_visible_browser_viewport(self):
        self.gui.focus = Mock(); self.gui.move = Mock(); self.gui.sleep = Mock()
        self.gui.pause = Mock(); self.gui.event = Mock()
        outer = Mock(left=100, top=100); outer.width.return_value = 900; outer.height.return_value = 700
        content = Mock(left=-100, top=-200); content.width.return_value = 3000; content.height.return_value = 4000
        window = Mock(handle=123); window.rectangle.return_value = outer
        doc = Mock(); doc.rectangle.return_value = content
        doc.iface_scroll.CurrentVerticallyScrollable = False
        self.modules['pyautogui'].position.return_value = (5, 5)  # outside the page
        self.gui.scroll_content(window, -1, doc)
        x, y = self.gui.move.call_args.args
        self.assertTrue(100 < x < 1000 and 100 < y < 800)

    def test_default_typing_never_introduces_errors_into_formula_or_path(self):
        self.gui.focus = Mock(); self.gui.sleep = Mock(); self.gui.event = Mock()
        pg = self.modules['pyautogui']
        for value in ('=D2*E2', r'C:\Users\User\inventory.ods', '5000'):
            self.gui.type_text(value, Mock(handle=123))
        self.assertNotIn('backspace', [c.args[0] for c in pg.press.call_args_list])
        self.gui.event.assert_not_called()

    def test_click_points_stay_inside_central_half(self):
        rect = types.SimpleNamespace(left=100, top=200, right=300, bottom=260)
        c = Mock(); c.rectangle.return_value = rect
        for _ in range(100):
            x, y = self.gui.control_point(c)
            self.assertTrue(150 <= x <= 250)
            self.assertTrue(215 <= y <= 245)

    def test_offscreen_control_has_no_click_point(self):
        c = Mock(); c.rectangle.return_value = types.SimpleNamespace(left=2000, top=200, right=2050, bottom=250)
        with self.assertRaises(RuntimeError):
            self.gui.control_point(c)

    def test_scroll_without_pattern_reports_unverified_wheel_input(self):
        self.gui.focus = Mock(); self.gui.move = Mock(); self.gui.sleep = Mock()
        self.gui.pause = Mock(); self.gui.event = Mock()
        rect = Mock(left=0, top=0); rect.width.return_value = 1000; rect.height.return_value = 800
        window = Mock(handle=123); window.rectangle.return_value = rect
        changed = self.gui.scroll_content(window, -1)
        self.assertIsNone(changed)
        self.assertIsNone(self.gui.event.call_args.kwargs['movement_verified'])
        pg = self.modules['pyautogui']
        units = [c.args[0] for c in pg.scroll.call_args_list]
        self.assertTrue(units and all(u == -120 for u in units), units)
        # 800 px viewport at 100 px per notch: roughly 0.75-1.1 viewports.
        self.assertTrue(6 <= len(units) <= 9, len(units))
        self.assertEqual(sum(self.gui.event.call_args.kwargs['notches']), len(units))
        dwell = self.gui.sleep.call_args_list[-1].args[0]
        self.assertTrue(0.6 <= dwell <= 9.1)

    def test_pointer_moves_at_high_rate_without_library_pauses(self):
        self.gui.sleep = Mock(); self.gui.event = Mock(); self.gui.check_stop = Mock()
        pg = self.modules['pyautogui']
        pg.position.return_value = (100, 100); pg.moveTo.reset_mock()
        self.gui.move(700, 400)
        self.assertGreater(pg.moveTo.call_count, 25)
        self.assertTrue(all(c.kwargs.get('_pause') is False for c in pg.moveTo.call_args_list))
        self.assertEqual(pg.moveTo.call_args.args, (700, 400))
        pg.position.return_value = (500, 500)

    def test_fling_sends_many_fast_notches_rolling_sends_fewer_slow_ones(self):
        self.gui.focus = Mock(); self.gui.move = Mock(); self.gui.pause = Mock(); self.gui.event = Mock()
        rect = Mock(left=0, top=0); rect.width.return_value = 1000; rect.height.return_value = 800
        window = Mock(handle=123); window.rectangle.return_value = rect
        pg = self.modules['pyautogui']
        waits = []
        self.gui.sleep = Mock(side_effect=waits.append)
        pg.scroll.reset_mock()
        self.gui.scroll_content(window, 1, fraction_range=(4, 4), fling=True)
        self.assertGreaterEqual(pg.scroll.call_count, 30)
        self.assertTrue(all(c.args[0] == 120 for c in pg.scroll.call_args_list))
        fling_waits = [w for w in waits[:-1]]
        self.assertTrue(all(w < 0.06 for w in fling_waits))
        pg.scroll.reset_mock(); waits.clear()
        self.gui.scroll_content(window, -1, fraction_range=(1, 1))
        self.assertTrue(all(c.args[0] == -120 for c in pg.scroll.call_args_list))
        self.assertGreater(max(waits), 0.17)

    def test_clicks_hold_the_button_instead_of_instant_click(self):
        self.gui.sleep = Mock(); self.gui.event = Mock(); self.gui.check_stop = Mock()
        self.gui.focus = Mock(); self.gui.move = Mock()
        pg = self.modules['pyautogui']
        control = Mock(); control.is_visible.return_value = True; control.is_enabled.return_value = True
        rect = Mock(left=0, top=0, right=1000, bottom=800)
        rect.width.return_value = 1000; rect.height.return_value = 800
        control.rectangle.return_value = rect
        pg.position.return_value = (500, 400)
        with patch.object(self.module.time, 'sleep') as hold:
            self.gui.click(control)
        pg.mouseDown.assert_called_once(); pg.mouseUp.assert_called_once()
        self.assertTrue(0.05 <= hold.call_args.args[0] <= 0.14)
        pg.position.return_value = (500, 500)

    def test_pointer_path_never_enters_a_screen_corner(self):
        """Overshooting a top-left desktop icon used to be able to reach (0, 0), which is
        pyautogui's emergency stop and would end the run."""
        self.gui.sleep = Mock(); self.gui.event = Mock(); self.gui.check_stop = Mock()
        pg = self.modules['pyautogui']
        for seed in range(60):
            random.seed(seed)
            pg.position.return_value = (960, 540); pg.moveTo.reset_mock()
            self.gui.move(6, 5)
            for call in pg.moveTo.call_args_list[:-1]:
                x, y = call.args
                self.assertTrue(3 <= x <= 1916 and 3 <= y <= 1076, (seed, x, y))
        pg.position.return_value = (500, 500)

    def test_document_recovery_dialog_is_named_in_the_launch_error(self):
        self.gui.event = Mock(); self.gui.hotkey = Mock(); self.gui.sleep = Mock()
        self.gui.click = Mock(); self.gui.find = Mock(return_value=Mock())
        self.gui.desktop = Mock()
        def handles(title_pattern=None, class_name=None):
            if class_name:
                return [1]
            if title_pattern and 'Recovery' in title_pattern:
                return [9] if self.gui.click.called else []
            return []
        self.gui.window_handles = handles
        clock = [0.0]
        with patch.object(self.module.time, 'monotonic', side_effect=lambda: clock.__setitem__(0, clock[0]+1) or clock[0]):
            with self.assertRaises(RuntimeError) as failure:
                self.gui.launch_shortcut('OpenOffice Calc', r'.*Calc.*')
        self.assertIn('Document Recovery', str(failure.exception))

    def test_pointer_travel_time_varies_with_distance(self):
        self.gui.sleep = Mock(); self.gui.event = Mock()
        self.gui.check_stop = Mock()
        with patch.object(self.module.random, 'uniform', side_effect=lambda low, high: (low+high)/2), \
             patch.object(self.module.random, 'randint', return_value=14), \
             patch.object(self.module.random, 'random', return_value=1.0):
            self.gui.move(550, 500)
            short = self.gui.event.call_args.kwargs['duration_seconds']
            self.gui.event.reset_mock()
            self.gui.move(1500, 900)
            long = self.gui.event.call_args.kwargs['duration_seconds']
        self.assertLess(short, long)
        self.assertEqual(self.modules['pyautogui'].moveTo.call_args.args, (1500, 900))

    def test_selection_is_collapsed_before_scrolling(self):
        self.gui.focus = Mock(); self.gui.event = Mock()
        doc = Mock(); doc.iface_text.GetSelection.return_value.Length = 1
        self.gui.clear_browser_selection(Mock(handle=123), doc)
        span = doc.iface_text.GetSelection.return_value.GetElement.return_value
        span.MoveEndpointByRange.assert_called_once_with(1, span, 0)
        span.Select.assert_called_once()

    def test_tree_traversal_stops_at_node_budget(self):
        def node():
            c = Mock(); c.element_info.control_type = 'Pane'; c.children.return_value = []
            return c
        root = node(); children = [node() for _ in range(100)]
        root.children.return_value = children
        result = list(self.gui.walk_controls(root, max_nodes=10))
        self.assertEqual(len(result), 10)
        self.assertTrue(all(not c.children.called for c in children[9:]))

    def test_document_bodies_are_pruned_during_discovery(self):
        root = Mock(); root.element_info.control_type = 'Document'
        self.assertEqual(list(self.gui.walk_controls(root, prune_types=('Document',))), [root])
        root.children.assert_not_called()

    def test_window_discovery_attaches_only_the_matching_native_handle(self):
        api = self.modules['win32gui']
        api.EnumWindows = Mock(side_effect=lambda callback, _: [callback(h, None) for h in (11, 22, 33)])
        api.IsWindowVisible = Mock(return_value=True)
        api.GetWindowText = Mock(side_effect=lambda h: {11: 'Notes', 22: 'Apple - Google Chrome', 33: 'Calc'}[h])
        self.gui.desktop = Mock()
        self.gui.wait = lambda predicate, *args: predicate()
        window = self.gui.window(r'.* - Google Chrome$')
        self.gui.desktop.window.assert_called_once_with(handle=22)
        self.gui.desktop.windows.assert_not_called()
        self.assertIs(window, self.gui.desktop.window.return_value.wrapper_object.return_value)


class KeysTimeLimitAndScreenTests(unittest.TestCase):
    def setUp(self):
        self.module, self.modules = load_gui()
        self.gui = self.module.GUI.__new__(self.module.GUI)
        self.gui.pace = 1.0
        self.gui.event = Mock()
        sleeper = patch.object(self.module.time, 'sleep'); sleeper.start(); self.addCleanup(sleeper.stop)
        self.pg, self.api = self.modules['pyautogui'], self.modules['win32api']

    def sent(self):
        return [c.args for c in self.api.keybd_event.call_args_list]

    def test_arrows_are_sent_as_extended_keys_so_shift_selects(self):
        """pyautogui sends arrows as keypad keys; with NumLock on, Windows then drops a
        held Shift, which is why no range was ever selected in the recording."""
        self.pg.keyDown('shift')
        self.gui.press('down')
        self.assertEqual(self.sent(), [(0x28, 0, 1, 0), (0x28, 0, 3, 0)])   # extended down, extended up
        self.assertNotIn('down', [c.args[0] for c in self.pg.keyDown.call_args_list])

    def test_every_navigation_key_is_extended_and_ordinary_keys_are_not(self):
        for key in ('up', 'down', 'left', 'right', 'home', 'end', 'pageup', 'pagedown', 'insert', 'delete'):
            self.api.keybd_event.reset_mock()
            self.gui.press(key)
            (code, _, down, _), (code_up, _, up, _) = self.sent()
            self.assertEqual((code, down, up), (self.module.EXTENDED_KEYS[key], 1, 3), key)
        self.api.keybd_event.reset_mock()
        for key in ('enter', 'tab', 'esc', 'backspace', 'f2', 's'):
            self.gui.press(key)
        self.api.keybd_event.assert_not_called()

    def test_shortcut_with_a_navigation_key_holds_the_modifier_around_it(self):
        order = []
        self.pg.keyDown.side_effect = lambda k: order.append(('down', k))
        self.pg.keyUp.side_effect = lambda k: order.append(('up', k))
        self.api.keybd_event.side_effect = lambda code, scan, flags, extra: order.append(
            ('up' if flags & 2 else 'down', 'home'))
        self.gui.chord('ctrl', 'home')
        self.assertEqual(order, [('down', 'ctrl'), ('down', 'home'), ('up', 'home'), ('up', 'ctrl')])

    def test_past_the_time_limit_every_check_raises_time_up(self):
        self.gui.time_limit = None
        self.gui.check_stop()                                           # no limit: nothing happens
        with patch.object(self.module.time, 'monotonic', return_value=100.0):
            self.gui.time_limit = 100.5
            self.gui.check_stop()
            self.gui.time_limit = 100.0
            with self.assertRaises(self.module.TimeUp):
                self.gui.check_stop()
            with self.assertRaises(self.module.TimeUp):
                self.gui.press('enter')                                 # no key is sent after the limit
        self.pg.press.assert_not_called()

    def test_time_up_is_a_stop_so_retry_handlers_let_it_through(self):
        self.assertTrue(issubclass(self.module.TimeUp, self.module.Stopped))
        self.gui.time_limit = 0.0
        with self.assertRaises(self.module.TimeUp):
            self.gui.wait(lambda: False, 'something', timeout=30)       # wait() does not swallow it

    def test_f8_still_wins_over_the_time_limit(self):
        self.gui.time_limit = 0.0
        self.api.GetAsyncKeyState.return_value = 0x8000
        with self.assertRaises(self.module.Stopped) as caught:
            self.gui.check_stop()
        self.assertNotIsInstance(caught.exception, self.module.TimeUp)

    def fake_screen(self, width, height):
        """win32ui stand-in whose bitmap holds blue-green-red-alpha bytes, row by row."""
        bits = bytearray()
        for y in range(height):
            for x in range(width):
                bits += bytes((x % 256, y % 256, 200, 255))            # blue=x, green=y, red=200
        ui = types.ModuleType('win32ui')
        self.bitmap = Mock(); self.bitmap.GetBitmapBits.return_value = bytes(bits)
        self.source = Mock(); self.memory = self.source.CreateCompatibleDC.return_value
        ui.CreateDCFromHandle = Mock(return_value=self.source)
        ui.CreateBitmap = Mock(return_value=self.bitmap)
        gui32 = self.modules['win32gui']
        gui32.GetWindowDC = Mock(return_value=77); gui32.ReleaseDC = Mock(); gui32.DeleteObject = Mock()
        return ui

    def test_screen_rectangle_is_read_in_screen_coordinates_and_released(self):
        ui = self.fake_screen(40, 10)
        with patch.dict(sys.modules, {'win32ui': ui}):
            pixel = self.gui.grab(100, 50, 40, 10)
        self.assertEqual(pixel(100, 50), (200, 0, 0))                   # (red, green, blue)
        self.assertEqual(pixel(125, 57), (200, 7, 25))
        self.assertEqual(pixel(99, 50), (255, 255, 255))                # outside the copy: blank
        self.assertEqual(pixel(140, 50), (255, 255, 255))
        self.memory.BitBlt.assert_called_once_with((0, 0), (40, 10), self.source, (100, 50), 0x00CC0020)
        self.modules['win32gui'].ReleaseDC.assert_called_once_with(0, 77)
        self.modules['win32gui'].DeleteObject.assert_called_once()
        self.memory.DeleteDC.assert_called_once(); self.source.DeleteDC.assert_called_once()

    def test_reading_is_clipped_to_the_screen(self):
        ui = self.fake_screen(20, 5)
        with patch.dict(sys.modules, {'win32ui': ui}):
            self.gui.grab(1900, 1075, 400, 300)                         # screen is 1920x1080
        self.assertEqual(self.memory.BitBlt.call_args.args[1], (20, 5))

    def test_unreadable_screen_gives_none_and_a_log_line_not_a_crash(self):
        ui = types.ModuleType('win32ui'); ui.CreateDCFromHandle = Mock(side_effect=OSError('no device'))
        gui32 = self.modules['win32gui']
        gui32.GetWindowDC = Mock(return_value=77); gui32.ReleaseDC = Mock(); gui32.DeleteObject = Mock()
        with patch.dict(sys.modules, {'win32ui': ui}):
            self.assertIsNone(self.gui.grab(0, 0, 100, 100))
        self.assertEqual(self.gui.event.call_args.args[0], 'screen_read_failed')
        gui32.ReleaseDC.assert_called_once_with(0, 77)

    def held(self, *codes):
        self.api.GetAsyncKeyState.side_effect = lambda code: 0x8000 if code in codes else 0

    def test_a_windows_key_stuck_down_is_released_before_a_shortcut(self):
        """A host shortcut (Cmd+Shift+5) reached the VM as Windows+Shift held down, and
        every Ctrl+S became Windows+Ctrl+S (Voice access) until the run failed unsaved."""
        self.held(0x5B, 0x10)                                   # left Windows key and Shift
        order = []
        self.api.keybd_event.side_effect = lambda code, scan, flags, extra: order.append(('up', code))
        self.pg.keyDown.side_effect = lambda key: order.append(('down', key))
        self.gui.chord('ctrl', 's')
        self.assertEqual(order[:3], [('up', 0x5B), ('up', 0x10), ('down', 'ctrl')])
        self.assertEqual([c.kwargs['key'] for c in self.gui.event.call_args_list], ['win', 'shift'])

    def test_stuck_keys_are_released_before_typing_too(self):
        self.held(0x5C, 0x12)                                   # right Windows key and Alt
        self.gui.focus = Mock(); self.gui.sleep = Mock()
        self.gui.type_text('ab', Mock(handle=123))
        released = [c.args[0] for c in self.api.keybd_event.call_args_list]
        self.assertEqual(released, [0x5C, 0x12])
        self.assertTrue(all(c.args[2] & 2 for c in self.api.keybd_event.call_args_list))   # key-up events

    def test_shift_held_on_purpose_survives_a_single_key(self):
        """Range selection holds Shift and presses arrows; that Shift must not be let go."""
        self.held(0x10)
        self.gui.press('down')
        codes = [c.args[0] for c in self.api.keybd_event.call_args_list]
        self.assertNotIn(0x10, codes)
        self.assertEqual(codes, [0x28, 0x28])                   # just the arrow, down and up

    def test_nothing_is_sent_when_no_modifier_is_stuck(self):
        self.gui.chord('ctrl', 's')
        self.api.keybd_event.assert_not_called()
        self.gui.event.assert_not_called()

    def test_closing_gives_the_caller_a_chance_to_answer_a_question(self):
        self.gui.hotkey = Mock(); self.gui.sleep = Mock()
        window = Mock(handle=55)
        still_open = iter([True, True, False])
        self.modules['win32gui'].IsWindow = Mock(side_effect=lambda handle: next(still_open))
        answered = Mock()
        self.gui.close_window(window, on_prompt=answered)
        self.gui.hotkey.assert_called_once_with('alt', 'f4', window=window)
        self.assertEqual(answered.call_count, 2)                # asked on each look while it is still open

    def window(self):
        rect = types.SimpleNamespace(left=0, top=0, right=1280, bottom=752)
        window = Mock(handle=123); window.rectangle.return_value = rect
        return window

    def test_cell_click_moves_there_then_presses_and_releases(self):
        self.gui.focus = Mock(); self.gui.move = Mock(); self.gui.sleep = Mock()
        self.pg.position.return_value = (240, 239)                      # the pointer arrived
        self.gui.click_at(240, 239, self.window(), quick=True)
        self.gui.move.assert_called_once_with(240, 239, width=20)
        self.pg.mouseDown.assert_called_once(); self.pg.mouseUp.assert_called_once()
        quick = sum(c.args[0] for c in self.gui.sleep.call_args_list)
        self.gui.sleep.reset_mock()
        self.gui.click_at(240, 239, self.window())
        self.assertLess(quick, 2.4)                                     # hover and settle are capped low

    def test_click_outside_the_window_or_after_losing_focus_is_refused(self):
        self.gui.focus = Mock(); self.gui.move = Mock(); self.gui.sleep = Mock()
        with self.assertRaises(RuntimeError):
            self.gui.click_at(1500, 239, self.window())
        self.modules['win32gui'].GetForegroundWindow.return_value = 999
        with self.assertRaises(RuntimeError):
            self.gui.click_at(240, 239, self.window())
        self.pg.mouseDown.assert_not_called()

    def test_drag_presses_at_the_start_and_releases_at_the_end(self):
        order = []
        self.gui.focus = Mock(); self.gui.sleep = Mock()
        self.pg.position.return_value = (60, 200)
        self.gui.move = Mock(side_effect=lambda x, y, width=20: order.append(('move', x, y)))
        self.pg.mouseDown.side_effect = lambda: order.append('down')
        self.pg.mouseUp.side_effect = lambda: order.append('up')
        self.gui.drag(60, 200, 60, 290, self.window())
        self.assertEqual(order, [('move', 60, 200), 'down', ('move', 60, 290), 'up'])

    def test_button_is_released_even_if_the_drag_is_interrupted(self):
        self.gui.focus = Mock(); self.gui.sleep = Mock()
        self.pg.position.return_value = (60, 200)
        self.gui.move = Mock(side_effect=[None, self.module.TimeUp('Time limit reached')])
        with self.assertRaises(self.module.TimeUp):
            self.gui.drag(60, 200, 60, 290, self.window())
        self.pg.mouseDown.assert_called_once(); self.pg.mouseUp.assert_called_once()

    def test_a_pointer_nudged_by_the_host_is_brought_back_once_before_the_press(self):
        """The host's mouse over the VM console moved the pointer between the move and the
        press, and a drag selected the wrong range: the position is checked first."""
        self.gui.focus = Mock(); self.gui.sleep = Mock()
        self.gui.move = Mock()
        self.pg.position.side_effect = [(300, 300), (240, 239)]
        self.gui.click_at(240, 239, self.window())
        self.assertEqual(self.gui.move.call_count, 2)                   # the first move and the repair
        self.assertEqual(self.gui.event.call_args_list[0].args[0], 'pointer_moved')
        self.pg.mouseDown.assert_called_once(); self.pg.mouseUp.assert_called_once()

    def test_a_pointer_that_keeps_moving_stops_the_click_and_presses_nothing(self):
        self.gui.focus = Mock(); self.gui.sleep = Mock()
        self.gui.move = Mock()
        self.pg.position.side_effect = None
        self.pg.position.return_value = (300, 300)
        with self.assertRaises(RuntimeError):
            self.gui.click_at(240, 239, self.window())
        self.pg.mouseDown.assert_not_called()
        with self.assertRaises(RuntimeError):
            self.gui.drag(60, 200, 60, 290, self.window())
        self.pg.mouseDown.assert_not_called()


if __name__ == '__main__':
    unittest.main()
