"""Non-sensitive status exchange for one owned persistent profile host."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Mapping


RUNTIME_DIRECTORY = "profile_runtime"
RUNTIME_STATUSES = {"starting", "ready", "needs_login", "error"}


def profile_runtime_path(profile_path: str | Path) -> Path:
    """Return this project's status file for one numbered browser profile."""

    profile = Path(profile_path).expanduser().resolve(strict=False)
    return profile.parent.parent / RUNTIME_DIRECTORY / f"{profile.name}.json"


def write_profile_state(profile_path: str | Path, *, status: str, pid: int | None = None) -> None:
    """Atomically write status only; authentication data is never included."""

    if status not in RUNTIME_STATUSES:
        raise ValueError(f"unsupported profile runtime status: {status}")
    destination = profile_runtime_path(profile_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "profile": Path(profile_path).name,
        "pid": int(pid if pid is not None else os.getpid()),
        "status": status,
        "updated_at": time.time(),
    }
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
    status = str(raw.get("status") or "")
    if status not in RUNTIME_STATUSES:
        return None
    try:
        updated_at = float(raw.get("updated_at"))
    except (TypeError, ValueError):
        return None
    if time.time() - updated_at > max_age_seconds:
        return None
    return {"status": status, "pid": raw.get("pid"), "updated_at": updated_at}


def clear_profile_state(profile_path: str | Path) -> None:
    """Remove a host status file after its visible browser closes."""

    try:
        profile_runtime_path(profile_path).unlink()
    except FileNotFoundError:
        pass
