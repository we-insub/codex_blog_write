"""Keep numbered Chrome profiles open and reconnect Playwright to them.

Chrome owns the persistent user-data directory for the lifetime of the visible
window.  Later profile checks and uploads attach over a loopback-only DevTools
endpoint instead of trying to launch the same locked profile a second time.
No cookies, credentials, or browser storage values are copied or printed.
"""

from __future__ import annotations

import errno
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit
from urllib.request import urlopen


DEVTOOLS_ACTIVE_PORT = "DevToolsActivePort"
PROFILE_LOCK_NAMES = ("SingletonLock", "lockfile")


def is_windows() -> bool:
    """Return whether the current host uses Windows process semantics."""

    return os.name == "nt"


def _pid_is_alive(pid: int) -> bool:
    """Return whether *pid* still identifies a process on this POSIX host."""

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return False
        return True
    return True


def _posix_singleton_lock_is_active(lock_path: Path) -> bool:
    """Classify Chromium's macOS/POSIX ``SingletonLock`` entry."""

    if not os.path.lexists(os.fspath(lock_path)):
        return False
    if lock_path.is_symlink():
        try:
            target = os.readlink(lock_path)
        except FileNotFoundError:
            return False
        except OSError:
            return True
        try:
            hostname, pid_text = Path(target).name.rsplit("-", 1)
            pid = int(pid_text)
        except (TypeError, ValueError):
            return True
        if not hostname or pid <= 0 or pid > 2_147_483_647:
            return True
        if hostname.casefold() != socket.gethostname().casefold():
            # A foreign-host lock may belong to a profile on shared storage.
            return True
        return _pid_is_alive(pid)

    # Chromium normally uses a symlink on macOS. Retain compatibility with an
    # older regular-file lock by attempting a non-blocking advisory lock.
    try:
        import fcntl

        descriptor = os.open(os.fspath(lock_path), os.O_RDWR)
    except FileNotFoundError:
        return False
    except (ImportError, OSError):
        return True
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            return True
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        return False
    finally:
        os.close(descriptor)


def _windows_lockfile_is_active(lock_path: Path) -> bool:
    """Classify Chromium's Windows ``lockfile`` using its open sharing state."""

    if not os.path.lexists(os.fspath(lock_path)):
        return False
    flags = os.O_RDWR | int(getattr(os, "O_BINARY", 0))
    try:
        descriptor = os.open(os.fspath(lock_path), flags)
    except FileNotFoundError:
        return False
    except PermissionError:
        # Chrome keeps its live lock open with incompatible Windows sharing.
        return True
    except OSError as exc:
        if exc.errno == errno.ENOENT or getattr(exc, "winerror", None) in {2, 3}:
            return False
        return True
    os.close(descriptor)
    return False


def profile_lock_exists(profile_path: str | Path) -> bool:
    """Return whether an active Chrome process owns this profile's lock."""

    directory = Path(profile_path)
    if is_windows():
        return _windows_lockfile_is_active(directory / "lockfile")
    return _posix_singleton_lock_is_active(directory / "SingletonLock")


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
        lines = active_port_file.read_text(encoding="utf-8").splitlines()
        port = int(lines[0].strip())
        websocket_path = lines[1].strip()
    except (OSError, ValueError, IndexError):
        return None
    if port <= 0 or port > 65535 or not websocket_path.startswith("/devtools/browser/"):
        return None
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            pass
    except OSError:
        return None
    try:
        with urlopen(f"http://127.0.0.1:{port}/json/version", timeout=0.75) as response:
            payload = json.loads(response.read().decode("utf-8"))
        websocket_url = urlsplit(str(payload.get("webSocketDebuggerUrl") or ""))
        if (
            websocket_url.scheme != "ws"
            or websocket_url.hostname not in {"127.0.0.1", "localhost", "::1"}
            or websocket_url.port != port
            or websocket_url.path != websocket_path
        ):
            return None
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    return f"http://127.0.0.1:{port}"


def open_profile_browser(
    profile: Mapping[str, Any],
    *,
    timeout_seconds: float = 20.0,
) -> dict[str, Any]:
    """Open a visible persistent Chrome window and leave it running."""

    profile_path = Path(str(profile["profile_path"])).expanduser().resolve(strict=False)
    if not profile_path.is_dir():
        raise RuntimeError(
            f"프로필 {profile['slot']} 폴더가 없습니다. 먼저 프로필을 생성해 주세요: {profile_path}"
        )
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
    if profile_lock_exists(profile_path):
        raise RuntimeError(
            f"프로필 {profile['slot']}이 연결기 없이 이미 열려 있습니다. "
            "해당 전용 창만 닫은 뒤 다시 열어주세요."
        )

    target_url = str(profile.get("write_url") or profile.get("blog_url") or "https://www.naver.com")
    windows = is_windows()
    creationflags = 0
    if windows:
        creationflags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) | int(
            getattr(subprocess, "DETACHED_PROCESS", 0)
        )
    process = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).with_name("profile_host.py")),
            "--profile-path",
            str(profile_path),
            "--target-url",
            target_url,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=creationflags,
        start_new_session=not windows,
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
    # This is a user's persistent default Chrome context, not a Playwright-
    # created incognito context. Chrome 150+ rejects Playwright's default
    # ``Browser.setDownloadBehavior`` override for that context. Keeping the
    # browser defaults also avoids changing focus/media behavior in the visible
    # login window while preserving normal page, cookie, and editor access.
    browser = playwright.chromium.connect_over_cdp(
        endpoint,
        is_local=True,
        no_defaults=True,
    )
    if not browser.contexts:
        raise RuntimeError("열린 Chrome 프로필의 브라우저 컨텍스트를 찾지 못했습니다.")
    return browser, browser.contexts[0]
