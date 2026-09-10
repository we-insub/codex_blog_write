"""Manage local Naver Chrome profiles without storing credentials.

Metadata is kept in ``~/.googleblog/mato-blog-codex/naver_profiles.json`` while
Chrome user data lives in ``~/.googleblog/mato-blog-codex/browser_profiles/naver_N``. Browser
automation is imported lazily and is used only for an explicit
``check --login`` command, which always opens a visible Chrome window for the
user to complete login manually.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

try:
    from .mato_common import (
        SCHEMA_VERSION,
        atomic_write_json,
        browser_profiles_dir,
        canonical_naver_blog_url,
        canonical_naver_write_url,
        extract_naver_blog_id,
        parse_positive_slots,
        profile_catalog_path,
        read_json,
        redact_text,
        timestamp_kst,
    )
    from .profile_connector import (
        connector_endpoint,
        open_profile_browser,
        profile_lock_exists,
    )
    from .profile_runtime import read_profile_state
except ImportError:  # Direct execution: ``python scripts/profiles.py``.
    from mato_common import (  # type: ignore[no-redef]
        SCHEMA_VERSION,
        atomic_write_json,
        browser_profiles_dir,
        canonical_naver_blog_url,
        canonical_naver_write_url,
        extract_naver_blog_id,
        parse_positive_slots,
        profile_catalog_path,
        read_json,
        redact_text,
        timestamp_kst,
    )
    from profile_connector import (  # type: ignore[no-redef]
        connector_endpoint,
        open_profile_browser,
        profile_lock_exists,
    )
    from profile_runtime import read_profile_state  # type: ignore[no-redef]


PROFILE_DIR_RE = re.compile(r"^naver_([1-9][0-9]*)$", re.IGNORECASE)
VOICE_STATUSES = {"unset", "pending", "ready", "error"}
LOGIN_STATUSES = {"unknown", "logged_in", "ready", "needs_login", "profile_missing", "error"}
NAVER_HOME_URL = "https://www.naver.com"
NAVER_LOGIN_URL = "https://nid.naver.com/nidlogin.login"


def profile_path_for_slot(slot: int) -> Path:
    """Return the persistent browser directory for a positive profile slot."""

    if slot <= 0:
        raise ValueError("프로필 번호는 1 이상의 정수여야 합니다.")
    return browser_profiles_dir() / f"naver_{slot}"


def _allowed_profile_paths(slot: int) -> tuple[Path, ...]:
    """Return the one Codex-only persistent profile path for a numbered slot.

    Mato Blog Codex does not load, copy, or modify Mato Helper's browser data.
    Its own numbered Chrome user-data folders retain the Naver session until
    the user explicitly initializes or deletes that Codex profile.
    """

    return (profile_path_for_slot(slot),)


def _path_key(path: str | Path) -> str:
    """Return a cross-platform lexical key without following links."""

    return os.path.normcase(os.path.abspath(os.fspath(Path(path).expanduser())))


def _is_link_or_junction(path: Path) -> bool:
    """Reject profile-folder redirects on POSIX and Windows."""

    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction and is_junction())
    except OSError:
        return True


def _safe_profile_path(slot: int, raw_path: object | None = None) -> Path:
    """Return the exact Codex-only local path permitted for a profile slot."""

    if raw_path:
        try:
            requested = Path(str(raw_path)).expanduser()
            for candidate in _allowed_profile_paths(slot):
                if _path_key(requested) == _path_key(candidate):
                    return candidate
        except (OSError, RuntimeError, ValueError):
            pass
    return profile_path_for_slot(slot)


def _is_safe_profile_directory(path: Path, slot: int | None = None) -> bool:
    """Return whether an existing directory is one permitted local profile path."""

    try:
        candidate = path.expanduser()
        if not candidate.is_dir() or _is_link_or_junction(candidate):
            return False
        name = candidate.name
        if slot is None:
            numbered = PROFILE_DIR_RE.fullmatch(name)
            if not numbered:
                return False
            slot = int(numbered.group(1))
        expected = profile_path_for_slot(slot)
        if _path_key(candidate) != _path_key(expected):
            return False
        resolved = candidate.resolve(strict=True)
        root = browser_profiles_dir().resolve(strict=True)
        if resolved != root / f"naver_{slot}":
            return False
        numbered = re.fullmatch(r"naver_([1-9][0-9]*)", name, re.IGNORECASE)
        return bool(numbered and int(numbered.group(1)) == slot)
    except (OSError, RuntimeError, ValueError):
        return False


def _empty_voice() -> dict[str, Any]:
    """Return the default voice-analysis metadata."""

    return {
        "status": "unset",
        "summary": "",
        "sample_count": 0,
        "updated_at": None,
    }


def _new_profile(slot: int, *, source: str = "created") -> dict[str, Any]:
    """Return a credential-free profile metadata record."""

    now = timestamp_kst()
    profile_path = profile_path_for_slot(slot)
    return {
        "slot": slot,
        "name": f"naver_{slot}",
        "account_alias": "",
        "blog_url": "",
        "write_url": "",
        "profile_path": str(profile_path),
        "profile_exists": _is_safe_profile_directory(profile_path),
        "login_status": "unknown",
        "last_checked_at": None,
        "source": source,
        "created_at": now,
        "updated_at": now,
        "voice": _empty_voice(),
    }


def _normalize_profile(raw: Mapping[str, Any], slot_hint: int | None = None) -> dict[str, Any]:
    """Normalize profile metadata while discarding unknown secret fields."""

    raw_slot = raw.get("slot", slot_hint)
    try:
        slot = int(raw_slot)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"프로필 번호가 올바르지 않습니다: {raw_slot}") from exc
    if slot <= 0:
        raise ValueError("프로필 번호는 1 이상의 정수여야 합니다.")

    profile = _new_profile(slot, source=str(raw.get("source") or "created"))
    alias = raw.get("account_alias", raw.get("alias", ""))
    profile["account_alias"] = redact_text(alias).strip() if alias is not None else ""

    blog_value = str(raw.get("blog_url") or "").strip()
    if blog_value:
        profile["blog_url"] = canonical_naver_blog_url(blog_value)
        profile["write_url"] = canonical_naver_write_url(blog_value)

    status = str(raw.get("login_status") or "unknown")
    profile["login_status"] = status if status in LOGIN_STATUSES else "unknown"
    profile["last_checked_at"] = raw.get("last_checked_at") or None
    profile["created_at"] = raw.get("created_at") or profile["created_at"]
    profile["updated_at"] = raw.get("updated_at") or profile["updated_at"]

    raw_voice = raw.get("voice") if isinstance(raw.get("voice"), Mapping) else {}
    voice_status = str(raw_voice.get("status") or "unset")
    try:
        sample_count = max(0, int(raw_voice.get("sample_count") or 0))
    except (TypeError, ValueError):
        sample_count = 0
    profile["voice"] = {
        "status": voice_status if voice_status in VOICE_STATUSES else "unset",
        "summary": redact_text(raw_voice.get("summary") or "").strip(),
        "sample_count": sample_count,
        "updated_at": raw_voice.get("updated_at") or None,
    }
    # Only the exact Codex-owned slot path is accepted. Catalog edits or an
    # older installation can never redirect a slot into another program's
    # Chrome data directory.
    selected_path = _safe_profile_path(slot, raw.get("profile_path"))
    profile["profile_path"] = str(selected_path)
    profile["profile_exists"] = _is_safe_profile_directory(selected_path, slot)
    profile["name"] = f"naver_{slot}"
    return profile


def _profiles_from_raw(raw: Any) -> list[dict[str, Any]]:
    """Read list and older slot-keyed profile catalog shapes."""

    if raw is None:
        return []
    profiles_raw = raw.get("profiles", []) if isinstance(raw, Mapping) else raw
    normalized: list[dict[str, Any]] = []
    if isinstance(profiles_raw, Mapping):
        for key, value in profiles_raw.items():
            if not isinstance(value, Mapping):
                continue
            try:
                slot_hint = int(key)
            except (TypeError, ValueError):
                name_match = PROFILE_DIR_RE.fullmatch(str(key))
                slot_hint = int(name_match.group(1)) if name_match else None
            normalized.append(_normalize_profile(value, slot_hint))
    elif isinstance(profiles_raw, list):
        for value in profiles_raw:
            if isinstance(value, Mapping):
                normalized.append(_normalize_profile(value))
    else:
        raise ValueError("naver_profiles.json의 profiles는 배열 또는 객체여야 합니다.")

    by_slot = {int(profile["slot"]): profile for profile in normalized}
    return [by_slot[slot] for slot in sorted(by_slot)]


def save_catalog(profiles: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Atomically save a sorted, credential-free profile catalog."""

    normalized = [_normalize_profile(profile) for profile in profiles]
    by_slot = {int(profile["slot"]): profile for profile in normalized}
    catalog = {
        "schema_version": SCHEMA_VERSION,
        "updated_at": timestamp_kst(),
        "profiles": [by_slot[slot] for slot in sorted(by_slot)],
    }
    atomic_write_json(profile_catalog_path(), catalog)
    return catalog


def discover_existing_profiles(
    profiles: Iterable[Mapping[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[int]]:
    """Discover Codex-only numbered Chrome profile folders by slot."""

    current = {
        int(profile["slot"]): _normalize_profile(profile)
        for profile in (profiles or [])
    }
    discovered: list[int] = []
    root = browser_profiles_dir()
    if root.exists():
        for path in root.iterdir():
            if not _is_safe_profile_directory(path):
                continue
            match = PROFILE_DIR_RE.fullmatch(path.name)
            if not match:
                continue
            slot = int(match.group(1))
            if slot not in current:
                current[slot] = _new_profile(slot, source="discovered")
                discovered.append(slot)
    return [current[slot] for slot in sorted(current)], sorted(discovered)


def load_catalog(*, discover: bool = True, persist_discovery: bool = True) -> dict[str, Any]:
    """Load the catalog and optionally discover/persist existing directories."""

    path = profile_catalog_path()
    raw = read_json(path, {})
    profiles = _profiles_from_raw(raw)
    discovered_slots: list[int] = []
    if discover:
        profiles, discovered_slots = discover_existing_profiles(profiles)
    if discovered_slots and persist_discovery:
        return save_catalog(profiles)
    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": raw.get("updated_at") if isinstance(raw, Mapping) else None,
        "profiles": profiles,
    }


def get_profile(slot: int, *, catalog: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return one registered profile or raise a useful error."""

    source = catalog or load_catalog()
    for profile in source.get("profiles", []):
        if int(profile["slot"]) == slot:
            return dict(profile)
    raise ValueError(f"프로필 {slot}번이 등록되어 있지 않습니다.")


def add_profile(
    slot: int | None = None,
    *,
    alias: str = "",
    blog_url: str = "",
    replace: bool = False,
) -> dict[str, Any]:
    """Register a slot and create its browser directory without opening Chrome."""

    catalog = load_catalog()
    profiles = [dict(profile) for profile in catalog["profiles"]]
    used = {int(profile["slot"]) for profile in profiles}
    if slot is None:
        slot = 1
        while slot in used:
            slot += 1
    if slot <= 0:
        raise ValueError("프로필 번호는 1 이상의 정수여야 합니다.")

    existing_index = next(
        (index for index, profile in enumerate(profiles) if int(profile["slot"]) == slot),
        None,
    )
    if existing_index is not None:
        existing = profiles[existing_index]
        can_enrich_discovered = (
            existing.get("source") == "discovered"
            and not existing.get("account_alias")
            and not existing.get("blog_url")
        )
        if not replace and not can_enrich_discovered:
            raise ValueError(f"프로필 {slot}번이 이미 등록되어 있습니다. 수정은 edit을 사용하세요.")
        profile = existing
    else:
        profile = _new_profile(slot)

    if alias:
        profile["account_alias"] = redact_text(alias).strip()
    if blog_url:
        profile["blog_url"] = canonical_naver_blog_url(blog_url)
        profile["write_url"] = canonical_naver_write_url(blog_url)
    profile["source"] = "created"
    profile["updated_at"] = timestamp_kst()
    profile_path = profile_path_for_slot(slot)
    existed_before = _is_safe_profile_directory(profile_path, slot)
    profile_path.mkdir(parents=True, exist_ok=True)
    profile["profile_exists"] = _is_safe_profile_directory(profile_path, slot)
    if not profile["profile_exists"]:
        raise ValueError("프로필 폴더가 브라우저 프로필 루트 밖을 가리켜 등록을 중단했습니다.")
    if not existed_before:
        profile["login_status"] = "needs_login"
        profile["last_checked_at"] = None

    if existing_index is None:
        profiles.append(profile)
    else:
        profiles[existing_index] = profile
    save_catalog(profiles)
    return _normalize_profile(profile)


def edit_profile(
    slot: int,
    *,
    alias: str | None = None,
    blog_url: str | None = None,
) -> dict[str, Any]:
    """Edit non-secret display metadata for a registered profile."""

    catalog = load_catalog()
    profiles = [dict(profile) for profile in catalog["profiles"]]
    for index, profile in enumerate(profiles):
        if int(profile["slot"]) != slot:
            continue
        if alias is not None:
            profile["account_alias"] = redact_text(alias).strip()
        if blog_url is not None:
            clean_url = blog_url.strip()
            profile["blog_url"] = canonical_naver_blog_url(clean_url) if clean_url else ""
            profile["write_url"] = canonical_naver_write_url(clean_url) if clean_url else ""
        profile["updated_at"] = timestamp_kst()
        profiles[index] = profile
        save_catalog(profiles)
        return _normalize_profile(profile)
    raise ValueError(f"프로필 {slot}번이 등록되어 있지 않습니다.")


def adopt_profile_path(slot: int, profile_path: str | Path) -> dict[str, Any]:
    """Select an existing, safe local Chrome profile for this slot.

    No browser data is copied, read, or changed.  This only records which
    supported local user-data directory the bundled uploader must open.
    """

    selected = _safe_profile_path(slot, profile_path)
    requested = Path(profile_path).expanduser()
    if _path_key(selected) != _path_key(requested):
        raise ValueError("프로필 경로는 해당 슬롯의 mato-blog-codex/browser_profiles/naver_N 경로여야 합니다.")
    if not _is_safe_profile_directory(selected, slot):
        raise ValueError(f"안전한 기존 프로필 폴더가 아닙니다: {selected}")

    catalog = load_catalog()
    profiles = [dict(profile) for profile in catalog["profiles"]]
    for index, profile in enumerate(profiles):
        if int(profile["slot"]) != slot:
            continue
        profile["profile_path"] = str(selected)
        profile["profile_exists"] = True
        profile["source"] = "adopted"
        profile["updated_at"] = timestamp_kst()
        profiles[index] = profile
        save_catalog(profiles)
        return _normalize_profile(profile)
    raise ValueError(f"프로필 {slot}번이 등록되어 있지 않습니다.")


def reset_profile(slot: int, *, confirmation: str) -> dict[str, Any]:
    """Reset only one Codex-owned browser profile after exact confirmation."""

    expected_confirmation = f"RESET-{slot}"
    if confirmation != expected_confirmation:
        raise ValueError(f"프로필 초기화 확인값은 정확히 {expected_confirmation} 이어야 합니다.")

    catalog = load_catalog()
    get_profile(slot, catalog=catalog)
    profile_path = profile_path_for_slot(slot)
    if connector_endpoint(profile_path) or profile_lock_exists(profile_path):
        raise RuntimeError(
            f"프로필 {slot} Chrome이 열려 있거나 잠겨 있습니다. "
            "작성 중인 글을 확인하고 해당 전용 창을 정상 종료한 뒤 다시 시도하세요."
        )

    profile_existed = os.path.lexists(os.fspath(profile_path))
    if profile_existed and not _is_safe_profile_directory(profile_path, slot):
        raise ValueError(f"초기화할 프로필 경로가 안전하지 않습니다: {profile_path}")

    # Commit the conservative catalog state before changing browser bytes. If
    # this atomic write fails, the existing Chrome profile remains untouched
    # and cannot be paired with a stale ``ready`` status after a partial reset.
    profiles = [dict(profile) for profile in catalog["profiles"]]
    updated: dict[str, Any] | None = None
    for index, profile in enumerate(profiles):
        if int(profile["slot"]) != slot:
            continue
        profile["profile_path"] = str(profile_path)
        profile["profile_exists"] = True
        profile["login_status"] = "needs_login"
        profile["last_checked_at"] = None
        profile["source"] = "reset"
        profile["updated_at"] = timestamp_kst()
        profiles[index] = profile
        saved = save_catalog(profiles)
        updated = get_profile(slot, catalog=saved)
        break
    if updated is None:
        raise ValueError(f"프로필 {slot}번이 등록되어 있지 않습니다.")

    backup_path: Path | None = None
    if profile_existed:
        backup_path = profile_path.parent / (
            f".{profile_path.name}.reset-{os.getpid()}-{time.time_ns()}"
        )
        profile_path.replace(backup_path)

    try:
        profile_path.mkdir(parents=True, exist_ok=False)
        if not _is_safe_profile_directory(profile_path, slot):
            raise RuntimeError("새 프로필 폴더의 안전성을 확인하지 못했습니다.")
        if backup_path is not None:
            shutil.rmtree(backup_path)
    except Exception as exc:
        try:
            if profile_path.is_dir() and not any(profile_path.iterdir()):
                profile_path.rmdir()
            if backup_path is not None and backup_path.exists() and not profile_path.exists():
                backup_path.replace(profile_path)
        except OSError:
            pass
        raise RuntimeError(f"프로필 {slot} 초기화를 완료하지 못했습니다: {exc}") from exc

    # Re-normalize the derived folder flag after creating the fresh directory;
    # the already-saved login state remains ``needs_login`` even if a later
    # metadata operation is interrupted.
    updated = _normalize_profile(updated)
    return {
        "slot": slot,
        "name": updated["name"],
        "account_alias": updated["account_alias"],
        "blog_url": updated["blog_url"],
        "profile_path": updated["profile_path"],
        "profile_exists": updated["profile_exists"],
        "login_status": updated["login_status"],
        "reset": True,
    }


def _is_login_page(page: Any) -> bool:
    """Return whether the current Playwright page is a Naver login screen."""

    current_url = str(getattr(page, "url", "")).lower()
    if "nidlogin" in current_url or "nid.naver.com" in current_url:
        return True
    try:
        return page.locator("input#id, input[name='id']").count() > 0
    except Exception:
        return False


def _is_access_restricted(page: Any) -> bool:
    """Detect CAPTCHA/access restriction screens without attempting a bypass."""

    current_url = str(getattr(page, "url", "")).lower()
    if "captcha" in current_url:
        return True
    try:
        body_text = page.locator("body").inner_text(timeout=1_000).lower()
    except Exception:
        return False
    return any(
        marker in body_text
        for marker in (
            "captcha",
            "자동입력 방지",
            "비정상적인 접근",
            "접근이 제한",
            "보안 확인",
        )
    )


def _is_editor_ready(page: Any) -> bool:
    """Return whether actual editable controls, not just a URL, are present."""

    current_url = str(getattr(page, "url", "")).lower()
    if _is_login_page(page) or _is_access_restricted(page):
        return False
    selectors = (
        ".se-documentTitle, .se-title-text, "
        "[contenteditable='true']"
    )
    scopes = [page]
    try:
        if page.locator("iframe#mainFrame").count() > 0:
            scopes.insert(0, page.frame_locator("iframe#mainFrame"))
    except Exception:
        pass
    for scope in scopes:
        try:
            if scope.locator(selectors).count() > 0:
                return True
        except Exception:
            continue
    # A redirect-style write URL can remain in the address bar while Naver is
    # still showing a login form.  Never treat its URL alone as authentication.
    return False


def _profile_check_urls(profile: Mapping[str, Any]) -> tuple[str, str | None]:
    """Return the safe visible navigation sequence for a profile check.

    A configured writing profile always opens the Naver home page first. This
    lets Chrome restore its own local session before the editor redirect is
    evaluated. Session maintenance stays inside that persistent profile.
    """

    blog_url = str(profile.get("blog_url") or "")
    if not blog_url:
        return NAVER_LOGIN_URL, None
    write_url = str(profile.get("write_url") or canonical_naver_write_url(blog_url))
    return NAVER_HOME_URL, write_url


def _remaining_navigation_timeout_ms(deadline: float) -> int:
    """Return a bounded page-navigation timeout for an active check."""

    remaining_ms = int((deadline - time.monotonic()) * 1_000)
    if remaining_ms <= 0:
        raise TimeoutError("프로필 로그인 확인 시간이 만료되었습니다.")
    return min(60_000, max(3_000, remaining_ms))


def _interactive_login_check(profile: Mapping[str, Any], timeout_seconds: int) -> str:
    """Read login status from the Playwright process that owns the profile.

    Mato Helper launches a persistent context and performs login detection and
    cookie persistence inside that same context. This command follows the same
    ownership rule: it starts the dedicated host when needed, then polls only a
    non-sensitive status file. It never attaches a second Playwright process to
    the visible browser and therefore cannot close the user's profile window.
    """

    if timeout_seconds <= 0:
        raise ValueError("--timeout은 1초 이상이어야 합니다.")

    user_data_dir = Path(str(profile["profile_path"]))
    if not _is_safe_profile_directory(user_data_dir, int(profile["slot"])):
        raise RuntimeError(f"프로필 폴더가 없거나 안전하지 않습니다: {user_data_dir}")
    deadline = time.monotonic() + timeout_seconds

    print(
        "전용 Playwright 프로필에서 네이버 로그인 상태를 확인합니다. "
        "비밀번호는 이 프로그램에 입력하거나 저장하지 않습니다.",
        file=sys.stderr,
    )
    last_status = "starting"
    if connector_endpoint(user_data_dir) is None:
        open_profile_browser(profile)
    expected_blog_id = extract_naver_blog_id(str(profile["write_url"])) if profile.get("write_url") else ""
    while time.monotonic() < deadline:
        state = read_profile_state(user_data_dir)
        if state is not None:
            last_status = str(state["status"])
            if last_status == "ready":
                if connector_endpoint(user_data_dir) is None:
                    return "error"
                if str(state.get("blog_id") or "").casefold() != expected_blog_id.casefold():
                    return "error"
                return "ready"
            if last_status == "logged_in" and not profile.get("write_url"):
                return "logged_in" if connector_endpoint(user_data_dir) else "error"
            if last_status == "error":
                return "error"
        time.sleep(0.5)
    return last_status if last_status in {"needs_login", "logged_in"} else "error"


def open_profiles(profile_slots: str | Iterable[int | str]) -> list[dict[str, Any]]:
    """Open numbered profile Chrome windows and leave them available for reuse."""

    catalog = load_catalog()
    results: list[dict[str, Any]] = []
    for slot in parse_positive_slots(profile_slots):
        profile = get_profile(slot, catalog=catalog)
        profile_path = Path(str(profile["profile_path"]))
        if not _is_safe_profile_directory(profile_path):
            raise ValueError(f"프로필 폴더가 없습니다: {profile_path}")
        opened = open_profile_browser(profile)
        if opened.get("status") == "opened":
            # profile_host owns the persistent context and manages Naver's
            # keep-login option itself. A second short-lived attachment here
            # can tear down the host's browser connection on some Chrome builds.
            opened["keep_login_managed"] = True
        results.append(opened)
    return results


def check_profile(
    slot: int,
    *,
    login: bool = False,
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    """Check local profile presence, optionally opening visible manual login."""

    catalog = load_catalog()
    profile = get_profile(slot, catalog=catalog)
    path_exists = _is_safe_profile_directory(Path(profile["profile_path"]))
    result = {
        "slot": slot,
        "name": profile["name"],
        "account_alias": profile["account_alias"],
        "blog_url": profile["blog_url"],
        "profile_path": profile["profile_path"],
        "profile_path_exists": path_exists,
        "login_check_performed": login,
        "login_status": profile["login_status"] if login else "not_checked",
        "last_login_status": profile["login_status"],
        "last_checked_at": profile.get("last_checked_at"),
        "session_source": "plugin" if login else None,
    }
    if not login:
        if not path_exists:
            result["login_status"] = "profile_missing"
        return result
    if not path_exists:
        raise ValueError(f"프로필 폴더가 없습니다: {profile['profile_path']}")
    try:
        login_status = _interactive_login_check(profile, timeout_seconds)
    except Exception:
        login_status = "error"
        _update_login_status(slot, login_status)
        raise
    updated = _update_login_status(slot, login_status)
    result["login_status"] = updated["login_status"]
    result["last_checked_at"] = updated["last_checked_at"]
    return result


def _update_login_status(slot: int, status: str) -> dict[str, Any]:
    """Persist login status without persisting browser session material."""

    if status not in LOGIN_STATUSES:
        raise ValueError(f"알 수 없는 로그인 상태입니다: {status}")
    catalog = load_catalog()
    profiles = [dict(profile) for profile in catalog["profiles"]]
    for index, profile in enumerate(profiles):
        if int(profile["slot"]) == slot:
            profile["login_status"] = status
            profile["last_checked_at"] = timestamp_kst()
            profile["updated_at"] = profile["last_checked_at"]
            profiles[index] = profile
            save_catalog(profiles)
            return _normalize_profile(profile)
    raise ValueError(f"프로필 {slot}번이 등록되어 있지 않습니다.")


def round_robin_assign(
    items: Sequence[Any],
    profile_slots: str | Iterable[int | str],
    *,
    catalog: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Assign items to registered profiles in stable round-robin order."""

    slots = parse_positive_slots(profile_slots)
    source = catalog or load_catalog()
    profiles = {int(item["slot"]): dict(item) for item in source.get("profiles", [])}
    missing = [slot for slot in slots if slot not in profiles]
    if missing:
        joined = ", ".join(str(slot) for slot in missing)
        raise ValueError(f"등록되지 않은 프로필 번호: {joined}")

    assignments: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        slot = slots[index % len(slots)]
        profile = profiles[slot]
        assignments.append(
            {
                "index": index + 1,
                "item": item,
                "profile_slot": slot,
                "profile_name": profile["name"],
                "account_alias": profile["account_alias"],
                "blog_url": profile["blog_url"],
                "write_url": profile["write_url"],
            }
        )
    return assignments


def assign_count(count: int, profile_slots: str | Iterable[int | str]) -> list[dict[str, Any]]:
    """Create numbered variant assignments for a requested post count."""

    if count <= 0:
        raise ValueError("--count는 1 이상의 정수여야 합니다.")
    return round_robin_assign(list(range(1, count + 1)), profile_slots)


def voice_status(slot: int | None = None) -> list[dict[str, Any]]:
    """Return voice-analysis status without article samples or source bodies."""

    catalog = load_catalog()
    profiles = catalog["profiles"]
    if slot is not None:
        profiles = [get_profile(slot, catalog=catalog)]
    return [
        {
            "slot": int(profile["slot"]),
            "account_alias": profile["account_alias"],
            "blog_url": profile["blog_url"],
            **dict(profile.get("voice") or _empty_voice()),
        }
        for profile in profiles
    ]


def set_voice(
    slot: int,
    *,
    status: str,
    summary: str = "",
    sample_count: int = 0,
) -> dict[str, Any]:
    """Save a compact style summary; never save sampled article text."""

    if status not in VOICE_STATUSES:
        raise ValueError(f"문체 상태는 {', '.join(sorted(VOICE_STATUSES))} 중 하나여야 합니다.")
    if sample_count < 0:
        raise ValueError("표본 수는 0 이상이어야 합니다.")
    safe_summary = redact_text(summary).strip()
    if len(safe_summary) > 2_000:
        raise ValueError("문체 요약은 2,000자 이하여야 하며 원문을 포함하면 안 됩니다.")

    catalog = load_catalog()
    profiles = [dict(profile) for profile in catalog["profiles"]]
    for index, profile in enumerate(profiles):
        if int(profile["slot"]) != slot:
            continue
        profile["voice"] = {
            "status": status,
            "summary": safe_summary,
            "sample_count": sample_count,
            "updated_at": timestamp_kst(),
        }
        profile["updated_at"] = profile["voice"]["updated_at"]
        profiles[index] = profile
        save_catalog(profiles)
        return dict(profile["voice"])
    raise ValueError(f"프로필 {slot}번이 등록되어 있지 않습니다.")


def _resolve_slot(args: argparse.Namespace) -> int:
    """Resolve optional/positional slot CLI forms for compatibility."""

    slot = getattr(args, "slot", None)
    positional = getattr(args, "slot_pos", None)
    if slot is not None and positional is not None and slot != positional:
        raise ValueError("프로필 번호를 서로 다르게 두 번 지정했습니다.")
    resolved = slot if slot is not None else positional
    if resolved is None:
        raise ValueError("--slot으로 프로필 번호를 지정하세요.")
    return int(resolved)


def _profile_table(profiles: Sequence[Mapping[str, Any]]) -> str:
    """Render a user-facing profile table."""

    lines = [
        "| 번호 | 계정 별칭 | 블로그 URL | 프로필 폴더 | 마지막 로그인 확인 | 문체 상태 | 확인 시각 |",
        "|---:|---|---|---|---|---|---|",
    ]
    for profile in profiles:
        voice = profile.get("voice") if isinstance(profile.get("voice"), Mapping) else {}
        lines.append(
            "| {slot} | {alias} | {url} | {exists} | {login} | {voice} | {checked} |".format(
                slot=profile["slot"],
                alias=str(profile.get("account_alias") or "-").replace("|", "\\|"),
                url=str(profile.get("blog_url") or "-").replace("|", "\\|"),
                exists="있음" if profile.get("profile_exists") else "없음",
                login=profile.get("login_status") or "unknown",
                voice=voice.get("status") or "unset",
                checked=profile.get("last_checked_at") or "-",
            )
        )
    return "\n".join(lines)


def _assignment_table(assignments: Sequence[Mapping[str, Any]]) -> str:
    """Render a user-facing variant-to-profile assignment table."""

    lines = [
        "| 원고 | 프로필 | 계정 별칭 | 블로그 URL |",
        "|---:|---:|---|---|",
    ]
    for item in assignments:
        lines.append(
            f"| {item['item']} | {item['profile_slot']} | "
            f"{str(item.get('account_alias') or '-').replace('|', chr(92) + '|')} | "
            f"{str(item.get('blog_url') or '-').replace('|', chr(92) + '|')} |"
        )
    return "\n".join(lines)


def _add_slot_arguments(parser: argparse.ArgumentParser, *, positional: bool = True) -> None:
    """Add compatible positional and documented ``--slot`` arguments."""

    if positional:
        parser.add_argument("slot_pos", nargs="?", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--slot", type=int, help="프로필 번호")


def build_parser() -> argparse.ArgumentParser:
    """Build the profile command-line parser."""

    parser = argparse.ArgumentParser(description="Mato Blog Codex 네이버 프로필 관리")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    discover_parser = subparsers.add_parser("discover", help="기존 naver_N 폴더 자동 등록")
    discover_parser.add_argument("--json", action="store_true", help="JSON으로 출력")

    list_parser = subparsers.add_parser("list", help="등록 프로필 목록")
    list_parser.add_argument("--json", action="store_true", help="JSON으로 출력")

    add_parser = subparsers.add_parser("add", help="프로필 슬롯 등록")
    _add_slot_arguments(add_parser, positional=False)
    add_parser.add_argument("--alias", default="", help="사용자가 알아볼 계정 별칭")
    add_parser.add_argument("--blog-url", default="", help="네이버 블로그 ID 또는 URL")
    add_parser.add_argument("--replace", action="store_true", help="기존 메타데이터 교체")
    add_parser.add_argument("--json", action="store_true")

    edit_parser = subparsers.add_parser("edit", help="프로필 표시 정보 수정")
    _add_slot_arguments(edit_parser)
    edit_parser.add_argument("--alias", help="새 계정 별칭; 빈 문자열은 별칭 삭제")
    edit_parser.add_argument("--blog-url", help="새 네이버 블로그 ID/URL; 빈 문자열은 삭제")
    edit_parser.add_argument("--json", action="store_true")

    reset_parser = subparsers.add_parser("reset", help="프로필 로그인 데이터를 명시적으로 초기화")
    _add_slot_arguments(reset_parser)
    reset_parser.add_argument("--confirm", required=True, help="정확한 RESET-N 확인값")
    reset_parser.add_argument("--json", action="store_true")

    check_parser = subparsers.add_parser("check", help="프로필 폴더 또는 로그인 확인")
    _add_slot_arguments(check_parser)
    check_parser.add_argument(
        "--login",
        action="store_true",
        help="표시되는 Chrome에서 수동 로그인 확인(이 옵션에서만 브라우저 실행)",
    )
    check_parser.add_argument("--timeout", type=int, default=300, help="수동 로그인 대기 초")
    check_parser.add_argument("--json", action="store_true")

    open_parser = subparsers.add_parser("open", help="전용 Chrome 프로필 창을 계속 열어두기")
    open_parser.add_argument("--slots", required=True, help="예: 1,2")
    open_parser.add_argument("--json", action="store_true")

    assign_parser = subparsers.add_parser("assign", help="원고를 프로필에 라운드로빈 배정")
    assign_parser.add_argument("--profiles", required=True, help="예: 1,2,3")
    assign_parser.add_argument("--count", required=True, type=int, help="배정할 원고 수")
    assign_parser.add_argument("--json", action="store_true")

    voice_status_parser = subparsers.add_parser("voice-status", help="프로필 문체 분석 상태")
    _add_slot_arguments(voice_status_parser)
    voice_status_parser.add_argument("--json", action="store_true")

    voice_set_parser = subparsers.add_parser("voice-set", help="프로필 문체 요약 상태 저장")
    _add_slot_arguments(voice_set_parser)
    voice_set_parser.add_argument("--status", choices=sorted(VOICE_STATUSES), required=True)
    voice_set_parser.add_argument("--summary", default="", help="원문이 아닌 2,000자 이하 문체 요약")
    voice_set_parser.add_argument("--sample-count", type=int, default=0)
    voice_set_parser.add_argument("--json", action="store_true")
    return parser


def _print_value(value: Any, *, as_json: bool) -> None:
    """Print either JSON or a compact readable representation."""

    if as_json:
        print(json.dumps(value, ensure_ascii=False, indent=2))
    elif isinstance(value, str):
        print(value)
    else:
        print(json.dumps(value, ensure_ascii=False, indent=2))


def main(argv: Sequence[str] | None = None) -> int:
    """Run the profile command-line interface."""

    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.subcommand == "discover":
            raw = read_json(profile_catalog_path(), {})
            current = _profiles_from_raw(raw)
            profiles, discovered = discover_existing_profiles(current)
            save_catalog(profiles)
            result = {"discovered_slots": discovered, "profiles": profiles}
            _print_value(result if args.json else _profile_table(profiles), as_json=args.json)
        elif args.subcommand == "list":
            profiles = load_catalog()["profiles"]
            _print_value(profiles if args.json else _profile_table(profiles), as_json=args.json)
        elif args.subcommand == "add":
            profile = add_profile(
                args.slot,
                alias=args.alias,
                blog_url=args.blog_url,
                replace=args.replace,
            )
            _print_value(profile, as_json=args.json)
        elif args.subcommand == "edit":
            if args.alias is None and args.blog_url is None:
                raise ValueError("수정할 --alias 또는 --blog-url을 지정하세요.")
            profile = edit_profile(
                _resolve_slot(args),
                alias=args.alias,
                blog_url=args.blog_url,
            )
            _print_value(profile, as_json=args.json)
        elif args.subcommand == "reset":
            result = reset_profile(
                _resolve_slot(args),
                confirmation=args.confirm,
            )
            _print_value(result, as_json=args.json)
        elif args.subcommand == "check":
            result = check_profile(
                _resolve_slot(args),
                login=args.login,
                timeout_seconds=args.timeout,
            )
            _print_value(result, as_json=args.json)
        elif args.subcommand == "open":
            result = open_profiles(args.slots)
            _print_value(result, as_json=args.json)
        elif args.subcommand == "assign":
            assignments = assign_count(args.count, args.profiles)
            _print_value(
                assignments if args.json else _assignment_table(assignments),
                as_json=args.json,
            )
        elif args.subcommand == "voice-status":
            requested_slot = args.slot if args.slot is not None else args.slot_pos
            result = voice_status(requested_slot)
            _print_value(result, as_json=args.json)
        elif args.subcommand == "voice-set":
            result = set_voice(
                _resolve_slot(args),
                status=args.status,
                summary=args.summary,
                sample_count=args.sample_count,
            )
            _print_value(result, as_json=args.json)
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(2, f"오류: {exc}\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
