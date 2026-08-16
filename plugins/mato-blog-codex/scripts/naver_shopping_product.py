"""Collect a Naver Shopping product page into a seller-image product bundle.

The Browser supplies the rendered product and review-tab HTML.  This module
never collects buyer photo reviews: it accepts only the product-gallery image
nodes served from ``shop-phinf.pstatic.net`` and removes Naver's ``type=``
resizing query before downloading the original seller asset.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

try:
    from .mato_common import atomic_write_json, googleblog_home
except ImportError:
    from mato_common import atomic_write_json, googleblog_home  # type: ignore[no-redef]


IMAGE_HOST = "shop-phinf.pstatic.net"
IMAGE_MANIFEST_KIND = "naver_shopping_product_images"
MAX_IMAGES = 80
_PRODUCT_PATH = re.compile(r"^/[^/]+/products/(?P<product_id>[1-9][0-9]*)/?$", re.I)
_SHORT_PATH = re.compile(r"^/[A-Za-z0-9_-]{4,64}/?$")
_PRICE_RE = re.compile(r"(?<!\d)(\d{1,3}(?:,\d{3})+|\d+)\s*원")


class NaverShoppingProductError(RuntimeError):
    """Base error for the Naver Shopping product pipeline."""


class ProductURLValidationError(NaverShoppingProductError):
    pass


class ProductParseError(NaverShoppingProductError):
    pass


class ProductImageError(NaverShoppingProductError):
    pass


@dataclass(frozen=True)
class ProductReference:
    input_url: str
    canonical_url: str | None
    product_id: str | None
    kind: str


def _text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _https_url(value: object) -> str:
    raw = str(value or "").strip()
    parsed = urlsplit(raw)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ProductURLValidationError("상품 URL 포트가 올바르지 않습니다.") from exc
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        raise ProductURLValidationError("https 상품 URL만 사용할 수 있습니다.")
    return urlunsplit(("https", parsed.netloc.lower(), parsed.path, parsed.query, ""))


def validate_product_url(value: str) -> ProductReference:
    """Validate a Naver short URL or a canonical Brand Store product URL."""

    normalized = _https_url(value)
    parsed = urlsplit(normalized)
    host = (parsed.hostname or "").lower()
    if host == "naver.me" and _SHORT_PATH.fullmatch(parsed.path) and not parsed.query:
        return ProductReference(normalized, None, None, "short")
    match = _PRODUCT_PATH.fullmatch(parsed.path)
    if host == "brand.naver.com" and match:
        canonical = urlunsplit(("https", host, parsed.path.rstrip("/"), "", ""))
        return ProductReference(normalized, canonical, match.group("product_id"), "canonical")
    raise ProductURLValidationError("네이버 쇼핑 단축 URL 또는 브랜드스토어 상품 URL이 필요합니다.")


def resolve_product_url(value: str, *, session: Any | None = None, timeout: float = 20) -> ProductReference:
    """Follow a user-supplied Naver short URL only to its Brand Store product."""

    initial = validate_product_url(value)
    if initial.canonical_url:
        return initial
    if session is None:
        try:
            import requests
        except Exception as exc:  # pragma: no cover - dependency failure
            raise ProductURLValidationError(f"requests를 불러오지 못했습니다: {exc}") from exc
        session = requests.Session()
    try:
        response = session.get(initial.input_url, allow_redirects=True, timeout=timeout)
        response.raise_for_status()
    except Exception as exc:
        raise ProductURLValidationError(f"네이버 단축 URL을 확인하지 못했습니다: {exc}") from exc
    final_url = str(getattr(response, "url", "") or "")
    final = validate_product_url(final_url)
    if final.kind != "canonical" or not final.canonical_url:
        raise ProductURLValidationError("단축 URL이 브랜드스토어 상품 페이지로 연결되지 않습니다.")
    return ProductReference(initial.input_url, final.canonical_url, final.product_id, "short")


def _original_image_url(value: object) -> str:
    parsed = urlsplit(str(value or "").strip())
    try:
        port = parsed.port
    except ValueError as exc:
        raise ProductImageError("상품 이미지 URL 포트가 올바르지 않습니다.") from exc
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").lower() != IMAGE_HOST
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or not parsed.path.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
    ):
        raise ProductImageError("브랜드스토어 상품 원본 이미지 URL이 아닙니다.")
    # Naver's `type=f40`, `o1000`, etc. are display transforms. The path is
    # the seller's original uploaded asset and is deliberately downloaded
    # without a resize query.
    return urlunsplit(("https", IMAGE_HOST, parsed.path, "", ""))


def _is_product_gallery_image(node: Any) -> bool:
    parent = node.find_parent(attrs={"title": "상품 이미지"})
    if parent is not None:
        return True
    alt = _text(node.get("alt"))
    return bool(re.fullmatch(r"(?:대표이미지|추가이미지\d+)", alt))


def extract_seller_images(rendered_html: str) -> list[str]:
    """Return every unique product-gallery image in its visible DOM order."""

    try:
        from bs4 import BeautifulSoup
    except Exception as exc:  # pragma: no cover
        raise ProductParseError(f"BeautifulSoup을 불러오지 못했습니다: {exc}") from exc
    soup = BeautifulSoup(rendered_html, "html.parser")
    result: list[str] = []
    seen: set[str] = set()
    for node in soup.find_all("img"):
        if node.find_parent(["noscript", "template"]) is not None or not _is_product_gallery_image(node):
            continue
        # Select only the literal HTML ``src`` value.  ``data-src`` can be a
        # lazy-loading or UI derivative and is not an independently displayed
        # seller image in the captured DOM.
        raw = node.get("src") or ""
        try:
            original = _original_image_url(raw)
        except ProductImageError:
            continue
        if original not in seen:
            seen.add(original)
            result.append(original)
    if not result:
        raise ProductParseError("상품 이미지 영역에서 원본 업체 이미지를 찾지 못했습니다.")
    if len(result) > MAX_IMAGES:
        raise ProductImageError(f"상품 원본 이미지가 제한 {MAX_IMAGES}개를 초과했습니다.")
    return result


def _meta_content(soup: Any, *selectors: str) -> str:
    for selector in selectors:
        node = soup.select_one(selector)
        if node is not None:
            value = _text(node.get("content") or node.get_text(" ", strip=True))
            if value:
                return value
    return ""


def _title_from_soup(soup: Any) -> str:
    title = _meta_content(soup, 'meta[property="og:title"]', 'meta[name="twitter:title"]')
    if not title:
        for selector in ("h1", "h2", "[data-shp-area='product_name']"):
            node = soup.select_one(selector)
            if node is not None and _text(node.get_text(" ", strip=True)):
                title = _text(node.get_text(" ", strip=True))
                break
    title = re.sub(r"\s*[-|]\s*네이버\s*(?:쇼핑|브랜드스토어).*$", "", title).strip()
    if not title:
        raise ProductParseError("상품명을 렌더링 HTML에서 찾지 못했습니다.")
    return title


def _price_from_soup(soup: Any) -> tuple[str, int | None]:
    values = [
        _meta_content(soup, 'meta[property="product:price:amount"]', 'meta[itemprop="price"]'),
        _text(soup.get_text(" ", strip=True)),
    ]
    for value in values:
        match = _PRICE_RE.search(value.replace(" ", ""))
        if match:
            price_text = f"{match.group(1)}원"
            return price_text, int(match.group(1).replace(",", ""))
    return "", None


def parse_reviews(review_html: str, *, limit: int = 5) -> list[dict[str, Any]]:
    """Use the first five expanded text reviews; never collect review photos."""

    try:
        from bs4 import BeautifulSoup
    except Exception as exc:  # pragma: no cover
        raise ProductParseError(f"BeautifulSoup을 불러오지 못했습니다: {exc}") from exc
    soup = BeautifulSoup(review_html, "html.parser")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in soup.select("div.W2ktnZBARU"):
        text = _text(node.get_text(" ", strip=True))
        if len(text) < 20 or text in seen:
            continue
        seen.add(text)
        card = node.find_parent("li")
        tags: list[str] = []
        if card is not None:
            for label in card.select("span.wdRzwtn18F"):
                value = _text(label.get_text(" ", strip=True))
                if value and value not in tags:
                    tags.append(value)
        rows.append({"text": text, "tags": tags})
        if len(rows) == limit:
            break
    if len(rows) < min(limit, 5):
        raise ProductParseError("리뷰 탭에서 확장된 텍스트 후기 5개를 찾지 못했습니다.")
    return rows


def parse_product_html(
    product_html: str,
    review_html: str,
    *,
    canonical_url: str,
) -> dict[str, Any]:
    try:
        from bs4 import BeautifulSoup
    except Exception as exc:  # pragma: no cover
        raise ProductParseError(f"BeautifulSoup을 불러오지 못했습니다: {exc}") from exc
    reference = validate_product_url(canonical_url)
    if not reference.canonical_url or not reference.product_id:
        raise ProductParseError("정규 브랜드스토어 상품 URL이 필요합니다.")
    soup = BeautifulSoup(product_html, "html.parser")
    title = _title_from_soup(soup)
    price_text, price_krw = _price_from_soup(soup)
    review_soup = BeautifulSoup(review_html, "html.parser")
    review_count_match = re.search(r"리뷰\s*([\d,]+)", _text(review_soup.get_text(" ", strip=True)))
    rating_match = re.search(r"총\s*5점\s*중\s*([0-5](?:\.\d+)?)", _text(review_soup.get_text(" ", strip=True)))
    return {
        "source_type": "naver_shopping_product",
        "product_id": reference.product_id,
        "canonical_url": reference.canonical_url,
        "title": title,
        "price_text": price_text,
        "price_krw": price_krw,
        "rating": float(rating_match.group(1)) if rating_match else None,
        "review_count": int(review_count_match.group(1).replace(",", "")) if review_count_match else None,
        "reviews": parse_reviews(review_html),
    }


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _prepared_jpeg(payload: bytes) -> tuple[bytes, int, int]:
    try:
        from PIL import Image, ImageOps
    except Exception as exc:  # pragma: no cover
        raise ProductImageError(f"Pillow를 불러오지 못했습니다: {exc}") from exc
    from io import BytesIO

    try:
        with Image.open(BytesIO(payload)) as opened:
            opened.load()
            normalized = ImageOps.exif_transpose(opened).convert("RGB")
            width, height = normalized.size
            output = BytesIO()
            normalized.save(output, format="JPEG", quality=92, optimize=True)
    except Exception as exc:
        raise ProductImageError(f"다운로드한 파일이 이미지가 아닙니다: {exc}") from exc
    if width <= 0 or height <= 0:
        raise ProductImageError("이미지 크기가 올바르지 않습니다.")
    return output.getvalue(), width, height


def _proof(value: object, *, canonical_url: str, image_count: int) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ProductParseError("브라우저 수집 증빙이 없습니다.")
    try:
        stable = [int(item) for item in value.get("stable_image_counts", [])]
        declared = int(value.get("seller_image_count") or 0)
    except (TypeError, ValueError) as exc:
        raise ProductParseError("브라우저 이미지 수집 증빙이 올바르지 않습니다.") from exc
    if (
        str(value.get("page_url") or "").rstrip("/") != canonical_url.rstrip("/")
        or value.get("document_ready_state") != "complete"
        or value.get("review_tab_opened") is not True
        or value.get("review_more_expanded") is not True
        or declared != image_count
        or stable != [image_count, image_count]
    ):
        raise ProductParseError("상품·리뷰 탭 브라우저 수집 증빙이 불완전합니다.")
    return {
        "page_url": canonical_url,
        "document_ready_state": "complete",
        "review_tab_opened": True,
        "review_more_expanded": True,
        "seller_image_count": image_count,
        "stable_image_counts": stable,
    }


def build_product_bundle(
    payload: Mapping[str, Any],
    output_dir: str | Path,
    *,
    session: Any | None = None,
    timeout: float = 20,
) -> dict[str, Any]:
    """Download original seller gallery images and write an atomic bundle."""

    product_url = str(payload.get("product_url") or "")
    canonical_url = str(payload.get("canonical_url") or "")
    product_html = str(payload.get("product_html") or payload.get("rendered_html") or "")
    review_html = str(payload.get("review_html") or "")
    if not product_url or not canonical_url or not product_html or not review_html:
        raise ProductParseError("product_url, canonical_url, product_html, review_html이 필요합니다.")
    requested = validate_product_url(product_url)
    canonical = validate_product_url(canonical_url)
    if not canonical.canonical_url or not canonical.product_id:
        raise ProductParseError("정규 브랜드스토어 상품 URL이 필요합니다.")
    if requested.kind == "canonical" and requested.canonical_url != canonical.canonical_url:
        raise ProductParseError("요청 URL과 브라우저 상품 URL이 다릅니다.")
    images = extract_seller_images(product_html)
    proof = _proof(payload.get("collection_proof"), canonical_url=canonical.canonical_url, image_count=len(images))
    facts = parse_product_html(product_html, review_html, canonical_url=canonical.canonical_url)
    if session is None:
        try:
            import requests
        except Exception as exc:  # pragma: no cover
            raise ProductImageError(f"requests를 불러오지 못했습니다: {exc}") from exc
        session = requests.Session()
    target = Path(output_dir).expanduser().resolve()
    if target.exists():
        raise ProductImageError("상품 이미지 출력 폴더가 이미 있습니다.")
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".naver-shopping-product-", dir=target.parent))
    accepted: list[dict[str, Any]] = []
    try:
        originals = stage / "originals"
        prepared = stage / "prepared"
        originals.mkdir(parents=True)
        prepared.mkdir(parents=True)
        byte_seen: dict[str, int] = {}
        pixel_seen: dict[str, int] = {}
        duplicates: list[dict[str, Any]] = []
        for source_index, source_url in enumerate(images, start=1):
            try:
                response = session.get(source_url, timeout=timeout)
                response.raise_for_status()
            except Exception as exc:
                raise ProductImageError(f"상품 원본 이미지를 받지 못했습니다: {source_url} ({exc})") from exc
            raw = bytes(response.content)
            jpeg, width, height = _prepared_jpeg(raw)
            byte_hash = _sha256(raw)
            pixel_hash = _sha256(jpeg)
            duplicate_of = byte_seen.get(byte_hash) or pixel_seen.get(pixel_hash)
            if duplicate_of:
                duplicates.append({"source_index": source_index, "source_url": source_url, "duplicate_of": duplicate_of})
                continue
            index = len(accepted) + 1
            byte_seen[byte_hash] = index
            pixel_seen[pixel_hash] = index
            suffix = Path(urlsplit(source_url).path).suffix.lower() or ".img"
            (originals / f"image_{index}{suffix}").write_bytes(raw)
            (prepared / f"image_{index}.jpg").write_bytes(jpeg)
            accepted.append(
                {
                    "index": index,
                    "classification": "product",
                    "source_role": "gallery",
                    "source_url": source_url,
                    "file": f"prepared/image_{index}.jpg",
                    "sha256": _sha256(jpeg),
                    "width": width,
                    "height": height,
                }
            )
        if not accepted:
            raise ProductImageError("중복 제거 후 남은 상품 원본 이미지가 없습니다.")
        manifest = {
            "schema_version": 1,
            "kind": IMAGE_MANIFEST_KIND,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source_url": product_url,
            "canonical_url": canonical.canonical_url,
            "product_id": canonical.product_id,
            "image_policy": {
                "seller_only": True,
                "original_url_without_resize_query": True,
                "review_images_excluded": True,
                "profile_images_excluded": True,
                "ui_images_excluded": True,
                "max_images": MAX_IMAGES,
                "prepared_format": "JPEG",
            },
            "candidate_count": len(images),
            "unique_url_count": len(images),
            "image_count": len(accepted),
            "accepted": accepted,
            "duplicates": duplicates,
            "collection_proof": proof,
            "facts": {key: value for key, value in facts.items() if key != "reviews"},
        }
        atomic_write_json(stage / "manifest.json", manifest)
        os.replace(stage, target)
        return {**manifest, "manifest": str((target / "manifest.json").resolve()), "parsed_facts": facts}
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def _cli_paths(args: argparse.Namespace) -> tuple[Path, Path, Path | None]:
    source = Path(args.input).expanduser().resolve()
    output = Path(args.output_dir).expanduser().resolve()
    facts = Path(args.facts_output).expanduser().resolve() if args.facts_output else None
    stage_root = (googleblog_home() / "mato-blog-codex" / "staging").resolve()
    if not source.is_file() or source.is_symlink() or output.exists():
        raise NaverShoppingProductError("입력 staging 또는 새 출력 경로가 올바르지 않습니다.")
    if facts is not None and (facts.exists() or facts.parent != stage_root):
        raise NaverShoppingProductError("facts-output은 관리 staging 최상위의 새 파일이어야 합니다.")
    return source, output, facts


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a Naver Shopping seller-image product bundle.")
    parser.add_argument("--input", required=True, help="staging browser Naver Shopping JSON")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--facts-output")
    args = parser.parse_args(argv)
    try:
        input_path, output_dir, facts_path = _cli_paths(args)
        raw = json.loads(input_path.read_text(encoding="utf-8-sig"))
        if not isinstance(raw, Mapping):
            raise NaverShoppingProductError("입력 JSON은 객체여야 합니다.")
        result = build_product_bundle(raw, output_dir)
        if facts_path is not None:
            atomic_write_json(facts_path, result["parsed_facts"])
        print(json.dumps({"ok": True, "output_dir": str(output_dir), "image_count": result["image_count"]}, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
