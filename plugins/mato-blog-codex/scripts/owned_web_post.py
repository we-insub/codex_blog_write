"""Download a user-owned public web post into a local Mato run.

Naver URLs continue to use the dedicated Naver downloader.  This module adds
the equivalent first stage for a user-owned WordPress or other public web URL
without reading an external application's folders or configuration.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

try:
    from .history import append_event, create_run, update_run
    from .mato_common import atomic_write_json, atomic_write_text, slugify
    from .owned_post import prepare_owned_post_download
except ImportError:
    from history import append_event, create_run, update_run  # type: ignore[no-redef]
    from mato_common import atomic_write_json, atomic_write_text, slugify  # type: ignore[no-redef]
    from owned_post import prepare_owned_post_download  # type: ignore[no-redef]


USER_AGENT = "MatoBlogCodex/0.2 (+local-owned-post-import)"


def _validate_url(value: str) -> str:
    raw = str(value or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("a public http(s) URL without embedded credentials is required")
    return raw


def _page_parts(url: str) -> tuple[str, str, list[str]]:
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    for node in soup.select("script, style, noscript, svg, nav, footer, aside, form"):
        node.decompose()
    title = ""
    meta_title = soup.select_one("meta[property='og:title']")
    if meta_title and meta_title.get("content"):
        title = str(meta_title["content"])
    if not title:
        heading = soup.select_one("h1")
        title = heading.get_text(" ", strip=True) if heading else ""
    if not title and soup.title:
        title = soup.title.get_text(" ", strip=True)
    title = re.sub(r"\s+", " ", title).strip() or "내 글"
    article = soup.select_one("article, main, .entry-content, .post-content, .article-content") or soup.body
    if article is None:
        raise ValueError("the source page did not contain readable article content")
    lines: list[str] = []
    for element in article.select("h1, h2, h3, h4, p, li, blockquote, td, th"):
        text = re.sub(r"\s+", " ", element.get_text(" ", strip=True)).strip()
        if text and (not lines or lines[-1] != text):
            lines.append(text)
    body = "\n\n".join(lines)[:80_000].strip()
    if len(body) < 40:
        raise ValueError("the source page did not expose enough readable article text")
    images: list[str] = []
    for image in article.select("img[src]"):
        source = urljoin(response.url, str(image.get("src") or "").strip())
        parsed = urlparse(source)
        if parsed.scheme in {"http", "https"} and parsed.netloc and source not in images:
            images.append(source)
    return title, body, images


def _download_owned_images(urls: list[str], folder: Path) -> list[dict[str, Any]]:
    """Download image bytes only after explicit user ownership confirmation."""

    try:
        from PIL import Image
    except ModuleNotFoundError as exc:  # pragma: no cover - bootstrap guards it
        raise RuntimeError("Pillow is required to normalize source images") from exc
    records: list[dict[str, Any]] = []
    for index, url in enumerate(urls, start=1):
        try:
            response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
            response.raise_for_status()
            with Image.open(io.BytesIO(response.content)) as opened:
                opened.load()
                output = folder / f"image_{index}.jpg"
                opened.convert("RGB").save(output, format="JPEG", quality=92, optimize=True)
            records.append({"index": index, "url": url, "file": output.name, "ok": True})
        except Exception as exc:
            records.append({"index": index, "url": url, "ok": False, "error": str(exc)[:240]})
    return records


def prepare_owned_web_post(
    url: str,
    *,
    versions: int,
    command: str,
    include_images: bool = False,
    images_authorized: bool = False,
    run_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Create a run from a public page the user confirms they own or may reuse."""

    source_url = _validate_url(url)
    parsed = urlparse(source_url)
    if parsed.hostname and parsed.hostname.lower() in {"blog.naver.com", "m.blog.naver.com"}:
        return prepare_owned_post_download(source_url, versions=versions, command=command, run_dir=run_dir)
    if versions <= 0:
        raise ValueError("versions must be positive")
    if include_images and not images_authorized:
        raise ValueError("source images require explicit ownership or reuse permission")

    title, body, images = _page_parts(source_url)
    directory, _state = create_run(
        f"내 글 {slugify(title, fallback='owned-web')}",
        "integrated",
        int(versions),
        command or source_url,
        run_dir=run_dir,
    )
    folder_name = f"01_{slugify(title, fallback='owned-web')[:80]}"
    source_folder = directory / "sources" / "items" / folder_name
    source_folder.mkdir(parents=True, exist_ok=True)
    note_path = source_folder / f"{slugify(title, fallback='owned-web')[:80]}_원본_함축.txt"
    atomic_write_text(note_path, f"제목을입력해주세요1: {title}\n\n본문2:\n{body}\n")
    image_records = _download_owned_images(images, source_folder) if include_images else []
    metadata_path = directory / "sources" / "sources.json"
    atomic_write_json(
        metadata_path,
        {
            "schema_version": 1,
            "collector": "owned-web-url",
            "usable_count": 1,
            "sources": [{"rank": 1, "title": title, "url": source_url, "status": "owned_by_user", "note_file": str(note_path.relative_to(directory))}],
            "images": image_records,
        },
    )

    def update(state: dict[str, Any]) -> None:
        state["status"] = "collected"
        state["sources"] = {
            "collector": "owned-web-url",
            "metadata_file": str(metadata_path),
            "owned_source_url": source_url,
            "owned_source_folder": folder_name,
            "source_images": sum(1 for item in image_records if item.get("ok")),
        }

    update_run(directory, update)
    append_event(
        directory,
        "owned_web_post_download_completed",
        status="collected",
        message="사용자 소유로 확인한 웹 글을 로컬 원본 자료로 준비했습니다.",
        details={"source_url": source_url, "images": sum(1 for item in image_records if item.get("ok"))},
    )
    return {"ok": True, "run_dir": str(directory), "source_folder": str(source_folder), "title": title, "images": image_records}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download a user-owned web post as a local Mato source.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--versions", type=int, required=True)
    parser.add_argument("--command", default="")
    parser.add_argument("--run-dir", default="")
    parser.add_argument("--include-images", action="store_true")
    parser.add_argument("--images-authorized", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = prepare_owned_web_post(
            args.url,
            versions=args.versions,
            command=args.command,
            include_images=args.include_images,
            images_authorized=args.images_authorized,
            run_dir=args.run_dir or None,
        )
    except (OSError, ValueError, RuntimeError, requests.RequestException) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
