"""Regression checks using fake UI controls; no actual browser is operated."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from domain import Phase


def load_browser():
    pg = types.ModuleType('pyautogui')
    pg.press = Mock()
    pg.FailSafeException = type('FailSafeException', (Exception,), {})
    support = types.ModuleType('gui_support')
    support.Stopped = type('Stopped', (Exception,), {})
    support.TimeUp = type('TimeUp', (support.Stopped,), {})
    spec = importlib.util.spec_from_file_location(
        'browser_under_test', Path(__file__).resolve().parents[1]/'browser_action.py')
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'pyautogui': pg, 'gui_support': support}):
        spec.loader.exec_module(module)
    return module


def control(label, kind='Button'):
    c = Mock()
    c.window_text.return_value = label
    c.element_info.control_type = kind
    c.is_visible.return_value = True
    c.is_enabled.return_value = True
    return c


class BrowserContractTests(unittest.TestCase):
    def setUp(self):
        self.module = load_browser()
        self.gui = Mock()
        self.clock = [0.0]
        self.gui.sleep.side_effect = self.advance
        self.gui.walk_controls.return_value = []
        self.gui.pace = 1.0
        self.homepage = 'https://www.apple.com'
        self.browser = self.module.BrowserAction(self.gui, {
            'chrome_shortcut': 'Google Chrome',
            # Random habits are switched off here and tested on their own below.
            'tab_keyboard_probability': 0, 'browser_extra_action_probability': 0,
            'keyboard_scroll_probability': 0,
            'homepages': [{'url': self.homepage, 'term': 'Apple'}]})
        self.browser.window = Mock()
        self.browser.window.window_text.return_value = 'Apple - Google Chrome'
        self.browser.url = Mock(return_value=self.homepage)
        self.doc = control('Apple page', 'Document')
        self.doc.iface_value.CurrentValue = 'https://www.apple.com/'   # page really loaded
        self.browser.document = Mock(return_value=self.doc)
        self.browser.page_text = Mock(return_value='Apple homepage')
        self.browser.page_controls = Mock(return_value=[])
        self.browser.notice_controls = Mock(return_value=[])
        self.timer = patch.object(self.module.time, 'monotonic', side_effect=lambda: self.clock[0])
        self.timer.start(); self.addCleanup(self.timer.stop)

    def advance(self, seconds):
        self.clock[0] += seconds

    def test_host_named_proceed_wording_variations_and_rejections(self):
        for label in ('Proceed to www.apple.com (unsafe)', 'Continue to apple.com',
                      'CONTINUE ANYWAY TO https://www.apple.com/',
                      'Proceed to apple.com (not secure)', 'Continue',
                      'Continue to site', 'Proceed anyway'):
            self.assertTrue(self.module.proceed_label_matches(label, 'www.apple.com'), label)
        for label in ('Proceed to evil.example',
                      'Proceed to apple.com.evil.example', 'Proceed to notapple.com',
                      'Proceed to evil.apple.com', 'Download from apple.com'):
            self.assertFalse(self.module.proceed_label_matches(label, 'apple.com'), label)

    def test_advanced_then_proceed_only_for_approved_homepage(self):
        advanced = control('Advanced')
        proceed = control('Proceed to apple.com (unsafe)', 'Hyperlink')
        self.browser.notice_controls.side_effect = [[advanced], [proceed]]
        self.assertTrue(self.browser.continue_organiser_notice(self.homepage))
        self.assertEqual([c.args[0] for c in self.gui.click.call_args_list], [advanced, proceed])
        self.assertGreaterEqual(self.clock[0], 0.6)

    def test_other_hosts_and_subpages_do_not_get_proceed_click(self):
        for url in ('https://example.com', 'https://www.apple.com/shop',
                    'http://www.apple.com', 'https://www.apple.com/?q=x',
                    'https://www.apple.com:8443', 'https://user@www.apple.com'):
            self.browser.url.return_value = url
            self.assertFalse(self.browser.continue_organiser_notice(self.homepage))
        self.gui.click.assert_not_called()

    def test_address_change_after_expansion_prevents_proceed(self):
        advanced = control('Advanced')
        proceed = control('Proceed to apple.com (unsafe)', 'Hyperlink')
        self.browser.notice_controls.side_effect = [[advanced], [proceed]]
        self.browser.url.side_effect = [self.homepage, 'https://evil.example']
        self.assertFalse(self.browser.continue_organiser_notice(self.homepage))
        self.gui.click.assert_called_once_with(advanced, window=self.browser.window)

    def test_notice_without_advanced_does_not_guess_coordinates(self):
        self.assertFalse(self.browser.continue_organiser_notice(self.homepage))
        self.gui.click.assert_not_called()

    def test_hsts_is_checked_after_advanced_is_clicked(self):
        advanced = control('Advanced')
        self.browser.notice_controls.return_value = [advanced]
        self.browser.page_text.return_value = 'You cannot visit www.apple.com because the website uses HSTS.'
        self.assertFalse(self.browser.continue_organiser_notice(self.homepage))
        self.gui.click.assert_called_once_with(advanced, window=self.browser.window)
        self.assertGreaterEqual(self.clock[0], 0.6)

    def test_hsts_discovered_after_advanced_skips(self):
        advanced = control('Show advanced')
        self.browser.notice_controls.return_value = [advanced]
        self.browser.page_text.side_effect = ['Your connection is not private',
            'You cannot visit this website right now because it uses HSTS.']
        self.assertFalse(self.browser.continue_organiser_notice(self.homepage))
        self.gui.click.assert_called_once_with(advanced, window=self.browser.window)
        self.assertLessEqual(self.clock[0], 4)  # skips promptly, far below the 8 s wait

    def test_missing_or_wrong_proceed_link_times_out_without_popup(self):
        advanced = control('Advanced')
        wrong = control('Proceed to evil.example (unsafe)', 'Hyperlink')
        calls = [0]
        def controls(*args, **kwargs):
            calls[0] += 1
            return [advanced] if calls[0] == 1 else [wrong]
        self.browser.notice_controls.side_effect = controls
        self.assertFalse(self.browser.continue_organiser_notice(self.homepage))
        self.gui.click.assert_called_once_with(advanced, window=self.browser.window)
        self.assertLessEqual(self.clock[0], 9.2)
        self.gui.handoff_browser_notice.assert_not_called()

    def test_chrome_interstitial_automation_ids_work_without_text(self):
        advanced = control('')
        advanced.element_info.automation_id = 'details-button'
        proceed = control('', 'Hyperlink')
        proceed.element_info.automation_id = 'proceed-link'
        self.browser.notice_controls.side_effect = [[advanced], [proceed]]
        self.assertTrue(self.browser.continue_organiser_notice(self.homepage))
        self.assertEqual([c.args[0] for c in self.gui.click.call_args_list],
                         [advanced, proceed])

    def test_same_host_redirect_path_does_not_block_advanced(self):
        advanced = control('Advanced')
        proceed = control('Continue', 'Hyperlink')
        self.browser.url.return_value = 'https://www.apple.com/sg/store?source=home'
        self.browser.notice_controls.side_effect = [[advanced], [proceed]]
        self.assertTrue(self.browser.continue_organiser_notice(self.homepage))
        self.assertEqual(self.gui.click.call_count, 2)

    def test_persistent_warning_is_not_verified_as_homepage(self):
        self.browser.window.window_text.return_value = 'Privacy error - Google Chrome'
        self.browser.page_text.return_value = 'Your connection is not private'
        self.browser.continue_organiser_notice = Mock(return_value=True)
        self.assertFalse(self.browser.navigate(self.homepage))
        self.assertFalse(self.browser.content_verified)
        self.assertEqual(self.browser.continue_organiser_notice.call_count, 3)
        self.assertEqual(self.browser.last_failure, 'warning_persists')
        self.assertFalse(any(c.args[0] == 'homepage_loaded' for c in self.gui.event.call_args_list))

    def test_second_warning_after_a_redirect_is_also_continued(self):
        self.browser.continue_organiser_notice = Mock(return_value=True)
        # A warning shows until it has been continued twice (original host, then redirect).
        self.browser.window.window_text.side_effect = lambda: (
            'Privacy error - Google Chrome'
            if self.browser.continue_organiser_notice.call_count < 2 else 'Apple - Google Chrome')
        self.assertTrue(self.browser.navigate(self.homepage))
        self.assertEqual(self.browser.continue_organiser_notice.call_count, 2)
        self.assertEqual(self.browser.notices_continued, 2)

    def test_locale_landing_pages_are_accepted_but_subpages_are_not(self):
        for url in ('https://www.apple.com/sg/', 'https://www.apple.com/en-sg/',
                    'https://www.apple.com/index.html', 'https://www.apple.com'):
            self.browser.url.return_value = url
            self.assertTrue(self.browser.navigate(self.homepage), url)
        self.browser.url.return_value = 'https://www.apple.com/?ref=nav'   # tracking query
        self.assertTrue(self.browser.navigate(self.homepage))
        for url in ('https://www.apple.com/shop/buy', 'https://www.apple.com/sg/shop',
                    'https://www.apple.com/search?q=x', 'https://evil.example/'):
            self.browser.url.return_value = url
            self.assertFalse(self.browser.navigate(self.homepage), url)
            self.assertEqual(self.browser.last_failure, 'left_homepage')

    def test_no_proceed_is_not_retried(self):
        self.gui.launch_shortcut.return_value = self.browser.window
        self.browser.prepare_next_tab = Mock()
        def navigate(url):
            self.browser.last_failure = 'no_proceed'; self.advance(40); return False
        self.browser.navigate = Mock(side_effect=navigate)
        with patch.object(self.module, 'Phase', side_effect=lambda seconds: Phase(seconds, clock=lambda: self.clock[0])):
            report = self.browser.run(30)
        self.assertEqual(self.browser.navigate.call_count, 1)
        self.assertEqual(report['sites'][0]['reason'], 'no_proceed')

    def test_warning_on_a_retained_tab_is_continued_not_skipped(self):
        self.browser.tabs = 2; self.browser.active_tab = 2
        self.browser.retained_tab = 1; self.browser.retained_term = 'Apple'
        self.browser.retained_url = self.homepage
        self.browser.visits = 1
        self.browser.inspect_homepage = Mock()
        self.browser.window.window_text.side_effect = ['Privacy error - Google Chrome', 'Apple - Google Chrome']
        self.browser.continue_organiser_notice = Mock(return_value=True)
        self.browser.revisit_retained()
        self.browser.continue_organiser_notice.assert_called_once_with(self.homepage)
        self.browser.inspect_homepage.assert_called_once_with('Apple', revisit=True)

    def test_warning_disappears_before_success_is_reported(self):
        self.browser.continue_organiser_notice = Mock(return_value=True)
        self.browser.window.window_text.side_effect = lambda: (
            'Apple - Google Chrome' if self.browser.continue_organiser_notice.called
            else 'Privacy error - Google Chrome')
        self.assertTrue(self.browser.navigate(self.homepage))
        self.assertTrue(self.browser.content_verified)

    def test_missing_proceed_skips_without_manual_attention(self):
        self.browser.window.window_text.return_value = 'Privacy error - Google Chrome'
        self.browser.continue_organiser_notice = Mock(return_value=False)
        self.assertFalse(self.browser.navigate(self.homepage))
        self.gui.handoff_browser_notice.assert_not_called()
        self.gui.hotkey.assert_called_once_with('ctrl', 'l', window=self.browser.window)

    def test_missing_accessible_text_still_allows_unverified_scroll_phase(self):
        self.browser.page_text.return_value = ''
        self.assertTrue(self.browser.navigate(self.homepage))
        self.assertFalse(self.browser.content_verified)
        self.gui.handoff_browser_notice.assert_not_called()

    def test_browser_typo_probability_does_not_affect_other_typing(self):
        with patch.object(self.module.random, 'random', side_effect=[0.19, 0.21]):
            self.browser.type_browser_text(self.homepage, address=True)
            self.browser.type_browser_text('Apple')
        self.assertEqual([c.kwargs['corrected_typo'] for c in self.gui.type_text.call_args_list], [True, False])
        self.assertEqual([c.kwargs['clear_inline_completion'] for c in self.gui.type_text.call_args_list], [True, False])

    def test_retained_tab_is_revisited_without_navigation_or_reload(self):
        self.browser.tabs = 3; self.browser.active_tab = 3
        self.browser.retained_tab = 1; self.browser.retained_term = 'Apple'
        self.browser.visits = 1
        self.browser.inspect_homepage = Mock()
        self.browser.navigate = Mock()
        self.browser.revisit_retained()
        self.assertEqual([c.args for c in self.gui.hotkey.call_args_list], [('ctrl', '1'), ('ctrl', '3')])
        self.browser.navigate.assert_not_called()
        self.browser.inspect_homepage.assert_called_once_with('Apple', revisit=True)
        self.assertEqual(self.browser.active_tab, 3)

    def test_visible_new_tab_button_is_clicked_before_keyboard_fallback(self):
        new_tab = control('New tab')
        self.gui.walk_controls.return_value = [new_tab]
        self.browser.visits = 1
        self.browser.prepare_next_tab()
        self.gui.click.assert_called_once_with(new_tab, window=self.browser.window)
        self.assertFalse(any(c.args == ('ctrl', 't') for c in self.gui.hotkey.call_args_list))

    def test_visible_tab_is_clicked_for_revisit(self):
        first, second = control('Apple', 'TabItem'), control('AWS', 'TabItem')
        first.rectangle.return_value.left = 100
        second.rectangle.return_value.left = 300
        self.gui.walk_controls.return_value = [second, first]
        self.browser.activate_tab(1)
        self.gui.click.assert_called_once_with(first, window=self.browser.window)
        self.assertFalse(any(c.args == ('ctrl', '1') for c in self.gui.hotkey.call_args_list))

    def test_closing_disposable_tab_preserves_retained_tab_index(self):
        self.browser.tabs = 3; self.browser.active_tab = 2
        self.browser.retained_tab = 3; self.browser.retained_term = 'Apple'
        self.browser.visits = 3
        self.browser.inspect_homepage = Mock()
        self.browser.revisit_retained()
        self.gui.hotkey.assert_called_with('ctrl', 'w', window=self.browser.window)
        self.assertEqual((self.browser.tabs, self.browser.retained_tab), (2, 2))

    def test_next_tab_never_overwrites_retained_tab_or_exceeds_three(self):
        self.browser.visits = 1
        for _ in range(6):
            self.browser.prepare_next_tab()
            if self.browser.tabs == 3:
                self.browser.retained_tab = 3
        self.assertEqual(self.browser.tabs, 3)
        self.assertEqual(self.browser.active_tab, 2)

    def test_unchanged_scroll_retries_once_and_unavailable_position_is_honest(self):
        for outcomes, expected in (([False, True], 1), ([False, False], 0), ([None], 0)):
            self.gui.scroll_content.reset_mock()
            self.gui.scroll_content.side_effect = outcomes
            self.browser.verified_scrolls = 0
            self.browser.scroll(-1)
            self.assertEqual(self.gui.scroll_content.call_count, len(outcomes))
            self.assertEqual(self.browser.verified_scrolls, expected)

    def test_browser_scroll_requests_a_viewport_sized_section(self):
        self.gui.scroll_content.return_value = True
        self.browser.scroll(-1)
        kwargs = self.gui.scroll_content.call_args.kwargs
        self.assertEqual(kwargs['fraction_range'], (0.75, 1.1))
        self.assertEqual(kwargs['notch_px'], 100)
        self.browser.scroll(1, fraction_range=(1.2, 2.2))
        self.assertEqual(self.gui.scroll_content.call_args.kwargs['fraction_range'], (1.2, 2.2))

    def test_scroll_does_not_clear_selection_every_time(self):
        self.gui.scroll_content.return_value = True
        self.browser.scroll(-1)
        self.gui.clear_browser_selection.assert_not_called()

    def test_text_pattern_fallback_clicks_proceed_when_uia_lists_no_link(self):
        advanced = control('Advanced')
        self.browser.notice_controls.side_effect = [[advanced]] + [[]]*30
        found = self.doc.iface_text.DocumentRange.FindText.return_value
        found.GetBoundingRectangles.return_value = (100.0, 200.0, 80.0, 20.0)
        found.Clone.return_value.GetText.return_value = 'Proceed to apple.com (unsafe)'
        self.assertTrue(self.browser.continue_organiser_notice(self.homepage))
        self.gui.click_point.assert_called_once()
        x, y = self.gui.click_point.call_args.args
        self.assertTrue(100 <= x <= 180 and 200 <= y <= 220)

    def test_text_pattern_fallback_rejects_other_hosts(self):
        found = self.doc.iface_text.DocumentRange.FindText.return_value
        found.GetBoundingRectangles.return_value = (100.0, 200.0, 80.0, 20.0)
        found.Clone.return_value.GetText.return_value = 'Proceed to evil.example (unsafe)'
        self.assertIsNone(self.browser.locate_proceed_text('www.apple.com'))

    def test_advanced_is_polled_until_chrome_exposes_it(self):
        advanced = control('Advanced')
        proceed = control('Proceed to apple.com (unsafe)', 'Hyperlink')
        self.browser.notice_controls.side_effect = [[], [], [advanced], [proceed]]
        self.assertTrue(self.browser.continue_organiser_notice(self.homepage))
        self.assertEqual([c.args[0] for c in self.gui.click.call_args_list], [advanced, proceed])

    def test_empty_address_bar_copy_does_not_block_proceed(self):
        advanced = control('Advanced')
        proceed = control('Proceed to apple.com (unsafe)', 'Hyperlink')
        self.browser.url.return_value = ''
        self.browser.notice_controls.side_effect = [[advanced], [proceed]]
        self.assertTrue(self.browser.continue_organiser_notice(self.homepage))

    def test_skip_logs_what_chrome_exposed(self):
        advanced = control('Advanced')
        self.browser.notice_controls.side_effect = [[advanced]] + [[control('Back')]]*30
        self.assertFalse(self.browser.continue_organiser_notice(self.homepage))
        last = self.gui.event.call_args_list[-1]
        self.assertEqual(last.args[0], 'notice_not_continued')
        self.assertIn('Back', last.kwargs['seen'])

    def test_homepage_inspection_never_uses_page_search(self):
        self.gui.scroll_content.return_value = True
        self.browser.hover_content = Mock(); self.browser.safe_anchor = Mock()
        for _ in range(25):
            self.browser.inspect_homepage('Apple')
        self.assertFalse(any(c.args[:2] == ('ctrl', 'f') for c in self.gui.hotkey.call_args_list))
        self.assertFalse(any(c.args[0] == 'find_in_page' for c in self.gui.event.call_args_list))
        self.gui.type_text.assert_not_called()

    def test_page_engagement_varies_between_visits(self):
        self.gui.scroll_content.return_value = True
        self.browser.hover_content = Mock(); self.browser.safe_anchor = Mock()
        for _ in range(60):
            self.browser.inspect_homepage('Apple')
        modes = {c.kwargs['mode'] for c in self.gui.event.call_args_list if c.args[0] == 'page_engagement'}
        self.assertGreaterEqual(len(modes), 2)

    def test_return_trip_is_a_fast_fling_not_slow_rolling(self):
        self.gui.scroll_content.return_value = True
        self.browser.scroll(1, fraction_range=(3.5, 6.0), fling=True)
        self.assertTrue(self.gui.scroll_content.call_args.kwargs['fling'])

    def run_sites(self, urls, seconds, navigate):
        self.site_browser(urls)
        self.gui.launch_shortcut.return_value = self.browser.window
        self.browser.prepare_next_tab = Mock(); self.browser.revisit_retained = Mock()
        self.browser.inspect_homepage = Mock()
        self.browser.navigate = Mock(side_effect=navigate)
        with patch.object(self.module, 'Phase', side_effect=lambda s: Phase(s, clock=lambda: self.clock[0])):
            return self.browser.run(seconds)

    def test_each_address_is_typed_once_and_the_phase_ends_when_all_are_visited(self):
        def navigate(url, reload=False):
            self.advance(30); self.browser.content_verified = True
            return True
        urls = ['https://a.example', 'https://b.example', 'https://c.example']
        report = self.run_sites(urls, 420, navigate)
        typed = [c.args[0] for c in self.browser.navigate.call_args_list]
        self.assertEqual(sorted(typed), sorted(urls))          # every site, exactly once
        self.assertTrue(report['ended_early'])
        self.assertAlmostEqual(report['unused_seconds'], 420-90, delta=1)
        self.assertLess(self.clock[0], 420)                    # did not loop to fill 7 minutes
        self.assertEqual([s['url'] for s in report['sites']], typed)
        self.assertTrue(all(s['loaded'] for s in report['sites']))
        self.gui.close_window.assert_called_once()

    def test_deadline_still_ends_the_phase_when_sites_remain(self):
        def navigate(url, reload=False):
            self.advance(200); self.browser.content_verified = True
            return True
        report = self.run_sites(['https://a.example', 'https://b.example', 'https://c.example',
                                 'https://d.example'], 420, navigate)
        self.assertFalse(report['ended_early'])
        self.assertEqual(report['unused_seconds'], 0)
        self.assertLess(self.browser.navigate.call_count, 4)

    def test_failed_sites_are_recorded_with_a_reason_and_never_retyped(self):
        def navigate(url, reload=False):
            self.advance(10)
            ok = url != 'https://b.example'
            self.browser.last_failure = None if ok else 'no_proceed'
            self.browser.content_verified = ok
            return ok
        report = self.run_sites(['https://a.example', 'https://b.example', 'https://c.example'],
                                420, navigate)
        typed = [c.args[0] for c in self.browser.navigate.call_args_list]
        self.assertEqual(typed.count('https://b.example'), 1)
        status = {s['url']: (s['loaded'], s['reason']) for s in report['sites']}
        self.assertEqual(status['https://b.example'], (False, 'no_proceed'))
        self.assertEqual(report['blocked_sites'], {'https://b.example': 'no_proceed'})
        self.assertTrue(report['verified'])

    def test_transient_failure_is_retried_by_reloading_not_by_retyping(self):
        results = iter([False, True])
        def navigate(url, reload=False):
            ok = next(results, True)
            self.browser.last_failure = None if ok else 'page_blocked'
            self.browser.content_verified = ok
            self.advance(20)
            return ok
        report = self.run_sites(['https://a.example'], 420, navigate)
        calls = self.browser.navigate.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].kwargs.get('reload', False), False)
        self.assertTrue(calls[1].kwargs['reload'])
        self.assertTrue(report['sites'][0]['loaded'])

    def test_reload_navigation_presses_f5_and_types_nothing(self):
        self.browser.type_browser_text = Mock()
        self.assertTrue(self.browser.navigate(self.homepage, reload=True))
        self.browser.type_browser_text.assert_not_called()
        self.gui.hotkey.assert_called_once_with('f5', window=self.browser.window)

    # --- waiting for the navigation to really happen

    def test_previous_page_text_is_not_mistaken_for_the_new_site(self):
        """The tab still shows the New Tab page for a moment; a warning then appears."""
        self.browser.continue_organiser_notice = Mock(return_value=True)
        looks = {'n': 0}
        def title():
            looks['n'] += 1
            if self.browser.continue_organiser_notice.called:
                return 'Apple - Google Chrome'
            return 'New Tab - Google Chrome' if looks['n'] <= 4 else 'Privacy error - Google Chrome'
        self.browser.window.window_text.side_effect = title
        def page_url():
            return ('https://www.apple.com/' if self.browser.continue_organiser_notice.called
                    else 'chrome://new-tab-page/')
        type(self.doc.iface_value).CurrentValue = property(lambda _: page_url())
        self.browser.page_text.return_value = 'Search Google or type a URL'
        self.assertTrue(self.browser.navigate(self.homepage))
        self.browser.continue_organiser_notice.assert_called_once()     # the late warning was handled

    def test_page_that_never_arrives_is_a_timeout_not_a_success(self):
        self.doc.iface_value.CurrentValue = 'chrome://new-tab-page/'
        self.browser.page_text.return_value = 'Search Google or type a URL'
        self.assertFalse(self.browser.navigate(self.homepage))
        self.assertEqual(self.browser.last_failure, 'load_timeout')
        self.assertIn('load_timeout', self.module.RETRYABLE)

    def test_without_page_url_a_changed_title_marks_the_new_page(self):
        self.doc.iface_value.CurrentValue = Mock()          # Chrome exposes no document value
        self.doc.legacy_properties.return_value = {}
        titles = iter(['New Tab - Google Chrome', 'New Tab - Google Chrome'])
        self.browser.window.window_text.side_effect = lambda: next(titles, 'Apple - Google Chrome')
        self.assertTrue(self.browser.navigate(self.homepage))

    def test_real_page_url_is_preferred_and_error_pages_fall_back_to_the_address_bar(self):
        real = self.real_url_browser('apple.com')
        real.document = Mock(return_value=self.doc)
        self.doc.iface_value.CurrentValue = 'http://www.apple.com/'
        self.assertEqual(real.url(), 'http://www.apple.com/')         # true scheme, not assumed
        self.doc.iface_value.CurrentValue = 'chrome-error://chromewebdata/'
        self.assertEqual(real.url(), 'https://apple.com')             # address bar instead

    def test_warning_appearing_mid_visit_is_continued(self):
        self.gui.scroll_content.return_value = True
        self.browser.hover_content = Mock(); self.browser.safe_anchor = Mock()
        self.browser.current_url = self.homepage
        self.browser.brief_read = Mock()
        self.browser.continue_organiser_notice = Mock(return_value=True)
        looks = {'n': 0}
        def title():
            looks['n'] += 1
            return 'Privacy error - Google Chrome' if looks['n'] == 2 else 'Apple - Google Chrome'
        self.browser.window.window_text.side_effect = title
        with patch.object(self.module.random, 'choices', return_value=[('normal', 5, 1.0)]):
            self.browser.inspect_homepage('Apple')
        self.browser.continue_organiser_notice.assert_called_once_with(self.homepage)

    def test_visit_ends_if_a_mid_visit_warning_cannot_be_passed(self):
        self.gui.scroll_content.return_value = True
        self.browser.hover_content = Mock(); self.browser.safe_anchor = Mock()
        self.browser.current_url = self.homepage
        self.browser.window.window_text.return_value = 'Privacy error - Google Chrome'
        self.browser.continue_organiser_notice = Mock(return_value=False)
        self.browser.inspect_homepage('Apple')
        self.gui.scroll_content.assert_not_called()

    def test_page_text_never_uses_select_all_and_copy_by_default(self):
        real = self.module.BrowserAction(self.gui, dict(self.browser.config))
        real.window = self.browser.window
        real.document = Mock(return_value=self.doc)
        self.doc.iface_text.DocumentRange.GetText.return_value = ''
        self.assertEqual(real.page_text(), '')
        self.gui.clear_clipboard.assert_not_called()
        self.assertFalse(any(c.args == ('ctrl', 'a') for c in self.gui.hotkey.call_args_list))

    def test_error_after_a_site_loaded_keeps_one_log_entry_marked_interrupted(self):
        def navigate(url, reload=False):
            self.advance(20); self.browser.content_verified = True
            return True
        self.site_browser(['https://a.example'])
        self.gui.launch_shortcut.return_value = self.browser.window
        self.browser.prepare_next_tab = Mock(); self.browser.revisit_retained = Mock()
        self.browser.inspect_homepage = Mock(side_effect=RuntimeError('element went stale'))
        self.browser.navigate = Mock(side_effect=navigate)
        with patch.object(self.module, 'Phase', side_effect=lambda s: Phase(s, clock=lambda: self.clock[0])):
            report = self.browser.run(420)
        self.assertEqual(len(report['sites']), 1)
        self.assertTrue(report['sites'][0]['loaded'])
        self.assertIn('stale', report['sites'][0]['interrupted'])

    def test_chrome_refusing_to_close_does_not_discard_the_browsing_result(self):
        def navigate(url, reload=False):
            self.advance(20); self.browser.content_verified = True
            return True
        self.gui.close_window.side_effect = RuntimeError('Timed out waiting for window closed')
        report = self.run_sites(['https://a.example'], 420, navigate)
        self.assertFalse(report['closed'])
        self.assertTrue(report['verified'])

    def test_next_tab_shortcut_is_judged_from_the_tab_being_left(self):
        self.browser.config['tab_keyboard_probability'] = 1
        self.browser.tabs = 3; self.browser.active_tab = 2; self.browser.retained_tab = 1
        self.browser.visits = 1
        self.browser.prepare_next_tab()
        self.gui.hotkey.assert_called_once_with('ctrl', 'tab', window=self.browser.window)
        self.assertEqual(self.browser.active_tab, 3)

    def test_word_click_that_opens_a_new_tab_closes_it_again(self):
        plain = control('Apple designs products that people use every single day', 'Text')
        plain.parent.return_value = None
        self.browser.page_controls.return_value = [plain]
        self.browser.brief_read = Mock()
        counts = iter([2, 3])
        self.browser.tab_count = Mock(side_effect=lambda: next(counts))
        self.assertFalse(self.browser.select_word())
        self.gui.hotkey.assert_called_once_with('ctrl', 'w', window=self.browser.window)

    def test_stale_element_during_hover_is_skipped_not_raised(self):
        item = control('Mac', 'Text')
        self.browser.page_controls.return_value = [item]
        self.gui.control_point.side_effect = OSError('COM error: element not available')
        self.assertFalse(self.browser.hover_content())

    def test_reading_stops_at_the_phase_deadline(self):
        self.gui.scroll_content.return_value = True
        self.browser.hover_content = Mock(); self.browser.safe_anchor = Mock()
        self.browser.phase = Phase(10, clock=lambda: self.clock[0])
        self.clock[0] = 11
        self.browser.inspect_homepage('Apple')
        self.gui.scroll_content.assert_not_called()

    def test_recovery_backs_off_instead_of_spinning(self):
        self.gui.launch_shortcut.return_value = self.browser.window
        self.browser.prepare_next_tab = Mock(side_effect=RuntimeError('boom'))
        with patch.object(self.module, 'Phase', side_effect=lambda seconds: Phase(seconds, clock=lambda: self.clock[0])):
            self.browser.run(30)
        recoveries = sum(c.args[0] == 'browser_recovery' for c in self.gui.event.call_args_list)
        self.assertLessEqual(recoveries, 30)  # at least ~1 s between attempts

    def test_url_is_typed_without_scheme_like_the_manual_run(self):
        self.browser.type_browser_text = Mock()
        self.browser.navigate('https://www.apple.com')
        self.browser.type_browser_text.assert_called_once_with('www.apple.com', address=True)
        self.browser.type_browser_text.reset_mock()
        self.browser.config['homepages'].append({'url': 'https://coingecko.com', 'term': 'x'})
        self.browser.navigate('https://coingecko.com')
        self.browser.type_browser_text.assert_called_once_with('coingecko.com', address=True)

    def test_only_neighbour_key_mistakes_by_default(self):
        with patch.object(self.module.random, 'random', return_value=0.0):
            self.browser.type_browser_text('www.aws.com', address=True)
        self.assertEqual(self.gui.type_text.call_args.kwargs['typo_kind'], 'wrong')

    # --- address bar without keyboard or clipboard

    def real_url_browser(self, value):
        real = self.module.BrowserAction(self.gui, dict(self.browser.config))
        real.window = self.browser.window
        box = control('Address and search bar', 'Edit')
        box.iface_value.CurrentValue = value
        self.gui.walk_controls.return_value = [control('Back'), box]
        return real

    def test_address_is_read_through_accessibility_without_keys_or_clipboard(self):
        real = self.real_url_browser('https://www.apple.com/')
        self.assertEqual(real.url(), 'https://www.apple.com/')
        self.gui.hotkey.assert_not_called()
        self.gui.clear_clipboard.assert_not_called()
        self.gui.read_clipboard.assert_not_called()

    def test_hidden_scheme_is_restored_for_ordinary_pages(self):
        real = self.real_url_browser('apple.com/sg/')
        self.assertEqual(real.url(), 'https://apple.com/sg/')

    def test_an_unreadable_address_is_never_fetched_with_ctrl_l(self):
        """Ctrl+L, Ctrl+C left the whole address selected (blue) with the suggestion list
        open on every privacy-warning page in the recording."""
        real = self.module.BrowserAction(self.gui, dict(self.browser.config))
        real.window = self.browser.window
        self.gui.walk_controls.return_value = []                      # address bar not exposed
        self.doc.iface_value.CurrentValue = 'chrome-error://chromewebdata/'
        real.document = Mock(return_value=self.doc)
        self.assertEqual(real.url(), '')
        self.gui.hotkey.assert_not_called()
        self.gui.clear_clipboard.assert_not_called()

    def test_clipboard_route_is_used_only_when_config_asks_for_it(self):
        real = self.module.BrowserAction(self.gui, {**self.browser.config,
                                                    'address_bar_method': 'clipboard'})
        real.window = self.browser.window
        self.gui.read_clipboard.return_value = 'https://www.apple.com/ '
        self.assertEqual(real.url(), 'https://www.apple.com/')
        self.gui.hotkey.assert_any_call('ctrl', 'l', window=real.window)

    def test_empty_accessibility_value_forgets_the_stale_control(self):
        real = self.real_url_browser('')
        self.assertEqual(real.url(), '')
        self.assertIsNone(real._omnibox)
        self.gui.hotkey.assert_not_called()

    def test_address_bar_is_found_with_one_query_by_name(self):
        """The bounded walk gave up before reaching it once several tabs were open."""
        real = self.module.BrowserAction(self.gui, dict(self.browser.config))
        real.window = Mock()
        box = control('Address and search bar', 'Edit')
        box.iface_value.CurrentValue = 'https://www.apple.com'
        real.window.descendants.return_value = [box]
        self.doc.iface_value.CurrentValue = 'chrome-error://chromewebdata/'
        real.document = Mock(return_value=self.doc)
        self.assertEqual(real.url(), 'https://www.apple.com')
        self.assertEqual(real.url(), 'https://www.apple.com')
        real.window.descendants.assert_called_once_with(control_type='Edit',
                                                        title='Address and search bar')
        self.gui.walk_controls.assert_not_called()

    def test_a_missing_address_bar_is_not_searched_for_on_every_page(self):
        real = self.module.BrowserAction(self.gui, dict(self.browser.config))
        real.window = Mock()
        real.window.descendants.return_value = []
        self.gui.walk_controls.return_value = []
        for _ in range(8):
            self.assertIsNone(real.omnibox())
        self.assertEqual(real.window.descendants.call_count, 3)

    def test_privacy_warning_is_passed_without_touching_the_address_bar(self):
        real = self.module.BrowserAction(self.gui, dict(self.browser.config))
        real.window = self.browser.window
        self.gui.walk_controls.return_value = []
        self.doc.iface_value.CurrentValue = 'chrome-error://chromewebdata/'
        real.document = Mock(return_value=self.doc)
        real.page_text = Mock(return_value='Your connection is not private')
        real.notice_controls = Mock(side_effect=[[control('Advanced')],
                                                 [control('Proceed to www.apple.com (unsafe)', 'Hyperlink')]])
        self.assertTrue(real.continue_organiser_notice(self.homepage))
        self.assertEqual(self.gui.click.call_count, 2)                # Advanced, then Proceed
        self.gui.hotkey.assert_not_called()                           # no Ctrl+L, no Ctrl+C
        self.gui.press.assert_not_called()

    def test_loaded_page_with_unreadable_address_is_taken_as_the_one_asked_for(self):
        self.browser.url.return_value = ''
        self.assertTrue(self.browser.navigate(self.homepage))
        self.assertEqual(self.browser.current_url, self.homepage)

    # --- redirects of approved homepages

    def test_a_site_may_land_on_its_own_subdomain_or_a_listed_partner_host(self):
        cnn, bitly = 'https://www.cnn.com', 'https://www.bit.ly'
        self.browser.config['homepages'] = [{'url': cnn, 'term': 'news'},
                                            {'url': bitly, 'term': 'links', 'also': ['bitly.com']}]
        same = self.browser.same_site
        self.assertTrue(same('www.cnn.com', cnn)); self.assertTrue(same('edition.cnn.com', cnn))
        self.assertTrue(same('bitly.com', bitly)); self.assertTrue(same('www.bit.ly', bitly))
        self.assertTrue(same('app.bitly.com', bitly))
        for host in ('cnn.com.evil.example', 'notcnn.com', 'bitly.com', 'evil.example', '', None):
            self.assertFalse(same(host, cnn), host)
        self.assertFalse(same('cnn.com', bitly))

    def test_redirect_to_a_subdomain_is_a_successful_visit(self):
        cnn = 'https://www.cnn.com'
        self.browser.config['homepages'] = [{'url': cnn, 'term': 'news'}]
        self.doc.iface_value.CurrentValue = 'https://edition.cnn.com/'
        self.browser.url.return_value = 'https://edition.cnn.com/'
        self.assertTrue(self.browser.navigate(cnn))
        self.browser.url.return_value = 'https://edition.cnn.com/world/article'   # not a homepage
        self.assertFalse(self.browser.navigate(cnn))
        self.assertEqual(self.browser.last_failure, 'left_homepage')

    def test_locale_index_pages_are_landing_pages(self):
        booking = 'https://www.booking.com'
        self.browser.config['homepages'] = [{'url': booking, 'term': 'hotels'}]
        self.doc.iface_value.CurrentValue = 'https://www.booking.com/index.en-gb.html'
        for url in ('https://www.booking.com/index.en-gb.html?label=x', 'https://www.booking.com/index.html'):
            self.browser.url.return_value = url
            self.assertTrue(self.browser.navigate(booking), url)
        self.browser.url.return_value = 'https://www.booking.com/hotel/sg/x.en-gb.html'
        self.assertFalse(self.browser.navigate(booking))

    def test_every_configured_homepage_is_https_and_unique(self):
        import json
        config = json.loads((Path(__file__).resolve().parents[1]/'config.json').read_text())
        urls = [site['url'] for site in config['homepages']]
        self.assertEqual(len(urls), len(set(urls)))
        self.assertEqual(len(urls), 27)
        self.assertTrue(all(url.startswith('https://') and site.get('term')
                            for url, site in zip(urls, config['homepages'])))

    # --- the seven-minute cap is firm

    def test_time_limit_is_set_for_the_phase_and_lifted_before_closing(self):
        seen = {}
        self.gui.launch_shortcut.return_value = self.browser.window
        self.gui.time_limit = None
        def browse(phase, sources):
            seen.update(limit=self.gui.time_limit, deadline=phase.deadline)
            return True
        self.browser.browse = Mock(side_effect=browse)
        self.gui.close_window.side_effect = lambda w: seen.update(at_close=self.gui.time_limit)
        report = self.browser.run(420)
        self.assertEqual(seen['limit'], seen['deadline'])
        self.assertIsNone(seen['at_close'])
        self.assertTrue(report['ended_early'])

    def test_time_up_mid_visit_ends_the_phase_and_still_closes_chrome(self):
        self.gui.launch_shortcut.return_value = self.browser.window
        self.site_browser(['https://a.example', 'https://b.example'])
        visits = []
        def navigate(url, reload=False):
            visits.append(url)
            if len(visits) == 2:
                raise self.module.TimeUp('Time limit reached')     # cap hits while b is loading
            self.browser.content_verified = True
            return True
        self.browser.navigate = Mock(side_effect=navigate)
        self.browser.inspect_homepage = Mock(); self.browser.revisit_retained = Mock()
        self.browser.prepare_next_tab = Mock()
        with patch.object(self.module.random, 'shuffle'):
            report = self.browser.run(420)
        self.gui.close_window.assert_called_once()
        self.assertTrue(report['closed'])
        self.assertFalse(report['ended_early'])
        status = {site['url']: site['reason'] for site in report['sites']}
        self.assertEqual(status, {'https://a.example': None, 'https://b.example': 'time ran out'})
        self.assertTrue(any(c.args[0] == 'browser_time_up' for c in self.gui.event.call_args_list))

    def test_with_nothing_readable_browsing_ends_early_and_gives_the_time_to_calc(self):
        """A blocked last page is never scrolled or retried: lingering returns True at once
        (the old code kept waiting out the phase) and the unused time is carried to Calc."""
        phase = Phase(20, clock=lambda: self.clock[0])
        self.browser.content_verified = False            # the last address did not load
        self.browser.retained_tab = None
        self.browser.inspect_homepage = Mock(); self.browser.continue_organiser_notice = Mock()
        self.browser.brief_read = Mock(side_effect=lambda *a, **k: self.advance(4))
        self.assertTrue(self.browser.linger(phase))
        self.browser.inspect_homepage.assert_not_called()
        self.browser.brief_read.assert_not_called()
        self.browser.continue_organiser_notice.assert_not_called()
        self.assertEqual(self.clock[0], 0)
        self.assertTrue(any(c.args[0] == 'browser_ended_early' for c in self.gui.event.call_args_list))

    def test_a_readable_page_is_read_until_the_phase_ends(self):
        phase = Phase(20, clock=lambda: self.clock[0])
        self.browser.content_verified = True; self.browser.tab_dead = False
        self.browser.retained_tab = None
        self.browser.inspect_homepage = Mock()
        self.browser.brief_read = Mock(side_effect=lambda *a, **k: self.advance(4))
        self.assertFalse(self.browser.linger(phase))
        self.assertGreaterEqual(self.clock[0], 20)
        self.browser.inspect_homepage.assert_called()

    def test_a_dead_page_with_a_retained_tab_goes_back_to_it_while_returns_remain(self):
        phase = Phase(20, clock=lambda: self.clock[0])
        self.browser.content_verified = False; self.browser.tab_dead = True
        self.browser.retained_tab = 'first'; self.browser.active_tab = 'second'
        self.browser.retained_url = 'https://a.example'; self.browser.revisits = 0
        self.browser.activate_tab = Mock(); self.browser.invalidate_page = Mock()
        self.browser.inspect_homepage = Mock()
        self.browser.brief_read = Mock(side_effect=lambda *a, **k: self.advance(10))
        self.assertFalse(self.browser.linger(phase))
        self.browser.activate_tab.assert_called_once_with('first')
        self.assertEqual(self.browser.revisits, 1)
        self.browser.activate_tab.reset_mock()
        self.browser.active_tab = 'second'; self.browser.revisits = 2    # browser_max_revisits
        self.clock[0] = 0
        self.assertTrue(self.browser.linger(Phase(20, clock=lambda: self.clock[0])))
        self.browser.activate_tab.assert_not_called()

    def test_no_new_address_is_typed_in_the_last_thirty_seconds(self):
        self.gui.launch_shortcut.return_value = self.browser.window
        self.site_browser(['https://a.example', 'https://b.example', 'https://c.example'])
        def navigate(url, reload=False):
            self.advance(395)                      # the first visit uses up most of the phase
            self.browser.content_verified = True
            return True
        self.browser.navigate = Mock(side_effect=navigate)
        self.browser.inspect_homepage = Mock(side_effect=lambda *a, **k: self.advance(10))
        self.browser.revisit_retained = Mock(); self.browser.prepare_next_tab = Mock()
        self.browser.brief_read = Mock(side_effect=lambda *a, **k: self.advance(3))
        with patch.object(self.module.random, 'shuffle'), \
             patch.object(self.module, 'Phase', side_effect=lambda s: Phase(s, clock=lambda: self.clock[0])):
            report = self.browser.run(420)
        self.assertEqual(self.browser.navigate.call_count, 1)      # nothing new was started
        self.assertEqual(len(self.browser.typed), 1)
        self.assertTrue(any(c.args[0] == 'browser_lingering' for c in self.gui.event.call_args_list))
        revisits = [c for c in self.browser.inspect_homepage.call_args_list if c.kwargs.get('revisit')]
        self.assertTrue(revisits)                                  # kept reading instead

    # --- blocked sites are not retyped

    def site_browser(self, urls):
        self.browser.config['homepages'] = [{'url': u, 'term': 'x'} for u in urls]
        return self.browser.config['homepages']

    # --- on-page habits

    def test_tabs_are_sometimes_switched_with_the_keyboard(self):
        self.browser.config['tab_keyboard_probability'] = 1
        self.browser.tabs = 3; self.browser.active_tab = 1
        self.browser.activate_tab(2)
        self.gui.hotkey.assert_called_once_with('ctrl', 'tab', window=self.browser.window)
        self.gui.hotkey.reset_mock()
        self.browser.activate_tab(3)
        self.gui.hotkey.assert_called_once_with('ctrl', '3', window=self.browser.window)
        self.gui.click.assert_not_called()

    def test_keyboard_scroll_focuses_the_page_then_presses_one_key(self):
        self.browser.config['keyboard_scroll_probability'] = 1
        self.gui.scroll_position.side_effect = [10.0, 22.0]
        self.assertTrue(self.browser.scroll(-1))
        self.doc.set_focus.assert_called_once()
        self.assertIn(self.gui.press.call_args.args[0], ('pagedown', 'space'))
        self.gui.scroll_content.assert_not_called()
        self.assertEqual(self.browser.verified_scrolls, 1)

    def test_upward_flick_never_uses_the_keyboard(self):
        self.browser.config['keyboard_scroll_probability'] = 1
        self.gui.scroll_content.return_value = True
        self.browser.scroll(1, fraction_range=(3.5, 6.0), fling=True)
        self.gui.press.assert_not_called()

    def test_zoom_is_always_reset_even_if_reading_is_interrupted(self):
        self.browser.brief_read = Mock(side_effect=RuntimeError('focus lost'))
        with self.assertRaises(RuntimeError):
            self.browser.zoom_briefly()
        self.assertEqual([c.args for c in self.gui.hotkey.call_args_list],
                         [('ctrl', '='), ('ctrl', '0')])

    def test_reload_continues_through_a_returning_warning(self):
        self.browser.current_url = self.homepage
        self.browser.brief_read = Mock()
        self.browser.window.window_text.return_value = 'Privacy error - Google Chrome'
        self.browser.continue_organiser_notice = Mock(return_value=True)
        self.browser.reload_page()
        self.gui.hotkey.assert_called_once_with('f5', window=self.browser.window)
        self.browser.continue_organiser_notice.assert_called_once_with(self.homepage)

    def test_word_selection_avoids_text_inside_links(self):
        linked = control('Shop the latest iPhone models and accessories', 'Text')
        linked.parent.return_value.element_info.control_type = 'Hyperlink'
        plain = control('Apple designs products that people use every single day', 'Text')
        plain.parent.return_value.element_info.control_type = 'Group'
        plain.parent.return_value.parent.return_value = None
        self.browser.page_controls.return_value = [linked, plain]
        self.browser.brief_read = Mock()
        self.assertTrue(self.browser.select_word())
        self.gui.click.assert_called_once_with(plain, double=True, window=self.browser.window)
        self.gui.clear_browser_selection.assert_called_once()

    def test_word_selection_goes_back_if_the_page_changed(self):
        plain = control('Apple designs products that people use every single day', 'Text')
        plain.parent.return_value = None
        self.browser.page_controls.return_value = [plain]
        self.browser.brief_read = Mock()
        self.browser.window.window_text.side_effect = ['Apple - Google Chrome', 'Store - Google Chrome']
        self.assertFalse(self.browser.select_word())
        self.gui.hotkey.assert_called_once_with('alt', 'left', window=self.browser.window)

    def test_a_failing_habit_never_ends_the_visit(self):
        self.browser.config['browser_extra_action_probability'] = 1
        self.gui.scroll_content.return_value = True
        self.browser.hover_content = Mock(); self.browser.safe_anchor = Mock()
        for name in ('zoom_briefly', 'reload_page', 'select_word'):
            failing = Mock(side_effect=RuntimeError('boom')); failing.__name__ = name
            setattr(self.browser, name, failing)
        self.browser.inspect_homepage('Apple')
        self.assertTrue(any(c.args[0] == 'extra_action_failed' for c in self.gui.event.call_args_list))

    def test_browser_read_pause_uses_manual_video_cadence(self):
        with patch.object(self.module, 'heavy_pause', return_value=1.7) as pause:
            self.browser.brief_read()
        self.gui.sleep.assert_called_once_with(1.7)
        median = pause.call_args.args[0]
        self.assertAlmostEqual(median, (1.0*2.8)**0.5, places=2)  # centre of the measured band

    def test_interest_pause_uses_longer_manual_video_band(self):
        with patch.object(self.module, 'heavy_pause', return_value=6.0) as pause:
            self.browser.brief_read(interest=True)
        self.gui.sleep.assert_called_once_with(6.0)
        self.assertAlmostEqual(pause.call_args.args[0], (4.0*8.0)**0.5, places=2)

    def test_reading_pauses_are_skewed_with_cv_above_the_workshop_guide(self):
        import random as r, statistics as st
        r.seed(11)
        draws = []
        for _ in range(600):
            self.gui.sleep.reset_mock()
            self.browser.brief_read()
            draws.append(self.gui.sleep.call_args.args[0])
        self.assertGreater(st.pstdev(draws)/st.mean(draws), 0.4)
        self.assertGreater(max(draws), 2*st.median(draws))     # occasional long look
        self.assertLess(max(draws), 2.8*3.5*1.01)              # but capped

    def test_page_controls_are_invalidated_and_text_read_is_cached(self):
        # Exercise the real methods instead of the navigation mocks above.
        real = self.module.BrowserAction(self.gui, self.browser.config)
        real.window = self.browser.window
        self.doc.iface_text.DocumentRange.GetText.return_value = 'Apple homepage'
        self.gui.walk_controls.return_value = [self.doc]
        self.assertEqual(real.page_text(), 'Apple homepage')
        self.assertEqual(real.page_text(), 'Apple homepage')
        self.assertEqual(self.doc.iface_text.DocumentRange.GetText.call_count, 1)
        self.gui.walk_controls.assert_called_once()
        real.invalidate_page(); real.page_text()
        self.assertEqual(self.doc.iface_text.DocumentRange.GetText.call_count, 2)
        self.assertEqual(self.gui.walk_controls.call_count, 2)

    def test_stop_is_propagated_from_warning_discovery(self):
        self.browser.notice_controls.side_effect = self.module.Stopped('F8')
        with self.assertRaises(self.module.Stopped):
            self.browser.continue_organiser_notice(self.homepage)
        self.gui.click.assert_not_called()


if __name__ == '__main__':
    unittest.main()
