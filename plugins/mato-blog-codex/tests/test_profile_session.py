from __future__ import annotations

import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from _support import SCRIPTS_DIR

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import profile_host
import profile_session


TARGET = "https://blog.naver.com/owner1?Redirect=Write&"


class Page:
    def __init__(self, events, *, authenticated=True, url="about:blank"):
        self.events = events
        self.authenticated = authenticated
        self.url = url
        self.editor = False
        self.closed = False

    def is_closed(self):
        return self.closed

    def goto(self, url, **kwargs):
        self.events.append(("goto", url))
        self.url = url
        self.editor = url == TARGET and self.authenticated

    def wait_for_timeout(self, duration):
        pass


class ProfileSessionTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.page = Page(self.events)
        self.context = SimpleNamespace(pages=[self.page])
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(profile_session, "is_naver_login_required", side_effect=lambda p, c: not p.authenticated))
        self.stack.enter_context(patch.object(profile_session, "is_access_restricted", return_value=False))
        self.challenge = self.stack.enter_context(patch.object(profile_session, "has_security_challenge", return_value=False))
        self.stack.enter_context(patch.object(profile_session, "is_editor_ready", side_effect=lambda p, target=None: p.editor))
        self.keep = self.stack.enter_context(patch.object(profile_session, "ensure_keep_login_checked"))
        self.persist = self.stack.enter_context(patch.object(profile_session, "persistent_login_ready", side_effect=self.save))

    def save(self, context, page):
        self.events.append(("persist", page.url))
        return page.authenticated

    def session(self):
        return profile_session.ProfileSession(self.context, self.page, TARGET)

    def test_restore_then_save_then_editor_and_never_reload_while_writing(self):
        session = self.session()
        self.assertEqual(session.poll(), "ready")
        self.assertEqual(self.events, [("goto", profile_session.NAVER_HOME_URL), ("persist", profile_session.NAVER_HOME_URL), ("goto", TARGET)])
        self.assertEqual(session.poll(), "ready")
        self.assertEqual(sum(event[0] == "goto" for event in self.events), 2)

    def test_manual_login_is_saved_before_return_to_registered_editor(self):
        self.page.authenticated = False
        session = self.session()
        self.assertEqual(session.poll(), "needs_login")
        self.assertEqual(self.page.url, profile_session.NAVER_LOGIN_URL)
        self.keep.assert_called_once_with(self.page)
        self.persist.assert_not_called()
        self.page.authenticated = True
        self.page.url = profile_session.NAVER_HOME_URL
        self.assertEqual(session.poll(), "ready")
        self.assertEqual(self.events[-2:], [("persist", profile_session.NAVER_HOME_URL), ("goto", TARGET)])

    def test_last_unrelated_tab_does_not_mask_profile_session(self):
        unrelated = Page(self.events, authenticated=False, url="https://example.test")
        self.context.pages.append(unrelated)
        self.assertEqual(self.session().poll(), "ready")
        self.persist.assert_called_once_with(self.context, self.page)
        self.assertEqual(unrelated.url, "https://example.test")

    def test_login_finishing_in_new_naver_tab_is_preserved(self):
        self.page.authenticated = False
        session = self.session()
        self.assertEqual(session.poll(), "needs_login")
        completed = Page(self.events, url=profile_session.NAVER_HOME_URL)
        self.context.pages.append(completed)
        self.assertEqual(session.poll(), "ready")
        self.assertIs(session.page, completed)

    def test_editor_navigation_failure_does_not_lose_save_or_force_new_login(self):
        session = self.session()
        session.start()
        with patch.object(self.page, "goto", side_effect=TimeoutError):
            with self.assertRaises(TimeoutError):
                session.poll()
        self.persist.assert_called_once()
        self.assertFalse(session.target_opened)
        self.assertEqual(session.poll(), "ready")

    def test_login_lost_while_redirect_settles_does_not_open_editor(self):
        session = self.session()
        session.start()
        def wait(duration):
            if duration == 2_000:
                self.page.authenticated = False
        self.page.wait_for_timeout = wait
        self.assertEqual(session.poll(), "needs_login")
        self.assertNotIn(("goto", TARGET), self.events)
        self.assertFalse(session.target_opened)

    def test_editor_redirect_gets_app_settle_time_before_ready(self):
        waits = []
        self.page.wait_for_timeout = waits.append
        self.assertEqual(self.session().poll(), "ready")
        self.assertEqual(waits, [6_500, 2_000, 6_500])

    def test_failed_cookie_persistence_cannot_be_ready(self):
        self.persist.side_effect = None
        self.persist.return_value = False
        self.assertEqual(self.session().poll(), "needs_login")
        self.assertNotIn(("goto", TARGET), self.events)

    def test_new_authenticated_tab_challenge_stops_before_save_or_navigation(self):
        self.page.authenticated = False
        session = self.session()
        self.assertEqual(session.poll(), "needs_login")
        completed = Page(self.events, url="https://blog.naver.com/captcha")
        self.context.pages.append(completed)
        self.challenge.side_effect = lambda page: page is completed
        self.assertEqual(session.poll(), "error")
        self.persist.assert_not_called()
        self.assertEqual(completed.url, "https://blog.naver.com/captcha")

    def test_no_editor_is_logged_in_not_ready_or_logged_out(self):
        session = self.session()
        session.poll()
        self.page.editor = False
        self.page.url = "https://blog.naver.com/owner1/12345"
        self.assertEqual(session.poll(), "logged_in")
        self.assertEqual(sum(event[0] == "goto" for event in self.events), 2)

    def test_login_only_profile_does_not_claim_editor_ready(self):
        session = profile_session.ProfileSession(self.context, self.page, profile_session.NAVER_LOGIN_URL)
        self.assertEqual(session.poll(), "logged_in")

    def test_restriction_stops_before_cookie_save_and_editor_navigation(self):
        session = self.session()
        with patch.object(profile_session, "is_access_restricted", return_value=True):
            self.assertEqual(session.poll(), "error")
        self.persist.assert_not_called()
        self.assertNotIn(("goto", TARGET), self.events)

    def test_final_save_does_not_navigate_or_touch_unrelated_tab(self):
        self.page.url = profile_session.NAVER_HOME_URL
        self.context.pages.insert(0, Page(self.events, url="https://example.test"))
        self.session().preserve_before_close()
        self.assertEqual(self.events, [("persist", profile_session.NAVER_HOME_URL)])

    def test_security_challenge_wins_even_with_editor_underneath(self):
        session = self.session()
        self.assertEqual(session.poll(), "ready")
        self.challenge.return_value = True
        self.assertEqual(session.poll(), "error")


class EditorReadinessTests(unittest.TestCase):
    def page(self, url):
        page = MagicMock(url=url, frames=[])
        page.locator.return_value.count.return_value = 1
        page.locator.return_value.first.is_visible.return_value = True
        page.frame_locator.return_value.locator.return_value.count.return_value = 1
        page.frame_locator.return_value.locator.return_value.first.is_visible.return_value = True
        return page

    def test_home_or_foreign_page_with_contenteditable_is_not_editor(self):
        for url in (profile_session.NAVER_HOME_URL, "https://example.test", "https://blog.naver.com/owner1/12345", "https://blog.naver.com/owner1"):
            self.assertFalse(profile_session.is_editor_ready(self.page(url), TARGET))

    def test_wrong_blog_or_redirected_iframe_is_not_ready(self):
        self.assertFalse(profile_session.is_editor_ready(self.page("https://blog.naver.com/other?Redirect=Write"), TARGET))
        page = self.page(TARGET)
        page.frames = [SimpleNamespace(url="https://blog.naver.com/PostWriteForm.naver?blogId=other")]
        self.assertFalse(profile_session.is_editor_ready(page, TARGET))

    def test_visible_editor_for_registered_blog_is_ready(self):
        self.assertTrue(profile_session.is_editor_ready(self.page(TARGET), TARGET))

    def test_hidden_editor_is_not_ready(self):
        page = self.page(TARGET)
        page.locator.return_value.first.is_visible.return_value = False
        page.frame_locator.return_value.locator.return_value.first.is_visible.return_value = False
        self.assertFalse(profile_session.is_editor_ready(page, TARGET))

    def test_body_level_input_proxy_and_editor_canvas_are_ready(self):
        page = self.page(TARGET)
        visible = {".se-container .se-content", ".se-documentTitle .se-title-text", "[contenteditable='true']"}

        def locator(selector):
            node = MagicMock()
            node.count.return_value = int(selector in visible)
            node.first.is_visible.return_value = selector in visible
            return node

        page.locator.side_effect = locator
        self.assertTrue(profile_session.is_editor_ready(page, TARGET))
        for missing in tuple(visible):
            with self.subTest(missing=missing):
                visible.remove(missing)
                self.assertFalse(profile_session.is_editor_ready(page, TARGET))
                visible.add(missing)


class ProfileHostLifecycleTests(unittest.TestCase):
    def test_transient_poll_error_keeps_owner_alive_and_final_save_precedes_close(self):
        events = []
        page = Page(events)
        context = MagicMock(pages=[page])
        context.new_page.return_value = page
        context.close.side_effect = lambda: events.append("close")
        playwright = MagicMock()
        playwright.chromium.launch_persistent_context.return_value = context
        manager = MagicMock()
        manager.__enter__.return_value = playwright
        session = MagicMock()
        session.poll.side_effect = [TimeoutError("temporary navigation failure"), "ready"]
        session.preserve_before_close.side_effect = lambda: events.append("save")
        with patch.dict(sys.modules, {"playwright.sync_api": SimpleNamespace(sync_playwright=lambda: manager)}), patch.object(profile_host, "ProfileSession", return_value=session), patch.object(profile_host, "write_profile_state") as state, patch.object(profile_host, "clear_profile_state") as clear:
            result = profile_host._run_context(Path("synthetic-profile"), TARGET, [], stop_requested=MagicMock(side_effect=[False, False, True]))
        self.assertEqual(result, 0)
        self.assertEqual([call.kwargs["status"] for call in state.call_args_list], ["starting", "error", "ready"])
        self.assertEqual(events, ["save", "close"])
        clear.assert_called_once()


if __name__ == "__main__":
    unittest.main()
