"""Persistent Naver Chrome runtime ported from Mato Helper's profile flow."""

from __future__ import annotations

import platform
from pathlib import Path
from typing import Any


def _mato_user_agent() -> str:
    if platform.system() == "Darwin":
        return (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        )
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )


def launch_mato_profile_context(
    playwright: Any,
    profile_path: str | Path,
    *,
    headless: bool = False,
) -> Any:
    """Open a local profile with Mato Helper's persistent-context settings.

    The browser owns cookies and session encryption in its user-data directory.
    This function never exports, copies, or prints browser authentication data.
    """

    path = Path(profile_path).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    options = {
        "headless": headless,
        "args": [
            "--window-size=1280,1024",
            "--window-position=0,0",
            "--disable-blink-features=AutomationControlled",
        ],
        "user_agent": _mato_user_agent(),
        "viewport": {"width": 1280, "height": 1024},
        "locale": "ko-KR",
        "timezone_id": "Asia/Seoul",
    }
    errors: list[str] = []
    for channel in ("chrome", "msedge", None):
        kwargs = dict(options)
        if channel:
            kwargs["channel"] = channel
        try:
            return playwright.chromium.launch_persistent_context(str(path), **kwargs)
        except Exception as exc:
            errors.append(f"{channel or 'chromium'}: {exc}")
    detail = " | ".join(errors[-3:])
    raise RuntimeError(f"설치된 Chrome 또는 Edge로 프로필을 열지 못했습니다: {detail}")
