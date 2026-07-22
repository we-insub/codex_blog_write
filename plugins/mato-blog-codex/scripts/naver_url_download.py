"""Download public Naver blog posts into a local Mato work folder.

This module is an in-project implementation.  It deliberately has no runtime
dependency on Mato Helper or the old ``google-blog-auto`` checkout.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin


StatusCallback = Callable[[int, str], None]


def _safe_name(value: str, fallback: str) -> str:
    text = str(value or "").strip() or fallback
    text = re.sub(r'[<>:"/\\|?*\r\n\t]+', "_", text)
    return text.strip(" .")[:120] or fallback


def _read_lines(path: Path) -> list[str]:
    for encoding in ("utf-8", "utf-8-sig", "cp949"):
        try:
            return [line.strip() for line in path.read_text(encoding=encoding).splitlines() if line.strip()]
        except UnicodeDecodeError:
            continue
    return [line.strip() for line in path.read_text(errors="ignore").splitlines() if line.strip()]


def parse_url_keyword_pairs(txt_path: str) -> list[dict[str, str]]:
    path = Path(txt_path).expanduser()
    if not path.is_file():
        raise FileNotFoundError("URL/키워드 TXT 파일을 찾지 못했습니다.")
    lines = _read_lines(path)
    return [
        {"url": lines[index], "keyword": lines[index + 1] if index + 1 < len(lines) else ""}
        for index in range(0, len(lines), 2)
        if lines[index]
    ]


def _fetch(session: Any, url: str, *, headers: dict[str, str], timeout: int) -> Any:
    import requests

    try:
        return session.get(url, headers=headers, timeout=timeout)
    except requests.exceptions.SSLError:
        return session.get(url, headers=headers, timeout=timeout, verify=False)


def _image_extension(url: str) -> str:
    extension = Path(url.split("?", 1)[0]).suffix.lower() or ".jpg"
    return extension if extension.startswith(".") and len(extension) <= 5 else ".jpg"


def _save_image(session: Any, image_url: str, out_dir: Path, index: int, *, headers: dict[str, str]) -> str:
    response = _fetch(session, image_url, headers=headers, timeout=10)
    response.raise_for_status()
    filename = f"image_{index}{_image_extension(image_url)}"
    (out_dir / filename).write_bytes(response.content)
    return filename


def _extract_page(
    *, session: Any, url: str, keyword: str, out_dir: Path, max_images: int, headers: dict[str, str]
) -> dict[str, Any]:
    from bs4 import BeautifulSoup

    response = _fetch(session, url, headers=headers, timeout=15)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    lines = [f"원본 URL: {url}", ""]
    image_counter = 0
    photo_sets = soup.select('li[id^="photo_"]')
    if photo_sets:
        elements = photo_sets
    else:
        main = soup.find("div", class_="se-main-container") or soup.find("div", class_="blog_view") or soup.body
        if main is None:
            raise RuntimeError("본문 컨테이너를 찾지 못했습니다.")
        elements = main.find_all(["p", "div", "img"], recursive=True)

    pending_text = ""
    for element in elements:
        if max_images > 0 and image_counter >= max_images:
            break
        if getattr(element, "name", "") in ("p", "div"):
            text = element.get_text(strip=True)
            if text:
                pending_text = text
            continue
        image = element.find("img") if photo_sets else element
        if image is None or getattr(image, "name", "") != "img":
            continue
        image_url = image.get("data-lazy-src") or image.get("data-src") or image.get("src") or ""
        if not image_url:
            continue
        try:
            image_counter += 1
            filename = _save_image(session, urljoin(url, image_url), out_dir, image_counter, headers=headers)
            description = ""
            if photo_sets:
                description_tag = element.find("p", class_="contentsDescription")
                description = description_tag.get_text(strip=True) if description_tag else ""
            if description:
                lines.append(description)
            elif pending_text:
                lines.append(pending_text)
                pending_text = ""
            lines.append(f"[{filename}]")
        except Exception:
            image_counter -= 1
    if pending_text:
        lines.append(pending_text)
    text_file = f"{_safe_name(keyword, out_dir.name)}_원본.txt"
    (out_dir / text_file).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"keyword": keyword, "folder": out_dir.name, "url": url, "ok": True, "images": image_counter, "text_file": text_file}


def run_naver_url_download(
    *, txt_path: str, output_dir: str, max_items: int = 0, max_images_per_page: int = 0,
    status_callback: StatusCallback | None = None,
) -> dict[str, Any]:
    """Download URL/title pairs, keeping all page data only in the local run."""
    pairs = parse_url_keyword_pairs(txt_path)
    if max_items > 0:
        pairs = pairs[:max_items]
    if not pairs:
        raise RuntimeError("TXT에서 유효한 URL/키워드 쌍을 찾지 못했습니다.")
    out_root = Path(output_dir).expanduser()
    out_root.mkdir(parents=True, exist_ok=True)
    import requests

    session = requests.Session()
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
    started = time.time()
    results: list[dict[str, Any]] = []
    for index, pair in enumerate(pairs, start=1):
        keyword = pair["keyword"] or f"no_keyword_{index}"
        folder = out_root / _safe_name(keyword, f"item_{index}")
        folder.mkdir(parents=True, exist_ok=True)
        if status_callback:
            status_callback(int(90 * (index - 1) / len(pairs)), f"[{index}/{len(pairs)}] URL 다운로드 시작: {keyword}")
        try:
            result = _extract_page(session=session, url=pair["url"], keyword=keyword, out_dir=folder,
                                   max_images=max(0, int(max_images_per_page or 0)), headers=headers)
        except Exception as exc:
            result = {"keyword": keyword, "folder": folder.name, "url": pair["url"], "ok": False, "images": 0, "reason": str(exc)[:200]}
        results.append(result)
        if status_callback:
            status_callback(int(90 * index / len(pairs)), f"[{'완료' if result.get('ok') else '실패'}] {keyword}")
    succeeded = sum(1 for item in results if item.get("ok"))
    return {"kind": "naver_url_download", "processed": len(results), "succeeded": succeeded,
            "failed": len(results) - succeeded, "elapsed_sec": round(time.time() - started, 2),
            "output_dir": str(out_root), "entries": results[:50], "truncated_entries": len(results) > 50}
