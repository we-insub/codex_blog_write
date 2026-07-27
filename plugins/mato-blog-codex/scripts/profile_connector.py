"""Keep numbered Chrome profiles open and reconnect Playwright to them.

Chrome owns the persistent user-data directory for the lifetime of the visible
window.  Later profile checks and uploads attach over a loopback-only DevTools
endpoint instead of trying to launch the same locked profile a second time.
No cookies, credentials, or browser storage values are copied or printed.
"""

from __future__ import annotations

import os
import platform
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Mapping


DEVTOOLS_ACTIVE_PORT = "DevToolsActivePort"


def chrome_executable_candidates(system: str | None = None) -> tuple[Path, ...]:
    """Return platform-specific Google Chrome locations in preference order."""

    system = system or platform.system()
    candidates: list[Path] = []
    if system == "Windows":
        for env_name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            root = os.environ.get(env_name)
            if root:
                candidates.append(Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe")
    elif system == "Darwin":
        candidates.extend(
            (
                Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            )
        )
    else:
        candidates.extend(
            (
                Path("/usr/bin/google-chrome"),
                Path("/usr/bin/google-chrome-stable"),
                Path("/snap/bin/chromium"),
            )
        )
    return tuple(candidates)


def find_chrome_executable() -> Path:
    """Return the installed Google Chrome executable on supported desktops."""

    system = platform.system()
    candidates = chrome_executable_candidates(system)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError(f"{system}에서 설치된 Google Chrome을 찾지 못했습니다.")


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


def clear_stale_macos_profile_lock(profile_path: Path) -> bool:
    """Remove only a dead macOS Chrome instance lock for this exact profile."""

    if platform.system() != "Darwin":
        return False
    lock = profile_path / "SingletonLock"
    if not lock.is_symlink():
        return False
    try:
        target = os.readlink(lock)
        pid = int(target.rsplit("-", 1)[1])
        os.kill(pid, 0)
    except ProcessLookupError:
        pass
    except (OSError, ValueError, IndexError):
        return False
    else:
        return False

    for filename in ("SingletonLock", "SingletonCookie", "SingletonSocket", DEVTOOLS_ACTIVE_PORT):
        candidate = profile_path / filename
        try:
            candidate.unlink()
        except FileNotFoundError:
            continue
    return True


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
    lock = profile_path / "SingletonLock"
    if os.path.lexists(lock) and clear_stale_macos_profile_lock(profile_path):
        lock = profile_path / "SingletonLock"
    if os.path.lexists(lock):
        raise RuntimeError(
            f"프로필 {profile['slot']}이 연결기 없이 이미 열려 있습니다. "
            "해당 전용 창만 닫은 뒤 다시 열어주세요."
        )

    target_url = str(profile.get("write_url") or profile.get("blog_url") or "https://www.naver.com")
    chrome = find_chrome_executable()
    chrome_args = [
        f"--user-data-dir={profile_path}",
        "--remote-debugging-address=127.0.0.1",
        "--remote-debugging-port=0",
        "--no-first-run",
        "--no-default-browser-check",
        "--new-window",
        target_url,
    ]
    creationflags = 0
    if os.name == "nt":
        creationflags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) | int(
            getattr(subprocess, "DETACHED_PROCESS", 0)
        )
    if platform.system() == "Darwin":
        # macOS can terminate a directly spawned app-bundle executable after it
        # forwards the startup request. ``open -na`` keeps this profile in its
        # own Chrome app instance while retaining the requested data directory.
        command = ["/usr/bin/open", "-na", "Google Chrome", "--args", *chrome_args]
        exits_after_launch = True
    else:
        command = [str(chrome), *chrome_args]
        exits_after_launch = False
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=creationflags,
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
        if not exits_after_launch and process.poll() is not None:
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
