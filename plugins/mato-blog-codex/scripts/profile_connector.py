"""Keep numbered Chrome profiles open and reconnect Playwright to them.

Chrome owns the persistent user-data directory for the lifetime of the visible
window.  Later profile checks and uploads attach over a loopback-only DevTools
endpoint instead of trying to launch the same locked profile a second time.
No cookies, credentials, or browser storage values are copied or printed.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping


DEVTOOLS_ACTIVE_PORT = "DevToolsActivePort"


def find_chrome_executable() -> Path:
    """Return the installed Google Chrome executable on this host."""

    candidates = []
    if sys.platform == "darwin":
        candidates.extend(
            [
                Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            ]
        )
    for env_name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        root = os.environ.get(env_name)
        if root:
            candidates.append(Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError("설치된 Google Chrome을 찾지 못했습니다.")


def connector_endpoint(profile_path: str | Path) -> str | None:
    """Return a live loopback CDP endpoint for one exact profile, if present."""

    active_port_file = Path(profile_path) / DEVTOOLS_ACTIVE_PORT
    try:
        first_line = active_port_file.read_text(encoding="utf-8").splitlines()[0].strip()
        port = int(first_line)
    except (OSError, ValueError, IndexError):
        return None
    if port <= 0 or port > 65535:
        return None
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            pass
    except OSError:
        return None
    return f"http://127.0.0.1:{port}"


def open_profile_browser(
    profile: Mapping[str, Any],
    *,
    timeout_seconds: float = 20.0,
) -> dict[str, Any]:
    """Open a visible persistent Chrome window and leave it running."""

    profile_path = Path(str(profile["profile_path"])).expanduser().resolve(strict=False)
    profile_path.mkdir(parents=True, exist_ok=True)
    existing = connector_endpoint(profile_path)
    if existing:
        return {
            "slot": int(profile["slot"]),
            "profile_path": str(profile_path),
            "status": "already_open",
            "connector_ready": True,
        }

    # A live non-connector Chrome cannot be attached safely.  Do not attempt a
    # second launch against its locked user-data directory.
    if (profile_path / "SingletonLock").exists():
        raise RuntimeError(
            f"프로필 {profile['slot']}이 연결기 없이 이미 열려 있습니다. "
            "해당 전용 창만 닫은 뒤 다시 열어주세요."
        )

    chrome = find_chrome_executable()
    target_url = str(profile.get("write_url") or profile.get("blog_url") or "https://www.naver.com")
    creationflags = 0
    if os.name == "nt":
        creationflags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) | int(
            getattr(subprocess, "DETACHED_PROCESS", 0)
        )
    process = subprocess.Popen(
        [
            str(chrome),
            f"--user-data-dir={profile_path}",
            "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=0",
            "--no-first-run",
            "--no-default-browser-check",
            "--new-window",
            target_url,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=creationflags,
        start_new_session=os.name != "nt",
    )
    deadline = time.monotonic() + max(3.0, float(timeout_seconds))
    while time.monotonic() < deadline:
        endpoint = connector_endpoint(profile_path)
        if endpoint:
            return {
                "slot": int(profile["slot"]),
                "profile_path": str(profile_path),
                "status": "opened",
                "connector_ready": True,
                "process_id": int(process.pid),
            }
        if process.poll() is not None:
            break
        time.sleep(0.2)
    raise RuntimeError(f"프로필 {profile['slot']} Chrome 연결기가 준비되지 않았습니다.")


def connect_profile_context(playwright: Any, profile_path: str | Path) -> tuple[Any, Any] | None:
    """Attach to an open profile and return ``(browser, context)``."""

    endpoint = connector_endpoint(profile_path)
    if not endpoint:
        return None
    browser = playwright.chromium.connect_over_cdp(endpoint)
    if not browser.contexts:
        raise RuntimeError("열린 Chrome 프로필의 브라우저 컨텍스트를 찾지 못했습니다.")
    return browser, browser.contexts[0]
