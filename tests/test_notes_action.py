"""The notes task with a fake GUI and a fake clock: nothing is typed anywhere."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from domain import NOTE_LINE_SECONDS, Phase, typing_seconds


def load_notes():
    pg = types.ModuleType('pyautogui')
    pg.FailSafeException = type('FailSafeException', (Exception,), {})
    support = types.ModuleType('gui_support')
    support.Stopped = type('Stopped', (Exception,), {})
    support.TimeUp = type('TimeUp', (support.Stopped,), {})
    spec = importlib.util.spec_from_file_location(
        'notes_under_test', Path(__file__).resolve().parents[1]/'notes_action.py')
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'pyautogui': pg, 'gui_support': support}):
        spec.loader.exec_module(module)
    return module, support, pg


class Clock:
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now


class FakeGui:
    """Typing takes `speed` times the planned time on the fake clock."""

    def __init__(self, clock, speed=1.0, pace=1.0):
        self.clock, self.speed, self.pace = clock, speed, pace
        self.typed = []            # (text, time_limit when typed)
        self.pressed = []
        self.hotkeys = []
        self.events = []
        self.time_limit = None
        self.fail_on = {}          # text -> exception raised when typed
        self.launch_shortcut = Mock()
        self.window = Mock()
        self.focus = Mock()

    def type_text(self, text, window=None, **kwargs):
        if text in self.fail_on:
            raise self.fail_on[text]
        self.typed.append((text, self.time_limit))
        self.clock.now += typing_seconds(text, self.pace)*self.speed

    def press(self, key):
        self.pressed.append(key)

    def hotkey(self, *keys, **kwargs):
        self.hotkeys.append((keys, kwargs))

    def pause(self):
        self.clock.now += 0.5

    def sleep(self, seconds):
        self.clock.now += seconds

    def event(self, action, **details):
        self.events.append((action, details))

    def names(self):
        return [name for name, _ in self.events]

    def texts(self):
        return [text for text, _ in self.typed]


def notepad(title='Untitled - Notepad', text=''):
    window = Mock(); window.handle = 77
    window.window_text.return_value = title
    editor = Mock(); editor.is_visible.return_value = True
    editor.iface_text.DocumentRange.GetText.return_value = text
    editor.iface_value.CurrentValue = ''
    window.descendants.side_effect = lambda control_type=None: [editor] if control_type == 'Document' else []
    return window


class NotesCase(unittest.TestCase):
    def setUp(self):
        self.module, self.support, pg = load_notes()
        modules = patch.dict(sys.modules, {'pyautogui': pg, 'gui_support': self.support})
        modules.start(); self.addCleanup(modules.stop)   # run() imports gui_support itself
        self.clock = Clock()
        patcher = patch.object(self.module, 'time', self.clock)
        patcher.start(); self.addCleanup(patcher.stop)
        phase_patch = patch.object(self.module, 'Phase', lambda s: Phase(s, clock=self.clock.monotonic))
        phase_patch.start(); self.addCleanup(phase_patch.stop)
        random_patch = patch.object(self.module.random, 'random', return_value=0.9)
        random_patch.start(); self.addCleanup(random_patch.stop)
        self.gui = FakeGui(self.clock)
        self.action = self.module.NotesAction(self.gui, {'notes_shortcut': 'Notepad'})

    def ready(self, title='Untitled - Notepad', text=''):
        self.action.window = notepad(title, text)
        self.gui.launch_shortcut.return_value = self.action.window
        return self.action.window


LINES = [(3, 'Minor detail: nothing to add about the sidebar at all today.'),
         (1, 'Important: workbook saved on the desktop with every item row done.'),
         (2, 'Visited three sites and read the front page of each of them.')]


class LineChoiceTests(NotesCase):
    def cost(self, line, pace=1.0):
        return typing_seconds(line[1], pace)+NOTE_LINE_SECONDS

    def test_the_most_important_lines_that_fit_are_typed_in_the_order_they_happened(self):
        self.ready()
        room = self.cost(LINES[1])+self.cost(LINES[2])+0.5          # priorities 1 and 2, not 3
        self.action.write(LINES, self.clock.now+room)
        self.assertEqual(self.gui.texts(), [LINES[1][1], LINES[2][1]])
        self.assertEqual(self.action.typed, 2)

    def test_with_room_for_everything_all_lines_are_typed_in_order(self):
        self.ready()
        self.action.write(LINES, self.clock.now+600)
        self.assertEqual(self.gui.texts(), [text for _, text in LINES])
        self.assertEqual(self.gui.pressed, ['enter', 'enter'])      # one Enter between lines, none before the first

    def test_a_line_that_was_left_out_in_front_is_reported(self):
        self.ready()
        self.action.write(LINES[:2], self.clock.now+self.cost(LINES[1])+0.5)
        self.assertIn(('notes_lines_left_out', {'lines': 1}), self.gui.events)

    def test_typing_slower_than_planned_makes_the_next_choice_drop_the_lesser_lines(self):
        self.ready()
        self.gui.speed = 2.0                                         # twice as slow as the plan
        lines = [(1, 'Important: workbook saved on the desktop with every row done.'),
                 (2, 'Visited three sites and read the front page of each of them.'),
                 (3, 'Minor detail: nothing to add about the sidebar at all today.')]
        room = sum(self.cost(line) for line in lines)+0.1          # all fit at the planned speed
        self.action.write(lines, self.clock.now+room)
        self.assertEqual(self.gui.texts(), [lines[0][1]])           # measured speed shows they do not

    def test_a_line_that_no_longer_fits_ends_after_the_last_whole_word_that_does(self):
        self.ready()
        self.gui.speed = 2.0               # the first line takes twice its plan, so slower == 2
        first = (1, 'Short first line of the notes.')
        second = (2, 'Second line which is much longer than what the remaining time can hold.')
        after_first = typing_seconds(first[1])*2
        left_for_second = typing_seconds(second[1], 2.0)+NOTE_LINE_SECONDS+1.0   # chosen, yet too slow
        self.action.write([first, second], self.clock.now+after_first+left_for_second)
        typed = ''.join(self.gui.texts()[1:])
        self.assertTrue(typed)
        self.assertTrue(second[1].startswith(typed))
        self.assertNotEqual(typed, second[1])
        self.assertEqual(second[1][len(typed)], ' ')                    # stops between words
        self.assertIn(('notes_line_shortened', {'words': len(typed.split(' ')),
                                                'of': len(second[1].split(' '))}), self.gui.events)
        self.assertEqual(self.action.typed, 2)

    def test_nothing_is_typed_when_there_is_no_time(self):
        self.ready()
        self.action.write(LINES, self.clock.now)
        self.assertEqual(self.gui.typed, [])
        self.assertEqual(self.action.typed, 0)


class EndLineTests(NotesCase):
    def run_notes(self, seconds, lines=LINES):
        self.gui.launch_shortcut.side_effect = lambda name, title: self.ready()
        return self.action.run(seconds, lines)

    def test_the_last_line_is_always_END(self):
        self.run_notes(300)
        self.assertEqual(self.gui.texts()[-1], 'END')
        self.assertEqual(self.gui.texts().count('END'), 1)

    def test_END_is_typed_on_its_own_line_after_the_notes(self):
        self.run_notes(300)
        self.assertEqual(self.gui.pressed[-1], 'enter')
        self.assertEqual(self.gui.texts()[-2], LINES[2][1])

    def test_END_is_typed_with_a_short_budget_and_finishes_before_the_deadline(self):
        start = self.clock.now
        many = [(index % 4, f'Line number {index} describing one more thing done in this run.')
                for index in range(30)]
        result = self.run_notes(60, many)
        self.assertEqual(self.gui.texts()[-1], 'END')
        self.assertLessEqual(self.clock.now, start+60)
        self.assertTrue(result['end_written'])
        self.assertLess(result['lines_typed'], len(many))

    def test_the_notes_stop_early_enough_for_END_and_END_has_the_whole_phase_as_its_limit(self):
        start = self.clock.now
        many = [(1, f'Line number {index} describing one more thing done in this run.') for index in range(30)]
        self.run_notes(60, many)
        end_cost = 2*NOTE_LINE_SECONDS+typing_seconds('END', 1.0)*1.3
        lines_limit = start+60-self.module.END_RESERVE-end_cost
        for text, limit in self.gui.typed[:-1]:
            self.assertAlmostEqual(limit, lines_limit, places=6)
        self.assertEqual(self.gui.typed[-1], ('END', start+60))
        self.assertIsNone(self.gui.time_limit)                     # cleared afterwards

    def test_the_result_reports_lines_end_and_that_notepad_is_left_open(self):
        result = self.run_notes(300)
        self.assertEqual(result, {'lines_planned': 3, 'lines_typed': 3, 'end_written': True, 'left_open': True})
        self.assertIn('notes_phase_complete', self.gui.names())

    def test_nothing_is_saved_or_closed(self):
        self.run_notes(300)
        self.assertNotIn('s', [k for keys, _ in self.gui.hotkeys for k in keys if k == 's'])
        self.assertFalse(hasattr(self.gui, 'close_window') and self.gui.close_window.called)
        self.assertFalse(self.action.window.close.called)

    def test_time_up_while_typing_the_lines_still_writes_END(self):
        self.gui.fail_on[LINES[0][1]] = self.support.TimeUp('out of time')
        result = self.run_notes(300, [LINES[0], LINES[1]])
        self.assertTrue(result['cut_short'])
        self.assertEqual(self.gui.texts(), ['END'])
        self.assertTrue(result['end_written'])
        self.assertIn('notes_time_up', self.gui.names())

    def test_an_error_while_typing_a_line_is_reported_and_END_is_still_written(self):
        self.gui.fail_on[LINES[0][1]] = RuntimeError('Notepad lost focus')
        result = self.run_notes(300, [LINES[0], LINES[1]])
        self.assertEqual(result['error'], 'Notepad lost focus')
        self.assertTrue(result['end_written'])

    def test_when_notepad_never_opened_nothing_is_typed_and_END_is_not_attempted(self):
        self.gui.launch_shortcut.side_effect = RuntimeError('Notepad window did not appear')
        result = self.action.run(300, LINES)
        self.assertEqual(self.gui.typed, [])
        self.assertEqual(result['error'], 'Notepad window did not appear')
        self.assertFalse(result['end_written']); self.assertFalse(result['left_open'])
        self.assertEqual(result['lines_typed'], 0)

    def test_stopping_is_not_swallowed(self):
        self.gui.fail_on[LINES[0][1]] = self.support.Stopped('operator stop')
        with self.assertRaises(self.support.Stopped):
            self.run_notes(300, [LINES[0]])
        self.assertIsNone(self.gui.time_limit)

    def test_a_pace_that_is_not_a_number_counts_as_one(self):
        self.gui.pace = Mock()
        self.assertEqual(self.action.pace(), 1.0)
        self.gui.pace = 1.4
        self.assertEqual(self.action.pace(), 1.4)


class NewPageTests(NotesCase):
    def new_page_pressed(self):
        return [keys for keys, _ in self.gui.hotkeys if keys == ('ctrl', 'n')]

    def test_an_untitled_empty_page_is_used_as_it_is(self):
        self.ready('Untitled - Notepad', '')
        self.action.blank_page()
        self.assertEqual(self.gui.hotkeys, [])

    def test_a_new_tab_is_opened_when_the_page_holds_text(self):
        window = self.ready('Untitled - Notepad', 'notes from the last session')
        self.action.blank_page()
        self.assertEqual(self.gui.hotkeys, [(('ctrl', 'n'), {'window': window})])
        self.assertIn('notes_new_page', self.gui.names())

    def test_a_new_tab_is_opened_when_the_title_is_not_untitled(self):
        self.ready('Demo run 17 Oct 2026 - Notepad', '')
        self.action.blank_page()
        self.assertEqual(len(self.new_page_pressed()), 1)

    def test_the_title_check_ignores_case(self):
        self.ready('UNTITLED - Notepad', '')
        self.action.blank_page()
        self.assertEqual(self.gui.hotkeys, [])


class HasTextTests(NotesCase):
    def test_text_in_the_document_is_found_and_whitespace_is_not_text(self):
        self.ready('Untitled - Notepad', '  \n ')
        self.assertFalse(self.action.has_text())
        self.ready('Untitled - Notepad', 'hello')
        self.assertTrue(self.action.has_text())

    def test_the_value_pattern_is_read_when_the_text_pattern_fails(self):
        window = self.ready()
        editor = window.descendants('Document')[0]
        editor.iface_text.DocumentRange.GetText.side_effect = RuntimeError('no text pattern')
        editor.iface_value.CurrentValue = 'typed earlier'
        self.assertTrue(self.action.has_text())

    def test_a_page_that_cannot_be_read_counts_as_empty(self):
        window = self.ready()
        window.descendants.side_effect = RuntimeError('window is gone')
        self.assertFalse(self.action.has_text())

    def test_a_hidden_editor_is_ignored(self):
        window = self.ready('Untitled - Notepad', 'text')
        window.descendants('Document')[0].is_visible.return_value = False
        self.assertFalse(self.action.has_text())


class OpenTests(NotesCase):
    def test_the_desktop_shortcut_is_used_first_and_win_r_is_not(self):
        window = Mock()
        self.gui.launch_shortcut.return_value = window
        self.action.open()
        self.gui.launch_shortcut.assert_called_once_with('Notepad', self.module.TITLE)
        self.assertIs(self.action.window, window)
        self.assertEqual(self.gui.hotkeys, [])

    def test_a_missing_shortcut_falls_back_to_win_r_notepad_enter(self):
        self.gui.launch_shortcut.side_effect = RuntimeError('No desktop shortcut named "Notepad" was found')
        run_dialog, notes_window = Mock(), Mock(); notes_window.handle = 5
        self.gui.window = Mock(side_effect=[run_dialog, notes_window])
        self.action.open()
        self.assertEqual(self.gui.hotkeys, [(('win', 'r'), {})])
        self.assertEqual(self.gui.window.call_args_list[0].args, (r'^Run$', 10))
        self.assertEqual(self.gui.window.call_args_list[1].args, (self.module.TITLE, 20))
        self.assertEqual(self.gui.texts(), ['notepad'])
        self.assertEqual(self.gui.pressed, ['enter'])
        notes_window.maximize.assert_called_once()
        self.gui.focus.assert_called_once_with(notes_window)
        self.assertIs(self.action.window, notes_window)
        self.assertIn('notes_shortcut_missing', self.gui.names())
        self.assertIn(('run_dialog_launch', {'program': 'notepad', 'hwnd': 5}), self.gui.events)

    def test_any_other_launch_error_is_not_hidden_by_the_fallback(self):
        self.gui.launch_shortcut.side_effect = RuntimeError('Notepad is already open')
        with self.assertRaises(RuntimeError):
            self.action.open()
        self.assertEqual(self.gui.hotkeys, [])

    def test_the_shortcut_name_comes_from_the_config(self):
        action = self.module.NotesAction(self.gui, {'notes_shortcut': 'Editor'})
        action.open()
        self.assertEqual(self.gui.launch_shortcut.call_args.args[0], 'Editor')


if __name__ == '__main__':
    unittest.main()
