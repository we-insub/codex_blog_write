"""Keep a Naver login reusable inside one local persistent Chrome profile.

Authentication values never leave the Playwright context and are never printed
or written to the profile catalog or run history.
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
PERSIST_SECONDS = 30 * 24 * 60 * 60


def _auth_cookie_state(context: Any) -> tuple[bool, bool, dict[str, Mapping[str, Any]]]:
    """Return the in-memory state needed to preserve the same profile."""

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

    has_required = all(name in auth for name in AUTH_COOKIE_NAMES)
    now = time.time()

    def is_persistent(cookie: Mapping[str, Any]) -> bool:
        try:
            expires = float(cookie.get("expires") or -1)
        except (TypeError, ValueError):
            expires = -1
        return expires > now + 60

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
    selectors = (
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
    # Naver's signed-in home page can still expose login-shaped header links
    # (including ``#log.login``).  Only actual login fields count as visible
    # login UI; otherwise the profile's auth state decides the result.
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


def _promote_auth_cookies(context: Any, page: Any, auth: Mapping[str, Mapping[str, Any]]) -> bool:
    """Make authentication reusable in the same persistent profile only."""

    if context is None or not auth:
        return False
    now = time.time()
    promoted: list[dict[str, Any]] = []
    for name in AUTH_COOKIE_NAMES:
        cookie = auth.get(name)
        if not cookie:
            return False
        try:
            expires = float(cookie.get("expires") or -1)
        except (TypeError, ValueError):
            expires = -1
        if expires <= now + 60:
            expires = now + PERSIST_SECONDS
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
        if same_site in ("Strict", "Lax", "None"):
            item["sameSite"] = same_site
        promoted.append(item)
    try:
        context.add_cookies(promoted)
        if page is not None:
            page.wait_for_timeout(1_000)
    except Exception:
        return False
    _has_required, persistent, _auth = _auth_cookie_state(context)
    return bool(persistent)


def persistent_login_ready(context: Any, page: Any) -> bool:
    """Ensure Naver auth survives a clean close of this persistent profile."""

    has_required, persistent, auth = _auth_cookie_state(context)
    if persistent:
        return True
    if not has_required:
        return False
    return _promote_auth_cookies(context, page, auth)
