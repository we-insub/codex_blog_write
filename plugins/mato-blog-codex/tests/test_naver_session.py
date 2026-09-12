from __future__ import annotations

import sys
import time
import unittest
from unittest.mock import patch

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


class SelectiveFakePage(FakePage):
    def __init__(self, visible_selectors: set[str]) -> None:
        super().__init__()
        self.visible_selectors = visible_selectors

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(selector in self.visible_selectors)


class HiddenLoginLinkPage(FakePage):
    def locator(self, selector: str) -> FakeLocator:
        if "nidlogin.login" in selector:
            locator = FakeLocator(False)
            locator.count = lambda: 1  # type: ignore[method-assign]
            return locator
        return FakeLocator(False)


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
    def test_session_only_cookies_cannot_be_made_restart_safe_by_local_expiry(self) -> None:
        context = FakeContext(auth_cookies(-1))
        page = FakePage()

        self.assertFalse(naver_session.persistent_login_ready(context, page))
        self.assertEqual(context.added, [])
        self.assertEqual(page.waits, [])

    def test_long_lived_persistent_cookies_are_not_rewritten(self) -> None:
        context = FakeContext(auth_cookies(time.time() + 180 * 24 * 60 * 60))
        self.assertTrue(naver_session.persistent_login_ready(context, FakePage()))
        self.assertEqual(context.added, [])

    def test_near_expiry_persistent_cookies_are_left_unchanged(self) -> None:
        context = FakeContext(auth_cookies(time.time() + 86_400))
        self.assertTrue(naver_session.persistent_login_ready(context, FakePage()))
        self.assertEqual(context.added, [])

    def test_server_expiry_is_never_extended_at_the_readiness_boundary(self) -> None:
        with patch.object(naver_session.time, "time", return_value=1_000):
            for remaining in (0, 1, 59, 60, 61):
                with self.subTest(remaining=remaining):
                    context = FakeContext(auth_cookies(1_000 + remaining))
                    self.assertEqual(naver_session.persistent_login_ready(context, FakePage()), remaining > 60)
                    self.assertEqual(context.added, [])

    def test_mixed_pair_is_not_rewritten_or_claimed_restart_safe(self) -> None:
        cookies = auth_cookies(time.time() + 86_400)
        cookies[0]["expires"] = -1
        context = FakeContext(cookies)
        self.assertFalse(naver_session.persistent_login_ready(context, FakePage()))
        self.assertEqual(context.added, [])
        self.assertEqual(context.cookie_rows[1], cookies[1])

    def test_invalid_expiry_and_empty_values_are_not_authenticated(self) -> None:
        for expires in (-2, float("nan"), float("inf"), "invalid"):
            context = FakeContext(auth_cookies(expires))
            self.assertFalse(naver_session.persistent_login_ready(context, FakePage()))
            self.assertEqual(context.added, [])
        cookies = auth_cookies(-1)
        cookies[0]["value"] = ""
        self.assertFalse(naver_session.has_naver_login(FakeContext(cookies)))

    def test_visible_login_ui_prevents_a_reusable_profile_result(self) -> None:
        context = FakeContext(auth_cookies(time.time() + 86_400))
        self.assertFalse(
            naver_session.persistent_login_ready(context, FakePage(login_visible=True))
        )

    def test_expired_positive_cookies_are_not_resurrected(self) -> None:
        context = FakeContext(auth_cookies(time.time() - 60))
        self.assertFalse(naver_session.has_naver_login(context))
        self.assertTrue(naver_session.is_naver_login_required(FakePage(), context))
        self.assertFalse(naver_session.persistent_login_ready(context, FakePage()))
        self.assertEqual(context.added, [])

    def test_missing_auth_cookie_is_not_treated_as_login(self) -> None:
        context = FakeContext(auth_cookies(-1)[:1])
        self.assertFalse(naver_session.has_naver_login(context))
        self.assertFalse(naver_session.persistent_login_ready(context, FakePage()))
        self.assertEqual(context.added, [])

    def test_visible_login_ui_wins_even_when_old_cookies_exist(self) -> None:
        context = FakeContext(auth_cookies(time.time() + 86_400))
        page = FakePage(login_visible=True)
        self.assertTrue(naver_session.is_naver_login_required(page, context))

    def test_authenticated_home_does_not_require_login(self) -> None:
        context = FakeContext(auth_cookies(time.time() + 86_400))
        self.assertFalse(naver_session.is_naver_login_required(FakePage(), context))

    def test_hidden_login_shaped_home_link_does_not_override_valid_profile_cookies(self) -> None:
        context = FakeContext(auth_cookies(time.time() + 86_400))
        page = HiddenLoginLinkPage()
        self.assertFalse(naver_session.is_naver_login_required(page, context))

    def test_visible_login_link_overrides_stale_profile_cookies(self) -> None:
        context = FakeContext(auth_cookies(time.time() + 86_400))
        page = SelectiveFakePage({'a[href*="nid.naver.com/nidlogin.login"]'})
        self.assertTrue(naver_session.is_naver_login_required(page, context))

    def test_missing_keep_login_control_is_not_success(self) -> None:
        self.assertFalse(naver_session.ensure_keep_login_checked(FakePage()))


class KeepLoginTests(unittest.TestCase):
    def page(self, control_id="loginStay", *, checked=False, click_works=True):
        from unittest.mock import MagicMock
        page = MagicMock()
        checkbox = MagicMock()
        checkbox.is_checked.side_effect = lambda **kw: checked_state[0]
        checkbox.get_attribute.return_value = control_id
        checked_state = [checked]
        label = MagicMock()
        label.is_visible.return_value = True
        def click(**kwargs):
            if click_works:
                checked_state[0] = True
        label.click.side_effect = click
        def locator(selector):
            result = MagicMock()
            if selector == f'input[type="checkbox"][id="{control_id}"]':
                result.count.return_value = 1
                result.first = checkbox
            elif selector == f'label[for="{control_id}"]':
                result.count.return_value = 1
                result.first = label
            else:
                result.count.return_value = 0
            return result
        page.locator.side_effect = locator
        return page, checkbox, label

    def test_current_naver_control_is_selected_and_verified(self):
        page, checkbox, label = self.page()
        self.assertTrue(naver_session.ensure_keep_login_checked(page))
        label.click.assert_called_once()
        self.assertEqual(checkbox.is_checked.call_count, 2)

    def test_legacy_keep_control_still_works(self):
        page, _, label = self.page("keep")
        self.assertTrue(naver_session.ensure_keep_login_checked(page))
        label.click.assert_called_once()

    def test_already_checked_control_is_never_toggled_off(self):
        page, _, label = self.page(checked=True)
        for _ in range(3):
            self.assertTrue(naver_session.ensure_keep_login_checked(page))
        label.click.assert_not_called()

    def test_click_without_state_change_is_not_success(self):
        page, _, _ = self.page(click_works=False)
        self.assertFalse(naver_session.ensure_keep_login_checked(page))


if __name__ == "__main__":
    unittest.main()
