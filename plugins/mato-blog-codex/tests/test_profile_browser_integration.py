"""Opt-in real Chrome tests using disposable profiles and synthetic cookies.

Run with MATO_BROWSER_TESTS=1 and a Python environment containing Playwright.
The normal dependency-free suite skips these tests. No existing profile is
opened, and every page request is either fulfilled locally or blocked.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from _support import SCRIPTS_DIR

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import naver_session
import profile_session


FIXTURE_URL = "http://127.0.0.1:8765/mato-profile-fixture"
FIXTURE_TITLE = "Mato disposable profile fixture"
FIXTURE_HTML = f"<!doctype html><title>{FIXTURE_TITLE}</title><p>Local fixture</p>"


def _isolate_page_requests(context):  # type: ignore[no-untyped-def]
    def serve_fixture(route):  # type: ignore[no-untyped-def]
        if route.request.url == FIXTURE_URL:
            route.fulfill(status=200, content_type="text/html", body=FIXTURE_HTML)
        else:
            route.abort()

    context.route("**/*", serve_fixture)


def _synthetic_auth_cookies() -> list[dict[str, object]]:
    return [
        {
            "name": name,
            "value": f"dummy-integration-cookie-{index}",
            "domain": ".naver.com",
            "path": "/",
            "httpOnly": True,
            "secure": True,
            "sameSite": "Lax",
            "expires": time.time() + 7 * 86_400,
        }
        for index, name in enumerate(naver_session.AUTH_COOKIE_NAMES, start=1)
    ]


@unittest.skipUnless(os.environ.get("MATO_BROWSER_TESTS") == "1", "opt-in Chrome test")
class ProfileBrowserIntegrationTests(unittest.TestCase):
    def test_current_keep_login_markup_checks_actual_control_only(self) -> None:
        from playwright.sync_api import sync_playwright
        with TemporaryDirectory(prefix="mato-keep-login-test-") as temporary:
            with sync_playwright() as pw:
                context = pw.chromium.launch_persistent_context(temporary, channel="chrome", headless=True)
                try:
                    page = context.pages[0]
                    page.set_content("""<div><p>로그인 상태 유지 안내</p>
                      <input id="loginStay" name="nvlong" type="checkbox" role="checkbox"
                        aria-checked="false" style="position:absolute;opacity:0"
                        onchange="this.setAttribute('aria-checked',String(this.checked))">
                      <label for="loginStay">로그인 상태 유지</label>
                      <input id="switchIP" type="checkbox" checked><label for="switchIP">IP 보안</label>
                    </div>""")
                    for _ in range(2):
                        self.assertTrue(naver_session.ensure_keep_login_checked(page))
                        self.assertTrue(page.locator('#loginStay').is_checked())
                        self.assertEqual(page.locator('#loginStay').get_attribute('aria-checked'), 'true')
                        self.assertTrue(page.locator('#switchIP').is_checked())
                    page.set_content('<div>로그인 상태 유지</div>')
                    self.assertFalse(naver_session.ensure_keep_login_checked(page))
                finally:
                    context.close()

    def test_session_controller_restores_then_checks_fixture_editor_after_restart(self) -> None:
        from playwright.sync_api import sync_playwright

        target = "https://blog.naver.com/synthetic_owner?Redirect=Write&"
        # Mirror the observed editor: input proxy is outside the canvas,
        # rather than a contenteditable descendant of the displayed title.
        editor = """<!doctype html>
        <div class='se-container'><div class='se-content'>
          <div class='se-documentTitle'><div class='se-title-text'>Fixture title</div></div>
        </div></div><div contenteditable='true'>Input proxy</div>"""
        visits = []

        def serve_fixture(route):
            url = route.request.url
            visits.append(url)
            if url in {profile_session.NAVER_HOME_URL, profile_session.NAVER_HOME_URL + "/", target}:
                route.fulfill(status=200, content_type="text/html", body=editor if url == target else FIXTURE_HTML)
            else:
                route.abort()

        with TemporaryDirectory(prefix="mato-profile-controller-test-") as temporary:
            profile = Path(temporary) / "naver_1"
            with sync_playwright() as playwright:
                for attempt in range(2):
                    with self.subTest(restart=attempt):
                        context = playwright.chromium.launch_persistent_context(str(profile), channel="chrome", headless=True, timeout=45_000)
                        try:
                            context.route("**/*", serve_fixture)
                            if attempt == 0:
                                context.add_cookies(_synthetic_auth_cookies())
                            else:
                                self.assertTrue(naver_session.has_naver_login(context, require_persistent=True))
                            session = profile_session.ProfileSession(context, context.pages[0], target)
                            self.assertEqual(session.poll(), "ready")
                            self.assertEqual(session.page.url, target)
                            navigation_count = len(visits)
                            self.assertEqual(session.poll(), "ready")
                            self.assertEqual(len(visits), navigation_count)
                            self.assertTrue(naver_session.has_naver_login(context, require_persistent=True))
                            session.preserve_before_close()
                        finally:
                            context.close()

    def test_server_persistent_cookies_survive_same_disposable_profile_restart(self) -> None:
        from playwright.sync_api import sync_playwright

        with TemporaryDirectory(prefix="mato-profile-browser-test-") as temporary:
            profile_path = Path(temporary) / "naver_1"
            expected = {cookie["name"]: cookie["value"] for cookie in _synthetic_auth_cookies()}
            with sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(
                    str(profile_path), channel="chrome", headless=True, timeout=45_000
                )
                try:
                    _isolate_page_requests(context)
                    page = context.pages[0]
                    page.goto(FIXTURE_URL, wait_until="domcontentloaded")
                    context.add_cookies(_synthetic_auth_cookies())
                    self.assertTrue(naver_session.has_naver_login(context))
                    self.assertTrue(
                        naver_session.has_naver_login(context, require_persistent=True)
                    )
                    self.assertTrue(naver_session.persistent_login_ready(context, page))
                finally:
                    context.close()

                reopened = playwright.chromium.launch_persistent_context(
                    str(profile_path), channel="chrome", headless=True, timeout=45_000
                )
                try:
                    _isolate_page_requests(reopened)
                    cookies = {
                        cookie["name"]: cookie
                        for cookie in reopened.cookies(list(naver_session.NAVER_ORIGINS))
                        if cookie["name"] in naver_session.AUTH_COOKIE_NAMES
                    }
                    self.assertEqual(set(cookies), set(naver_session.AUTH_COOKIE_NAMES))
                    self.assertTrue(naver_session.has_naver_login(reopened, require_persistent=True))
                    for name, value in expected.items():
                        with self.subTest(cookie_name=name):
                            self.assertEqual(cookies[name]["value"], value)
                            self.assertGreater(cookies[name]["expires"], time.time() + 86_400)
                            self.assertEqual(cookies[name]["domain"], ".naver.com")
                            self.assertEqual(cookies[name]["path"], "/")
                            self.assertTrue(cookies[name]["httpOnly"])
                            self.assertTrue(cookies[name]["secure"])
                finally:
                    reopened.close()

    def test_client_disconnect_leaves_owning_chrome_available_for_reattach(self) -> None:
        from playwright.sync_api import sync_playwright

        # A separate process owns each client so the sync Playwright event
        # loops never nest on one thread. The client intentionally stops its
        # transport only; browser.close()/context.close() would close the owner.
        client_script = """
import sys
sys.path.insert(0, sys.argv[1])
from playwright.sync_api import sync_playwright
from profile_connector import connect_profile_context

playwright = sync_playwright().start()
try:
    attached = connect_profile_context(playwright, sys.argv[2])
    assert attached is not None, 'Disposable profile connector unavailable'
    browser, context = attached
    assert browser.is_connected(), 'Client failed to connect'
    assert any(page.title() == sys.argv[3] for page in context.pages), 'Fixture missing'
finally:
    playwright.stop()
"""
        with TemporaryDirectory(prefix="mato-profile-browser-test-") as temporary:
            profile_path = Path(temporary) / "naver_2"
            with sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(
                    str(profile_path),
                    channel="chrome",
                    headless=True,
                    timeout=45_000,
                    args=["--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0"],
                )
                try:
                    _isolate_page_requests(context)
                    page = context.pages[0]
                    page.goto(FIXTURE_URL, wait_until="domcontentloaded")
                    self.assertTrue((profile_path / "DevToolsActivePort").is_file())
                    for attempt in range(2):
                        with self.subTest(attach_attempt=attempt + 1):
                            result = subprocess.run(
                                [
                                    sys.executable,
                                    "-c",
                                    client_script,
                                    str(SCRIPTS_DIR),
                                    str(profile_path),
                                    FIXTURE_TITLE,
                                ],
                                capture_output=True,
                                text=True,
                                timeout=30,
                                check=False,
                            )
                            self.assertEqual(result.returncode, 0, result.stderr)
                            self.assertFalse(page.is_closed())
                            self.assertEqual(page.title(), FIXTURE_TITLE)
                finally:
                    context.close()


if __name__ == "__main__":
    unittest.main()
