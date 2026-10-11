"""Homepage-only browsing through visible Chrome UI, with bounded discovery."""
import random
import re
import time
from urllib.parse import urlsplit

import pyautogui as pg

from domain import Phase, safe_same_page_link, heavy_pause
from gui_support import Stopped, TimeUp

ERRORS = ('this site can’t be reached', "this site can't be reached", 'access denied',
          'err_name_not_resolved', 'err_connection', '403 forbidden', '404 not found')
# A homepage may land on its own locale root such as /sg/, /en-sg/, /index.html or
# /index.en-gb.html.
LANDING_PATH = re.compile(r'^/(?:[a-z]{2}(?:[-_][a-z]{2,4})?/?'
                          r'|index(?:\.[a-z]{2}(?:-[a-z]{2,4})?)?\.(?:html?|php))?$', re.I)
RETRYABLE = ('page_blocked', 'warning_unverified', 'warning_persists', 'load_timeout')
CERTIFICATE_ERRORS = ('your connection is not private', "your connection isn't private",
                      'privacy error', 'err_cert_', 'net::err_cert')


def normalize_host(host):
    return (host or '').lower().removeprefix('www.')


def hsts_blocked(text):
    text = ' '.join(text.casefold().split())
    return 'hsts' in text and any(s in text for s in
        ('cannot visit', "can't visit", 'can’t visit', 'cannot proceed', 'can’t proceed'))


def proceed_label_matches(label, host):
    """Accept Chrome's host-named and generic post-Advanced wording."""
    label = ' '.join(label.casefold().split())
    if not re.match(r'^(?:proceed|continue)\b', label):
        return False
    hosts = re.findall(r'(?:https?://)?([a-z0-9][a-z0-9.-]*\.[a-z]{2,})'
                       r'(?=[:/\s)(]|$)', label)
    # Some Chrome builds expose only "Continue" or "Proceed anyway".  At this
    # point the caller has already verified the approved URL and clicked the
    # warning's Advanced control, so generic wording is valid.  If a hostname
    # is present, it still has to match the requested host.
    return (not hosts or
            any(normalize_host(candidate) == normalize_host(host) for candidate in hosts))


def control_names(control):
    """Collect the names Chrome may expose through different UIA providers."""
    values = []
    try:
        values.append(control.window_text())
    except Exception:
        pass
    try:
        values.append(control.element_info.name)
    except Exception:
        pass
    try:
        legacy = control.legacy_properties()
        values.extend((legacy.get('Name', ''), legacy.get('Value', '')))
    except Exception:
        pass
    return tuple(dict.fromkeys(str(value).strip() for value in values if value))


def automation_id(control):
    try:
        return (control.element_info.automation_id or '').casefold().strip()
    except Exception:
        return ''


class BrowserAction:
    def __init__(self, gui, config):
        self.gui, self.config = gui, config
        self.window = None
        self.tabs = 1
        self.active_tab = 1
        self.retained_tab = None
        self.retained_term = None
        self.retained_url = None
        self.phase = None
        self.last_failure = None
        self.notices_continued = 0
        self.site_log = []
        self.blocked = {}        # url -> reason it did not load
        self.typed = set()       # addresses already typed in this run (never typed twice)
        self.current_url = None  # homepage shown in the active tab
        self.in_progress = None  # address being visited when the time cap hits
        self._omnibox = None
        self._omnibox_misses = 0
        self.visits = 0
        self.accessible_visits = 0
        self.scrolled_visits = 0  # Visits with wheel input; not proof of movement.
        self.verified_scrolls = 0
        self.content_verified = False
        self.invalidate_page()

    def invalidate_page(self, new_page=True):
        """Forget cached page state.  `new_page=False` only refreshes the cache while
        waiting for the same navigation, so one-off fallbacks are not repeated."""
        self._document = None
        self._document_checked = False
        self._document_checked_at = 0.0
        self._text = ''
        self._text_at = None
        if new_page:
            self._clipboard_attempted = False

    def document(self):
        now = time.monotonic()
        if self._document_checked and now-self._document_checked_at < 5:
            return self._document
        self._document_checked = True
        self._document_checked_at = now
        self._document = None
        # One FindAll call is far faster than walking Chrome's tree node by node.
        try:
            found = self.window.descendants(control_type='Document')
            if isinstance(found, (list, tuple)):
                self._document = next((c for c in found if c.is_visible()), None)
        except (Stopped, pg.FailSafeException):
            raise
        except Exception:
            pass
        if self._document is not None:
            return self._document
        # Fallback: prune page bodies while looking for the top-level Document.
        for control in self.gui.walk_controls(self.window, max_depth=7, max_nodes=200,
                                              prune_types=('Document',)):
            if control.element_info.control_type == 'Document' and control.is_visible():
                self._document = control
                break
        return self._document

    def page_controls(self, max_nodes=240, depth=5):
        doc = self.document()
        root = doc if doc is not None else self.window
        return self.gui.walk_controls(root, max_depth=depth, max_nodes=max_nodes)

    def notice_controls(self):
        """Find the interstitial's buttons and links with FindAll, not a timed tree walk.

        Chrome answers per-node UIA calls slowly, so the old breadth-first walk
        could time out before it ever reached Advanced or Proceed."""
        found = []
        queries = ({'control_type': 'Button'}, {'control_type': 'Hyperlink'},
                   {'auto_id': 'details-button'}, {'auto_id': 'proceed-link'})
        for query in queries:
            try:
                result = self.window.descendants(**query)
            except (Stopped, pg.FailSafeException):
                raise
            except Exception:
                continue
            if isinstance(result, (list, tuple)):
                found.extend(c for c in result if c not in found)
        if found:
            return found
        doc = self.document()
        return self.gui.walk_controls(doc if doc is not None else self.window,
                                      max_depth=10, max_nodes=500, timeout=4.0)

    def notice_diagnostics(self, controls):
        """Short record of what Chrome exposed, so a skip can be explained from the log."""
        names = []
        for control in controls:
            names.extend(control_names(control)[:1] or [automation_id(control)])
        return [name[:60] for name in names if name][:15]

    def locate_proceed_text(self, hostname):
        """Fallback when UIA lists no Proceed link: find its text and return a click point."""
        doc = self.document()
        if doc is None:
            return None
        try:
            for prefix in ('Proceed to', 'Continue to'):
                found = doc.iface_text.DocumentRange.FindText(prefix, False, True)
                if found is None:
                    continue
                rects = found.GetBoundingRectangles()
                line = found.Clone()
                line.ExpandToEnclosingUnit(3)  # TextUnit_Line
                if not proceed_label_matches(line.GetText(300), hostname):
                    continue
                if rects and len(rects) >= 4 and rects[2] > 2 and rects[3] > 2:
                    return (rects[0]+rects[2]*random.uniform(0.3, 0.7),
                            rects[1]+rects[3]*random.uniform(0.35, 0.65))
        except (Stopped, pg.FailSafeException):
            raise
        except Exception:
            pass
        return None

    def omnibox(self):
        """Chrome's address bar as a UI Automation control (cached; one per window)."""
        if self._omnibox is not None:
            return self._omnibox
        # One query by name and type.  The bounded walk below gave up before reaching the
        # address bar once several tabs were open, which forced the Ctrl+L fallback.
        found = None
        if self._omnibox_misses < 3:           # a whole-window query: not worth repeating forever
            try:
                found = self.window.descendants(control_type='Edit', title='Address and search bar')
            except (Stopped, pg.FailSafeException):
                raise
            except Exception:
                found = None
            if isinstance(found, (list, tuple)) and found:
                self._omnibox = found[0]
                return self._omnibox
            self._omnibox_misses += 1
        for candidate in self.browser_chrome_controls():
            try:
                if (candidate.element_info.control_type == 'Edit'
                        and 'address and search bar' in candidate.window_text().casefold()):
                    self._omnibox = candidate
                    break
            except (Stopped, pg.FailSafeException):
                raise
            except Exception:
                continue
        return self._omnibox

    def url_from_accessibility(self):
        """Read the address bar without touching the keyboard or the clipboard."""
        box = self.omnibox()
        if box is None:
            return ''
        value = ''
        for read in (lambda: box.iface_value.CurrentValue,
                     lambda: box.legacy_properties().get('Value', '')):
            try:
                value = read()
            except (Stopped, pg.FailSafeException):
                raise
            except Exception:
                continue
            if isinstance(value, str) and value.strip():
                break
        if not isinstance(value, str) or not value.strip():
            self._omnibox = None  # stale control: look it up again next time
            return ''
        value = value.strip()
        # Chrome hides "https://" (and "www.") for ordinary secure pages.
        return value if '://' in value else 'https://'+value

    def document_url(self):
        """URL of the page actually loaded in the tab (Chrome exposes it as the document's
        value).  Unlike the address bar it keeps the scheme and only changes once the
        navigation has really happened; error pages report chrome-error://..."""
        doc = self.document()
        if doc is None:
            return ''
        for read in (lambda: doc.iface_value.CurrentValue,
                     lambda: doc.legacy_properties().get('Value', '')):
            try:
                value = read()
            except (Stopped, pg.FailSafeException):
                raise
            except Exception:
                continue
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ''

    def url_from_clipboard(self):
        g = self.gui
        g.hotkey('ctrl', 'l', window=self.window)
        g.clear_clipboard(); g.hotkey('ctrl', 'c')
        value = g.read_clipboard().strip()
        self.gui.press('esc')
        return value

    def url(self):
        """Current address, read without touching the keyboard; '' when it cannot be read.

        Ctrl+L, Ctrl+C on a page is not something people do: it left the whole address
        selected (blue) with the suggestion list open on every privacy-warning page.  That
        route is used only when config asks for it (address_bar_method: "clipboard").
        Callers fall back to the address they asked for."""
        if self.config.get('address_bar_method', 'accessibility') == 'clipboard':
            return self.url_from_clipboard()
        page = self.document_url()
        if page.startswith(('http://', 'https://')):
            return page          # the true URL, scheme included
        return self.url_from_accessibility()

    def same_site(self, host, homepage):
        """Whether `host` is where an approved homepage legitimately lands: the requested
        host, one of its sub-domains (cnn.com -> edition.cnn.com), or a host listed for
        that site under "also" in config (bit.ly -> bitly.com)."""
        host = normalize_host(host)
        allowed = [normalize_host(urlsplit(homepage).hostname)]
        for site in self.config.get('homepages', ()):
            if site.get('url') == homepage:
                allowed += [normalize_host(other) for other in site.get('also', ())]
        return bool(host) and any(base and (host == base or host.endswith('.'+base))
                                  for base in allowed)

    def type_browser_text(self, text, address=False):
        typo = random.random() < self.config.get('browser_typo_probability', 0.2)
        kind = random.choices(('wrong', 'double', 'swap'),
                              weights=self.config.get('typo_kind_weights', (1, 0, 0)))[0]
        self.gui.type_text(text, self.window, corrected_typo=typo,
                           clear_inline_completion=address, typo_kind=kind)

    def brief_read(self, interest=False):
        """Use the two dwell bands measured in the 3:43 manual recording."""
        setting = 'browser_interest_seconds' if interest else 'browser_read_seconds'
        default = [4.0, 8.0] if interest else [1.0, 2.8]
        low, high = self.config.get(setting, default)
        # The configured band is the typical range; real dwell times are skewed, with
        # the occasional much longer look, so draw from a lognormal around its middle.
        scale = getattr(self.gui, 'pause_scale', 1.0)
        scale = scale if isinstance(scale, (int, float)) else 1.0
        median = (low*high)**0.5*self.gui.pace*scale
        self.gui.sleep(heavy_pause(median, 0.4 if interest else 0.5, low*0.6, high*3.5))

    def browser_chrome_controls(self):
        """Inspect only Chrome chrome; page Documents are pruned for speed."""
        return self.gui.walk_controls(self.window, max_depth=6, max_nodes=160,
                                      prune_types=('Document',))

    def open_new_tab(self):
        """Mirror the manual click on Chrome's + control, with a shortcut fallback."""
        button = None
        for candidate in self.browser_chrome_controls():
            try:
                label = candidate.window_text().casefold().strip().rstrip('.…')
                if (candidate.element_info.control_type == 'Button'
                        and label in ('new tab', 'new tab button')
                        and candidate.is_visible() and candidate.is_enabled()):
                    button = candidate
                    break
            except (Stopped, pg.FailSafeException):
                raise
            except Exception:
                continue
        if button is not None:
            self.gui.click(button, window=self.window)
            self.gui.event('new_tab_opened', method='visible_control')
        else:
            self.gui.hotkey('ctrl', 't', window=self.window)
            self.gui.event('new_tab_opened', method='keyboard_fallback')

    def activate_tab(self, index):
        """Click a visible tab like the manual run; use Ctrl+number if UIA omits it.
        Sometimes people switch with the keyboard instead (Ctrl+Tab / Ctrl+number)."""
        if random.random() < self.config.get('tab_keyboard_probability', 0.3):
            next_tab = self.active_tab % max(self.tabs, 1)+1
            keys = ('ctrl', 'tab') if index == next_tab else ('ctrl', str(index))
            self.gui.hotkey(*keys, window=self.window)
            self.gui.event('tab_activated', tab=index, method='keyboard', keys='+'.join(keys))
            return
        tabs = []
        for candidate in self.browser_chrome_controls():
            try:
                if (candidate.element_info.control_type == 'TabItem'
                        and candidate.is_visible() and candidate.is_enabled()):
                    tabs.append(candidate)
            except (Stopped, pg.FailSafeException):
                raise
            except Exception:
                continue
        try:
            tabs.sort(key=lambda item: item.rectangle().left)
            target = tabs[index-1]
        except (IndexError, AttributeError, TypeError):
            target = None
        if target is not None:
            self.gui.click(target, window=self.window)
            self.gui.event('tab_activated', tab=index, method='visible_control')
        else:
            self.gui.hotkey('ctrl', str(index), window=self.window)
            self.gui.event('tab_activated', tab=index, method='keyboard_fallback')

    def page_text(self, refresh=False):
        # Cache briefly during load polls; never reuse across navigation or tab switches.
        now = time.monotonic()
        if not refresh and self._text_at is not None and now-self._text_at < 2.5:
            return self._text
        doc = self.document()
        text = ''
        if doc is not None:
            try:
                # Read Text pattern without Ctrl+A or enumerating a large page tree.
                text = doc.iface_text.DocumentRange.GetText(20000)
            except (Stopped, pg.FailSafeException):
                raise
            except Exception:
                pass
        if not text.strip():
            parts = []
            size = 0
            for control in self.page_controls():
                try:
                    # Browser chrome text alone is not evidence of page content.
                    if doc is None and control.element_info.control_type not in ('Text', 'Hyperlink'):
                        continue
                    value = control.window_text()
                except (Stopped, pg.FailSafeException):
                    raise
                except Exception:
                    continue
                if value:
                    parts.append(value); size += len(value)+1
                if size >= 20000:
                    break
            text = ' '.join(parts)[:20000]
        if (not text.strip() and doc is not None and not self._clipboard_attempted
                and self.config.get('page_text_clipboard_fallback', False)):
            self._clipboard_attempted = True
            try:
                self.gui.focus(self.window); doc.set_focus()
                self.gui.clear_clipboard()
                self.gui.hotkey('ctrl', 'a'); self.gui.hotkey('ctrl', 'c')
                text = self.gui.read_clipboard()[:20000]
            except (Stopped, pg.FailSafeException):
                raise
            except Exception:
                pass
            finally:
                self.gui.clear_browser_selection(self.window, doc)
        self._text, self._text_at = text, time.monotonic()
        return text

    def continue_organiser_notice(self, homepage):
        """Click Advanced, then click visible Continue/Proceed; otherwise skip."""
        g = self.gui
        requested = urlsplit(homepage)
        approved = {s['url'] for s in self.config['homepages']}
        current = urlsplit(self.url())
        if not current.hostname:
            # Chrome sometimes copies nothing from an error page's address bar.
            current = requested
        if (homepage not in approved or requested.scheme != 'https'
                or current.scheme != 'https' or current.username or current.password
                or not self.same_site(current.hostname, homepage)
                or current.port not in (None, 443)):
            g.event('notice_not_continued', reason='Not the requested approved homepage')
            return False
        g.focus(self.window)
        # Chrome builds the page's accessibility tree lazily, so poll for Advanced.
        advanced, scanned = None, []
        end = time.monotonic()+self.config.get('privacy_advanced_timeout_seconds', 8.0)
        while advanced is None:
            g.check_stop()
            scanned = list(self.notice_controls())
            advanced = next((c for c in scanned if c.is_visible() and c.is_enabled()
                and (automation_id(c) == 'details-button' or (
                    c.element_info.control_type in ('Button', 'Hyperlink') and any(
                    name.casefold().strip().rstrip('.…') in ('advanced', 'show advanced')
                    for name in control_names(c))))), None)
            if advanced is not None or time.monotonic() >= end:
                break
            g.sleep(random.uniform(0.4, 0.7))
        if advanced is None:
            g.event('notice_not_continued', reason='Advanced control missing',
                    seen=self.notice_diagnostics(scanned))
            return False
        # Always expand first. Chrome reveals HSTS/no-Proceed status only afterward.
        g.click(advanced, window=self.window); g.sleep(random.uniform(0.6, 1.1))
        self.invalidate_page()
        end = time.monotonic()+self.config.get('privacy_proceed_timeout_seconds', 8.0)
        scanned, text = [], ''
        while time.monotonic() < end:
            g.check_stop()
            text = self.page_text()
            # Test for the exposed Continue/Proceed control first.  This keeps
            # the organiser's required condition exact even when warning text
            # varies between Chrome versions.
            scanned = list(self.notice_controls())
            control = next((c for c in scanned if c.is_visible() and c.is_enabled()
                and (automation_id(c) == 'proceed-link' or (
                    c.element_info.control_type in ('Hyperlink', 'Button') and any(
                    proceed_label_matches(name, current.hostname)
                    for name in control_names(c))))), None)
            if control is not None:
                address = urlsplit(self.url())
                if not address.hostname:
                    address = current
                if (address.scheme != 'https' or address.username or address.password
                        or normalize_host(address.hostname) != normalize_host(current.hostname)
                        or address.port not in (None, 443)):
                    g.event('notice_not_continued', reason='Address changed')
                    return False
                names = control_names(control)
                label = names[0] if names else automation_id(control)
                g.focus(self.window); g.click(control, window=self.window)
                self.invalidate_page()
                g.event('organiser_notice_continued', url=address.geturl(), control=label)
                return True
            if control is None and not hsts_blocked(text):
                point = self.locate_proceed_text(current.hostname)
                if point is not None:
                    g.click_point(*point, window=self.window)
                    self.invalidate_page()
                    g.event('organiser_notice_continued', url=current.geturl(),
                            control='text-pattern link')
                    return True
            if hsts_blocked(text):
                low, high = self.config.get('warning_inspect_seconds', [0.8, 1.6])
                g.sleep(random.uniform(low, high)*g.pace)
                g.event('website_skipped', url=homepage, reason='HSTS: no Continue/Proceed option')
                return False
            g.sleep(min(random.uniform(0.45, 0.75), max(0, end-time.monotonic())))
        g.event('notice_not_continued', reason='No visible Continue/Proceed control after Advanced',
                seen=self.notice_diagnostics(scanned), text=' '.join(text.split())[:160])
        return False

    def navigate(self, homepage, reload=False):
        """Load a homepage, handling a privacy warning every time one appears.

        A redirect (for example to the www host) can raise a second warning, so up
        to three are handled per navigation, each with Advanced -> Continue.
        `reload=True` refreshes the page already in the tab (F5) instead of typing
        the address again: an address is typed at most once per run."""
        g = self.gui
        self.content_verified = False
        self.last_failure = None
        self.notices_continued = 0
        self.invalidate_page()
        previous_title = self.window.window_text().casefold()
        if reload:
            g.hotkey('f5', window=self.window)
        else:
            g.hotkey('ctrl', 'l', window=self.window)
            # Typed the way the manual run did: 'www.apple.com', without the scheme.
            self.type_browser_text(urlsplit(homepage).netloc, address=True)
            g.press('enter')
        load_timeout = self.config.get('browser_load_timeout_seconds', 14.0)
        end = time.monotonic()+load_timeout
        requested = urlsplit(homepage)
        text = ''
        warned = False
        warning_grace = None
        committed = False   # the tab really shows the requested site, not the page before it
        settled = 0
        while time.monotonic() < end:
            g.sleep(random.uniform(0.6, 1.2))
            self.invalidate_page(new_page=False)
            title = self.window.window_text().casefold()
            title_warning = any(error in title for error in CERTIFICATE_ERRORS)
            text = '' if title_warning else self.page_text().casefold()
            warning = title_warning or any(error in text for error in CERTIFICATE_ERRORS)
            if warning:
                warned = True
                settled = 0
                if warning_grace is not None and time.monotonic() < warning_grace:
                    continue  # Chrome may still be tearing the old warning down
                if self.notices_continued >= self.config.get('max_notices_per_visit', 3):
                    self.last_failure = 'warning_persists'
                    g.event('website_skipped', url=homepage, reason='Warning remains after Proceed')
                    return False
                if not self.continue_organiser_notice(homepage):
                    self.last_failure = 'no_proceed'
                    g.event('website_skipped', url=homepage, reason='No usable Proceed option')
                    return False
                self.notices_continued += 1
                g.focus(self.window)
                end = time.monotonic()+load_timeout
                warning_grace = time.monotonic()+self.config.get(
                    'privacy_verify_timeout_seconds', 4.0)
                continue
            if any(error in title+' '+text for error in ERRORS):
                self.last_failure = 'page_blocked'
                g.event('page_blocked', url=homepage)
                return False
            # Until the navigation commits, the tab still holds the previous page (or the
            # New Tab page) and its text.  Judging that as "loaded" would miss a warning
            # that appears a moment later, so wait for the site itself.
            page = self.document_url()
            if page:
                committed = self.same_site(urlsplit(page).hostname, homepage)
            else:
                committed = reload or title != previous_title
            if committed and text.strip():
                settled += 1
                if settled >= 2:      # two looks in a row: no late warning, content present
                    break
            else:
                settled = 0
        if not committed:
            self.last_failure = 'load_timeout'
            g.event('website_skipped', url=homepage, reason='Page did not load in time')
            return False
        # If the address cannot be read, the tab holds what was just asked for: the load
        # was confirmed above from the page itself.
        current = self.url() or homepage
        address = urlsplit(current)
        # A query string on the landing path (tracking parameters) is still the homepage.
        if (address.scheme != 'https' or address.username or address.password
                or not self.same_site(address.hostname, homepage)
                or address.port not in (None, 443)
                or not LANDING_PATH.match(address.path or '/')):
            self.last_failure = 'left_homepage'
            g.event('navigation_skipped', requested=homepage, current=current)
            return False
        if warned and not text.strip():
            self.last_failure = 'warning_unverified'
            g.event('website_skipped', url=homepage, reason='Warning disappearance could not be verified')
            return False
        self.content_verified = bool(text.strip()) and self.document() is not None
        self.current_url = homepage
        g.event('homepage_loaded', url=current, content_verified=self.content_verified,
                notices_continued=self.notices_continued)
        if not self.content_verified:
            g.event('page_unverified', url=current, reason='No readable document content')
        return True

    def late_warning(self):
        """A privacy warning that shows up after the page was judged loaded (slow redirect,
        reload, back/forward).  Returns None if there is none, else whether it was passed."""
        title = self.window.window_text().casefold()
        if not any(error in title for error in CERTIFICATE_ERRORS) or not self.current_url:
            return None
        g = self.gui
        g.event('late_warning_seen', url=self.current_url)
        self.invalidate_page()
        passed = self.continue_organiser_notice(self.current_url)
        self.invalidate_page()
        if passed:
            self.brief_read()
        return passed

    def safe_anchor(self):
        """Follow one same-page (#fragment) link if the page has one, then come back."""
        g = self.gui
        current = self.url() or self.current_url or ''
        for link in self.page_controls():
            try:
                if (link.element_info.control_type != 'Hyperlink' or not link.is_visible()
                        or not link.is_enabled()):
                    continue
                destination = link.legacy_properties().get('Value', '')
                label = link.window_text()
            except (Stopped, pg.FailSafeException):
                raise
            except Exception:
                continue      # element went stale while the page changed
            if safe_same_page_link(current, destination):
                g.click(link, window=self.window)
                self.invalidate_page()
                g.event('same_page_link', label=label, destination=destination)
                self.brief_read()
                g.hotkey('alt', 'left', window=self.window)
                self.invalidate_page(); self.brief_read()
                g.hotkey('alt', 'right', window=self.window)
                self.invalidate_page()
                return
        g.event('link_skipped', reason='No visible link with a verified same-page destination')

    def hover_content(self, interest=False):
        # Hover only; never activate a product, registration or account link.
        candidates = []
        for control in self.page_controls(max_nodes=100, depth=4):
            try:
                if control.element_info.control_type not in ('Hyperlink', 'Image', 'Text'):
                    continue
                label = control.window_text()
                if not label or any(word in label.casefold() for word in
                    ('buy', 'purchase', 'account', 'sign in', 'login', 'register', 'download', 'cart')):
                    continue
                if control.is_visible() and control.is_enabled():
                    candidates.append(control)
                if len(candidates) >= 8:
                    break
            except (Stopped, pg.FailSafeException):
                raise
            except Exception:
                continue
        if candidates:
            control = random.choice(candidates)
            try:
                self.gui.move(*self.gui.control_point(control))
                self.gui.sleep(random.uniform(0.2, 0.8)*self.gui.pace)
                if interest:
                    self.brief_read(interest=True)
                self.gui.event('content_hover', label=control.window_text()[:120],
                               interest=interest)
                return True
            except (Stopped, pg.FailSafeException):
                raise
            except Exception:
                pass          # target moved or went stale: just skip the hover

        return False

    def scroll(self, direction, dwell_range=None, fraction_range=None, fling=False):
        """Move about a viewport (one 'section'); selection is cleared after find, not here."""
        doc = self.document()
        if (direction < 0 and not fling and doc is not None
                and random.random() < self.config.get('keyboard_scroll_probability', 0.15)):
            return self.keyboard_scroll(doc, dwell_range)
        kwargs = {
            'fraction_range': tuple(fraction_range or
                                    self.config.get('browser_scroll_viewport_fraction', [0.75, 1.1])),
            'notch_px': self.config.get('browser_scroll_notch_px', 100),
            'flick_range': tuple(self.config.get('browser_scroll_flicks', [2, 4])),
            'dwell_range': (tuple(dwell_range) if dwell_range is not None else
                            tuple(self.config.get('browser_scroll_dwell_seconds', [1.0, 2.6]))),
            'fling': fling,
        }
        changed = self.gui.scroll_content(self.window, direction, document=doc, **kwargs)
        self.verified_scrolls += int(changed is True)
        if changed is False:
            self.gui.event('scroll_retry', reason='Accessible scroll position unchanged')
            changed = self.gui.scroll_content(self.window, direction, document=doc, **kwargs)
            self.verified_scrolls += int(changed is True)
        return changed

    def keyboard_scroll(self, doc, dwell_range=None):
        """Page Down or Space instead of the wheel, as people often do while reading."""
        g = self.gui
        g.focus(self.window)
        before = g.scroll_position(doc)
        doc.set_focus()                      # keys must go to the page, not the address bar
        key = random.choice(('pagedown', 'space'))
        g.press(key)
        low, high = dwell_range or self.config.get('browser_scroll_dwell_seconds', [1.0, 2.6])
        g.sleep(heavy_pause((low*high)**0.5*g.pace, 0.5, low*0.6, high*3.5))
        after = g.scroll_position(doc)
        changed = None if before is None or after is None else abs(after-before) > 0.01
        self.verified_scrolls += int(changed is True)
        g.event('keyboard_scroll', key=key, before_percent=before, after_percent=after,
                movement_verified=changed)
        return changed

    def zoom_briefly(self):
        """Zoom in to read something small, then return to normal size."""
        g = self.gui
        g.hotkey('ctrl', '=', window=self.window)
        try:
            self.brief_read()
        finally:
            g.hotkey('ctrl', '0', window=self.window)
        g.event('zoomed_in_and_reset')

    def reload_page(self):
        """Refresh the page; if the privacy warning comes back, continue through it again."""
        g = self.gui
        g.hotkey('f5', window=self.window)
        self.invalidate_page()
        self.brief_read()
        title = self.window.window_text().casefold()
        warned = any(error in title for error in CERTIFICATE_ERRORS)
        if warned and self.current_url:
            self.continue_organiser_notice(self.current_url)
            self.invalidate_page(); self.brief_read()
        g.event('page_reloaded', warning_seen=warned)

    def tab_count(self):
        """Number of tabs Chrome shows, or 0 when it does not expose them."""
        count = 0
        for candidate in self.browser_chrome_controls():
            try:
                count += candidate.element_info.control_type == 'TabItem'
            except (Stopped, pg.FailSafeException):
                raise
            except Exception:
                continue
        return count

    def select_word(self):
        """Double-click a word in body text, as readers do; never inside a link or button."""
        g = self.gui
        target = None
        for control in self.page_controls(max_nodes=120, depth=5):
            try:
                if (control.element_info.control_type != 'Text'
                        or len(control.window_text()) < 30
                        or not control.is_visible()):
                    continue
                ancestor, clickable = control, False
                for _ in range(5):
                    ancestor = ancestor.parent()
                    if ancestor is None:
                        break
                    if ancestor.element_info.control_type in ('Hyperlink', 'Button', 'MenuItem'):
                        clickable = True
                        break
                if not clickable:
                    target = control
                    break
            except (Stopped, pg.FailSafeException):
                raise
            except Exception:
                continue
        if target is None:
            return False
        title = self.window.window_text()
        tabs_before = self.tab_count()
        g.click(target, double=True, window=self.window)
        self.brief_read()
        if tabs_before and self.tab_count() > tabs_before:
            # The text opened a new tab: close it so the tab bookkeeping stays right.
            g.hotkey('ctrl', 'w', window=self.window)
            self.invalidate_page()
            g.event('word_selection_closed_new_tab')
            return False
        if self.window.window_text() != title:
            # The text turned out to be clickable and the page changed: go straight back.
            g.hotkey('alt', 'left', window=self.window)
            self.invalidate_page()
            g.event('word_selection_navigated_back')
            return False
        g.clear_browser_selection(self.window, self.document())
        g.event('word_selected', text=target.window_text()[:60])
        return True

    def extra_action(self):
        """One small on-page habit per visit at most; a failure never ends the visit."""
        action = random.choice((self.zoom_briefly, self.reload_page, self.select_word))
        try:
            action()
        except (Stopped, pg.FailSafeException):
            raise
        except Exception as exc:
            self.gui.event('extra_action_failed', action=action.__name__, error=str(exc)[:120])
            self.invalidate_page()

    def inspect_homepage(self, term, revisit=False):
        """Read a homepage the way a person does: engagement varies page to page.

        `term` is kept for the caller's signature; there is no in-page search,
        because people rarely use Ctrl+F while casually browsing."""
        g = self.gui
        g.focus(self.window)
        if revisit:
            mode, steps, dwell = 'revisit', random.randint(2, 3), 0.8
        else:
            mode, steps, dwell = random.choices(
                (('skim', random.randint(2, 4), 0.6),
                 ('normal', random.randint(5, 8), 1.0),
                 ('deep', random.randint(9, 13), 1.4)), weights=(0.22, 0.58, 0.2))[0]
        g.event('page_engagement', mode=mode, sections=steps)
        interest_step = random.randrange(1, steps) if mode != 'skim' and steps > 2 else -1
        extra_step = (random.randrange(steps) if not revisit and random.random() <
                      self.config.get('browser_extra_action_probability', 0.3) else -1)
        for step in range(steps):
            if self.phase is not None and self.phase.expired:
                return  # hard cap: stop reading when the phase time is up
            if self.late_warning() is False:
                g.event('visit_ended', reason='Privacy warning could not be passed')
                return
            low, high = self.config.get('browser_scroll_dwell_seconds', [1.0, 2.6])
            moved = self.scroll(-1, dwell_range=(low*dwell, high*dwell))
            if step == interest_step or random.random() < 0.38:
                self.hover_content(interest=step == interest_step)
            if step == extra_step:
                self.extra_action()
            if moved is False:
                # An exposed scroll percentage says the page is already at its
                # limit.  Do not keep wheeling at the same section.
                break
        # Manual run: reaching the bottom is followed by a quick flick back up.
        if mode != 'skim' and random.random() < 0.7 and not (self.phase and self.phase.expired):
            self.scroll(1, dwell_range=(0.45, 1.2), fraction_range=(3.5, 6.0), fling=True)
        if not revisit and mode != 'skim':
            self.safe_anchor()

    def prepare_next_tab(self):
        if not self.visits:
            return
        g = self.gui
        if self.tabs < 3:
            self.open_new_tab()
            self.tabs += 1; self.active_tab = self.tabs
        else:
            target = 3 if self.retained_tab != 3 else 2
            self.activate_tab(target)        # active_tab still names the tab being left
            self.active_tab = target
        self.invalidate_page()

    def revisit_retained(self):
        if self.retained_tab is None or self.active_tab == self.retained_tab:
            return
        if self.phase is not None and self.phase.expired:
            return
        g = self.gui
        return_tab = self.active_tab
        self.activate_tab(self.retained_tab)
        self.active_tab = self.retained_tab; self.invalidate_page()
        title = self.window.window_text().casefold()
        text = self.page_text().casefold()
        if any(error in title+' '+text for error in CERTIFICATE_ERRORS) and self.retained_url:
            if self.continue_organiser_notice(self.retained_url):
                self.invalidate_page()
                title = self.window.window_text().casefold()
                text = self.page_text().casefold()
        if not any(error in title+' '+text for error in CERTIFICATE_ERRORS+ERRORS):
            g.event('tab_revisited', tab=self.retained_tab, reloaded=False)
            self.brief_read()
            shown, self.current_url = self.current_url, self.retained_url
            try:
                self.inspect_homepage(self.retained_term, revisit=True)
            finally:
                self.current_url = shown
        else:
            g.event('tab_revisit_skipped', tab=self.retained_tab, reason='Page is now blocked')
        self.activate_tab(return_tab)
        self.active_tab = return_tab; self.invalidate_page()
        if self.tabs == 3 and self.visits % 4 == 3:
            g.hotkey('ctrl', 'w', window=self.window)
            self.tabs = 2
            if return_tab < self.retained_tab:
                self.retained_tab -= 1
            self.active_tab = min(return_tab, self.tabs)
            self.invalidate_page()
            g.event('disposable_tab_closed', retained_tab=self.retained_tab)

    def next_source(self, sources):
        """Next homepage that has not been typed yet in this run, or None when all have."""
        for source in sources:
            if source['url'] not in self.typed:
                return source
        return None

    def remember_outcome(self, url, loaded):
        """Keep the status of every site visited (reported under `sites`)."""
        if not loaded:
            self.blocked[url] = self.last_failure
            self.gui.event('site_marked_blocked', url=url, reason=self.last_failure)

    def run(self, seconds):
        g = self.gui
        phase = self.phase = Phase(seconds)
        self.window = g.launch_shortcut(self.config['chrome_shortcut'], r'.* - Google Chrome$')
        sources = list(self.config['homepages']); random.shuffle(sources)
        ended_early = False
        # Hard cap: at the deadline every wait and key press raises TimeUp, which ends the
        # phase wherever it is (the window is then closed below).
        g.time_limit = phase.deadline
        try:
            ended_early = self.browse(phase, sources)
        except TimeUp:
            g.event('browser_time_up', visits=self.visits)
            if self.in_progress and all(e['url'] != self.in_progress for e in self.site_log):
                self.site_log.append({'url': self.in_progress, 'loaded': False,
                                      'reason': 'time ran out'})
        finally:
            g.time_limit = None
        unused = round(phase.remaining, 1)
        closed = True
        try:
            g.close_window(self.window)
        except (Stopped, pg.FailSafeException):
            raise
        except Exception as exc:
            # For example a page asking "Leave site?".  The browsing itself is done.
            closed = False
            g.event('browser_close_failed', error=str(exc)[:120])
        self.window = None
        g.event('browser_phase_complete', elapsed=time.monotonic()-phase.started,
                visits=self.visits, accessible_visits=self.accessible_visits,
                ended_early=ended_early)
        return {'visits': self.visits, 'ended_early': ended_early, 'unused_seconds': unused, 'accessible_visits': self.accessible_visits,
                'scrolled_visits': self.scrolled_visits, 'verified_scroll_bursts': self.verified_scrolls,
                'sites': self.site_log, 'blocked_sites': dict(self.blocked), 'closed': closed,
                'verified': self.accessible_visits > 0}

    def linger(self, phase):
        """Too little time left to start another site: keep reading instead."""
        g = self.gui
        g.event('browser_lingering', seconds_left=round(phase.remaining, 1))
        readable = self.content_verified
        if (not readable and self.retained_tab is not None
                and self.active_tab != self.retained_tab):
            self.activate_tab(self.retained_tab)        # the current tab has nothing to read
            self.active_tab = self.retained_tab; self.invalidate_page()
            self.current_url = self.retained_url
            readable = True
        while not phase.expired:
            self.brief_read()
            if readable:                                # never scroll or re-try a blocked page
                self.inspect_homepage(self.retained_term or '', revisit=True)

    def browse(self, phase, sources):
        """Visit the homepages one by one until the time is up or all were visited.
        Returns True when every site was visited before the time ran out."""
        g = self.gui
        ended_early = False
        while not phase.expired:
            source = self.next_source(sources)
            if source is None:
                # Every listed site has been visited once.  Typing the same addresses
                # again just to fill the time is not what a person would do: move on.
                ended_early = True
                g.event('all_sites_visited', sites=len(sources), unused_seconds=round(phase.remaining, 1))
                break
            if self.visits and phase.remaining < self.config.get('browser_min_visit_seconds', 30):
                self.linger(phase)
                break
            self.typed.add(source['url'])
            self.in_progress = source['url']
            entry = None
            try:
                self.prepare_next_tab()
                loaded = self.navigate(source['url'])
                if (not loaded and self.last_failure in RETRYABLE and not phase.expired):
                    # Transient trouble (blocked load, warning that would not clear): try once more.
                    g.event('navigation_retry', url=source['url'], reason=self.last_failure)
                    g.sleep(random.uniform(2.0, 4.0)*g.pace)
                    loaded = self.navigate(source['url'], reload=True)  # refresh, do not retype
                entry = {'url': source['url'], 'loaded': bool(loaded),
                         'reason': None if loaded else self.last_failure,
                         'notices_continued': self.notices_continued}
                self.site_log.append(entry)
                self.remember_outcome(source['url'], loaded)
                if loaded:
                    self.accessible_visits += int(self.content_verified)
                    if self.retained_tab is None and self.content_verified:
                        self.retained_tab = self.active_tab
                        self.retained_term = source['term']
                        self.retained_url = source['url']
                    self.inspect_homepage(source['term'])
                    self.scrolled_visits += 1
                    if not phase.expired and self.visits % 2 == 1:
                        self.revisit_retained()
                else:
                    g.sleep(random.uniform(0.2, 0.6)*g.pace)
            except (Stopped, pg.FailSafeException):
                raise
            except Exception as exc:
                if entry is None:
                    self.site_log.append({'url': source['url'], 'loaded': False,
                                          'reason': 'error: '+str(exc)[:120]})
                else:
                    entry['interrupted'] = str(exc)[:120]   # it loaded; the visit was cut short
                g.event('browser_recovery', error=str(exc), url=source['url'])
                self.invalidate_page()
                g.sleep(random.uniform(1.0, 2.5))  # never spin on a repeating failure
                try:
                    g.focus(self.window)
                except (Stopped, pg.FailSafeException):
                    raise
                except Exception as focus_error:
                    g.event('browser_focus_lost', error=str(focus_error))
            self.visits += 1
            # Attention and speed drift over a session instead of staying constant.
            g.pace = min(1.35, max(0.8, g.pace*random.uniform(0.94, 1.07)))
        return ended_early
