"""The Windows Save dialog, driven the way people use it: the folder is reached by clicking
in the dialog's left pane and file list, and only the file name is typed.  Shared by the
spreadsheet (OpenOffice's "Save As") and the notes (Notepad's "Save as")."""
import random
import re
import time


def folder_shown(dialog):
    """The folder the dialog shows, as its address bar names it (a toolbar named
    "Address: C:\\Users\\User\\Desktop\\agent"); '' when it cannot be read."""
    try:
        bars = dialog.descendants(control_type='ToolBar')
    except Exception:
        return ''
    if not isinstance(bars, (list, tuple)):
        return ''
    for bar in bars:
        try:
            name = bar.window_text() or ''
        except Exception:
            continue
        if name.casefold().startswith('address:'):
            return name.split(':', 1)[1].strip()
    return ''


def shows(dialog, folder):
    """Whether the dialog already shows `folder`: the whole path when the address bar
    gives one, otherwise the last part of it ("Documents > HackathonDemo")."""
    shown = folder_shown(dialog)
    if not shown:
        return False
    def norm(path):
        return str(path).replace('/', '\\').rstrip('\\').casefold()
    if re.match(r'^([a-z]:|\\\\|/)', shown, re.I):
        return norm(shown) == norm(folder)
    return re.split(r'[\\/>]', shown)[-1].strip().casefold() == folder.name.casefold()


def elsewhere(dialog, folder):
    """Whether the dialog plainly shows another folder ("This PC", Documents ...).  False
    when its address bar cannot be read: then where the file went is checked on disk."""
    return bool(folder_shown(dialog)) and not shows(dialog, folder)


def navigation_pane(dialog):
    """The dialog's left pane (a tree), to scroll it when a place is out of view."""
    try:
        trees = dialog.descendants(control_type='Tree')
    except Exception:
        return None
    if not isinstance(trees, (list, tuple)):
        return None
    return next((tree for tree in trees if tree.is_visible()), None)


def in_view(item, pane):
    """Whether a left-pane item is really on screen inside the pane.  An item scrolled out
    of the pane can still report itself visible, and a click on it would land elsewhere."""
    if pane is None:
        return True                      # the pane cannot be read: trust the item
    try:
        r, p = item.rectangle(), pane.rectangle()
        return r.right > r.left and r.bottom > r.top and p.top <= r.top and r.bottom <= p.bottom
    except Exception:
        return False


def pane_item(gui, dialog, name, pane):
    """The left-pane item called `name` that is really in view.  In the 7:55 PM recording
    Desktop was in view, yet This PC was clicked: a name can be listed more than once, and
    the one found first may be out of view, so every item of that name is looked at."""
    item = gui.find(dialog, [name], 'TreeItem')
    if item is not None and in_view(item, pane):
        return item
    try:
        items = dialog.descendants(control_type='TreeItem')
    except Exception:
        return None
    for item in items if isinstance(items, (list, tuple)) else ():
        try:
            if (str(item.window_text()).strip().casefold() == name.casefold()
                    and item.is_visible() and in_view(item, pane)):
                return item
        except Exception:
            continue
    return None


def scroll_into_view(gui, dialog, name, pane):
    """Roll the left pane with the wheel, a few notches at a time, until `name` is in it.
    In the VM the pane opened scrolled down: Music, Videos, This PC in view, Desktop above."""
    if pane is None:
        return None
    gui.move(*gui.control_point(pane))
    for notches in (3, 3, 4, -6, -4):         # up first (where Desktop was), then back down
        gui.wheel(notches)
        item = pane_item(gui, dialog, name, pane)
        if item is not None:
            return item
    return None


def browse_to(gui, dialog, folder):
    """Show `folder` in the dialog with clicks: its parent (Desktop, Documents ...) in the
    left pane, then a double-click on the folder in the list.  Nothing is clicked when the
    dialog already shows it: Windows reopens the folder used last.  Returns the clicks made.

    A recording showed the agent clicking Documents and HackathonDemo while the dialog
    was already in HackathonDemo, so the dialog is read first, once it has settled.  The
    next one showed the pane scrolled past Desktop and nothing clicked at all, so the pane
    is rolled until Desktop is really in it, with This PC > Desktop as a second way."""
    gui.sleep(random.uniform(0.6, 1.1))
    if shows(dialog, folder):
        gui.event('save_folder_browsed', clicks=0, reason='already there')
        return 0
    parent = folder.parent.name
    pane = navigation_pane(dialog)
    place = pane_item(gui, dialog, parent, pane) or scroll_into_view(gui, dialog, parent, pane)
    clicks = 0
    if place is not None:
        gui.click(place, window=dialog); clicks += 1
        gui.sleep(random.uniform(0.7, 1.3))
        gui.event('save_folder_step', clicked=parent, place='left pane')
    else:
        computer = pane_item(gui, dialog, 'This PC', pane) or scroll_into_view(gui, dialog, 'This PC', pane)
        if computer is None:
            gui.event('save_folder_browsed', clicks=0, reason=f'{parent} is not listed')
            return 0
        gui.click(computer, window=dialog); clicks += 1
        gui.sleep(random.uniform(0.7, 1.3))
        try:
            item = gui.wait(lambda: gui.find(dialog, [parent], 'ListItem'), parent, 4)
        except RuntimeError:
            gui.event('save_folder_browsed', clicks=clicks, reason=f'{parent} is not under This PC')
            return clicks
        gui.click(item, double=True, window=dialog); clicks += 1
        gui.sleep(random.uniform(0.7, 1.3))
        gui.event('save_folder_step', clicked=parent, place='This PC')
    try:
        item = gui.wait(lambda: gui.find(dialog, [folder.name], 'ListItem'), 'save folder', 4)
    except RuntimeError:
        gui.event('save_folder_browsed', clicks=clicks, reason=f'{folder.name} is not listed')
        return clicks
    gui.click(item, double=True, window=dialog); clicks += 1
    gui.sleep(random.uniform(0.7, 1.3))
    shown = folder_shown(dialog)
    gui.event('save_folder_browsed', clicks=clicks,
              reached=shows(dialog, folder) if shown else None)
    return clicks


def file_name_field(gui, dialog):
    edit = gui.find(dialog, ['File name', 'File name:'], 'Edit')
    if not edit:
        # The standard Windows Save dialog's edit identifier.
        candidates = dialog.descendants(control_type='Edit')
        edit = next((c for c in candidates if c.element_info.automation_id == '1001'), None)
    if not edit:
        raise RuntimeError('Cannot identify the Save dialog File name field')
    return edit


def confirm_replace(gui, replace=True):
    """Answer "... already exists. Do you want to replace it?" if Windows asks.  Yes only
    for a file of this agent in its own folder; No anywhere else (in the 7:55 PM run Yes
    replaced a copy left in This PC).  Returns whether the question came up."""
    gui.sleep(random.uniform(0.6, 1.0))
    if not gui.window_handles(title_pattern=r'(?i)^confirm save as$'):
        return False
    gui.press('y' if replace else 'n')
    gui.event('save_replace_confirmed' if replace else 'save_replace_declined')
    return True


def free_name(folders, base, extension):
    """A file name a person would type, e.g. inventory_oct16.ods; numbered when taken."""
    for number in range(1, 500):
        name = base+('' if number == 1 else f'_{number}')+extension
        if not any((folder/name).exists() for folder in folders):
            return name
    return base+f'_{int(time.time())}'+extension
