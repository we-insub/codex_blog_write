"""Restore, save and check one independent Naver profile in its owning host.

Only the initial login flow navigates. Once the editor has been checked this
monitor never reloads it, so an uploader or a human can safely write there.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlsplit

from mato_common import extract_naver_blog_id
from naver_session import (
    ensure_keep_login_checked,
    is_naver_login_required,
    persistent_login_ready,
)

NAVER_HOME_URL = "https://www.naver.com"
NAVER_LOGIN_URL = "https://nid.naver.com/nidlogin.login"


def is_naver_page(page: Any) -> bool:
    try:
        return urlsplit(page.url).hostname in {"www.naver.com", "nid.naver.com", "blog.naver.com"}
    except (TypeError, ValueError):
        return False


def is_access_restricted(page: Any) -> bool:
    if "captcha" in str(page.url).lower():
        return True
    try:
        body = page.locator("body").inner_text(timeout=1_000).lower()
    except Exception:
        return False
    return any(text in body for text in ("captcha", "자동입력 방지", "비정상적인 접근", "접근이 제한", "보안 확인"))


def has_security_challenge(page: Any) -> bool:
    """Check explicit challenges even when an editor remains underneath."""
    if "captcha" in str(page.url).lower():
        return True
    for frame in getattr(page, "frames", []):
        if "captcha" in str(frame.url).lower():
            return True
    for selector in ("input#captcha", "input[name='captcha']", "iframe[src*='captcha']"):
        locator = page.locator(selector)
        if locator.count() and locator.first.is_visible(timeout=1_000):
            return True
    return False


def is_editor_ready(page: Any, target_url: str | None = None) -> bool:
    """Require visible Naver editor controls and the configured blog identity."""
    if not is_naver_page(page) or urlsplit(page.url).hostname != "blog.naver.com":
        return False
    if target_url:
        try:
            expected = extract_naver_blog_id(target_url).casefold()
            if extract_naver_blog_id(page.url).casefold() != expected:
                return False
            # Naver can retain the outer requested URL while its iframe redirects.
            for frame in getattr(page, "frames", []):
                if frame == getattr(page, "main_frame", None):
                    continue
                if urlsplit(frame.url).hostname == "blog.naver.com":
                    try:
                        actual = extract_naver_blog_id(frame.url).casefold()
                    except ValueError:
                        continue
                    if actual != expected:
                        return False
        except (TypeError, ValueError):
            return False
    scopes = [page]
    urls = [page.url] + [frame.url for frame in getattr(page, "frames", [])]
    def is_write_url(url: str) -> bool:
        parsed = urlsplit(url)
        return parsed.hostname == "blog.naver.com" and (
            parsed.path.lower().endswith(("/postwrite", "/postwriteform.naver"))
            or any(key.lower() == "redirect" and any(value.lower() == "write" for value in values)
                   for key, values in parse_qs(parsed.query).items())
        )
    if not any(is_write_url(url) for url in urls):
        return False
    if page.locator("iframe#mainFrame").count():
        scopes.insert(0, page.frame_locator("iframe#mainFrame"))
    for scope in scopes:
        for selector in (".se-documentTitle [contenteditable='true']", ".se-title-text[contenteditable='true']", ".se-content [contenteditable='true']"):
            locator = scope.locator(selector)
            if locator.count() and locator.first.is_visible(timeout=1_000):
                return True
        # Smart Editor also uses a body-level editable input proxy outside
        # .se-content. Require the visible editor canvas AND title in this
        # same scope before accepting that proxy. A public post's title or
        # an unrelated editable field alone is never sufficient.
        proxy_selectors = (
            ".se-container .se-content",
            ".se-documentTitle .se-title-text",
            "[contenteditable='true']",
        )
        if all(
            scope.locator(selector).count()
            and scope.locator(selector).first.is_visible(timeout=1_000)
            for selector in proxy_selectors
        ):
            return True
    return False


class ProfileSession:
    def __init__(self, context: Any, page: Any, target_url: str) -> None:
        self.context = context
        self.page = page
        self.target_url = target_url
        self.target_opened = False
        self.started = False
        self.was_ready = False
        try:
            self.blog_id = extract_naver_blog_id(target_url)
            self.has_target = True
        except ValueError:
            self.blog_id = ""
            self.has_target = False

    def _goto(self, url: str) -> None:
        self.page.goto(url, wait_until="domcontentloaded", timeout=10_000)

    def start(self) -> None:
        # Match Mato Helper's restore-before-editor ordering, using this
        # tool's own user-data directory and no external cookie snapshots.
        self.started = True
        self._goto(NAVER_HOME_URL)
        self.page.wait_for_timeout(6_500)

    def poll(self) -> str:
        if not self.started:
            self.start()
        pages = [p for p in self.context.pages if not p.is_closed() and is_naver_page(p)]
        if self.page.is_closed() or not is_naver_page(self.page):
            if not pages:
                return "needs_login"
            self.page = pages[0]
        if has_security_challenge(self.page) or (not is_editor_ready(self.page) and is_access_restricted(self.page)):
            return "error"
        if is_naver_login_required(self.page, self.context):
            # A normal login may finish in a new Naver tab. Unrelated tabs
            # must never mask the page whose authentication needs saving.
            authenticated = next((p for p in pages if not is_naver_login_required(p, self.context)), None)
            if authenticated is not None:
                self.page = authenticated
                if has_security_challenge(self.page) or (not is_editor_ready(self.page) and is_access_restricted(self.page)):
                    return "error"
            else:
                if is_access_restricted(self.page):
                    return "error"
                self.target_opened = False
                self.was_ready = False
                if urlsplit(self.page.url).hostname == "www.naver.com":
                    self._goto(NAVER_LOGIN_URL)
                ensure_keep_login_checked(self.page)
                return "needs_login"

        # Save before any editor navigation, including when its redirect
        # later fails. A navigation error must not discard a completed login.
        if not persistent_login_ready(self.context, self.page):
            return "needs_login"
        if not self.has_target:
            return "logged_in"
        if not self.target_opened:
            if not is_editor_ready(self.page, self.target_url):
                self._goto(self.target_url)
            self.target_opened = True
        if is_naver_login_required(self.page, self.context):
            self.target_opened = False
            ensure_keep_login_checked(self.page)
            return "needs_login"
        if has_security_challenge(self.page):
            return "error"
        if is_editor_ready(self.page, self.target_url):
            self.was_ready = True
            return "ready"
        if not self.was_ready and is_access_restricted(self.page):
            return "error"
        return "logged_in"

    def preserve_before_close(self) -> None:
        """Best-effort final save on a normal host stop, without navigation."""
        for page in self.context.pages:
            if not page.is_closed() and is_naver_page(page):
                if has_security_challenge(page) or (not is_editor_ready(page) and is_access_restricted(page)):
                    continue
                if persistent_login_ready(self.context, page):
                    return
