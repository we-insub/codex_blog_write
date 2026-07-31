"""Shared, dependency-free helpers for the Mato Blog Codex scripts.

The module deliberately imports no browser or scraping packages.  This keeps
metadata, history, and validation commands usable before the per-machine
Playwright/Scrapling environment has been installed.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, TypeVar
from urllib.parse import parse_qs, unquote, urlparse

try:  # ``zoneinfo`` is part of Python 3.9+, but retain a safe Windows fallback.
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - supported Python versions include it.
    ZoneInfo = None  # type: ignore[assignment]


JSONValue = Any
T = TypeVar("T")
SCHEMA_VERSION = 1

_SENSITIVE_LABEL_PATTERN = (
    r"(?:[A-Za-z0-9]+[_-])*"
    r"(?:password|passwd|pwd|pw|비밀번호|token|"
    r"(?:access|refresh|id|auth)[_-]?token|(?:client[_-]?)?secret|"
    r"api[_ -]?key|cookie|session(?:[_-]?id)?|authorization)"
    r"(?:[_-][A-Za-z0-9]+)*"
)
_SENSITIVE_KEY_RE = re.compile(_SENSITIVE_LABEL_PATTERN, re.IGNORECASE)
_RAW_SOURCE_KEY_RE = re.compile(
    r"(?:(?:raw(?:_source)?|source|article|scraped|original|page|post)_?)?"
    r"(?:text|html|body|content)",
    re.IGNORECASE,
)
_NAVER_BLOG_ID_RE = re.compile(r"^[A-Za-z0-9._-]{2,64}$")
_RESERVED_NAVER_PATHS = {
    "PostView.naver",
    "PostWriteForm.naver",
    "GoBlogWrite.naver",
    "login",
}


def googleblog_home() -> Path:
    """Return the local-only Mato data root.

    ``MATO_GOOGLEBLOG_HOME`` is intended for tests and portable development.
    In normal use the exact location is ``~/.googleblog``.
    """

    override = os.environ.get("MATO_GOOGLEBLOG_HOME")
    return Path(override).expanduser() if override else Path.home() / ".googleblog"


def browser_profiles_dir() -> Path:
    """Return Codex-only directory containing persistent ``naver_N`` profiles.

    This is intentionally distinct from Mato Helper's profile directory.  The
    numbered Chrome data remains stable for this application until a user
    explicitly resets a profile.
    """

    return history_home() / "browser_profiles"


def profile_catalog_path() -> Path:
    """Return Codex-only local profile metadata path."""

    return history_home() / "naver_profiles.json"


def history_home() -> Path:
    """Return the local directory used for cross-run history."""

    return googleblog_home() / "mato-blog-codex"


def global_history_path() -> Path:
    """Return the local, append-only human-readable history path."""

    return history_home() / "HISTORY.md"


def kst_timezone() -> timezone:
    """Return the Asia/Seoul timezone without requiring third-party packages."""

    if ZoneInfo is not None:
        try:
            return ZoneInfo("Asia/Seoul")  # type: ignore[return-value]
        except Exception:
            pass
    return timezone(timedelta(hours=9), name="KST")


KST = kst_timezone()


def now_kst() -> datetime:
    """Return an aware current datetime in Korean Standard Time."""

    return datetime.now(tz=KST)


def timestamp_kst(value: datetime | None = None) -> str:
    """Return an ISO-8601 KST timestamp with second precision."""

    moment = value or now_kst()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=KST)
    return moment.astimezone(KST).isoformat(timespec="seconds")


def now_iso() -> str:
    """Compatibility alias returning the current KST ISO timestamp."""

    return timestamp_kst()


def display_timestamp_kst(value: datetime | None = None) -> str:
    """Return a compact timestamp suitable for Korean Markdown history."""

    moment = value or now_kst()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=KST)
    return moment.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S KST")


def read_json(path: str | Path, default: T) -> T:
    """Read UTF-8 JSON from *path*, returning *default* when absent.

    Invalid JSON is reported to the caller rather than silently discarded so a
    damaged profile catalog or run journal is never accidentally overwritten.
    """

    path = Path(path)
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)  # type: ignore[no-any-return]


def atomic_write_text(path: str | Path, text: str) -> None:
    """Atomically replace *path* with UTF-8 *text* on Windows and POSIX."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=str(path.parent),
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
    finally:
        if temporary_name:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                pass


def atomic_write_json(path: str | Path, value: JSONValue) -> None:
    """Atomically write pretty, deterministic UTF-8 JSON."""

    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False)
    atomic_write_text(path, payload + "\n")


def _windows_desktop_path() -> Path | None:
    """Return the current user's Windows Desktop Known Folder when available."""

    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class _GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", ctypes.c_ulong),
                ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        folder_id_desktop = _GUID(
            0xB4BFCC3A,
            0xDB2C,
            0x424C,
            (ctypes.c_ubyte * 8)(0xB0, 0x29, 0x7F, 0xE9, 0x9A, 0x87, 0xC6, 0x41),
        )
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        ole32 = ctypes.WinDLL("ole32", use_last_error=True)
        get_known_folder = shell32.SHGetKnownFolderPath
        get_known_folder.argtypes = [
            ctypes.POINTER(_GUID),
            wintypes.DWORD,
            wintypes.HANDLE,
            ctypes.POINTER(ctypes.c_wchar_p),
        ]
        get_known_folder.restype = ctypes.c_long
        free_memory = ole32.CoTaskMemFree
        free_memory.argtypes = [ctypes.c_void_p]
        free_memory.restype = None

        path_pointer = ctypes.c_wchar_p()
        result = get_known_folder(
            ctypes.byref(folder_id_desktop),
            0,
            None,
            ctypes.byref(path_pointer),
        )
        try:
            if result != 0 or not path_pointer.value:
                return None
            return Path(path_pointer.value)
        finally:
            if path_pointer:
                free_memory(ctypes.cast(path_pointer, ctypes.c_void_p))
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def default_runs_root() -> Path:
    """Return today's Desktop directory for generated research runs.

    Callers may set ``MATO_RUNS_ROOT`` for tests or a deliberate alternate
    workspace. ``MATO_DESKTOP_ROOT`` can override the detected Desktop while
    retaining the KST date directory. Local runs are never directed into the
    installed plugin.
    """

    override = os.environ.get("MATO_RUNS_ROOT")
    if override:
        return Path(override).expanduser()
    desktop_override = os.environ.get("MATO_DESKTOP_ROOT")
    if desktop_override:
        desktop = Path(desktop_override).expanduser()
    else:
        desktop = _windows_desktop_path() or Path.home() / "Desktop"
    return desktop / now_kst().strftime("%Y-%m-%d")


def slugify(value: object, *, max_length: int = 80, fallback: str = "run") -> str:
    """Create a readable Windows-safe path segment, preserving Korean text."""

    text = str(value).strip()
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", text)
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"-+", "-", text).strip(" .-")
    if not text:
        text = fallback
    # Windows device names are invalid even with a file extension.
    stem = text.split(".", 1)[0].upper()
    if stem in {"CON", "PRN", "AUX", "NUL"} or re.fullmatch(r"(?:COM|LPT)[1-9]", stem):
        text = f"_{text}"
    text = text[:max_length].rstrip(" .-")
    return text or fallback


def load_run(run_dir: str | Path) -> dict[str, Any]:
    """Load ``run.json`` from a run directory, returning an empty dict if absent."""

    path = Path(run_dir).expanduser() / "run.json"
    value = read_json(path, {})
    if not isinstance(value, dict):
        raise ValueError(f"run.json 최상위 값은 객체여야 합니다: {path}")
    return value


def save_run(run_dir: str | Path, data: Mapping[str, Any]) -> dict[str, Any]:
    """Atomically save a sanitized run state and return its dictionary copy."""

    directory = Path(run_dir).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    sanitized = sanitize_for_history(dict(data))
    if not isinstance(sanitized, dict):
        raise ValueError("실행 상태는 JSON 객체여야 합니다.")
    payload = sanitized
    atomic_write_json(directory / "run.json", payload)
    return payload


def atomic_append_text(path: str | Path, text: str, *, heading: str | None = None) -> None:
    """Append text by atomically replacing the complete destination file."""

    path = Path(path)
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    if not current and heading:
        current = heading.rstrip() + "\n\n"
    separator = "" if not current or current.endswith("\n") else "\n"
    atomic_write_text(path, current + separator + text)


def redact_text(value: object) -> str:
    """Redact common credentials from free-form commands and messages."""

    text = str(value)
    text = re.sub(
        r"(?i)(\bAuthorization\s*(?::|=|\s)\s*(?:Basic|Bearer)\s+)"
        r"[^\s,;]+",
        lambda match: match.group(1) + "[민감정보 삭제]",
        text,
    )
    text = re.sub(
        r"(?i)\bBearer\s+[A-Za-z0-9._~+\-/]+=*",
        "Bearer [민감정보 삭제]",
        text,
    )
    text = re.sub(
        r"(?im)(\b(?:cookie|set-cookie)\s*:\s*)[^\r\n]+",
        lambda match: match.group(1) + "[민감정보 삭제]",
        text,
    )
    text = re.sub(
        rf"(?i)((?<![A-Za-z0-9가-힣]){_SENSITIVE_LABEL_PATTERN}"
        r"(?![A-Za-z0-9가-힣])\s*[=:]\s*)"
        r"(?!(?:Basic|Bearer)\b|(?:[\"']?)\[민감정보 삭제\])"
        r"(\"[^\"]*\"|'[^']*'|[^\s,;]+)",
        lambda match: match.group(1) + "[민감정보 삭제]",
        text,
    )
    text = re.sub(
        rf"(?i)((?<![A-Za-z0-9가-힣]){_SENSITIVE_LABEL_PATTERN}"
        r"(?![A-Za-z0-9가-힣])\s+)"
        r"(?!(?:[\"']?)\[민감정보 삭제\])"
        r"(\"[^\"]*\"|'[^']*'|[^\s,;]+)",
        lambda match: match.group(1) + "[민감정보 삭제]",
        text,
    )
    text = re.sub(
        rf"(?i)(\"{_SENSITIVE_LABEL_PATTERN}\"\s*:\s*)"
        r"(?!\"\[민감정보 삭제\]\")\"[^\"]*\"",
        lambda match: match.group(1) + '"[민감정보 삭제]"',
        text,
    )
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "[민감정보 삭제]", text)
    # URL user-info can accidentally expose credentials in command history.
    text = re.sub(
        r"(?i)(https?://)[^/@\s:]+:[^/@\s]+@",
        r"\1[민감정보 삭제]@",
        text,
    )
    return text


def _is_sensitive_key(key: object) -> bool:
    """Return whether a structured key names or contains a credential field."""

    text = str(key)
    # Convert common camelCase/PascalCase spellings before checking composite
    # keys such as ``oauthClientSecret`` and ``naverAuthTokenBackup``.
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", text)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    normalized = re.sub(r"[^0-9A-Za-z가-힣]+", "_", text).strip("_")
    return bool(normalized and _SENSITIVE_KEY_RE.fullmatch(normalized))


def sanitize_for_history(value: JSONValue, *, key: str = "") -> JSONValue:
    """Recursively remove secrets and source bodies from history-safe data."""

    if key and _is_sensitive_key(key):
        return "[민감정보 삭제]"
    if key and _RAW_SOURCE_KEY_RE.fullmatch(key.replace("-", "_")):
        return "[원문 미기록]"
    if isinstance(value, Mapping):
        return {
            str(item_key): sanitize_for_history(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [sanitize_for_history(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def extract_naver_blog_id(value: str) -> str:
    """Extract and validate a Naver blog id from an id or Naver blog URL."""

    candidate = value.strip()
    if not candidate:
        raise ValueError("블로그 ID 또는 URL이 비어 있습니다.")

    if "://" not in candidate and "/" not in candidate and "?" not in candidate:
        blog_id = candidate
    else:
        parsed = urlparse(candidate if "://" in candidate else f"https://{candidate}")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("자격 증명이 포함된 URL은 사용할 수 없습니다.")
        host = (parsed.hostname or "").lower()
        if host not in {"blog.naver.com", "m.blog.naver.com"}:
            raise ValueError("네이버 블로그 URL만 사용할 수 있습니다.")
        query_id = parse_qs(parsed.query).get("blogId", [""])[0]
        if query_id:
            blog_id = query_id
        else:
            segments = [unquote(part) for part in parsed.path.split("/") if part]
            if not segments or segments[0] in _RESERVED_NAVER_PATHS:
                raise ValueError("URL에서 네이버 블로그 ID를 찾을 수 없습니다.")
            blog_id = segments[0]

    blog_id = unquote(blog_id).strip()
    if not _NAVER_BLOG_ID_RE.fullmatch(blog_id):
        raise ValueError("유효하지 않은 네이버 블로그 ID입니다.")
    return blog_id


def canonical_naver_blog_url(value: str) -> str:
    """Return ``https://blog.naver.com/<blog-id>`` for a Naver blog value."""

    return f"https://blog.naver.com/{extract_naver_blog_id(value)}"


def canonical_naver_write_url(value: str) -> str:
    """Return the canonical desktop editor URL for a Naver blog value."""

    return f"{canonical_naver_blog_url(value)}?Redirect=Write&"


def parse_positive_slots(values: str | Iterable[int | str]) -> list[int]:
    """Parse comma-separated or iterable profile slots, preserving order."""

    raw_values: Iterable[int | str]
    raw_values = values.split(",") if isinstance(values, str) else values
    result: list[int] = []
    for raw in raw_values:
        try:
            slot = int(str(raw).strip())
        except ValueError as exc:
            raise ValueError(f"프로필 번호가 정수가 아닙니다: {raw}") from exc
        if slot <= 0:
            raise ValueError("프로필 번호는 1 이상의 정수여야 합니다.")
        if slot not in result:
            result.append(slot)
    if not result:
        raise ValueError("프로필 번호를 하나 이상 지정해야 합니다.")
    return result


def compact_json(value: JSONValue) -> str:
    """Return single-line UTF-8 JSON for console and Markdown output."""

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
