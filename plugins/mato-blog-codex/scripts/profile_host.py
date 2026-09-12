"""Run one visible Mato Chrome profile through Playwright's persistent context.

This small detached host mirrors the lifecycle used by the Mato Helper app:
Playwright creates and owns the exact numbered profile directory for as long
as the visible Chrome window exists.  It never reads another project's
profile, never prints authentication material, and only keeps Naver's normal
login state inside the profile selected by the caller.
"""

from __future__ import annotations

import argparse
import signal
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Sequence

from profile_session import ProfileSession
from profile_runtime import clear_profile_state, write_profile_state


def _user_agent() -> str:
    if sys.platform == "darwin":
        return (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        )
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )


def run(profile_path: str | Path, target_url: str) -> int:
    """Keep a visible browser alive and persist a completed normal login."""

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - bootstrap owns dependency setup.
        raise RuntimeError("Playwright가 설치되어 있지 않습니다.") from exc

    profile = Path(profile_path).expanduser().resolve(strict=False)
    if not profile.is_dir():
        raise FileNotFoundError(f"프로필 폴더가 없습니다: {profile}")

    launch_args = [
        "--window-size=1280,1024",
        "--window-position=0,0",
        "--remote-debugging-address=127.0.0.1",
        "--remote-debugging-port=0",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    stop_requested = False

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stop_requested
        stop_requested = True

    previous_handlers = {
        signum: signal.signal(signum, request_stop)
        for signum in (signal.SIGTERM, signal.SIGINT)
    }
    try:
        return _run_context(profile, target_url, launch_args, stop_requested=lambda: stop_requested)
    finally:
        for signum, previous in previous_handlers.items():
            signal.signal(signum, previous)


def _run_context(
    profile: Path,
    target_url: str,
    launch_args: list[str],
    *,
    stop_requested: Callable[[], bool],
) -> int:
    """Own Chrome until its window closes or the host receives a stop signal."""

    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            channel="chrome",
            headless=False,
            args=launch_args,
            user_agent=_user_agent(),
            locale="ko-KR",
            timezone_id="Asia/Seoul",
            viewport={"width": 1280, "height": 1024},
        )
        session = None
        try:
            write_profile_state(profile, status="starting")
            # Match the app: the owning Playwright context creates the login tab.
            # Reattached clients are not involved in restoring/checking login.
            page = context.new_page()
            session = ProfileSession(context, page, target_url)
            while not stop_requested():
                pages = [item for item in context.pages if not item.is_closed()]
                if not pages:
                    return 0
                try:
                    status = session.poll()
                except Exception:
                    # Redirects, temporary network errors and a closed tab
                    # must not tear down the owner of a just-saved login.
                    status = "error"
                write_profile_state(profile, status=status, blog_id=session.blog_id if status == "ready" else None)
                try:
                    pages[0].wait_for_timeout(1_000)
                except Exception:
                    if not any(not item.is_closed() for item in context.pages):
                        return 0
            return 0
        finally:
            try:
                if session is not None:
                    session.preserve_before_close()
            except Exception:
                pass  # A user may already have closed the browser normally.
            try:
                context.close()
            finally:
                clear_profile_state(profile)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Keep one Mato Naver profile window alive.")
    parser.add_argument("--profile-path", required=True)
    parser.add_argument("--target-url", required=True)
    args = parser.parse_args(argv)
    try:
        return run(args.profile_path, args.target_url)
    except Exception:
        # The parent detects startup success through its loopback DevTools
        # endpoint. Do not write errors or browser state into a user's shell.
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
