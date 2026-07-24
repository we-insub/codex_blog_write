"""Standalone WordPress REST client for the Mato OneQ workflow.

It uses a WordPress application password, not a normal account password.  No
configuration is read from or written to the Google Blog Auto installation.
"""

from __future__ import annotations

import html
import io
import re
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote, urljoin, urlparse

import requests


class WordPressError(RuntimeError):
    """A useful WordPress REST/API failure."""


def _site_url(value: str) -> tuple[str, str]:
    raw = str(value or "").strip()
    if raw and "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("WordPress site URL must be an http(s) site home URL without credentials")
    site = f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}".rstrip("/")
    return site, (parsed.hostname or "").lower()


def _content_disposition(filename: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(filename).name).strip("-.") or "image.webp"
    return f"attachment; filename=\"{name}\"; filename*=UTF-8''{quote(Path(filename).name, safe='')}"


def _slug(value: str, fallback: str = "wordpress-image") -> str:
    source = re.sub(r"<[^>]+>", " ", html.unescape(str(value or "")))
    source = re.sub(r"[^0-9A-Za-z가-힣]+", "-", source).strip("-").lower()
    return source[:72].strip("-") or fallback


def image_to_webp(data: bytes) -> bytes:
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as opened:
            opened.load()
            mode = "RGBA" if "A" in opened.getbands() else "RGB"
            output = io.BytesIO()
            opened.convert(mode).save(output, format="WEBP", quality=88, method=6)
            return output.getvalue()
    except Exception as exc:
        raise WordPressError(f"could not convert image to WebP: {exc}") from exc


def replace_image_markers(
    body_html: str,
    *,
    image_urls: dict[str, str],
    image_alt: dict[str, str] | None = None,
) -> str:
    """Replace independent ``[image_N.jpg]`` lines with semantic image HTML."""

    alts = image_alt or {}

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        url = image_urls.get(name.lower()) or image_urls.get(name)
        if not url:
            raise WordPressError(f"missing uploaded URL for local image: {name}")
        label = html.escape(alts.get(name.lower()) or alts.get(name) or name, quote=True)
        return f'<figure class="wp-block-image"><img src="{html.escape(url, quote=True)}" alt="{label}" title="{label}" /></figure>'

    return re.sub(
        r"(?im)^\s*\[(image_[1-9]\d*\.(?:jpg|jpeg|png|webp|gif))\]\s*$",
        replace,
        str(body_html or ""),
    )


def append_source_link(body_html: str, url: str, label: str = "출처:") -> str:
    href = str(url or "").strip()
    if not href:
        return str(body_html or "").strip()
    parsed = urlparse(href)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("source URL must be an http(s) URL")
    source_line = f'<p><strong>{html.escape(label)}</strong> <a href="{html.escape(href, quote=True)}" rel="nofollow noopener" target="_blank">{html.escape(href)}</a></p>'
    return str(body_html or "").strip() + "\n" + source_line


class WordPressClient:
    """A small authenticated client for WordPress's standard REST API."""

    def __init__(self, site_url: str, username: str, app_password: str, *, timeout: int = 30) -> None:
        self.site_url, hostname = _site_url(site_url)
        self._wpcom_site = hostname if hostname.endswith(".wordpress.com") else ""
        self.username = str(username or "").strip()
        self.app_password = str(app_password or "").replace(" ", "").strip()
        self.timeout = max(5, min(int(timeout), 120))
        if not self.username or not self.app_password:
            raise ValueError("WordPress username and application password are required")

    def _api_url(self, endpoint: str) -> str:
        endpoint = endpoint.lstrip("/")
        if self._wpcom_site:
            return f"https://public-api.wordpress.com/wp/v2/sites/{self._wpcom_site}/{endpoint}"
        return urljoin(self.site_url + "/", "wp-json/wp/v2/" + endpoint)

    def _raise(self, response: requests.Response) -> None:
        if 200 <= response.status_code < 300:
            return
        detail = response.text[:800]
        try:
            payload = response.json()
            if isinstance(payload, dict):
                detail = str(payload.get("message") or payload.get("code") or detail)
        except ValueError:
            pass
        if response.status_code == 401:
            detail += " (use a WordPress application password; a normal password will not work)"
        if response.status_code == 404:
            detail += " (enter the site home URL, not a wp-admin URL)"
        raise WordPressError(f"WordPress REST API error {response.status_code}: {detail}")

    def test_connection(self) -> dict[str, Any]:
        response = requests.get(self._api_url("users/me"), auth=(self.username, self.app_password), timeout=self.timeout)
        self._raise(response)
        data = response.json()
        return {"id": data.get("id"), "name": data.get("name") or data.get("slug") or self.username}

    def upload_image(self, image_path: Path, *, title: str, index: int) -> dict[str, Any]:
        if not image_path.is_file():
            raise WordPressError(f"image file does not exist: {image_path}")
        filename = f"{_slug(title)}-image-{max(1, int(index))}.webp"
        response = requests.post(
            self._api_url("media"),
            auth=(self.username, self.app_password),
            headers={"Content-Disposition": _content_disposition(filename), "Content-Type": "image/webp"},
            data=image_to_webp(image_path.read_bytes()),
            timeout=max(self.timeout, 60),
        )
        self._raise(response)
        result = response.json()
        source_url = result.get("source_url") or _mapping(result.get("guid")).get("rendered")
        if not source_url:
            raise WordPressError("WordPress media response did not include a source URL")
        result["source_url"] = source_url
        media_id = int(result.get("id") or 0)
        if media_id:
            label = re.sub(r"\s+", " ", f"{title} {index}").strip()
            metadata = requests.post(
                self._api_url(f"media/{media_id}"),
                auth=(self.username, self.app_password),
                json={"alt_text": label, "title": label},
                timeout=self.timeout,
            )
            # Metadata failure must not invalidate an otherwise successful media upload.
            if not 200 <= metadata.status_code < 300:
                result["metadata_warning"] = f"HTTP {metadata.status_code}"
        return result

    def resolve_term_ids(self, taxonomy: str, terms: Iterable[int | str] | None) -> list[int]:
        endpoint = "tags" if taxonomy == "tags" else "categories"
        ids: list[int] = []
        for raw in terms or []:
            term = str(raw or "").strip()
            if not term:
                continue
            if term.isdigit():
                ids.append(int(term))
                continue
            lookup = requests.get(
                self._api_url(endpoint),
                auth=(self.username, self.app_password),
                params={"search": term, "per_page": 100},
                timeout=self.timeout,
            )
            self._raise(lookup)
            existing = next(
                (
                    row for row in lookup.json()
                    if isinstance(row, dict) and str(row.get("name") or "").casefold() == term.casefold()
                ),
                None,
            )
            if isinstance(existing, dict) and int(existing.get("id") or 0):
                ids.append(int(existing["id"]))
                continue
            created = requests.post(
                self._api_url(endpoint), auth=(self.username, self.app_password), json={"name": term}, timeout=self.timeout
            )
            if created.status_code == 400:
                try:
                    conflict = created.json()
                    existing_id = int(_mapping(conflict.get("data")).get("term_id") or 0)
                    if existing_id:
                        ids.append(existing_id)
                        continue
                except (ValueError, TypeError, AttributeError):
                    pass
            self._raise(created)
            ids.append(int(created.json().get("id") or 0))
        return list(dict.fromkeys(item for item in ids if item))

    def create_post(
        self,
        *,
        title: str,
        body_html: str,
        status: str,
        categories: Iterable[int | str] | None = None,
        tags: Iterable[int | str] | None = None,
        featured_media: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "title": str(title or "Untitled"),
            "content": str(body_html or ""),
            "status": "publish" if str(status).lower() == "publish" else "draft",
        }
        category_ids = self.resolve_term_ids("categories", categories)
        tag_ids = self.resolve_term_ids("tags", tags)
        if category_ids:
            payload["categories"] = category_ids
        if tag_ids:
            payload["tags"] = tag_ids
        if featured_media:
            payload["featured_media"] = int(featured_media)
        response = requests.post(self._api_url("posts"), auth=(self.username, self.app_password), json=payload, timeout=self.timeout)
        self._raise(response)
        return response.json()


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}
