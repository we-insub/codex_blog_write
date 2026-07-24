"""Local-only integration settings and Windows-protected secrets.

The OneQ workflow needs credentials for WordPress and Google Blogger, but a
run journal is intentionally shareable and must never contain them.  This
module stores ordinary destination settings separately from encrypted secret
values under ``~/.googleblog/mato-blog-codex/integrations``.  On supported
Windows hosts each secret is protected with DPAPI for the current Windows
user; copying the file to another account is therefore not useful.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import json
import os
import re
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

try:
    from .mato_common import atomic_write_json, googleblog_home, read_json
except ImportError:  # Direct execution: python scripts/integrations.py
    from mato_common import atomic_write_json, googleblog_home, read_json  # type: ignore[no-redef]


SCHEMA_VERSION = 1
SETTINGS_FILENAME = "settings.json"
SECRETS_FILENAME = "secrets.json"
WORDPRESS_SECRET_KEY = "wordpress.app_password"
BLOGSPOT_SECRET_KEY = "blogspot.client_secret"
BLOGSPOT_TOKEN_SECRET_KEY = "blogspot.oauth_token"


def integrations_home() -> Path:
    """Return the local, Git-independent integration settings directory."""

    return googleblog_home() / "mato-blog-codex" / "integrations"


def settings_path() -> Path:
    return integrations_home() / SETTINGS_FILENAME


def secrets_path() -> Path:
    return integrations_home() / SECRETS_FILENAME


def _default_settings() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "wordpress": {
            "site_url": "",
            "username": "",
            "categories": [],
            "tags": [],
            "default_status": "draft",
            "prompt_key": "wordpress",
        },
        "blogspot": {
            "client_id": "",
            "blog_id": "",
            "labels": [],
            "default_status": "draft",
            "prompt_key": "blogspot",
            "drive_public_image_sharing_acknowledged": False,
        },
    }


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def load_settings() -> dict[str, Any]:
    """Load ordinary, non-secret settings and fill in forward-compatible keys."""

    saved = read_json(settings_path(), {})
    result = _default_settings()
    if not isinstance(saved, Mapping):
        return result
    for platform in ("wordpress", "blogspot"):
        result[platform].update(_mapping(saved.get(platform)))
    result["schema_version"] = SCHEMA_VERSION
    return result


def save_settings(value: Mapping[str, Any]) -> dict[str, Any]:
    """Persist a normalized settings payload without any credential fields."""

    merged = _default_settings()
    for platform in ("wordpress", "blogspot"):
        merged[platform].update(_mapping(value.get(platform)))
    merged["schema_version"] = SCHEMA_VERSION
    atomic_write_json(settings_path(), merged)
    return merged


def _validate_site_url(value: str) -> str:
    raw = str(value or "").strip()
    if raw and "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("WordPress site URL must be an http(s) site home URL without credentials")
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}".rstrip("/")


def _clean_terms(values: Sequence[object] | str | None) -> list[str]:
    raw = values.split(",") if isinstance(values, str) else (values or [])
    result: list[str] = []
    for item in raw:
        term = re.sub(r"\s+", " ", str(item or "")).strip()
        if term and term not in result:
            result.append(term)
    return result


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _protect_bytes(value: bytes) -> bytes:
    if os.name != "nt":
        raise RuntimeError("secret storage is supported on Windows only")
    source_buffer = ctypes.create_string_buffer(value)
    source = _DataBlob(len(value), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_byte)))
    target = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob), wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    if not crypt32.CryptProtectData(ctypes.byref(source), "Mato Blog Codex", None, None, None, 0, ctypes.byref(target)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        kernel32.LocalFree(target.pbData)


def _unprotect_bytes(value: bytes) -> bytes:
    if os.name != "nt":
        raise RuntimeError("secret storage is supported on Windows only")
    source_buffer = ctypes.create_string_buffer(value)
    source = _DataBlob(len(value), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_byte)))
    target = _DataBlob()
    description = wintypes.LPWSTR()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob), ctypes.POINTER(wintypes.LPWSTR), ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source), ctypes.byref(description), None, None, None, 0, ctypes.byref(target)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        if description:
            kernel32.LocalFree(description)
        kernel32.LocalFree(target.pbData)


def _read_secret_store() -> dict[str, Any]:
    saved = read_json(secrets_path(), {})
    if not isinstance(saved, Mapping):
        return {"schema_version": SCHEMA_VERSION, "items": {}}
    items = saved.get("items")
    return {
        "schema_version": SCHEMA_VERSION,
        "items": dict(items) if isinstance(items, Mapping) else {},
    }


def set_secret(key: str, value: str) -> None:
    """Protect and save one UTF-8 secret without returning its value."""

    if not key or not str(value or ""):
        raise ValueError("secret key and value are required")
    store = _read_secret_store()
    encrypted = _protect_bytes(str(value).encode("utf-8"))
    store["items"][key] = base64.b64encode(encrypted).decode("ascii")
    atomic_write_json(secrets_path(), store)


def get_secret(key: str) -> str:
    """Read a current-user DPAPI secret, returning an empty string when absent."""

    raw = _read_secret_store()["items"].get(key)
    if not isinstance(raw, str) or not raw:
        return ""
    try:
        return _unprotect_bytes(base64.b64decode(raw.encode("ascii"))).decode("utf-8")
    except Exception as exc:
        raise RuntimeError(f"could not read the local secret for {key}") from exc


def has_secret(key: str) -> bool:
    return bool(_read_secret_store()["items"].get(key))


def delete_secret(key: str) -> None:
    store = _read_secret_store()
    if key in store["items"]:
        del store["items"][key]
        atomic_write_json(secrets_path(), store)


def configure_wordpress(
    *,
    site_url: str,
    username: str,
    app_password: str | None = None,
    categories: Sequence[object] | str | None = None,
    tags: Sequence[object] | str | None = None,
    default_status: str = "draft",
) -> dict[str, Any]:
    settings = load_settings()
    status = "publish" if str(default_status).lower() == "publish" else "draft"
    settings["wordpress"].update(
        {
            "site_url": _validate_site_url(site_url),
            "username": str(username or "").strip(),
            "categories": _clean_terms(categories),
            "tags": _clean_terms(tags),
            "default_status": status,
        }
    )
    if not settings["wordpress"]["username"]:
        raise ValueError("WordPress username is required")
    if app_password is not None:
        set_secret(WORDPRESS_SECRET_KEY, str(app_password).replace(" ", "").strip())
    save_settings(settings)
    return integration_status()["wordpress"]


def configure_blogspot(
    *,
    client_id: str,
    client_secret: str | None = None,
    blog_id: str = "",
    labels: Sequence[object] | str | None = None,
    default_status: str = "draft",
    acknowledge_public_drive_images: bool = False,
) -> dict[str, Any]:
    identifier = str(client_id or "").strip()
    if not identifier:
        raise ValueError("Google OAuth desktop client ID is required")
    settings = load_settings()
    settings["blogspot"].update(
        {
            "client_id": identifier,
            "blog_id": str(blog_id or "").strip(),
            "labels": _clean_terms(labels),
            "default_status": "publish" if str(default_status).lower() == "publish" else "draft",
            "drive_public_image_sharing_acknowledged": bool(acknowledge_public_drive_images),
        }
    )
    if client_secret is not None:
        set_secret(BLOGSPOT_SECRET_KEY, str(client_secret).strip())
        # A token issued to a previous client must not be reused.
        delete_secret(BLOGSPOT_TOKEN_SECRET_KEY)
    save_settings(settings)
    return integration_status()["blogspot"]


def integration_status() -> dict[str, Any]:
    settings = load_settings()
    wordpress = _mapping(settings.get("wordpress"))
    blogspot = _mapping(settings.get("blogspot"))
    return {
        "schema_version": SCHEMA_VERSION,
        "settings_path": str(settings_path()),
        "secrets_path": str(secrets_path()),
        "wordpress": {
            "configured": bool(wordpress.get("site_url") and wordpress.get("username") and has_secret(WORDPRESS_SECRET_KEY)),
            "site_url": wordpress.get("site_url") or "",
            "username": wordpress.get("username") or "",
            "categories": list(wordpress.get("categories") or []),
            "tags": list(wordpress.get("tags") or []),
            "default_status": wordpress.get("default_status") or "draft",
            "has_app_password": has_secret(WORDPRESS_SECRET_KEY),
        },
        "blogspot": {
            "configured": bool(blogspot.get("client_id") and has_secret(BLOGSPOT_SECRET_KEY)),
            "authorized": has_secret(BLOGSPOT_TOKEN_SECRET_KEY),
            "client_id": blogspot.get("client_id") or "",
            "blog_id": blogspot.get("blog_id") or "",
            "labels": list(blogspot.get("labels") or []),
            "default_status": blogspot.get("default_status") or "draft",
            "has_client_secret": has_secret(BLOGSPOT_SECRET_KEY),
            "drive_public_image_sharing_acknowledged": bool(blogspot.get("drive_public_image_sharing_acknowledged")),
        },
    }


def test_wordpress_connection() -> dict[str, Any]:
    """Verify the locally configured WordPress credentials without writing a post."""

    settings = load_settings()["wordpress"]
    if not settings.get("site_url") or not settings.get("username") or not has_secret(WORDPRESS_SECRET_KEY):
        raise ValueError("WordPress site URL, username, and application password must be configured first")
    try:
        from .wordpress_client import WordPressClient
    except ImportError:
        from wordpress_client import WordPressClient  # type: ignore[no-redef]
    identity = WordPressClient(
        str(settings["site_url"]), str(settings["username"]), get_secret(WORDPRESS_SECRET_KEY)
    ).test_connection()
    return {"ok": True, "site_url": settings["site_url"], "identity": identity}


def authorize_blogspot(*, blog_id: str = "") -> dict[str, Any]:
    """Run visible OAuth once, then return available Blogger destinations."""

    settings = load_settings()
    blogspot = settings["blogspot"]
    if not blogspot.get("client_id") or not has_secret(BLOGSPOT_SECRET_KEY):
        raise ValueError("Google OAuth client ID and secret must be configured first")
    try:
        from .blogspot_client import BlogspotClient
    except ImportError:
        from blogspot_client import BlogspotClient  # type: ignore[no-redef]
    blogs = BlogspotClient(str(blogspot["client_id"]), get_secret(BLOGSPOT_SECRET_KEY)).authorize()
    selected = str(blog_id or "").strip()
    if selected:
        if selected not in {str(item.get("id") or "") for item in blogs}:
            raise ValueError("the selected Blogspot blog ID is not available to the authorized Google account")
        blogspot["blog_id"] = selected
        save_settings(settings)
    return {"ok": True, "blogs": blogs, "blog_id": blogspot.get("blog_id") or ""}


def _secret_from_prompt(label: str) -> str:
    try:
        import getpass

        value = getpass.getpass(label)
    except (EOFError, KeyboardInterrupt) as exc:
        raise ValueError("secret input was cancelled") from exc
    if not value:
        raise ValueError("a non-empty secret is required")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Configure local WordPress and Blogspot integrations.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="show local setup status without printing secrets")
    wp = commands.add_parser("wordpress", help="store WordPress non-secret settings and app password")
    wp.add_argument("--site-url", required=True)
    wp.add_argument("--username", required=True)
    wp.add_argument("--categories", default="")
    wp.add_argument("--tags", default="")
    wp.add_argument("--status", choices=("draft", "publish"), default="draft")
    wp.add_argument("--app-password", default=None, help=argparse.SUPPRESS)
    bs = commands.add_parser("blogspot", help="store Blogger OAuth settings; authorizes separately")
    bs.add_argument("--client-id", required=True)
    bs.add_argument("--blog-id", default="")
    bs.add_argument("--labels", default="")
    bs.add_argument("--status", choices=("draft", "publish"), default="draft")
    bs.add_argument("--acknowledge-public-drive-images", action="store_true")
    bs.add_argument("--reuse-secret", action="store_true", help="reuse the existing local OAuth secret")
    bs.add_argument("--client-secret", default=None, help=argparse.SUPPRESS)
    commands.add_parser("test-wordpress", help="test configured WordPress credentials without publishing")
    authorize = commands.add_parser("authorize-blogspot", help="open Google OAuth and list Blogger destinations")
    authorize.add_argument("--blog-id", default="", help="optional Blog ID to save after authorization")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "status":
            result = integration_status()
        elif args.command == "wordpress":
            password = args.app_password if args.app_password is not None else _secret_from_prompt("WordPress application password: ")
            result = configure_wordpress(
                site_url=args.site_url,
                username=args.username,
                app_password=password,
                categories=args.categories,
                tags=args.tags,
                default_status=args.status,
            )
        elif args.command == "blogspot":
            if args.reuse_secret:
                if args.client_secret is not None:
                    raise ValueError("--reuse-secret cannot be combined with --client-secret")
                if not has_secret(BLOGSPOT_SECRET_KEY):
                    raise ValueError("there is no saved Google OAuth secret to reuse")
                secret = None
            else:
                secret = args.client_secret if args.client_secret is not None else _secret_from_prompt("Google OAuth client secret: ")
            result = configure_blogspot(
                client_id=args.client_id,
                client_secret=secret,
                blog_id=args.blog_id,
                labels=args.labels,
                default_status=args.status,
                acknowledge_public_drive_images=args.acknowledge_public_drive_images,
            )
        elif args.command == "test-wordpress":
            result = test_wordpress_connection()
        else:
            result = authorize_blogspot(blog_id=args.blog_id)
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
