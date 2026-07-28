from __future__ import annotations

import sys
import time
import unittest

from _support import SCRIPTS_DIR

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import naver_session


class FakeLocator:
    def __init__(self, visible: bool) -> None:
        self.visible = visible
        self.first = self

    def count(self) -> int:
        return 1 if self.visible else 0

    def is_visible(self, timeout: int = 0) -> bool:
        return self.visible


class FakePage:
    def __init__(self, *, url: str = "https://www.naver.com", login_visible: bool = False) -> None:
        self.url = url
        self.login_visible = login_visible
        self.evaluate_calls = 0
        self.waits: list[int] = []

    def is_closed(self) -> bool:
        return False

    def locator(self, _selector: str) -> FakeLocator:
        return FakeLocator(self.login_visible)

    def evaluate(self, _script: str) -> bool:
        self.evaluate_calls += 1
        return True

    def wait_for_timeout(self, milliseconds: int) -> None:
        self.waits.append(milliseconds)


class FakeContext:
    def __init__(self, cookies: list[dict[str, object]]) -> None:
        self.cookie_rows = [dict(cookie) for cookie in cookies]
        self.added: list[dict[str, object]] = []

    def cookies(self, _origins):  # type: ignore[no-untyped-def]
        return [dict(cookie) for cookie in self.cookie_rows]

    def add_cookies(self, cookies):  # type: ignore[no-untyped-def]
        self.added = [dict(cookie) for cookie in cookies]
        by_name = {str(cookie["name"]): dict(cookie) for cookie in self.cookie_rows}
        for cookie in cookies:
            by_name[str(cookie["name"])] = dict(cookie)
        self.cookie_rows = list(by_name.values())


def auth_cookies(expires: float) -> list[dict[str, object]]:
    return [
        {
            "name": name,
            "value": f"private-{index}",
            "domain": ".naver.com",
            "path": "/",
            "expires": expires,
            "httpOnly": True,
            "secure": True,
            "sameSite": "Lax",
        }
        for index, name in enumerate(naver_session.AUTH_COOKIE_NAMES, start=1)
    ]


class NaverSessionTests(unittest.TestCase):
    def test_session_cookies_are_promoted_inside_same_context(self) -> None:
        context = FakeContext(auth_cookies(-1))
        page = FakePage()

        self.assertTrue(naver_session.persistent_login_ready(context, page))

        self.assertEqual([row["name"] for row in context.added], ["NID_AUT", "NID_SES"])
        self.assertTrue(all(float(row["expires"]) > time.time() for row in context.added))
        self.assertEqual(page.waits, [1_000])

    def test_existing_persistent_cookies_are_not_rewritten(self) -> None:
        context = FakeContext(auth_cookies(time.time() + 86_400))
        self.assertTrue(naver_session.persistent_login_ready(context, FakePage()))
        self.assertEqual(context.added, [])

    def test_missing_auth_cookie_is_not_treated_as_login(self) -> None:
        context = FakeContext(auth_cookies(-1)[:1])
        self.assertFalse(naver_session.has_naver_login(context))
        self.assertFalse(naver_session.persistent_login_ready(context, FakePage()))
        self.assertEqual(context.added, [])
        self.assertTrue(naver_session.is_naver_login_required(FakePage(), context))

    def test_visible_login_ui_wins_even_when_old_cookies_exist(self) -> None:
        context = FakeContext(auth_cookies(time.time() + 86_400))
        page = FakePage(login_visible=True)
        self.assertTrue(naver_session.is_naver_login_required(page, context))

    def test_authenticated_home_does_not_require_login(self) -> None:
        context = FakeContext(auth_cookies(time.time() + 86_400))
        self.assertFalse(naver_session.is_naver_login_required(FakePage(), context))

    def test_keep_login_option_is_selected_in_page(self) -> None:
        page = FakePage(url="https://nid.naver.com/nidlogin.login")
        self.assertTrue(naver_session.ensure_keep_login_checked(page))
        self.assertEqual(page.evaluate_calls, 1)


if __name__ == "__main__":
    unittest.main()
