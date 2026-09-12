"""Keep a Naver login reusable inside one local persistent Chrome profile.

This is a self-contained port of the profile-session behavior used by the
original Google Blog Auto project. Authentication values never leave the
Playwright context, are never printed, and are not written to the profile
catalog or run history.
"""

from __future__ import annotations

import math
import time
from typing import Any, Mapping


NAVER_ORIGINS = (
    "https://www.naver.com",
    "https://nid.naver.com",
    "https://blog.naver.com",
)
AUTH_COOKIE_NAMES = ("NID_AUT", "NID_SES")


def _auth_cookie_state(context: Any) -> tuple[bool, bool, dict[str, Mapping[str, Any]]]:
    """Return only the in-memory state needed to preserve the same profile."""

    if context is None:
        return False, False, {}
    try:
        cookies = context.cookies(list(NAVER_ORIGINS))
    except Exception:
        return False, False, {}

    auth: dict[str, Mapping[str, Any]] = {}
    for cookie in cookies:
        if not isinstance(cookie, Mapping):
            continue
        name = str(cookie.get("name") or "")
        if name in AUTH_COOKIE_NAMES:
            auth[name] = cookie

    now = time.time()

    def expiry_value(cookie: Mapping[str, Any]) -> float | None:
        raw_expires = cookie.get("expires", -1)
        if raw_expires is None or raw_expires == "":
            raw_expires = -1
        try:
            expires = float(raw_expires)
            return expires if math.isfinite(expires) else None
        except (TypeError, ValueError):
            return None

    def is_current(cookie: Mapping[str, Any]) -> bool:
        expires = expiry_value(cookie)
        return bool(cookie.get("value")) and expires is not None and (expires == -1 or expires > now)

    def is_persistent(cookie: Mapping[str, Any]) -> bool:
        expires = expiry_value(cookie)
        return expires is not None and expires > now + 60

    has_required = all(name in auth and is_current(auth[name]) for name in AUTH_COOKIE_NAMES)
    persistent = has_required and all(is_persistent(auth[name]) for name in AUTH_COOKIE_NAMES)
    return has_required, persistent, auth


def has_naver_login(context: Any, *, require_persistent: bool = False) -> bool:
    has_required, persistent, _auth = _auth_cookie_state(context)
    return bool(persistent if require_persistent else has_required)


def is_naver_login_required(page: Any, context: Any) -> bool:
    """Detect login state from the visible page plus the active local context."""

    if page is None:
        return True
    try:
        if page.is_closed():
            return True
    except Exception:
        pass
    current_url = str(getattr(page, "url", "") or "").lower()
    if "nid.naver.com" in current_url or "nidlogin" in current_url:
        return True
    # Naver can leave login-shaped links in the DOM after authentication, so
    # only visible links override cookies. Actual credential fields and the
    # login URL remain authoritative.
    selectors = (
        'a[href*="nid.naver.com/nidlogin.login"]',
        'a[href*="/nidlogin.login"]',
        '#log\\.login',
        'input[name="id"]',
        'input[name="pw"]',
    )
    for selector in selectors:
        try:
            locator = page.locator(selector)
            if locator.count() <= 0:
                continue
            first = locator.first
            try:
                if first.is_visible(timeout=1_000):
                    return True
            except Exception:
                return True
        except Exception:
            continue
    return not has_naver_login(context)


def ensure_keep_login_checked(page: Any) -> bool:
    """Check the actual Naver control and verify its resulting state.

    Current Naver uses loginStay/nvlong; older pages used keep. Never click
    a generic text ancestor: it may contain the whole form without toggling
    the checkbox, and must not be reported as a successful selection.
    """

    if page is None:
        return False
    selectors = (
        'input[type="checkbox"][id="loginStay"]',
        'input[type="checkbox"][name="nvlong"]',
        'input[type="checkbox"][id="keep"]',
        'input[type="checkbox"][name="keep"]',
    )
    for selector in selectors:
        try:
            locator = page.locator(selector)
            if not locator.count():
                continue
            checkbox = locator.first
            if checkbox.is_checked(timeout=1_000):
                return True
            # Styled checkboxes can hide the input. Use its native label,
            # which dispatches the site's normal change handlers.
            control_id = checkbox.get_attribute("id")
            label = page.locator(f'label[for="{control_id}"]') if control_id in {"loginStay", "keep"} else None
            if label is not None and label.count() and label.first.is_visible(timeout=1_000):
                label.first.click(timeout=1_500)
            else:
                checkbox.check(timeout=1_500)
            return bool(checkbox.is_checked(timeout=1_000))
        except Exception:
            continue
    return False


def persist_naver_auth_cookies(context: Any, page: Any) -> bool:
    """Confirm Naver-issued persistence without changing cookie lifetimes.

    Chrome writes persistent cookies to this profile automatically. Giving a
    session-only token an invented expiry does not enable Naver's keep-login
    feature and can falsely report a restart-safe login. Normal login with
    the verified keep-login control must supply the persistent credentials.
    """

    return not is_naver_login_required(page, context) and has_naver_login(
        context, require_persistent=True
    )


def persistent_login_ready(context: Any, page: Any) -> bool:
    """Confirm persisted authentication; a real restart still verifies reuse."""

    return persist_naver_auth_cookies(context, page)
