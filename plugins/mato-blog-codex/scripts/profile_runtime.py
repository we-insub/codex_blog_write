"""Non-sensitive status exchange for one owned persistent profile host."""

from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path
from typing import Any, Mapping

try:
    from .mato_common import extract_naver_blog_id
except ImportError:  # Direct execution from the scripts directory.
    from mato_common import extract_naver_blog_id


RUNTIME_DIRECTORY = "profile_runtime"
RUNTIME_STATUSES = {"starting", "ready", "logged_in", "needs_login", "error"}


def profile_runtime_path(profile_path: str | Path) -> Path:
    """Return this project's status file for one numbered browser profile."""

    profile = Path(profile_path).expanduser().resolve(strict=False)
    return profile.parent.parent / RUNTIME_DIRECTORY / f"{profile.name}.json"


def _validated_blog_id(value: object) -> str:
    """Accept only a bare public blog ID, never a URL or arbitrary text."""

    if not isinstance(value, str):
        raise ValueError("profile runtime blog_id must be a string")
    if value and extract_naver_blog_id(value) != value:
        raise ValueError("profile runtime blog_id must be a bare blog ID")
    return value


def write_profile_state(
    profile_path: str | Path,
    *,
    status: str,
    pid: int | None = None,
    blog_id: str | None = None,
) -> None:
    """Atomically write status and optional public target ID, never auth data."""

    if status not in RUNTIME_STATUSES:
        raise ValueError(f"unsupported profile runtime status: {status}")
    public_blog_id = _validated_blog_id(blog_id) if blog_id is not None else ""
    destination = profile_runtime_path(profile_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "profile": Path(profile_path).name,
        "pid": int(pid if pid is not None else os.getpid()),
        "status": status,
        "updated_at": time.time(),
    }
    if public_blog_id:
        payload["blog_id"] = public_blog_id
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, destination)


def read_profile_state(profile_path: str | Path, *, max_age_seconds: float = 15.0) -> dict[str, Any] | None:
    """Read a fresh host status without opening or attaching to Chrome."""

    try:
        raw = json.loads(profile_runtime_path(profile_path).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(raw, Mapping):
        return None
    expected_profile = Path(profile_path).expanduser().resolve(strict=False).name
    if raw.get("profile") != expected_profile:
        return None
    pid = raw.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or not 0 < pid <= 2_147_483_647:
        return None
    status = str(raw.get("status") or "")
    if status not in RUNTIME_STATUSES:
        return None
    try:
        blog_id = _validated_blog_id(raw.get("blog_id", ""))
    except ValueError:
        return None
    try:
        updated_at = float(raw.get("updated_at"))
        max_age = float(max_age_seconds)
    except (TypeError, ValueError):
        return None
    age = time.time() - updated_at
    if not math.isfinite(age) or not math.isfinite(max_age) or not 0 <= age <= max_age:
        return None
    # The PID is metadata, not a liveness probe. In particular, os.kill(pid, 0)
    # is not a portable read-only check on Windows. The connector verifies CDP.
    return {"status": status, "pid": pid, "updated_at": updated_at, "blog_id": blog_id}


def clear_profile_state(profile_path: str | Path) -> None:
    """Remove a host status file after its visible browser closes."""

    try:
        profile_runtime_path(profile_path).unlink()
    except FileNotFoundError:
        pass
