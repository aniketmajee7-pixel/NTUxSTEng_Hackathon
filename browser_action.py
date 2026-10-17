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
RETRYABLE = ('page_blocked', 'warning_unverified', 'warning_persists', 'load_timeout',
             'advanced_not_opened')
# The site's own answer, not a passing problem: a reload brings the same page again.
PAGE_ERRORS = ('403 forbidden', '404 not found', 'access denied')
# Text Chrome shows once Advanced has opened the warning's details.
DETAILS_TEXT = ('could not prove', 'normally uses encryption', 'proceed to', 'hsts')
CERTIFICATE_ERRORS = ('your connection is not private', "your connection isn't private",
                      'privacy error', 'err_cert_', 'net::err_cert')
# A privacy warning names its host ("...steal your information from www.archive.org").
HOST_IN_TEXT = re.compile(r'\b(?:[a-z0-9-]+\.)+[a-z]{2,}\b')
# Consent and cookie pop-ups: CNN's "Legal Terms and Privacy" covered its page until Agree
# was clicked by hand.  Exact labels only; the generic ones count only when the page talks
# about cookies or consent.  Reject, Manage, Settings and the like are never clicked.
CONSENT_LABELS = ('agree', 'i agree', 'accept', 'accept all', 'accept all cookies',
                  'accept cookies', 'allow all', 'allow all cookies', 'allow cookies', 'i accept',
                  'agree and continue', 'agree & continue', 'accept and continue',
                  'accept & continue', 'accept and close', 'agree and close', 'yes, i agree',
                  'yes, i accept')
GENERIC_CONSENT_LABELS = ('ok', 'okay', 'got it', 'continue', 'i understand', 'understood')
CONSENT_WORDS = ('cookie', 'consent', 'privacy', 'terms of use', 'personal data')
# Pop-ups that only want something are closed with their X, as people close them.  Bitly's
# "Want to try Bitly for free?" opens when the pointer leaves the page at the top (the agent
# going up to the find bar or the + button) and stayed over the page, which went on
# scrolling behind it.  Only controls inside the page count (Chrome's own tabs and find bar
# have "Close" buttons too), and a plain "Close" or X only inside a dialog.
CLOSE_LABELS = ('close modal', 'close dialog', 'close popup', 'close pop-up', 'close this popup',
                'close this dialog', 'close the dialog', 'close the popup', 'close overlay',
                'dismiss', 'no thanks', 'no, thanks', 'no thank you', 'no, thank you',
                'maybe later', 'not now')
PLAIN_CLOSE_LABELS = ('close', '×', '✕', '✖', 'x')
POPUP_LABELS = frozenset(CONSENT_LABELS+GENERIC_CONSENT_LABELS+CLOSE_LABELS+PLAIN_CLOSE_LABELS)
# Chrome's find bar shows the matches as "2/7".
FIND_COUNT = re.compile(r'^\s*(\d+)\s*(?:/|of)\s*(\d+)\s*$')
# Words not worth looking up on a page.
COMMON_WORDS = frozenset('''about after again also back been before being best both
browser cookie cookies could each even every from have help here home into just know
learn like login make many menu more most much need news only other over page pages
policy privacy read right said same search should show sign site some such take terms
than that their them then there these they this those through time today under very
want week were what when where which while will with would year years your'''.split())


def normalize_host(host):
    return (host or '').lower().removeprefix('www.')


def consent_label(name):
    """'Accept All Cookies.' -> 'accept all cookies'."""
    return ' '.join(str(name).casefold().replace('’', "'").split()).strip(' .!')


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


def page_context(control, levels=30):
    """(inside the page, inside a dialog) from a control's ancestors.  The page is Chrome's
    Document; a dialog is what the page marks role="dialog", which Chrome passes on as the
    ARIA role and the localized control type."""
    dialog, node = False, control
    for _ in range(levels):
        try:
            node = node.parent()
            if node is None:
                return False, dialog
            info = node.element_info
            if info.control_type == 'Document':
                return True, dialog
            element = getattr(info, 'element', None)
            for attribute in ('CurrentAriaRole', 'CurrentLocalizedControlType'):
                value = getattr(element, attribute, '')
                dialog = dialog or (isinstance(value, str) and 'dialog' in value.casefold())
        except (Stopped, pg.FailSafeException):
            raise
        except Exception:
            return False, dialog
    return False, dialog


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
        self.loaded_sites = 0          # sites that loaded in this run
        self.revisits = 0              # returns to the first site's tab (at most two a run)
        self.sites_since_revisit = 0
        self.tab_dead = False          # the active tab holds a site that did not load
        self.entry = None              # site_log entry of the visit in progress
        self.consents_here = 0         # consent pop-ups accepted on the page being read
        self.popups_here = 0           # other pop-ups closed on the page being read
        self.notice_failure = None     # why the last privacy warning could not be passed
        self.searches = 0              # Ctrl+F searches made in this run
        self.searched_last = False     # whether the site read before this one got a search
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

    def warning_is_for(self, homepage):
        """Whether the privacy warning on screen belongs to `homepage`.

        Typing a new address on a tab that shows a warning leaves that warning up for a
        moment.  Seen in the VM: archive.org, samsung, bumble and binance were each dropped
        within seconds, because the warning of the site before was taken for theirs.  The
        warning names its host; if the text cannot be read, the address bar decides."""
        hosts = HOST_IN_TEXT.findall(self.page_text().casefold())
        if hosts:
            return any(self.same_site(host, homepage) for host in hosts)
        current = urlsplit(self.url()).hostname
        return not current or self.same_site(current, homepage)

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

    @staticmethod
    def advanced_control(scanned):
        return next((c for c in scanned if c.is_visible() and c.is_enabled()
            and (automation_id(c) == 'details-button' or (
                c.element_info.control_type in ('Button', 'Hyperlink') and any(
                name.casefold().strip().rstrip('.…') in ('advanced', 'show advanced')
                for name in control_names(c))))), None)

    def details_closed(self, scanned, text):
        """Whether the warning still shows "Advanced" with its details closed."""
        labels = [name.casefold().strip().rstrip('.…') for c in scanned for name in control_names(c)]
        if 'hide advanced' in labels or any(s in text.casefold() for s in DETAILS_TEXT):
            return False
        return self.advanced_control(scanned) is not None

    def continue_organiser_notice(self, homepage):
        """Click Advanced, then click visible Continue/Proceed; otherwise skip."""
        g = self.gui
        self.notice_failure = None
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
            advanced = self.advanced_control(scanned)
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
        clicked = time.monotonic()
        clicked_again = False
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
            if (not clicked_again and time.monotonic()-clicked > 2.0
                    and self.details_closed(scanned, text)):
                # Seen in the VM (coinmarketcap): the click on Advanced did nothing while the
                # page was still settling, and a site with a Proceed link was skipped.
                clicked_again = True
                again = self.advanced_control(scanned)
                g.event('advanced_clicked_again', url=homepage)
                g.sleep(random.uniform(0.8, 1.5)*g.pace)
                g.click(again, window=self.window); g.sleep(random.uniform(0.6, 1.1))
                self.invalidate_page()
                end = max(end, time.monotonic()+4.0)
                continue
            g.sleep(min(random.uniform(0.45, 0.75), max(0, end-time.monotonic())))
        if self.details_closed(scanned, text):
            self.notice_failure = 'advanced_not_opened'
            g.event('notice_not_continued', reason='Advanced did not open the details',
                    seen=self.notice_diagnostics(scanned))
            return False
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
        stale = False       # a warning seen so far belonged to the page before
        settled = 0
        while time.monotonic() < end:
            g.sleep(random.uniform(0.6, 1.2))
            self.invalidate_page(new_page=False)
            title = self.window.window_text().casefold()
            title_warning = any(error in title for error in CERTIFICATE_ERRORS)
            text = '' if title_warning else self.page_text().casefold()
            warning = title_warning or any(error in text for error in CERTIFICATE_ERRORS)
            if warning and not self.warning_is_for(homepage):
                if not stale:
                    g.event('previous_warning_ignored', url=homepage)
                stale = True
                settled = 0
                continue    # the old warning is still up: wait for the new site
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
                    self.last_failure = self.notice_failure or 'no_proceed'
                    g.event('website_skipped', url=homepage,
                            reason='Advanced did not open' if self.notice_failure
                            else 'No usable Proceed option')
                    return False
                self.notices_continued += 1
                g.focus(self.window)
                end = time.monotonic()+load_timeout
                warning_grace = time.monotonic()+self.config.get(
                    'privacy_verify_timeout_seconds', 4.0)
                continue
            if any(error in title+' '+text for error in ERRORS):
                # The site's own 404/403 comes back on a reload; only passing trouble
                # (cannot be reached, a dropped connection) is worth one more try.
                definitive = any(error in title+' '+text for error in PAGE_ERRORS)
                self.last_failure = 'page_error' if definitive else 'page_blocked'
                g.event('page_blocked', url=homepage, definitive=definitive)
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
            if stale and not warned:
                # Reloading would bring back the site before, so this is not retried.
                self.last_failure = 'not_reached'
                g.event('website_skipped', url=homepage,
                        reason='The warning of the site before never gave way')
            else:
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
        if not self.warning_is_for(self.current_url):
            g.event('foreign_warning', url=self.current_url)
            return False                     # another host's warning: this visit is over
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
        if warned and self.current_url and self.warning_is_for(self.current_url):
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

    # --- pop-ups and keyword search -----------------------------------------------------

    def labelled_controls(self):
        """(control, labels) for the visible buttons and links of the window that could
        answer a pop-up.  One walk through the page serves both the consent check and the
        close check.  Names are read only for controls on screen (a page such as CNN has
        hundreds of links, and each name costs several calls into Chrome)."""
        found = []
        for kind in ('Button', 'Hyperlink'):
            try:
                result = self.window.descendants(control_type=kind)
            except (Stopped, pg.FailSafeException):
                raise
            except Exception:
                continue
            if isinstance(result, (list, tuple)):
                found.extend(result)
        labelled = []
        for control in found:
            try:
                if not (control.is_visible() and control.is_enabled()):
                    continue
                labels = [consent_label(name) for name in control_names(control)]
            except (Stopped, pg.FailSafeException):
                raise
            except Exception:
                continue                     # went stale while the page changed
            if any(label in POPUP_LABELS for label in labels):
                labelled.append((control, labels))
        return labelled

    def consent_target(self, labelled=None):
        """(control, label) of a visible accept button on a consent or cookie pop-up."""
        generic = None
        for control, labels in (self.labelled_controls() if labelled is None else labelled):
            exact = next((label for label in labels if label in CONSENT_LABELS), None)
            if exact:
                return control, exact
            if generic is None:
                label = next((label for label in labels if label in GENERIC_CONSENT_LABELS), None)
                if label:
                    generic = control, label
        if generic and any(word in self.page_text().casefold() for word in CONSENT_WORDS):
            return generic
        return None

    def close_target(self, labelled=None):
        """(control, label) of the X of a pop-up over the page, or None."""
        plain = None
        for control, labels in (self.labelled_controls() if labelled is None else labelled):
            label = next((label for label in labels if label in CLOSE_LABELS), None)
            loose = next((label for label in labels if label in PLAIN_CLOSE_LABELS), None)
            if not (label or (loose and plain is None)):
                continue
            inside, dialog = page_context(control)
            if not inside:
                continue                     # Chrome's own: a tab, the find bar
            if label:
                return control, label
            if dialog:
                plain = control, loose
        return plain

    def left_page(self, event):
        """After a click on a pop-up: if it was a link that left the homepage, go back."""
        address = urlsplit(self.url() or self.current_url or '')
        if (self.current_url and address.hostname
                and (not self.same_site(address.hostname, self.current_url)
                     or not LANDING_PATH.match(address.path or '/'))):
            self.gui.hotkey('alt', 'left', window=self.window)
            self.invalidate_page()
            self.gui.event(event, url=address.geturl())
            return True
        return False

    def page_entry(self):
        """The site_log entry of the page in front.  On a return to the first site that is
        not the entry of the site read last."""
        return next((entry for entry in reversed(self.site_log)
                     if entry.get('url') == self.current_url), self.entry)

    def locate_consent_text(self):
        """Fallback when Chrome lists no button: find the label through the Text pattern,
        on a line of its own (not the word inside 'By clicking "Agree", you agree...')."""
        doc = self.document()
        if doc is None or not any(word in self.page_text().casefold() for word in CONSENT_WORDS):
            return None
        try:
            for label in ('Accept all', 'Agree', 'I agree', 'Accept', 'Allow all'):
                search = doc.iface_text.DocumentRange.Clone()
                for _ in range(8):
                    found = search.FindText(label, False, True)
                    if found is None:
                        break
                    line = found.Clone()
                    line.ExpandToEnclosingUnit(3)                 # TextUnit_Line
                    rects = found.GetBoundingRectangles()
                    if (consent_label(line.GetText(80)) == consent_label(label)
                            and rects and len(rects) >= 4 and rects[2] > 2 and rects[3] > 2):
                        return ((rects[0]+rects[2]*random.uniform(0.3, 0.7),
                                 rects[1]+rects[3]*random.uniform(0.35, 0.65)),
                                consent_label(label))
                    search.MoveEndpointByRange(0, found, 1)       # Start -> end of this match
        except (Stopped, pg.FailSafeException):
            raise
        except Exception:
            pass
        return None

    def accept_consent(self, labelled=None):
        """Accept a consent or cookie pop-up the way people do: read it, then click its
        accept button.  At most two per page.  Returns the label clicked, or None."""
        if not self.config.get('browser_accept_consent', True) or self.consents_here >= 2:
            return None
        g = self.gui
        target = self.consent_target(labelled)
        point = None if target is not None else self.locate_consent_text()
        if target is None and point is None:
            return None
        label = target[1] if target is not None else point[1]
        g.sleep(heavy_pause(1.6*g.pace, 0.4, 0.8, 4.0))           # read what it says first
        if target is not None:
            g.click(target[0], window=self.window)
            method = 'control'
        else:
            g.click_point(*point[0], window=self.window)
            method = 'text-pattern'
        self.consents_here += 1
        self.invalidate_page()
        if self.left_page('consent_click_navigated'):             # it was a link: gone back
            return None
        closed = self.consent_target() is None
        g.event('consent_accepted', url=self.current_url, label=label, method=method, closed=closed)
        entry = self.page_entry()
        if entry is not None:
            entry['consent'] = label
        return label

    def close_popup(self, labelled=None):
        """Close a pop-up that only wants something (an offer, a newsletter) the way people
        do: look at it, then click its X, and press Esc if it stays.  At most two a page.
        Returns the label clicked, or None."""
        if not self.config.get('browser_close_popups', True) or self.popups_here >= 2:
            return None
        target = self.close_target(labelled)
        if target is None:
            return None
        control, label = target
        g = self.gui
        g.sleep(heavy_pause(1.4*g.pace, 0.4, 0.7, 3.5))           # see what it is first
        g.click(control, window=self.window)
        self.popups_here += 1
        self.invalidate_page()
        if self.left_page('popup_click_navigated'):
            return None
        g.sleep(random.uniform(0.4, 0.8)*g.pace)
        method = 'control'
        if self.close_target() is not None:
            g.focus(self.window)
            g.press('esc')                                         # the other way to close one
            g.sleep(random.uniform(0.4, 0.8)*g.pace)
            method = 'escape'
        closed = self.close_target() is None
        g.event('popup_closed', url=self.current_url, label=label, method=method, closed=closed)
        entry = self.page_entry()
        if entry is not None:
            entry['popup'] = label
        return label

    def clear_popups(self):
        """Answer what covers the page, as people do: a consent or cookie notice is accepted,
        any other pop-up is closed with its X.  Returns the label clicked, or None."""
        if ((not self.config.get('browser_accept_consent', True) or self.consents_here >= 2)
                and (not self.config.get('browser_close_popups', True) or self.popups_here >= 2)):
            return None
        labelled = self.labelled_controls()                        # one walk serves both
        accepted = self.accept_consent(labelled)
        # After a consent click the page has changed, so it is looked at again.
        closed = self.close_popup(None if accepted else labelled)
        return accepted or closed

    def find_matches(self):
        """(current, total) from Chrome's find bar, e.g. '2/7'; None when it is not exposed."""
        for control in self.gui.walk_controls(self.window, max_depth=9, max_nodes=300,
                                              prune_types=('Document',)):
            try:
                if control.element_info.control_type != 'Text':
                    continue
                match = FIND_COUNT.match(control.window_text() or '')
            except (Stopped, pg.FailSafeException):
                raise
            except Exception:
                continue
            if match:
                return int(match.group(1)), int(match.group(2))
        return None

    def thin_page(self):
        """A page with almost no text: only a header shows (archive.org rendered like that
        in the VM).  Nothing to search, little to read."""
        return len(' '.join(self.page_text().split())) < self.config.get('browser_blank_text_chars', 300)

    def search_word(self, term):
        """A word to look up that is really on the page and below its menu, so that the
        page visibly moves to it: the site's keyword if it qualifies, else a distinctive
        word from the page itself.  None when there is no such word."""
        text = ' '.join(self.page_text().split())
        if not text:
            return None
        lowered = text.casefold()
        start = int(len(text)*0.25)          # the menu and the first screen come first
        def later(word):
            first = lowered.find(word.casefold())
            return first >= start and lowered.count(word.casefold()) <= 12
        if term and later(term):
            return term
        seen, candidates = set(), []
        for match in re.finditer(r'\b[A-Za-z][a-z]{3,13}\b', text[start:]):
            word = match.group(0)
            key = word.casefold()
            if key in seen or key in COMMON_WORDS:
                continue
            seen.add(key)
            if later(word):
                candidates.append(word)
        if not candidates:
            return None
        # Names (capitalised words) are what people look for first.
        names = [word for word in candidates if word[0].isupper()]
        return random.choice((names or candidates)[:12])

    def find_bar_button(self, name):
        """A button of Chrome's find bar ("Next", "Previous", "Close"); the page itself is
        not searched, so a button on the website can never be taken for it."""
        for control in self.gui.walk_controls(self.window, max_depth=9, max_nodes=300,
                                              prune_types=('Document',)):
            try:
                if (control.element_info.control_type == 'Button'
                        and control.window_text().casefold().strip() == name
                        and control.is_visible() and control.is_enabled()):
                    return control
            except (Stopped, pg.FailSafeException):
                raise
            except Exception:
                continue
        return None

    def visible_match(self, word):
        """Screen point of a match of `word` that is in view, or None."""
        doc = self.document()
        if doc is None:
            return None
        try:
            area = doc.rectangle()
            search = doc.iface_text.DocumentRange.Clone()
            for _ in range(15):
                found = search.FindText(word, False, True)
                if found is None:
                    break
                rects = found.GetBoundingRectangles()
                if rects and len(rects) >= 4 and rects[2] > 1 and rects[3] > 1:
                    x, y = rects[0]+rects[2]/2, rects[1]+rects[3]/2
                    if area.left < x < area.right and area.top < y < area.bottom:
                        return x, y
                search.MoveEndpointByRange(0, found, 1)       # Start -> end of this match
        except (Stopped, pg.FailSafeException):
            raise
        except Exception:
            pass
        return None

    def look_at_match(self, word):
        """Rest the pointer on the word the page went to, and read around it."""
        point = self.visible_match(word)
        if point is not None:
            self.gui.move(*point)
        self.brief_read(interest=True)
        return point is not None

    def find_on_page(self, word):
        """Look a word up with Chrome's find bar and go to it: Enter goes to the first
        match, the bar's Next arrow (clicked with the mouse) to the following ones; the
        page scrolls to each.  Then Esc closes the bar."""
        g = self.gui
        g.hotkey('ctrl', 'f', window=self.window)
        g.sleep(random.uniform(0.3, 0.8)*g.pace)
        self.type_browser_text(word)
        g.press('enter')
        g.sleep(random.uniform(0.5, 1.0)*g.pace)
        count = self.find_matches()
        total = count[1] if count else None
        jumps, seen = 0, False
        if total != 0:
            self.invalidate_page()
            seen = self.look_at_match(word)
            if total is None or total > 1:
                for _ in range(random.randint(1, 2) if (total or 3) > 2 else 1):
                    button = self.find_bar_button('next')
                    if button is not None:
                        g.click(button, window=self.window)
                    else:
                        g.press('enter')
                    jumps += 1
                    g.sleep(random.uniform(0.3, 0.7)*g.pace)
                    self.invalidate_page()
                    seen = self.look_at_match(word) or seen
        g.event('find_in_page', term=word, matches=total, jumps=jumps, word_in_view=seen)
        g.clear_browser_selection(self.window, self.document())  # Esc closes the bar first
        self.invalidate_page()
        if self.entry is not None:
            self.entry['search'] = {'term': word, 'matches': total}

    def search_keyword(self, term):
        """At most `browser_max_searches` a run, never on two sites in a row, never on a
        page with almost no text, and only for a word that is really on the page.
        A failed search never ends the visit.  Returns True when a search was made."""
        g = self.gui
        word = None if self.thin_page() else self.search_word(term)
        if word is None:
            g.event('find_skipped', reason='no word worth looking up on this page')
            return False
        self.searches += 1
        try:
            self.find_on_page(word)
        except (Stopped, pg.FailSafeException):
            raise
        except Exception as exc:
            g.event('find_failed', term=word, error=str(exc)[:120])
            self.invalidate_page()
        return True

    def inspect_homepage(self, term, revisit=False):
        """Read a homepage the way a person does: engagement varies page to page.

        A pop-up over the page is answered first: a consent notice accepted, an offer
        closed with its X (on a return too: one may have opened after the tab was left).
        Now and then (`browser_find_probability`, at most `browser_max_searches` a run,
        never two sites in a row) a word is looked up with Chrome's find bar part way
        through.  A visit lasts at most `browser_visit_seconds`, so the short browsing time
        is spread over more sites."""
        g = self.gui
        g.focus(self.window)
        started = time.monotonic()
        limit = (float('inf') if revisit else
                 random.uniform(*self.config.get('browser_visit_seconds', [30, 45])))
        if revisit:
            mode, steps, dwell = 'revisit', random.randint(2, 3), 0.8
        else:
            mode, steps, dwell = random.choices(
                (('skim', random.randint(2, 4), 0.6),
                 ('normal', random.randint(5, 8), 1.0),
                 ('deep', random.randint(9, 13), 1.4)), weights=(0.22, 0.58, 0.2))[0]
        g.event('page_engagement', mode=mode, sections=steps)
        if not revisit:
            self.consents_here = self.popups_here = 0
        self.clear_popups()
        interest_step = random.randrange(1, steps) if mode != 'skim' and steps > 2 else -1
        extra_step = (random.randrange(steps) if not revisit and random.random() <
                      self.config.get('browser_extra_action_probability', 0.3) else -1)
        find_step = (random.randrange(1, steps)
                     if not revisit and steps > 1 and not self.searched_last
                     and self.searches < self.config.get('browser_max_searches', 2)
                     and random.random() < self.config.get('browser_find_probability', 0.3)
                     else -1)
        if not revisit:
            self.searched_last = False
        over = False
        for step in range(steps):
            if self.phase is not None and self.phase.expired:
                return  # hard cap: stop reading when the phase time is up
            if time.monotonic()-started >= limit:
                over = True
                g.event('visit_time_limit', seconds=round(limit, 1))
                break
            if self.late_warning() is False:
                g.event('visit_ended', reason='Privacy warning could not be passed')
                return
            low, high = self.config.get('browser_scroll_dwell_seconds', [1.0, 2.6])
            moved = self.scroll(-1, dwell_range=(low*dwell, high*dwell))
            if moved is False and not revisit and self.clear_popups():
                continue        # a pop-up held the page still; read on now that it is gone
            if step == interest_step or random.random() < 0.38:
                self.hover_content(interest=step == interest_step)
            if step == extra_step:
                self.extra_action()
            if step == find_step:
                self.searched_last = self.search_keyword(term)
                if self.searched_last:
                    # Going up to the find bar left the page at the top, which is what
                    # opens an exit offer such as Bitly's.
                    self.clear_popups()
            if moved is False:
                # An exposed scroll percentage says the page is already at its
                # limit.  Do not keep wheeling at the same section.
                break
        # Manual run: reaching the bottom is followed by a quick flick back up.
        if (mode != 'skim' and random.random() < 0.7 and not over
                and not (self.phase and self.phase.expired)):
            self.scroll(1, dwell_range=(0.45, 1.2), fraction_range=(3.5, 6.0), fling=True)
        if not revisit and mode != 'skim' and not over:
            self.safe_anchor()

    def prepare_next_tab(self):
        if not self.visits:
            return
        g = self.gui
        if self.tab_dead:
            # The last address did not load here: the next one goes into the same tab
            # (a recording kept Coursera's warning open in a tab for the whole run).
            g.event('tab_reused', tab=self.active_tab)
            self.invalidate_page()
            return
        if self.tabs < 3:
            self.open_new_tab()
            self.tabs += 1; self.active_tab = self.tabs
        else:
            target = 3 if self.retained_tab != 3 else 2
            self.activate_tab(target)        # active_tab still names the tab being left
            self.active_tab = target
        self.invalidate_page()

    def may_revisit(self, phase):
        """Going back to the first site's tab now and then: at most twice a run
        (`browser_max_revisits`), never right after the first site, never twice in a row,
        and not when the time left is better spent on one more new site."""
        if self.retained_tab is None or self.active_tab == self.retained_tab:
            return False
        if self.revisits >= self.config.get('browser_max_revisits', 2):
            return False
        if self.loaded_sites < 2 or self.sites_since_revisit < 2:
            return False
        if phase.remaining < 2*self.config.get('browser_min_visit_seconds', 30):
            return False
        return random.random() < self.config.get('browser_revisit_probability', 0.5)

    def revisit_retained(self):
        if self.retained_tab is None or self.active_tab == self.retained_tab:
            return
        if self.phase is not None and self.phase.expired:
            return
        g = self.gui
        self.revisits += 1
        self.sites_since_revisit = 0
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
            g.event('tab_revisited', tab=self.retained_tab, reloaded=False, revisits=self.revisits)
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

    # --- which sites first ----------------------------------------------------------------

    def order_sources(self, sources):
        """Keep the shuffled order, but put last the sites the VM recordings showed to be of
        no use (no Proceed link, the site's own error page, a blank page), listed in
        `browser_known_blocked`.  Nothing is remembered between runs in a file: that would
        show pythonw.exe writing files in Sysmon."""
        known = set(self.config.get('browser_known_blocked', ()))
        ordered = sorted(sources, key=lambda source: source['url'] in known)   # stable: keeps the shuffle
        last = [source['url'] for source in ordered if source['url'] in known]
        if last:
            self.gui.event('sites_tried_last', urls=last)
        return ordered

    def run(self, seconds):
        g = self.gui
        phase = self.phase = Phase(seconds)
        self.window = g.launch_shortcut(self.config['chrome_shortcut'], r'.* - Google Chrome$')
        sources = list(self.config['homepages']); random.shuffle(sources)
        sources = self.order_sources(sources)
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
                'revisits': self.revisits, 'verified': self.accessible_visits > 0}

    def linger(self, phase):
        """Too little time left to start another site: keep reading the page in front, or go
        back to the first site if a return is still allowed.  With nothing to read, browsing
        ends early and the spreadsheet gets the time.  Returns True when it ended early."""
        g = self.gui
        g.event('browser_lingering', seconds_left=round(phase.remaining, 1))
        readable = self.content_verified and not self.tab_dead
        if (not readable and self.retained_tab is not None
                and self.active_tab != self.retained_tab
                and self.revisits < self.config.get('browser_max_revisits', 2)):
            self.activate_tab(self.retained_tab)        # the current tab has nothing to read
            self.active_tab = self.retained_tab; self.invalidate_page()
            self.current_url = self.retained_url
            self.revisits += 1
            g.event('tab_revisited', tab=self.retained_tab, reloaded=False,
                    revisits=self.revisits, lingering=True)
            readable = True
        if not readable:                                # never scroll or re-try a blocked page
            g.event('browser_ended_early', reason='nothing left to read',
                    seconds_left=round(phase.remaining, 1))
            return True
        while not phase.expired:
            self.brief_read()
            self.inspect_homepage(self.retained_term or '', revisit=True)
        return False

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
                ended_early = self.linger(phase)
                break
            self.typed.add(source['url'])
            self.in_progress = source['url']
            entry = self.entry = None
            try:
                self.prepare_next_tab()
                loaded = self.navigate(source['url'])
                if (not loaded and self.last_failure in RETRYABLE and not phase.expired):
                    # Transient trouble (blocked load, warning that would not clear): try once more.
                    g.event('navigation_retry', url=source['url'], reason=self.last_failure)
                    g.sleep(random.uniform(2.0, 4.0)*g.pace)
                    loaded = self.navigate(source['url'], reload=True)  # refresh, do not retype
                self.tab_dead = not loaded
                entry = self.entry = {'url': source['url'], 'loaded': bool(loaded),
                                      'reason': None if loaded else self.last_failure,
                                      'notices_continued': self.notices_continued,
                                      'content_verified': bool(loaded and self.content_verified)}
                self.site_log.append(entry)
                self.remember_outcome(source['url'], loaded)
                if loaded:
                    self.accessible_visits += int(self.content_verified)
                    self.loaded_sites += 1
                    self.sites_since_revisit += 1
                    if self.retained_tab is None and self.content_verified:
                        self.retained_tab = self.active_tab
                        self.retained_term = source['term']
                        self.retained_url = source['url']
                    self.inspect_homepage(source['term'])
                    self.scrolled_visits += 1
                    if not phase.expired and self.may_revisit(phase):
                        self.revisit_retained()
                else:
                    g.sleep(random.uniform(0.2, 0.6)*g.pace)
            except (Stopped, pg.FailSafeException):
                raise
            except Exception as exc:
                if entry is None:
                    self.tab_dead = True                     # the address did not get through
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
