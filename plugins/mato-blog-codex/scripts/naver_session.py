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


def persistent_login_ready(context: Any, page: Any) -> bool:
    """Return only a session that Naver itself stored beyond this Chrome run.

    A visible authenticated page can still be backed by session-only cookies.
    Treating that as a ready profile meant a user could close the dedicated
    window and unexpectedly have to log in again next time. A Mato profile is
    ready only when both Naver authentication cookies have a future expiry
    chosen by Naver after its normal *keep me logged in* flow.

    This function never copies cookies, changes expiry dates, or otherwise
    attempts to extend Naver's server-controlled session.
    """

    if is_naver_login_required(page, context):
        return False
    return has_naver_login(context, require_persistent=True)
