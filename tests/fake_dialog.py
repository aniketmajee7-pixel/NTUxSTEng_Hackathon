"""A small fake of the Windows Save dialog and of the GUI calls save_dialog.py makes.

The dialog has an address bar (a ToolBar named "Address: <path>"), a left pane (a Tree whose
items scroll with the wheel) and a file list.  An item scrolled out of the pane still
reports itself visible, as in the VM: only its rectangle tells."""
import re
from types import SimpleNamespace

ROW = 20
PANE = (0, 100, 200, 300)          # left, top, right, bottom: ten rows are in view


class FakeControl:
    def __init__(self, name, kind, rect=(0, 0, 80, 20), rect_fn=None, auto_id=None,
                 on_click=None, enabled=True):
        self.name, self.kind, self.rect, self.rect_fn = name, kind, rect, rect_fn
        self.on_click = on_click
        self.enabled = enabled
        self.element_info = SimpleNamespace(control_type=kind, automation_id=auto_id)

    def window_text(self):
        return self.name() if callable(self.name) else self.name

    def is_visible(self):
        return True                    # even when scrolled out of the pane

    def is_enabled(self):
        return self.enabled

    def rectangle(self):
        left, top, right, bottom = self.rect_fn() if self.rect_fn else self.rect
        return SimpleNamespace(left=left, top=top, right=right, bottom=bottom,
                               width=lambda: right-left, height=lambda: bottom-top)

    def in_pane(self):
        left, top, right, bottom = self.rect_fn() if self.rect_fn else self.rect
        return PANE[1] <= top and bottom <= PANE[3]


class FakeDialog:
    """`address` is what the address bar names; None leaves it unreadable (no ToolBar)."""

    def __init__(self, address=None, scroll=0):
        self.address = address
        self.scroll = scroll           # rows the left pane is scrolled down
        self.controls = []
        self.unreadable = False
        self.pane = FakeControl('Navigation pane', 'Tree', rect=PANE)
        self.toolbar = FakeControl(lambda: f'Address: {self.address}', 'ToolBar')

    def tree_item(self, name, row, on_click=None):
        item = FakeControl(name, 'TreeItem', on_click=on_click, rect_fn=lambda: (
            PANE[0]+4, PANE[1]+(row-self.scroll)*ROW, PANE[2]-4, PANE[1]+(row-self.scroll+1)*ROW))
        self.controls.append(item)
        return item

    def list_item(self, name, on_click=None):
        item = FakeControl(name, 'ListItem', rect=(260, 120, 400, 140), on_click=on_click)
        self.controls.append(item)
        return item

    def edit(self, name, auto_id=None):
        item = FakeControl(name, 'Edit', rect=(260, 400, 500, 420), auto_id=auto_id)
        self.controls.append(item)
        return item

    def descendants(self, control_type=None):
        if self.unreadable:
            raise RuntimeError('the dialog cannot be read')
        found = [self.pane] if control_type in (None, 'Tree') else []
        if self.address is not None and control_type in (None, 'ToolBar'):
            found.append(self.toolbar)
        found += [c for c in self.controls if control_type in (None, c.kind)]
        return found


class FakeGui:
    """Just what save_dialog.py asks of the real GUI, with everything recorded."""

    def __init__(self, dialog):
        self.dialog = dialog
        self.clicks = []               # (control, double, was in the pane when clicked)
        self.wheels = []
        self.events = []
        self.pressed = []
        self.handles = []              # what window_handles(...) answers
        self.waited = []
        self.order = []                # calls in order: ('descendants'|'click'|'wheel'|'press', ...)

    def event(self, action, **details):
        self.events.append((action, details))

    def event_names(self):
        return [name for name, _ in self.events]

    def sleep(self, seconds):
        pass

    def move(self, x, y, width=40.0):
        pass

    def control_point(self, control):
        return (1, 1)

    def find(self, window, names, kind=None, bounded=False):
        norm = lambda text: text.casefold().replace('&', '').replace('…', '').rstrip('.:')
        wanted = {norm(n) for n in names}
        self.order.append(('find', tuple(names), kind))
        for control in window.descendants(control_type=kind) if kind else window.descendants():
            if kind and control.element_info.control_type != kind:
                continue
            if norm(control.window_text()) in wanted and control.is_visible() and control.is_enabled():
                return control
        return None

    def click(self, control, double=False, window=None):
        self.order.append(('click', control.window_text()))
        self.clicks.append((control, double, control.in_pane() if control.kind == 'TreeItem' else True))
        if control.on_click:
            control.on_click(double)

    def wheel(self, notches):
        self.order.append(('wheel', notches))
        self.wheels.append(notches)
        self.dialog.scroll = max(0, self.dialog.scroll-notches)     # positive: up

    def wait(self, predicate, label, timeout=30, poll_interval=0.4):
        self.waited.append(label)
        found = predicate()
        if not found:
            raise RuntimeError(f'Timed out waiting for {label}: not ready')
        return found

    def window_handles(self, title_pattern=None, class_name=None):
        pattern = re.compile(title_pattern) if title_pattern else None
        return [h for h in self.handles if pattern is None or pattern.match(h)]

    def press(self, key):
        self.order.append(('press', key))
        self.pressed.append(key)

    def clicked_names(self):
        return [(c.window_text(), double) for c, double, _ in self.clicks]
