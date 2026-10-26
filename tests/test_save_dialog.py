"""save_dialog.py against a fake Save dialog: nothing real is clicked."""
from pathlib import Path, PureWindowsPath
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import save_dialog
from fake_dialog import FakeControl, FakeDialog, FakeGui

AGENT = PureWindowsPath(r'C:\Users\User\Desktop\agent')
DESKTOP = r'C:\Users\User\Desktop'


def dialog_in(address, scroll=0, this_pc_row=14):
    """A dialog whose left pane lists Desktop, Documents ... This PC, and whose file list
    holds the agent folder when it shows the Desktop."""
    dialog = FakeDialog(address, scroll)
    def to_desktop(double):
        dialog.address = DESKTOP
    def to_agent(double):
        if double:
            dialog.address = str(AGENT)
    dialog.desktop = dialog.tree_item('Desktop', 2, on_click=to_desktop)
    dialog.tree_item('Documents', 3)
    dialog.tree_item('Music', 12)
    dialog.this_pc = dialog.tree_item(
        'This PC', this_pc_row, on_click=lambda double: setattr(dialog, 'address', 'This PC'))
    dialog.agent = dialog.list_item('agent', on_click=to_agent)
    return dialog


class AddressBarTests(unittest.TestCase):
    def test_the_folder_shown_is_read_from_the_address_toolbar(self):
        self.assertEqual(save_dialog.folder_shown(dialog_in(str(AGENT))), str(AGENT))

    def test_a_dialog_without_an_address_bar_shows_nothing_readable(self):
        self.assertEqual(save_dialog.folder_shown(dialog_in(None)), '')

    def test_a_dialog_that_cannot_be_read_gives_an_empty_answer_not_a_crash(self):
        dialog = dialog_in(str(AGENT)); dialog.unreadable = True
        self.assertEqual(save_dialog.folder_shown(dialog), '')
        self.assertFalse(save_dialog.shows(dialog, AGENT))
        self.assertFalse(save_dialog.elsewhere(dialog, AGENT))

    def test_the_whole_path_is_compared_ignoring_case_slashes_and_a_trailing_slash(self):
        for shown in (str(AGENT), str(AGENT).upper(), str(AGENT)+'\\', 'C:/Users/User/Desktop/agent'):
            self.assertTrue(save_dialog.shows(dialog_in(shown), AGENT), shown)

    def test_a_different_full_path_is_not_the_agent_folder(self):
        for shown in (DESKTOP, r'C:\Users\User\Documents\agent', r'C:\Users\User\Desktop\agent\old'):
            dialog = dialog_in(shown)
            self.assertFalse(save_dialog.shows(dialog, AGENT), shown)
            self.assertTrue(save_dialog.elsewhere(dialog, AGENT), shown)

    def test_a_breadcrumb_is_matched_on_its_last_part(self):
        self.assertTrue(save_dialog.shows(dialog_in('Desktop > agent'), AGENT))
        self.assertFalse(save_dialog.shows(dialog_in('Desktop > Documents'), AGENT))

    def test_the_agent_folder_is_not_elsewhere(self):
        self.assertFalse(save_dialog.elsewhere(dialog_in(str(AGENT)), AGENT))

    def test_an_unreadable_address_is_not_called_elsewhere(self):
        """Where the file went is then checked on disk instead."""
        self.assertFalse(save_dialog.elsewhere(dialog_in(None), AGENT))


class BrowseToTests(unittest.TestCase):
    def browse(self, dialog):
        gui = FakeGui(dialog)
        return save_dialog.browse_to(gui, dialog, AGENT), gui

    def test_the_address_bar_is_read_first_and_nothing_is_clicked_if_already_there(self):
        """Windows reopens the folder used last; a recording showed the agent clicking
        through Documents while the dialog was already in the folder."""
        clicks, gui = self.browse(dialog_in(str(AGENT)))
        self.assertEqual(clicks, 0)
        self.assertEqual(gui.clicks, [])
        self.assertEqual(gui.wheels, [])
        self.assertEqual(gui.order, [])                      # not even a lookup of a control
        action, details = gui.events[-1]
        self.assertEqual((action, details['clicks'], details['reason']),
                         ('save_folder_browsed', 0, 'already there'))

    def test_from_another_folder_the_parent_is_clicked_then_the_folder_is_double_clicked(self):
        dialog = dialog_in(r'C:\Users\User\Documents')
        clicks, gui = self.browse(dialog)
        self.assertEqual(clicks, 2)
        self.assertEqual(gui.clicked_names(), [('Desktop', False), ('agent', True)])
        self.assertEqual(dialog.address, str(AGENT))
        self.assertTrue(gui.events[-1][1]['reached'])

    def test_an_unreadable_address_bar_still_gets_the_clicks(self):
        dialog = dialog_in(None)
        dialog.desktop.on_click = dialog.agent.on_click = None      # nothing to read afterwards either
        clicks, gui = self.browse(dialog)
        self.assertEqual(gui.clicked_names(), [('Desktop', False), ('agent', True)])
        self.assertIsNone(gui.events[-1][1]['reached'])      # could not be confirmed

    def test_the_left_pane_is_scrolled_when_desktop_is_out_of_view(self):
        """The pane opened scrolled down (Music, Videos, This PC in view) and the item
        found first was above the pane: it is never clicked there."""
        dialog = dialog_in(r'C:\Users\User\Documents', scroll=6)
        self.assertFalse(dialog.desktop.in_pane())
        clicks, gui = self.browse(dialog)
        self.assertTrue(gui.wheels)
        self.assertGreater(gui.wheels[0], 0)                 # up first: that is where Desktop was
        self.assertEqual(clicks, 2)
        self.assertEqual(gui.clicked_names()[0], ('Desktop', False))
        self.assertTrue(gui.clicks[0][2])                    # it was inside the pane when clicked
        self.assertEqual(dialog.address, str(AGENT))

    def test_nothing_is_clicked_outside_the_pane(self):
        dialog = dialog_in(r'C:\Users\User\Documents', scroll=6)
        _, gui = self.browse(dialog)
        self.assertTrue(all(in_pane for _, _, in_pane in gui.clicks))

    def test_an_item_listed_twice_is_clicked_where_it_is_really_in_view(self):
        """In the recording Desktop was in view, yet This PC was clicked: a name can be
        listed more than once and the one found first may be out of view."""
        dialog = dialog_in(r'C:\Users\User\Documents', scroll=6)
        second = dialog.tree_item('Desktop', 9, on_click=dialog.desktop.on_click)   # in view at scroll 6
        self.assertFalse(dialog.desktop.in_pane()); self.assertTrue(second.in_pane())
        gui = FakeGui(dialog)
        pane = save_dialog.navigation_pane(dialog)
        self.assertIs(save_dialog.pane_item(gui, dialog, 'Desktop', pane), second)
        self.assertEqual(gui.wheels, [])                     # no need to scroll at all

    def test_with_no_desktop_in_the_pane_it_is_reached_through_this_pc(self):
        dialog = dialog_in(r'C:\Users\User\Documents', this_pc_row=4)
        dialog.controls.remove(dialog.desktop)
        dialog.list_item('Desktop', on_click=lambda double: setattr(dialog, 'address', DESKTOP))
        clicks, gui = self.browse(dialog)
        self.assertEqual(gui.clicked_names(), [('This PC', False), ('Desktop', True), ('agent', True)])
        self.assertEqual(clicks, 3)
        self.assertEqual(dialog.address, str(AGENT))

    def test_when_the_place_is_not_listed_anywhere_nothing_is_clicked_and_it_is_said(self):
        dialog = dialog_in(r'C:\Users\User\Documents')
        dialog.controls = [c for c in dialog.controls if c.kind != 'TreeItem']
        clicks, gui = self.browse(dialog)
        self.assertEqual(clicks, 0)
        self.assertEqual(gui.clicks, [])
        self.assertIn('not listed', gui.events[-1][1]['reason'])

    def test_a_folder_missing_from_the_list_ends_the_browse_with_the_clicks_made(self):
        dialog = dialog_in(r'C:\Users\User\Documents')
        dialog.controls.remove(dialog.agent)
        clicks, gui = self.browse(dialog)
        self.assertEqual(clicks, 1)
        self.assertIn('not listed', gui.events[-1][1]['reason'])


class ScrollIntoViewTests(unittest.TestCase):
    def test_the_pane_is_rolled_up_first_then_back_down(self):
        dialog = dialog_in(None, scroll=0)
        dialog.controls = [c for c in dialog.controls if c.window_text() != 'Desktop']
        gui = FakeGui(dialog)
        self.assertIsNone(save_dialog.scroll_into_view(
            gui, dialog, 'Desktop', save_dialog.navigation_pane(dialog)))
        self.assertEqual(gui.wheels, [3, 3, 4, -6, -4])      # gives up after five rolls

    def test_an_unreadable_pane_is_not_scrolled(self):
        dialog = dialog_in(None)
        gui = FakeGui(dialog)
        self.assertIsNone(save_dialog.scroll_into_view(gui, dialog, 'Desktop', None))
        self.assertEqual(gui.wheels, [])

    def test_in_view_needs_the_whole_item_inside_the_pane(self):
        dialog = dialog_in(None, scroll=0)
        pane = dialog.pane
        inside = FakeControl('a', 'TreeItem', rect=(4, 120, 196, 140))
        above = FakeControl('b', 'TreeItem', rect=(4, 90, 196, 110))
        below = FakeControl('c', 'TreeItem', rect=(4, 290, 196, 310))
        flat = FakeControl('d', 'TreeItem', rect=(4, 120, 4, 140))
        self.assertTrue(save_dialog.in_view(inside, pane))
        for item in (above, below, flat):
            self.assertFalse(save_dialog.in_view(item, pane), item.name)
        self.assertTrue(save_dialog.in_view(above, None))    # pane unreadable: trust the item

    def test_the_navigation_pane_is_the_visible_tree(self):
        dialog = dialog_in(None)
        self.assertIs(save_dialog.navigation_pane(dialog), dialog.pane)
        dialog.unreadable = True
        self.assertIsNone(save_dialog.navigation_pane(dialog))


class FileNameAndReplaceTests(unittest.TestCase):
    def test_the_file_name_field_is_found_by_its_name(self):
        dialog = dialog_in(None)
        edit = dialog.edit('File name')
        self.assertIs(save_dialog.file_name_field(FakeGui(dialog), dialog), edit)

    def test_the_standard_edit_identifier_is_the_fallback(self):
        dialog = dialog_in(None)
        dialog.edit('Something else', auto_id='4')
        edit = dialog.edit('Name', auto_id='1001')
        self.assertIs(save_dialog.file_name_field(FakeGui(dialog), dialog), edit)

    def test_no_file_name_field_is_an_error_not_a_guess(self):
        dialog = dialog_in(None)
        with self.assertRaises(RuntimeError):
            save_dialog.file_name_field(FakeGui(dialog), dialog)

    def test_no_replace_question_means_no_key_is_pressed(self):
        gui = FakeGui(dialog_in(None))
        self.assertFalse(save_dialog.confirm_replace(gui))
        self.assertEqual(gui.pressed, [])

    def test_a_replace_question_in_the_agent_folder_is_answered_yes(self):
        gui = FakeGui(dialog_in(None)); gui.handles = ['Confirm Save As']
        self.assertTrue(save_dialog.confirm_replace(gui))
        self.assertEqual(gui.pressed, ['y'])
        self.assertEqual(gui.event_names(), ['save_replace_confirmed'])

    def test_a_replace_question_outside_the_agent_folder_is_answered_no(self):
        """In the 7:55 PM run Yes replaced a copy left in This PC."""
        gui = FakeGui(dialog_in(None)); gui.handles = ['Confirm Save As']
        self.assertTrue(save_dialog.confirm_replace(gui, replace=False))
        self.assertEqual(gui.pressed, ['n'])
        self.assertEqual(gui.event_names(), ['save_replace_declined'])

    def test_other_windows_are_not_taken_for_the_replace_question(self):
        gui = FakeGui(dialog_in(None)); gui.handles = ['Save As', 'Untitled 1 - OpenOffice Calc']
        self.assertFalse(save_dialog.confirm_replace(gui, replace=False))
        self.assertEqual(gui.pressed, [])


class FreeNameTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.agent = Path(temp.name)/'agent'; self.agent.mkdir()
        self.desktop = self.agent.parent

    def test_a_free_name_is_used_as_it_is(self):
        self.assertEqual(save_dialog.free_name((self.agent, self.desktop), 'inventory_oct17', '.ods'),
                         'inventory_oct17.ods')

    def test_a_taken_name_gets_a_number_instead_of_an_overwrite_prompt(self):
        (self.agent/'inventory_oct17.ods').write_text('x')
        folders = (self.agent, self.desktop)
        self.assertEqual(save_dialog.free_name(folders, 'inventory_oct17', '.ods'), 'inventory_oct17_2.ods')
        (self.agent/'inventory_oct17_2.ods').write_text('x')
        self.assertEqual(save_dialog.free_name(folders, 'inventory_oct17', '.ods'), 'inventory_oct17_3.ods')

    def test_a_name_taken_in_any_of_the_folders_counts(self):
        """The dialog may have been somewhere else: a copy there must not be replaced."""
        (self.desktop/'inventory_oct17.ods').write_text('x')
        self.assertEqual(save_dialog.free_name((self.agent, self.desktop), 'inventory_oct17', '.ods'),
                         'inventory_oct17_2.ods')

    def test_when_every_number_is_taken_a_timestamp_makes_it_unique(self):
        class Always:
            def __truediv__(self, name):
                return type('P', (), {'exists': lambda s: True})()
        name = save_dialog.free_name((Always(),), 'inventory_oct17', '.ods')
        self.assertRegex(name, r'^inventory_oct17_\d{9,}\.ods$')


if __name__ == '__main__':
    unittest.main()
