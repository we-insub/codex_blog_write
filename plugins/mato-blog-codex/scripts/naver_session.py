"""Keep a Naver login reusable inside one local persistent Chrome profile.

This is a self-contained port of the profile-session behavior used by the
original Google Blog Auto project. Authentication values never leave the
Playwright context, are never printed, and are not written to the profile
catalog or run history.
"""

from __future__ import annotations

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
            return float(raw_expires)
        except (TypeError, ValueError):
            return None

    def is_current(cookie: Mapping[str, Any]) -> bool:
        expires = expiry_value(cookie)
        return expires is not None and (expires < 0 or expires > now)

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
    """Select Naver's normal keep-login option before a user logs in."""

    if page is None:
        return False
    try:
        return bool(
            page.evaluate(
                """() => {
                    const candidates = [
                      '#keep',
                      'input[name="keep"]',
                      'input[type="checkbox"][id*="keep"]',
                      'input[type="checkbox"][name*="keep"]'
                    ];
                    const keepLooksChecked = () => {
                      for (const selector of candidates) {
                        const checkbox = document.querySelector(selector);
                        if (!checkbox) continue;
                        const aria = String(checkbox.getAttribute('aria-checked') || '').toLowerCase();
                        const cls = String(checkbox.className || '').toLowerCase();
                        if (checkbox.checked || aria === 'true' || cls.includes('on') || cls.includes('checked')) {
                          return true;
                        }
                      }
                      return false;
                    };
                    for (const selector of candidates) {
                      const checkbox = document.querySelector(selector);
                      if (checkbox) {
                        if (!checkbox.checked) checkbox.click();
                        return true;
                      }
                    }
                    const labels = [
                      'label[for="keep"]',
                      '.keep_text',
                      '.keep_check',
                      '[class*="keep"] label'
                    ];
                    for (const selector of labels) {
                      const label = document.querySelector(selector);
                      if (label) {
                        if (!keepLooksChecked()) label.click();
                        return true;
                      }
                    }
                    const nodes = Array.from(document.querySelectorAll('label, span, strong, em, div, a, button'));
                    for (const element of nodes) {
                      const text = (element.innerText || element.textContent || '').replace(/\\s+/g, '');
                      if (text.includes('로그인상태유지') || text.includes('Keepmeloggedin')) {
                        if (!keepLooksChecked()) element.click();
                        return true;
                      }
                    }
                    return false;
                }"""
            )
        )
    except Exception:
        return False


def persist_naver_auth_cookies(context: Any, page: Any) -> bool:
    """Store the current user's Naver auth pair in this same profile.

    This is the local profile-persistence behavior used by Mato Helper.  It
    runs only after a normal, visible user login has produced a valid
    ``NID_AUT``/``NID_SES`` pair in the active context.  The values never
    leave that context, are never logged, and are only written back to the
    exact Chrome profile that supplied them.  No other project's profile
    folder or authentication data is read.
    """

    if is_naver_login_required(page, context):
        return False
    has_required, persistent, auth = _auth_cookie_state(context)
    if persistent:
        return True
    if not has_required:
        return False

    now = time.time()
    promoted: list[dict[str, Any]] = []
    for name in AUTH_COOKIE_NAMES:
        cookie = auth.get(name)
        if not cookie:
            return False
        raw_expiry = cookie.get("expires", -1)
        try:
            expires = float(raw_expiry if raw_expiry not in (None, "") else -1)
        except (TypeError, ValueError):
            expires = -1
        if expires <= now + 60:
            expires = now + (30 * 24 * 60 * 60)
        item: dict[str, Any] = {
            "name": name,
            "value": str(cookie.get("value") or ""),
            "domain": str(cookie.get("domain") or ".naver.com"),
            "path": str(cookie.get("path") or "/"),
            "expires": expires,
            "httpOnly": bool(cookie.get("httpOnly", True)),
            "secure": bool(cookie.get("secure", True)),
        }
        same_site = cookie.get("sameSite")
        if same_site in {"Strict", "Lax", "None"}:
            item["sameSite"] = same_site
        promoted.append(item)

    try:
        context.add_cookies(promoted)
        if page is not None:
            page.wait_for_timeout(1_000)
    except Exception:
        return False
    return has_naver_login(context, require_persistent=True)


def persistent_login_ready(context: Any, page: Any) -> bool:
    """Return a restart-safe Naver session for the same persistent profile."""

    return persist_naver_auth_cookies(context, page)
