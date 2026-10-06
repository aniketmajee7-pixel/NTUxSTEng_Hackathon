import random
import time
from urllib.parse import urlsplit

import pyautogui as pg

from domain import Phase, safe_same_page_link
from gui_support import Stopped

ERRORS = ('your connection is not private', "your connection isn't private",
          'this site can’t be reached', "this site can't be reached", 'access denied',
          'err_name_not_resolved', 'err_connection', 'privacy error', '403 forbidden')


class BrowserAction:
    def __init__(self, gui, config):
        self.gui, self.config = gui, config
        self.window = None
        self.tabs = 1
        self.visits = 0
        self.accessible_visits = 0

    def url(self):
        g = self.gui
        g.hotkey('ctrl', 'l', window=self.window)
        g.clear_clipboard(); g.hotkey('ctrl', 'c')
        value = g.read_clipboard().strip()
        pg.press('esc')
        return value

    def page_text(self):
        # No accessibility command-line flags: missing page accessibility is allowed.
        documents = self.window.descendants(control_type='Document', depth=5)
        for doc in documents:
            if not doc.is_visible():
                continue
            text = ' '.join(c.window_text() for c in doc.descendants(depth=3))
            if text.strip():
                return text[:20000]
            try:
                doc.set_focus()
                self.gui.clear_clipboard()
                self.gui.hotkey('ctrl', 'a')
                self.gui.hotkey('ctrl', 'c')
                copied = self.gui.read_clipboard()
                pg.press('esc')
                return copied[:20000]
            except (Stopped, pg.FailSafeException):
                raise
            except Exception:
                return ''
        return ''

    def navigate(self, homepage):
        g = self.gui
        g.hotkey('ctrl', 'l', window=self.window)
        g.type_text(homepage, self.window); pg.press('enter')
        # Slow, bounded readiness checks plus a separate reading pause.
        end = time.monotonic()+20
        while time.monotonic() < end:
            g.sleep(2)
            title = self.window.window_text().lower()
            if any(error in title for error in ERRORS):
                g.event('page_blocked', url=homepage, reason=title)
                return False
            text = self.page_text().lower()
            if any(error in text for error in ERRORS):
                g.event('page_blocked', url=homepage)
                return False
            if text.strip():
                break
        current = self.url()
        host = urlsplit(current).hostname or ''
        expected = urlsplit(homepage).hostname or ''
        if host.removeprefix('www.') != expected.removeprefix('www.'):
            g.event('navigation_skipped', requested=homepage, current=current)
            return False
        g.event('homepage_loaded', url=current, content_verified=bool(text.strip()))
        # If no accessible text is exposed, do not claim successful page inspection.
        if not text.strip():
            g.event('page_unverified', url=current, reason='No readable page content')
            return False
        return True

    def safe_anchor(self):
        g = self.gui
        current = self.url()
        for doc in self.window.descendants(control_type='Document', depth=5):
            for link in doc.descendants(control_type='Hyperlink', depth=4):
                if not link.is_visible() or not link.is_enabled():
                    continue
                try:
                    destination = link.legacy_properties().get('Value', '')
                except Exception:
                    continue
                if safe_same_page_link(current, destination):
                    g.click(link)
                    g.event('same_page_link', label=link.window_text(), destination=destination)
                    g.pause(reading=True)
                    g.hotkey('alt', 'left', window=self.window)
                    g.hotkey('alt', 'right', window=self.window)
                    return
        g.event('link_skipped', reason='No visible link with a verified same-page destination')

    def inspect_homepage(self, term):
        g = self.gui
        g.focus(self.window)
        rect = self.window.rectangle()
        # Move over content without clicking arbitrary webpage buttons.
        g.move(rect.left+rect.width()*0.75, rect.top+rect.height()*0.55)
        for _ in range(random.randint(3, 5)):
            g.focus(self.window); pg.scroll(-random.randint(2, 6))
            g.event('scroll_down'); g.pause(reading=True)
        g.hotkey('ctrl', 'f', window=self.window)
        g.type_text(term, self.window); pg.press('enter'); g.pause(reading=True); pg.press('esc')
        g.event('find_in_page', term=term)
        for _ in range(random.randint(2, 3)):
            g.focus(self.window); pg.scroll(random.randint(2, 5)); g.pause(reading=True)
        self.safe_anchor()

    def run(self, seconds):
        g = self.gui
        phase = Phase(seconds)
        self.window = g.launch_shortcut(self.config['chrome_shortcut'], r'.* - Google Chrome$')
        sources = list(self.config['homepages'])
        random.shuffle(sources)
        while not phase.expired:
            source = sources[self.visits % len(sources)]
            try:
                if self.visits and self.tabs < 3:
                    g.hotkey('ctrl', 't', window=self.window); self.tabs += 1
                elif self.visits and self.tabs == 3:
                    g.hotkey('ctrl', 'tab', window=self.window)
                    if self.visits % 3 == 0:
                        g.hotkey('ctrl', 'w', window=self.window); self.tabs -= 1
                if self.navigate(source['url']):
                    self.accessible_visits += 1
                    if self.visits:
                        # Ordinary browser history navigation, even when no safe anchor exists.
                        g.hotkey('alt', 'left', window=self.window); g.sleep(2)
                        g.hotkey('alt', 'right', window=self.window); g.sleep(2)
                        g.event('history_back_forward')
                    self.inspect_homepage(source['term'])
                else:
                    # Return safely using the address bar; never bypass warnings.
                    g.hotkey('alt', 'left', window=self.window)
                    g.pause()
            except (Stopped, pg.FailSafeException):
                raise
            except Exception as exc:
                g.event('browser_recovery', error=str(exc), url=source['url'])
                g.focus(self.window)
            self.visits += 1
        g.close_window(self.window)
        self.window = None
        g.event('browser_phase_complete', elapsed=time.monotonic()-phase.started,
                visits=self.visits, accessible_visits=self.accessible_visits)
        return {'visits': self.visits, 'accessible_visits': self.accessible_visits,
                'verified': self.accessible_visits > 0}
