"""Parse MyRealTrip products and preserve seller-provided images safely.

Rendered HTML is classified by DOM provenance. Review photos,
recommendations, placeholders, profiles, and UI assets are denied before the
seller-image allow rules are considered. Image bundles retain exact response
bytes and add a metadata-free JPEG copy without upscaling. A sibling staging
directory makes the final output atomic.
"""

from __future__ import annotations

import hashlib
import html as html_module
import io
import json
import os
import re
import shutil
import tempfile
import argparse
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qs, unquote, urljoin, urlparse, urlunparse

try:
    from .mato_common import atomic_write_json, googleblog_home
except ImportError:
    from mato_common import atomic_write_json, googleblog_home  # type: ignore[no-redef]


CANONICAL_HOST = "experiences.myrealtrip.com"
SHORT_HOST = "myrealt.rip"
BRIDGE_HOSTS = {"myrealtrip.com", "www.myrealtrip.com"}
SELLER_IMAGE_HOSTS = {
    "d2ur7st6jjikze.cloudfront.net",
    "dry7pvlp22cox.cloudfront.net",
}
REVIEW_IMAGE_HOSTS = {"d6bztw1vgnv55.cloudfront.net"}
HARD_IMAGE_CAP = 80
# Retained as an API default for older callers. Prepared copies now preserve
# the EXIF-corrected original pixel dimensions and never resize or upscale.
DEFAULT_PREPARED_MAX_SIDE = 0
MAX_ORIGINAL_BYTES = 40 * 1024 * 1024
MAX_DECODED_PIXELS = 100_000_000

_PRODUCT_PATH_RE = re.compile(r"^/products/([1-9][0-9]*)/?$")
_SHORT_PATH_RE = re.compile(r"^/([A-Za-z0-9_-]{4,64})/?$")
_PRICE_RE = re.compile(r"([0-9][0-9,]*)\s*원(~?)")
_REVIEW_COUNT_RE = re.compile(r"후기\s*([0-9][0-9,]*)\s*개")
_RATING_RE = re.compile(r"(?<![0-9])([0-5](?:\.[0-9]+)?)(?![0-9])")
_DURATION_RE = re.compile(
    r"(?:(?P<hours>[0-9]+)\s*시간)?\s*(?:(?P<minutes>[0-9]+)\s*분)?\s*소요"
)
_RECOMMENDATION_SECTION_IDS = {
    "RECOMMEND_VIEW_TOGETHER",
    "RELATED_PRODUCT",
    "RECOMMENDATION",
}
_UI_SECTION_IDS = {
    "PARTNER",
    "PARTNER_METRICS",
    "PAYMENT_BENEFITS",
    "INCLUDE_EXCLUDE",
    "RELATED_PRODUCT",
    "USAGE",
    "ESSENTIALS",
    "REFUND",
    "AD_BANNER",
}
_PLACEHOLDER_PATH_MARKERS = (
    "/etc/img-placeholder",
    "/icons/",
    "/logos/",
    "/settle/",
)
_REVIEW_PATH_MARKERS = ("/review/", "/reviews/", "/review_images/")
_PRODUCT_SECTION_IDS = (
    "INTRODUCTION",
    "ITINERARIES",
    "INCLUDE_EXCLUDE",
    "USAGE",
    "ESSENTIALS",
    "REFUND",
    "REVIEW",
)
# MyRealTrip product v1 relies on these sections for the factual and booking
# safety contract. Itinerary cards are genuinely optional on some products.
_REQUIRED_PRODUCT_SECTION_IDS = frozenset(
    {"INTRODUCTION", "INCLUDE_EXCLUDE", "USAGE", "ESSENTIALS", "REFUND", "REVIEW"}
)
_EXPANDABLE_PRODUCT_SECTION_IDS = frozenset(
    {"INTRODUCTION", "ITINERARIES", "ESSENTIALS", "REVIEW"}
)
_IMAGE_FORMAT_SUFFIXES = {
    "JPEG": ".jpg",
    "JPG": ".jpg",
    "PNG": ".png",
    "WEBP": ".webp",
    "GIF": ".gif",
    "BMP": ".bmp",
    "TIFF": ".tiff",
}
_IMAGE_FORMAT_SOURCE_SUFFIXES = {
    "JPEG": {".jpg", ".jpeg", ".jpe"},
    "JPG": {".jpg", ".jpeg", ".jpe"},
    "PNG": {".png"},
    "WEBP": {".webp"},
    "GIF": {".gif"},
    "BMP": {".bmp"},
    "TIFF": {".tif", ".tiff"},
}


class MyRealTripProductError(RuntimeError):
    """Base error for strict product processing."""


class ProductURLValidationError(MyRealTripProductError):
    """Raised for unsupported or unsafe product URLs."""


class ProductParseError(MyRealTripProductError):
    """Raised when required rendered facts cannot be found."""


class ProductImageError(MyRealTripProductError):
    """Raised for unsafe candidates or failed atomic image bundles."""


@dataclass(frozen=True)
class ProductReference:
    """Validated input and, when known, its canonical product identity."""

    input_url: str
    kind: str
    canonical_url: str | None = None
    product_id: str | None = None
    short_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_url": self.input_url,
            "kind": self.kind,
            "canonical_url": self.canonical_url,
            "product_id": self.product_id,
            "short_code": self.short_code,
        }


@dataclass(frozen=True)
class ImageCandidate:
    """One image whose trusted role came from the rendered product DOM."""

    candidate_id: str
    dom_index: int
    classification: str
    source_role: str | None
    source_url: str | None
    allowed: bool
    rejection_reason: str | None
    requires_hydration: bool
    natural_width: int | None = None
    natural_height: int | None = None

    @property
    def role(self) -> str | None:
        return self.source_role

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "dom_index": self.dom_index,
            "classification": self.classification,
            "source_role": self.source_role,
            "role": self.source_role,
            "source_url": self.source_url,
            "allowed": self.allowed,
            "rejection_reason": self.rejection_reason,
            "requires_hydration": self.requires_hydration,
            "natural_width": self.natural_width,
            "natural_height": self.natural_height,
        }


def _normalized_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalized_https_url(value: object, *, base_url: str | None = None) -> str:
    raw = html_module.unescape(str(value or "")).strip()
    if not raw:
        raise ProductURLValidationError("빈 URL입니다.")
    absolute = urljoin(base_url, raw) if base_url else raw
    parsed = urlparse(absolute)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ProductURLValidationError("잘못된 URL 포트입니다.") from exc
    if parsed.scheme.lower() != "https":
        raise ProductURLValidationError("HTTPS URL만 허용됩니다.")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ProductURLValidationError("인증 정보가 포함된 URL은 허용되지 않습니다.")
    if port not in {None, 443}:
        raise ProductURLValidationError("기본 HTTPS 포트만 허용됩니다.")
    return urlunparse(
        ("https", parsed.hostname.lower(), parsed.path or "/", "", parsed.query, "")
    )


def validate_product_url(value: str) -> ProductReference:
    """Validate a canonical, short, or marketing bridge product URL."""

    normalized = _normalized_https_url(value)
    parsed = urlparse(normalized)
    host = str(parsed.hostname or "").lower()
    if host == CANONICAL_HOST:
        match = _PRODUCT_PATH_RE.fullmatch(parsed.path)
        if not match:
            raise ProductURLValidationError("마이리얼트립 상품 URL 경로가 아닙니다.")
        product_id = match.group(1)
        return ProductReference(
            input_url=normalized,
            kind="canonical",
            canonical_url=f"https://{CANONICAL_HOST}/products/{product_id}",
            product_id=product_id,
        )
    if host == SHORT_HOST:
        match = _SHORT_PATH_RE.fullmatch(parsed.path)
        if not match or parsed.query:
            raise ProductURLValidationError("마이리얼트립 단축 URL 형식이 아닙니다.")
        return ProductReference(
            input_url=normalized,
            kind="short",
            short_code=match.group(1),
        )
    if host in BRIDGE_HOSTS and parsed.path.rstrip("/") == "/main/bridge/marketing":
        values = parse_qs(parsed.query).get("return_url") or []
        if len(values) != 1:
            raise ProductURLValidationError("브릿지에 유효한 return_url이 없습니다.")
        target = validate_product_url(unquote(str(values[0])))
        if not target.canonical_url:
            raise ProductURLValidationError("브릿지 return_url이 상품 URL이 아닙니다.")
        return ProductReference(
            input_url=normalized,
            kind="bridge",
            canonical_url=target.canonical_url,
            product_id=target.product_id,
        )
    raise ProductURLValidationError("허용된 마이리얼트립 URL이 아닙니다.")


def resolve_product_url(
    value: str,
    *,
    session: Any | None = None,
    timeout: float = 20,
) -> ProductReference:
    """Resolve a short URL without following an arbitrary redirect chain."""

    reference = validate_product_url(value)
    if reference.canonical_url:
        return reference
    if session is None:
        import requests

        session = requests.Session()
    response = session.get(
        reference.input_url,
        headers={"User-Agent": "Mozilla/5.0 MatoBlogCodex/1.0"},
        timeout=timeout,
        allow_redirects=False,
    )
    if int(getattr(response, "status_code", 0) or 0) not in {301, 302, 303, 307, 308}:
        raise ProductURLValidationError("단축 URL이 상품으로 리디렉션하지 않았습니다.")
    location = str(getattr(response, "headers", {}).get("Location") or "").strip()
    if not location:
        raise ProductURLValidationError("단축 URL 응답에 Location이 없습니다.")
    resolved = validate_product_url(
        _normalized_https_url(location, base_url=reference.input_url)
    )
    if not resolved.canonical_url or not resolved.product_id:
        raise ProductURLValidationError("단축 URL에서 상품 ID를 확인하지 못했습니다.")
    return ProductReference(
        input_url=reference.input_url,
        kind="short",
        canonical_url=resolved.canonical_url,
        product_id=resolved.product_id,
        short_code=reference.short_code,
    )


def _section_text_after_heading(section: Any, heading_text: str) -> str:
    for heading in section.find_all(["h2", "h3", "h4"]):
        if heading_text not in _normalized_text(heading.get_text(" ", strip=True)):
            continue
        paragraph = heading.find_next("p")
        if paragraph is not None and paragraph.find_parent("section") is section:
            return paragraph.get_text("\n", strip=True)
    return ""


def _section_has_heading(section: Any, heading_text: str) -> bool:
    if section is None:
        return False
    return any(
        heading_text in _normalized_text(heading.get_text(" ", strip=True))
        for heading in section.find_all(["h2", "h3", "h4"])
    )


def _split_detail_items(value: str) -> list[str]:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    pieces = re.split(r"(?:\n+|\s*⭐+\s*|\s*[•●]\s*)", text)
    return [item for item in (_normalized_text(piece) for piece in pieces) if item]


def _duration_minutes(value: str) -> int | None:
    match = _DURATION_RE.search(value)
    if not match or not (match.group("hours") or match.group("minutes")):
        return None
    return int(match.group("hours") or 0) * 60 + int(match.group("minutes") or 0)


def _price_payload(value: str) -> dict[str, Any]:
    text = _normalized_text(value)
    match = _PRICE_RE.search(text)
    return {
        "display": match.group(0) if match else text,
        "amount_krw": int(match.group(1).replace(",", "")) if match else None,
        "from": bool(match and match.group(2)),
    }


def _purchase_price_types(purchase: Any, display_text: str) -> dict[str, Any]:
    values: list[dict[str, Any]] = []
    if purchase is not None:
        for node in purchase.find_all(string=lambda text: bool(text and _PRICE_RE.search(str(text)))):
            raw = _normalized_text(node)
            match = _PRICE_RE.search(raw)
            if not match:
                continue
            parent_text = _normalized_text(node.parent.get_text(" ", strip=True)) if node.parent else raw
            context = parent_text[:160]
            if "쿠폰" in context:
                kind = "coupon"
            elif any(label in context for label in ("정가", "정상가", "판매가")):
                kind = "list"
            elif "결제" in context:
                kind = "payment"
            elif "할인" in context:
                kind = "discount"
            else:
                kind = "display"
            row = {
                "kind": kind,
                "display": match.group(0),
                "amount_krw": int(match.group(1).replace(",", "")),
                "from": bool(match.group(2)),
            }
            if row not in values:
                values.append(row)
    if not values and display_text:
        values.append({"kind": "display", **_price_payload(display_text)})
    by_kind: dict[str, dict[str, Any] | None] = {
        "display": None,
        "list": None,
        "coupon": None,
        "payment": None,
        "discount": None,
    }
    for row in values:
        kind = str(row["kind"])
        if by_kind.get(kind) is None:
            by_kind[kind] = row
    if by_kind["display"] is None and values:
        by_kind["display"] = values[0]
    return {"currency": "KRW", **by_kind, "all": values}


def _section_paragraphs(section: Any) -> list[str]:
    if section is None:
        return []
    values: list[str] = []
    for node in section.select("p, li"):
        text = _normalized_text(node.get_text(" ", strip=True))
        if text and text not in values:
            values.append(text)
    return values


def parse_product_html(
    rendered_html: str,
    *,
    source_url: str | None = None,
) -> dict[str, Any]:
    """Parse facts from a fully rendered MyRealTrip product DOM."""

    try:
        from bs4 import BeautifulSoup
    except Exception as exc:  # pragma: no cover
        raise ProductParseError(f"BeautifulSoup을 불러오지 못했습니다: {exc}") from exc
    if not str(rendered_html or "").strip():
        raise ProductParseError("렌더링된 HTML이 비어 있습니다.")
    soup = BeautifulSoup(rendered_html, "html.parser")
    title_node = soup.select_one("main h1.e2io25d1") or soup.select_one("main section h1")
    title_node = title_node or soup.select_one("h1.e2io25d1") or soup.find("h1")
    title = _normalized_text(title_node.get_text(" ", strip=True)) if title_node else ""
    if not title:
        raise ProductParseError("상품명을 확인하지 못했습니다.")
    header = title_node.find_parent("section") or title_node.parent
    rating_node = header.select_one(".e10qflf62") if header else None
    review_node = header.select_one(".e10qflf63") if header else None
    rating_match = (
        _RATING_RE.search(_normalized_text(rating_node.get_text(" ", strip=True)))
        if rating_node
        else None
    )
    review_match = (
        _REVIEW_COUNT_RE.search(_normalized_text(review_node.get_text(" ", strip=True)))
        if review_node
        else None
    )
    rating = float(rating_match.group(1)) if rating_match else None
    review_count = int(review_match.group(1).replace(",", "")) if review_match else None
    if rating is None or review_count is None:
        combined = re.search(
            r"([0-5](?:\.[0-9]+)?)\s*·?\s*후기\s*([0-9][0-9,]*)\s*개",
            _normalized_text(header.get_text(" ", strip=True)) if header else "",
        )
        if combined:
            rating = rating if rating is not None else float(combined.group(1))
            review_count = (
                review_count
                if review_count is not None
                else int(combined.group(2).replace(",", ""))
            )

    purchase = soup.select_one(".en857rz0")
    price_node = purchase.select_one(".esxxdu66") if purchase is not None else None
    if price_node is None and purchase is not None:
        price_node = purchase.find(string=lambda text: bool(text and _PRICE_RE.search(str(text))))
    price_node = price_node or soup.select_one(".esxxdu66")
    price_text = _normalized_text(
        price_node.get_text(" ", strip=True) if hasattr(price_node, "get_text") else price_node
    )
    price = _price_payload(price_text)
    prices = _purchase_price_types(purchase, price_text)

    chip_texts = (
        [
            _normalized_text(node.get_text(" ", strip=True))
            for node in header.select(".eg5633s0 .e1xhc0zq1")
            if _normalized_text(node.get_text(" ", strip=True))
        ]
        if header
        else []
    )
    duration_text = next((text for text in chip_texts if "소요" in text), "")
    minimum_people = next((text for text in chip_texts if "인원" in text), "")
    transport = next((text for text in chip_texts if "이동" in text), "")
    languages = next(
        (
            text
            for text in chip_texts
            if any(word in text for word in ("한국어", "영어", "중국어", "일본어"))
        ),
        "",
    )
    location = (
        [
            _normalized_text(node.get_text(" ", strip=True))
            for node in header.select(".e1qp43hc2")
            if _normalized_text(node.get_text(" ", strip=True))
        ]
        if header
        else []
    )

    itineraries: list[dict[str, Any]] = []
    itinerary_section = soup.select_one("#ITINERARIES")
    if itinerary_section is not None:
        for index, card in enumerate(itinerary_section.select(".e7xe4ph0"), start=1):
            duration_node = card.select_one(".e7xe4ph7")
            description_node = card.select_one(".e7xe4ph12")
            title_candidate = card.select_one(".e7xe4ph5 > span:first-of-type")
            if title_candidate is None:
                title_candidate = next(
                    (
                        node
                        for node in card.find_all("span")
                        if "소요" not in _normalized_text(node.get_text(" ", strip=True))
                    ),
                    None,
                )
            item_title = _normalized_text(title_candidate.get_text(" ", strip=True)) if title_candidate else ""
            item_duration = _normalized_text(duration_node.get_text(" ", strip=True)) if duration_node else ""
            description = _normalized_text(description_node.get_text(" ", strip=True)) if description_node else ""
            if item_title or item_duration or description:
                itineraries.append(
                    {
                        "order": index,
                        "title": item_title,
                        "duration": item_duration,
                        "duration_minutes": _duration_minutes(item_duration),
                        "description": description,
                    }
                )

    include_section = soup.select_one("#INCLUDE_EXCLUDE")
    included_text = _section_text_after_heading(include_section, "포함되어") if include_section else ""
    excluded_text = _section_text_after_heading(include_section, "불포함되어") if include_section else ""
    usage_section = soup.select_one("#USAGE")
    meeting_time = _section_text_after_heading(usage_section, "만나는시간") if usage_section else ""
    meeting_place = _section_text_after_heading(usage_section, "만나는장소") if usage_section else ""
    partner_section = soup.select_one("#PARTNER")
    seller_node = partner_section.select_one(".ek8rnbz4") if partner_section else None
    seller_name = _normalized_text(seller_node.get_text(" ", strip=True)) if seller_node else ""

    essentials_section = soup.select_one("#ESSENTIALS")
    refund_section = soup.select_one("#REFUND")
    essentials = _section_paragraphs(essentials_section)
    refund_policy = _section_paragraphs(refund_section)

    # The rendered site can place the labelled current-product number outside
    # ``#ESSENTIALS`` (the supplied capture puts it below that section).  Match
    # only the explicit Korean label across the current ``main`` tree and reject
    # ambiguity instead of treating unrelated numeric card IDs as product IDs.
    identity_root = soup.select_one("main") or soup
    identity_ids = sorted(
        set(
            re.findall(
                r"상품\s*번호\s*:?\s*([1-9][0-9]*)",
                identity_root.get_text(" ", strip=True),
            )
        )
    )
    if len(identity_ids) > 1:
        raise ProductParseError(f"HTML에 서로 다른 상품 번호가 있습니다: {identity_ids}")
    html_product_id = identity_ids[0] if identity_ids else ""

    reviews: list[dict[str, Any]] = []
    review_section = soup.select_one("#REVIEW")
    if review_section is not None:
        for card in review_section.select(".e15aqqks0"):
            body_node = card.select_one(".ebhs27u1")
            body = _normalized_text(body_node.get_text(" ", strip=True)) if body_node else ""
            if not body:
                continue
            date_node = card.select_one(".e1look9g2")
            tags_node = card.select_one(".e1ao7r1t0")
            reviews.append(
                {
                    "date": _normalized_text(date_node.get_text(" ", strip=True)) if date_node else "",
                    "text": body,
                    "tags": [
                        item.strip()
                        for item in _normalized_text(tags_node.get_text(" ", strip=True)).split("·")
                        if item.strip()
                    ]
                    if tags_node
                    else [],
                }
            )

    available_sections = [
        section_id
        for section_id in _PRODUCT_SECTION_IDS
        if soup.select_one(f"#{section_id}") is not None
    ]
    missing_sections = _REQUIRED_PRODUCT_SECTION_IDS.difference(available_sections)
    if missing_sections:
        raise ProductParseError(
            "필수 상품 섹션을 확인하지 못했습니다: "
            + ", ".join(sorted(missing_sections))
        )

    reference = validate_product_url(source_url) if source_url else None
    if reference is not None:
        if not html_product_id:
            raise ProductParseError("렌더링된 HTML에서 상품 번호를 확인하지 못했습니다.")
        if reference.product_id != html_product_id:
            raise ProductParseError(
                f"URL 상품 번호와 HTML 상품 번호가 다릅니다: "
                f"{reference.product_id} != {html_product_id}"
            )
    missing_required: list[str] = []
    for label, value in (
        ("평점", rating),
        ("후기 수", review_count),
        ("가격", price.get("amount_krw")),
        ("소요시간", _duration_minutes(duration_text)),
    ):
        if value is None or value == "":
            missing_required.append(label)
    if missing_required:
        raise ProductParseError(
            "필수 상품 정보를 확인하지 못했습니다: " + ", ".join(missing_required)
        )
    included = _split_detail_items(included_text)
    excluded = _split_detail_items(excluded_text)
    incomplete_sections: list[str] = []
    introduction_section = soup.select_one("#INTRODUCTION")
    introduction_text = (
        _normalized_text(introduction_section.get_text(" ", strip=True))
        if introduction_section is not None
        else ""
    )
    introduction_text = re.sub(
        r"(?:상품\s*소개|소개\s*더보기|더보기|접기)", "", introduction_text
    ).strip()
    introduction_media = (
        introduction_section.select(
            "img, video, [style*='background-image']"
        )
        if introduction_section is not None
        else []
    )
    if "INTRODUCTION" in available_sections and not (
        introduction_text or introduction_media
    ):
        incomplete_sections.append("INTRODUCTION")
    if "ITINERARIES" in available_sections and not itineraries:
        incomplete_sections.append("ITINERARIES")
    include_heading_present = _section_has_heading(include_section, "포함되어")
    exclude_heading_present = _section_has_heading(include_section, "불포함되어")
    if "INCLUDE_EXCLUDE" in available_sections and (
        (not included and not excluded)
        or (include_heading_present and not included)
        or (exclude_heading_present and not excluded)
    ):
        incomplete_sections.append("INCLUDE_EXCLUDE")
    meeting_time_heading_present = _section_has_heading(usage_section, "만나는시간")
    meeting_place_heading_present = _section_has_heading(usage_section, "만나는장소")
    if "USAGE" in available_sections and (
        (not meeting_time and not meeting_place)
        or (meeting_time_heading_present and not meeting_time)
        or (meeting_place_heading_present and not meeting_place)
    ):
        incomplete_sections.append("USAGE")
    if "ESSENTIALS" in available_sections and not essentials:
        incomplete_sections.append("ESSENTIALS")
    if "REFUND" in available_sections and not refund_policy:
        incomplete_sections.append("REFUND")
    if "REVIEW" in available_sections and int(review_count or 0) > 0 and not reviews:
        incomplete_sections.append("REVIEW")
    if incomplete_sections:
        raise ProductParseError(
            "렌더링된 상품 섹션이 비어 있거나 펼쳐지지 않았습니다: "
            + ", ".join(incomplete_sections)
        )
    return {
        "schema_version": 1,
        "kind": "myrealtrip_product_facts",
        "source_url": source_url,
        "canonical_url": reference.canonical_url if reference else None,
        "product_id": html_product_id or (reference.product_id if reference else None),
        "title": title,
        "rating": rating,
        "review_count": review_count,
        "price": price,
        "prices": prices,
        "price_text": price["display"],
        "price_krw": price["amount_krw"],
        "location": location,
        "seller_name": seller_name,
        "available_sections": available_sections,
        "tour_info": {
            "chips": chip_texts,
            "minimum_people": minimum_people,
            "duration": duration_text,
            "duration_minutes": _duration_minutes(duration_text),
            "transport": transport,
            "languages": languages,
            "meeting_time": _normalized_text(meeting_time),
            "meeting_place": _normalized_text(meeting_place),
        },
        "tour_duration": duration_text,
        "tour_duration_minutes": _duration_minutes(duration_text),
        "itineraries": itineraries,
        "included": included,
        "excluded": excluded,
        "included_text": _normalized_text(included_text),
        "excluded_text": _normalized_text(excluded_text),
        "essentials": essentials,
        "refund_policy": refund_policy,
        "usage_conditions": {
            "essentials": essentials,
            "refund": refund_policy,
        },
        "reviews": reviews,
    }


def _ancestor_section_id(node: Any) -> str:
    section = node.find_parent("section")
    return str(section.get("id") or "") if section is not None else ""


def _has_ancestor_class_prefix(node: Any, prefixes: Sequence[str]) -> bool:
    for ancestor in node.parents:
        for class_name in ancestor.get("class", []) if hasattr(ancestor, "get") else []:
            if any(str(class_name).startswith(prefix) for prefix in prefixes):
                return True
    return False


def _srcset_url(value: str) -> str:
    choices: list[tuple[int, str]] = []
    for position, part in enumerate(str(value or "").split(",")):
        bits = part.strip().split()
        if not bits:
            continue
        score = position
        if len(bits) > 1:
            match = re.fullmatch(r"([0-9]+)(?:w|x)", bits[1].lower())
            if match:
                score = int(match.group(1))
        choices.append((score, bits[0]))
    return max(choices, default=(0, ""), key=lambda item: item[0])[1]


def _image_attribute_url(node: Any) -> str:
    for attribute in ("currentSrc", "currentsrc"):
        if node.get(attribute):
            return str(node.get(attribute))
    if node.get("srcset"):
        value = _srcset_url(str(node.get("srcset")))
        if value:
            return value
    for attribute in ("src", "data-original", "data-lazy-src", "data-src"):
        if node.get(attribute):
            return str(node.get(attribute))
    return ""


def _safe_seller_image_url(value: object, *, base_url: str) -> str:
    try:
        normalized = _normalized_https_url(value, base_url=base_url)
    except ProductURLValidationError as exc:
        raise ProductImageError(str(exc)) from exc
    parsed = urlparse(normalized)
    host = str(parsed.hostname or "").lower()
    path = parsed.path.lower()
    if host in REVIEW_IMAGE_HOSTS or any(marker in path for marker in _REVIEW_PATH_MARKERS):
        raise ProductImageError("후기 이미지 URL은 허용되지 않습니다.")
    if host not in SELLER_IMAGE_HOSTS:
        raise ProductImageError("허용된 상품 이미지 호스트가 아닙니다.")
    if any(marker in path for marker in _PLACEHOLDER_PATH_MARKERS) or path.endswith(".svg"):
        raise ProductImageError("플레이스홀더 또는 UI 이미지는 허용되지 않습니다.")
    return normalized


def _review_role_signal(node: Any) -> bool:
    values: list[str] = []
    for current in [node, *list(node.parents)[:8]]:
        if not hasattr(current, "get"):
            continue
        for attribute in (
            "alt",
            "aria-label",
            "role",
            "data-role",
            "data-source-role",
            "data-image-role",
            "data-testid",
        ):
            values.append(str(current.get(attribute) or ""))
        values.extend(str(value) for value in current.get("class", []))
    blob = re.sub(r"[\s-]+", "_", " ".join(values)).casefold()
    return any(
        token in blob
        for token in ("review", "traveler", "user_generated", "usergenerated", "후기사진")
    )


def classify_image_candidates(rendered_html: str, *, page_url: str) -> dict[str, Any]:
    """Classify every image with deny-before-allow DOM rules."""

    try:
        from bs4 import BeautifulSoup
    except Exception as exc:  # pragma: no cover
        raise ProductImageError(f"BeautifulSoup을 불러오지 못했습니다: {exc}") from exc
    reference = validate_product_url(page_url)
    base_url = reference.canonical_url or reference.input_url
    soup = BeautifulSoup(rendered_html, "html.parser")
    rows: list[ImageCandidate] = []
    role_counts = {"gallery": 0, "introduction": 0, "itinerary": 0}

    # ``document.images`` does not include image markup held in inert
    # ``template`` content or the scripting-enabled ``noscript`` fallback.
    # Excluding those nodes keeps ``dom_index - 1`` bound to the Browser's
    # zero-based ``document.images`` index.
    document_images = [
        image
        for image in soup.find_all("img")
        if image.find_parent(["noscript", "template"]) is None
    ]
    for dom_index, image in enumerate(document_images, start=1):
        raw_url = _image_attribute_url(image)
        source_url: str | None = None
        if raw_url:
            try:
                source_url = _normalized_https_url(raw_url, base_url=base_url)
            except ProductURLValidationError:
                source_url = raw_url.strip()
        section_id = _ancestor_section_id(image)
        parsed_source = urlparse(source_url) if source_url and source_url.startswith("https://") else None
        path = parsed_source.path.lower() if parsed_source else ""
        host = str(parsed_source.hostname or "").lower() if parsed_source else ""
        alt = _normalized_text(image.get("alt"))
        classes = {str(value) for value in image.get("class", [])}
        gallery_cell = image.find_parent(
            class_=lambda value: value and "e1k35iz0" in str(value).split()
        )
        review_label = gallery_cell.select_one(".e1k35iz2") if gallery_cell else None
        review_text = _normalized_text(review_label.get_text(" ", strip=True)) if review_label else ""
        classification = "unknown"
        role: str | None = None
        allowed = False
        reason: str | None = "not_in_seller_image_allowlist"

        if (
            "css-1ydfimi" in classes
            or "placeholder" in alt.lower()
            or any(marker in path for marker in _PLACEHOLDER_PATH_MARKERS)
        ):
            classification, reason = "placeholder", "placeholder"
        elif any(
            str(getattr(current, "get", lambda _key: "")("data-mato-exclude") or "")
            .strip()
            .lower()
            in {"1", "true", "yes"}
            for current in [image, *list(image.parents)[:8]]
        ):
            # The Browser collector can mark seller images excluded for this
            # run while retaining DOM order for hydration binding.
            classification, reason = "excluded", "user_excluded"
        elif (
            section_id == "REVIEW"
            or "후기사진" in review_text
            or any(marker in path for marker in _REVIEW_PATH_MARKERS)
            or host in REVIEW_IMAGE_HOSTS
            or _has_ancestor_class_prefix(image, ("ebhs27u",))
            or _review_role_signal(image)
        ):
            classification, reason = "review", "review_image"
        elif section_id in _RECOMMENDATION_SECTION_IDS or _has_ancestor_class_prefix(
            image, ("e1hlbvyh", "e1ejw9ji")
        ):
            classification, reason = "recommendation", "recommendation_image"
        elif (
            image.find_parent("header") is not None
            or image.find_parent("nav") is not None
            or section_id in _UI_SECTION_IDS
            or _has_ancestor_class_prefix(image, ("eg5633s", "e1xhc0zq", "e1y9ro5d"))
            or "/profile_images/" in path
        ):
            classification, reason = "ui", "ui_or_profile_image"
        elif (
            gallery_cell is not None
            and image.find_parent(
                class_=lambda value: value and "e1k35iz1" in str(value).split()
            )
            is not None
        ):
            classification, role, allowed, reason = "product", "gallery", True, None
        elif (
            section_id == "INTRODUCTION"
            and _has_ancestor_class_prefix(image, ("e1kcu58w2",))
        ):
            classification, role, allowed, reason = "product", "introduction", True, None
        elif section_id == "ITINERARIES" and (
            "e7xe4ph14" in classes or _has_ancestor_class_prefix(image, ("e7xe4ph",))
        ):
            classification, role, allowed, reason = "product", "itinerary", True, None
        elif path.endswith(".svg") or "icon" in alt.lower():
            classification, reason = "ui", "ui_image"

        if allowed and role is not None:
            if source_url:
                try:
                    source_url = _safe_seller_image_url(source_url, base_url=base_url)
                except ProductImageError as exc:
                    allowed, classification, reason = False, "unsafe", str(exc)
            role_counts[role] += 1
            candidate_id = f"{role}:{role_counts[role]}"
        else:
            candidate_id = f"dom:{dom_index}"
        rows.append(
            ImageCandidate(
                candidate_id=candidate_id,
                dom_index=dom_index,
                classification=classification,
                source_role=role,
                source_url=source_url,
                allowed=allowed,
                rejection_reason=reason,
                requires_hydration=bool(allowed and not source_url),
            )
        )

    allowed_rows = [row for row in rows if row.allowed]
    rejected_rows = [row for row in rows if not row.allowed]
    available_sections = [
        section_id
        for section_id in _PRODUCT_SECTION_IDS
        if soup.select_one(f"#{section_id}") is not None
    ]
    return {
        "schema_version": 1,
        "kind": "myrealtrip_image_candidates",
        "page_url": page_url,
        "candidate_count": len(allowed_rows),
        "requires_hydration_count": sum(row.requires_hydration for row in allowed_rows),
        "available_sections": available_sections,
        "allowed": [row.to_dict() for row in allowed_rows],
        "rejected": [row.to_dict() for row in rejected_rows],
        "_allowed_objects": allowed_rows,
    }


def _candidate_objects(value: Any) -> list[ImageCandidate]:
    if isinstance(value, Mapping):
        private = value.get("_allowed_objects")
        if isinstance(private, list) and all(isinstance(item, ImageCandidate) for item in private):
            return list(private)
        value = value.get("allowed")
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ProductImageError("이미지 후보 목록 형식이 아닙니다.")
    objects: list[ImageCandidate] = []
    for raw in value:
        if isinstance(raw, ImageCandidate):
            objects.append(raw)
            continue
        if not isinstance(raw, Mapping):
            raise ProductImageError("이미지 후보 행 형식이 아닙니다.")
        objects.append(
            ImageCandidate(
                candidate_id=str(raw.get("candidate_id") or ""),
                dom_index=int(raw.get("dom_index") or 0),
                classification=str(raw.get("classification") or ""),
                source_role=str(raw.get("source_role") or raw.get("role") or "") or None,
                source_url=str(raw.get("source_url") or "") or None,
                allowed=raw.get("allowed") is True,
                rejection_reason=str(raw.get("rejection_reason") or "") or None,
                requires_hydration=raw.get("requires_hydration") is True,
                natural_width=int(raw.get("natural_width") or 0) or None,
                natural_height=int(raw.get("natural_height") or 0) or None,
            )
        )
    return objects


def validate_hydrated_candidates(
    candidates: Any,
    hydrated_rows: Sequence[Mapping[str, Any]],
    *,
    page_url: str,
    require_all: bool = True,
) -> list[ImageCandidate]:
    """Fill emitted candidate ids with strict browser-observed lazy URLs."""

    expected_rows = _candidate_objects(candidates)
    allowed_rows = [row for row in expected_rows if row.allowed]
    expected = {row.candidate_id: row for row in allowed_rows}
    if len(expected) != len(allowed_rows):
        raise ProductImageError("중복된 candidate_id가 있습니다.")
    candidate_safety_cap = HARD_IMAGE_CAP * 5
    if len(expected) > candidate_safety_cap:
        raise ProductImageError(
            f"이미지 후보는 안전 제한 {candidate_safety_cap}개를 초과할 수 없습니다."
        )
    supplied: dict[str, Mapping[str, Any]] = {}
    for raw in hydrated_rows:
        candidate_id = str(raw.get("candidate_id") or "")
        if candidate_id not in expected:
            raise ProductImageError(f"알 수 없거나 거부된 candidate_id입니다: {candidate_id}")
        if candidate_id in supplied:
            raise ProductImageError(f"중복 hydration 행입니다: {candidate_id}")
        supplied[candidate_id] = raw

    reference = validate_product_url(page_url)
    base_url = reference.canonical_url or reference.input_url
    result: list[ImageCandidate] = []
    for candidate in allowed_rows:
        raw = supplied.get(candidate.candidate_id)
        source_url = candidate.source_url
        width, height = candidate.natural_width, candidate.natural_height
        if raw is not None:
            if raw.get("complete") is not True:
                raise ProductImageError(f"이미지 로드가 완료되지 않았습니다: {candidate.candidate_id}")
            supplied_role = str(raw.get("source_role") or raw.get("role") or "")
            if supplied_role != candidate.source_role:
                raise ProductImageError(f"role이 DOM 분류와 다릅니다: {candidate.candidate_id}")
            try:
                supplied_dom_index = int(raw.get("dom_index"))
                supplied_document_index = int(raw.get("document_image_index"))
            except (TypeError, ValueError) as exc:
                raise ProductImageError(
                    f"Browser 이미지 인덱스가 없습니다: {candidate.candidate_id}"
                ) from exc
            if (
                supplied_dom_index != candidate.dom_index
                or supplied_document_index != candidate.dom_index - 1
            ):
                raise ProductImageError(
                    f"Browser 이미지 인덱스가 DOM 분류와 다릅니다: {candidate.candidate_id}"
                )
            observed_url = _safe_seller_image_url(
                raw.get("current_src") or raw.get("source_url") or source_url,
                base_url=base_url,
            )
            if candidate.source_url:
                expected_url = _safe_seller_image_url(candidate.source_url, base_url=base_url)
                if _url_dedupe_key(observed_url) != _url_dedupe_key(expected_url):
                    raise ProductImageError(
                        f"Browser 이미지 URL이 DOM 분류와 다릅니다: {candidate.candidate_id}"
                    )
            source_url = observed_url
            try:
                width = int(raw.get("natural_width") or 0)
                height = int(raw.get("natural_height") or 0)
            except (TypeError, ValueError) as exc:
                raise ProductImageError("이미지 크기가 정수가 아닙니다.") from exc
            if not (0 < width <= 100_000 and 0 < height <= 100_000):
                raise ProductImageError(f"유효하지 않은 크기입니다: {candidate.candidate_id}")
        elif require_all:
            raise ProductImageError(f"브라우저 이미지 검증 행이 누락됐습니다: {candidate.candidate_id}")
        elif source_url:
            source_url = _safe_seller_image_url(source_url, base_url=base_url)
        else:
            continue
        result.append(
            replace(
                candidate,
                source_url=source_url,
                requires_hydration=False,
                natural_width=width,
                natural_height=height,
            )
        )
    return result


def validate_collection_proof(
    proof: Mapping[str, Any] | None,
    classification: Mapping[str, Any],
    hydrated_rows: Sequence[Mapping[str, Any]],
    *,
    page_url: str,
) -> dict[str, Any]:
    """Require evidence that the live Browser expanded and exhausted the page.

    Rendered HTML alone cannot prove that lazy gallery/introduction images were
    discovered.  The in-app Browser collector records its expansion, scrolling,
    carousel, and stabilization observations; this function binds those counts
    to the DOM classification and hydration rows consumed by the bundle.
    """

    if not isinstance(proof, Mapping):
        raise ProductImageError("실제 브라우저 전체 수집 증명이 필요합니다.")
    reference = validate_product_url(page_url)
    observed_url = validate_product_url(str(proof.get("page_url") or ""))
    if observed_url.canonical_url != reference.canonical_url:
        raise ProductImageError("브라우저 수집 증명의 상품 URL이 다릅니다.")
    if str(proof.get("document_ready_state") or "").lower() != "complete":
        raise ProductImageError("상품 페이지 로딩 완료 증명이 없습니다.")
    available_raw = classification.get("available_sections")
    if not isinstance(available_raw, list):
        raise ProductImageError("DOM 분류에 상품 섹션 목록이 없습니다.")
    available = {
        str(value).strip().upper()
        for value in available_raw
        if str(value).strip()
    }
    if (
        len(available) != len(available_raw)
        or not available.issubset(set(_PRODUCT_SECTION_IDS))
        or not _REQUIRED_PRODUCT_SECTION_IDS.issubset(available)
    ):
        raise ProductImageError("DOM 분류의 상품 섹션 목록이 잘못됐습니다.")
    proof_available_raw = proof.get("available_sections")
    proof_available = (
        {
            str(value).strip().upper()
            for value in proof_available_raw
            if str(value).strip()
        }
        if isinstance(proof_available_raw, list)
        else set()
    )
    if (
        not isinstance(proof_available_raw, list)
        or len(proof_available) != len(proof_available_raw)
        or proof_available != available
    ):
        raise ProductImageError("브라우저 수집 증명의 상품 섹션이 DOM과 다릅니다.")
    expanded = {
        str(value).strip().upper()
        for value in proof.get("expanded_sections", [])
        if str(value).strip()
    } if isinstance(proof.get("expanded_sections"), list) else set()
    required_expanded = available.intersection(_EXPANDABLE_PRODUCT_SECTION_IDS)
    if not required_expanded.issubset(expanded):
        raise ProductImageError("현재 상품에 존재하는 확장 섹션 증명이 불완전합니다.")
    if expanded != required_expanded:
        raise ProductImageError("브라우저 수집 증명이 없는 상품 섹션 확장을 주장합니다.")
    review_available = "REVIEW" in available
    if review_available and proof.get("review_all_opened") is not True:
        raise ProductImageError("후기 모두 보기 확인 기록이 없습니다.")
    if proof.get("page_end_reached") is not True:
        raise ProductImageError("페이지 끝까지 스크롤한 기록이 없습니다.")
    try:
        scroll_height = int(proof.get("scroll_height") or 0)
        max_scroll_y = int(proof.get("max_scroll_y") or 0)
        viewport_height = int(proof.get("viewport_height") or 0)
        candidate_count = int(proof.get("seller_candidate_count") or 0)
        hydrated_count = int(proof.get("hydrated_candidate_count") or 0)
        gallery_visited = int(proof.get("gallery_visited_count") or 0)
    except (TypeError, ValueError) as exc:
        raise ProductImageError("브라우저 수집 증명의 수치 형식이 잘못됐습니다.") from exc
    if scroll_height <= 0 or viewport_height <= 0 or max_scroll_y + viewport_height < scroll_height - 8:
        raise ProductImageError("브라우저 스크롤 위치가 페이지 끝에 도달하지 않았습니다.")
    expected_rows = classification.get("allowed")
    if not isinstance(expected_rows, list):
        raise ProductImageError("DOM 이미지 분류 결과가 잘못됐습니다.")
    expected_count = len(expected_rows)
    expected_gallery = sum(
        1 for row in expected_rows if isinstance(row, Mapping) and row.get("source_role") == "gallery"
    )
    if candidate_count != expected_count or hydrated_count != expected_count:
        raise ProductImageError("브라우저 후보/로드 수가 렌더링 DOM과 다릅니다.")
    if gallery_visited < expected_gallery:
        raise ProductImageError("상품 갤러리 슬라이드를 모두 방문하지 않았습니다.")
    hydrated_ids = [str(row.get("candidate_id") or "") for row in hydrated_rows]
    expected_ids = [str(row.get("candidate_id") or "") for row in expected_rows]
    if hydrated_ids != expected_ids:
        raise ProductImageError("브라우저 hydration 순서가 DOM 후보 순서와 다릅니다.")
    stable_counts = proof.get("stable_candidate_counts")
    if (
        not isinstance(stable_counts, list)
        or len(stable_counts) < 2
        or int(stable_counts[-1]) != expected_count
        or int(stable_counts[-2]) != expected_count
    ):
        raise ProductImageError("두 번 연속 동일한 업체 이미지 후보 수를 확인하지 못했습니다.")
    return {
        "page_url": reference.canonical_url,
        "candidate_count": expected_count,
        "gallery_visited_count": gallery_visited,
        "stable_candidate_counts": [int(stable_counts[-2]), int(stable_counts[-1])],
        "page_end_reached": True,
        "available_sections": [
            section_id for section_id in _PRODUCT_SECTION_IDS if section_id in available
        ],
        "expanded_sections": sorted(required_expanded),
        "review_all_opened": bool(review_available),
    }


def _url_dedupe_key(value: str) -> str:
    parsed = urlparse(value)
    host = str(parsed.hostname or "").lower()
    # Query strings on the seller CDNs can select a different crop, format, or
    # resolution.  Only byte-for-byte normalized URLs are safe to discard
    # before download; content and decoded-pixel hashes handle real duplicates
    # afterwards.
    return urlunparse((parsed.scheme.lower(), host, parsed.path, "", parsed.query, ""))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _pixel_sha256(image: Any) -> str:
    canonical = image.convert("RGBA")
    digest = hashlib.sha256()
    digest.update(f"RGBA:{canonical.width}x{canonical.height}:".encode("ascii"))
    digest.update(canonical.tobytes())
    return digest.hexdigest()


def _decoded_image(payload: bytes) -> tuple[Any, str, int, int, str]:
    try:
        from PIL import Image, ImageOps
    except Exception as exc:  # pragma: no cover
        raise ProductImageError(f"Pillow를 불러오지 못했습니다: {exc}") from exc
    try:
        opened = Image.open(io.BytesIO(payload))
        opened.seek(0)
        image_format = str(opened.format or "").upper()
        normalized = ImageOps.exif_transpose(opened)
        normalized.load()
        width, height = normalized.size
    except Exception as exc:
        raise ProductImageError(f"응답을 이미지로 해독하지 못했습니다: {exc}") from exc
    if image_format not in _IMAGE_FORMAT_SUFFIXES:
        raise ProductImageError(f"지원하지 않는 원본 형식입니다: {image_format or 'unknown'}")
    if width <= 0 or height <= 0 or width * height > MAX_DECODED_PIXELS:
        raise ProductImageError("원본 이미지 크기가 안전 범위를 벗어났습니다.")
    return normalized, image_format, width, height, _pixel_sha256(normalized)


def _prepared_jpeg_bytes(
    image: Any,
    *,
    max_side: int,
    quality: int,
) -> tuple[bytes, int, int]:
    from PIL import Image

    # ``max_side`` remains accepted for call-site compatibility. Resizing is
    # intentionally forbidden: upload copies keep the source pixel size.
    del max_side
    normalized = image.copy()
    if "A" in normalized.getbands():
        canvas = Image.new("RGB", normalized.size, "white")
        canvas.paste(normalized.convert("RGB"), mask=normalized.getchannel("A"))
        normalized = canvas
    else:
        normalized = normalized.convert("RGB")
    stream = io.BytesIO()
    normalized.save(
        stream,
        format="JPEG",
        quality=max(70, min(100, int(quality))),
        optimize=True,
    )
    return stream.getvalue(), normalized.width, normalized.height


def _original_suffix(source_url: str, image_format: str) -> str:
    suffix = Path(unquote(urlparse(source_url).path)).suffix.lower()
    if suffix in _IMAGE_FORMAT_SOURCE_SUFFIXES.get(image_format, set()):
        return suffix
    return _IMAGE_FORMAT_SUFFIXES[image_format]


def _durable_facts(value: Mapping[str, Any]) -> dict[str, Any]:
    """Remove review prose while retaining non-reversible review evidence."""

    durable = json.loads(json.dumps(dict(value), ensure_ascii=False))
    raw_reviews = durable.pop("reviews", [])
    reviews = raw_reviews if isinstance(raw_reviews, list) else []
    combined = "\n".join(
        _normalized_text(row.get("text"))
        for row in reviews
        if isinstance(row, Mapping) and _normalized_text(row.get("text"))
    )
    tag_counts: Counter[str] = Counter()
    for row in reviews:
        if not isinstance(row, Mapping):
            continue
        tags = row.get("tags") if isinstance(row.get("tags"), list) else []
        tag_counts.update(_normalized_text(tag) for tag in tags if _normalized_text(tag))
    durable["review_count_sampled"] = len(reviews)
    durable["review_sample_sha256"] = (
        hashlib.sha256(combined.encode("utf-8")).hexdigest() if combined else None
    )
    durable["review_tag_counts"] = dict(sorted(tag_counts.items()))
    return durable


def _durable_rejected_images(value: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw in value or ():
        source_url = _normalized_text(raw.get("source_url"))
        result.append(
            {
                "candidate_id": _normalized_text(raw.get("candidate_id")),
                "dom_index": int(raw.get("dom_index") or 0),
                "classification": _normalized_text(raw.get("classification")) or "unknown",
                "source_role": _normalized_text(raw.get("source_role")) or None,
                "rejection_reason": _normalized_text(raw.get("rejection_reason"))
                or "not_in_seller_image_allowlist",
                "source_url_sha256": (
                    hashlib.sha256(source_url.encode("utf-8")).hexdigest()
                    if source_url
                    else None
                ),
            }
        )
    return result


def _coerce_download_candidates(value: Any, *, page_url: str) -> list[ImageCandidate]:
    reference = validate_product_url(page_url)
    base_url = reference.canonical_url or reference.input_url
    rows = _candidate_objects(value)
    result: list[ImageCandidate] = []
    seen_ids: set[str] = set()
    for row in rows:
        if not row.allowed or row.classification != "product":
            raise ProductImageError(f"거부되었거나 상품이 아닌 후보입니다: {row.candidate_id}")
        if row.source_role not in {"gallery", "introduction", "itinerary"}:
            raise ProductImageError(f"알 수 없는 role입니다: {row.source_role}")
        if not row.candidate_id or row.candidate_id in seen_ids:
            raise ProductImageError("빈 또는 중복된 candidate_id가 있습니다.")
        seen_ids.add(row.candidate_id)
        if not row.source_url:
            raise ProductImageError(f"이미지 URL이 없습니다: {row.candidate_id}")
        result.append(
            replace(
                row,
                source_url=_safe_seller_image_url(row.source_url, base_url=base_url),
                requires_hydration=False,
            )
        )
    return result


def download_product_images(
    candidates: Any,
    output_dir: str | Path,
    *,
    source_url: str,
    canonical_url: str | None = None,
    product_id: str | None = None,
    facts: Mapping[str, Any] | None = None,
    collection_proof: Mapping[str, Any] | None = None,
    rejected_candidates: Sequence[Mapping[str, Any]] | None = None,
    session: Any | None = None,
    timeout: float = 20,
    max_images: int = HARD_IMAGE_CAP,
    prepared_max_side: int = DEFAULT_PREPARED_MAX_SIDE,
    jpeg_quality: int = 92,
) -> dict[str, Any]:
    """Download seller images and atomically publish a provenance bundle."""

    reference = validate_product_url(canonical_url or source_url)
    if canonical_url and reference.canonical_url != canonical_url:
        raise ProductURLValidationError("canonical_url이 정규화된 URL과 다릅니다.")
    canonical = canonical_url or reference.canonical_url
    resolved_product_id = product_id or reference.product_id
    if not canonical or not resolved_product_id:
        raise ProductURLValidationError("번들에는 canonical_url과 product_id가 필요합니다.")
    try:
        requested_cap = int(max_images)
    except (TypeError, ValueError) as exc:
        raise ProductImageError("max_images는 정수여야 합니다.") from exc
    effective_cap = requested_cap
    if not (1 <= effective_cap <= HARD_IMAGE_CAP):
        raise ProductImageError(f"max_images는 1~{HARD_IMAGE_CAP}이어야 합니다.")
    rows = _coerce_download_candidates(candidates, page_url=canonical)
    unique_rows: list[ImageCandidate] = []
    url_seen: dict[str, str] = {}
    duplicates: list[dict[str, Any]] = []
    for row in rows:
        key = _url_dedupe_key(str(row.source_url))
        if key in url_seen:
            duplicates.append(
                {
                    "candidate_id": row.candidate_id,
                    "source_url": row.source_url,
                    "reason": "url_duplicate",
                    "duplicate_of": url_seen[key],
                }
            )
            continue
        url_seen[key] = row.candidate_id
        unique_rows.append(row)
    # Keep a separate abuse guard for an unexpectedly noisy DOM.  The user
    # visible limit applies to *deduplicated seller images*, not URL variants.
    candidate_safety_cap = max(HARD_IMAGE_CAP * 5, effective_cap * 5)
    if len(unique_rows) > candidate_safety_cap:
        raise ProductImageError(
            f"이미지 후보 {len(unique_rows)}개가 안전 제한 {candidate_safety_cap}개를 초과했습니다."
        )

    target = Path(output_dir).expanduser().resolve()
    if target.exists():
        raise ProductImageError(f"출력 경로가 이미 존재합니다: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{target.name}-", dir=str(target.parent)))
    (stage / "originals").mkdir()
    (stage / "prepared").mkdir()
    if session is None:
        import requests

        session = requests.Session()
    accepted: list[dict[str, Any]] = []
    content_seen: dict[str, str] = {}
    pixel_seen: dict[str, str] = {}
    try:
        for row in unique_rows:
            response = session.get(
                row.source_url,
                headers={
                    "User-Agent": "Mozilla/5.0 MatoBlogCodex/1.0",
                    "Referer": canonical,
                },
                timeout=timeout,
                allow_redirects=False,
                stream=True,
            )
            status_code = int(getattr(response, "status_code", 0) or 0)
            if 300 <= status_code < 400:
                raise ProductImageError("상품 이미지 리디렉션은 허용되지 않습니다.")
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            response_headers = getattr(response, "headers", {})
            content_type = str(response_headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            if not content_type.startswith("image/"):
                raise ProductImageError(f"이미지가 아닌 Content-Type 응답입니다: {content_type or 'missing'}")
            final_url = str(getattr(response, "url", "") or row.source_url)
            if _safe_seller_image_url(final_url, base_url=canonical) != row.source_url:
                raise ProductImageError("상품 이미지 최종 URL이 요청 URL과 다릅니다.")
            try:
                declared_bytes = int(response_headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                declared_bytes = 0
            if declared_bytes > MAX_ORIGINAL_BYTES:
                raise ProductImageError(f"이미지가 {MAX_ORIGINAL_BYTES}바이트 제한을 초과했습니다.")
            if hasattr(response, "iter_content"):
                chunks: list[bytes] = []
                received = 0
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    received += len(chunk)
                    if received > MAX_ORIGINAL_BYTES:
                        raise ProductImageError(
                            f"이미지가 {MAX_ORIGINAL_BYTES}바이트 제한을 초과했습니다."
                        )
                    chunks.append(bytes(chunk))
                payload = b"".join(chunks)
            else:  # Lightweight injected sessions used by the dependency-free tests.
                payload = bytes(getattr(response, "content", b""))
            if not payload:
                raise ProductImageError(f"빈 이미지 응답입니다: {row.source_url}")
            if len(payload) > MAX_ORIGINAL_BYTES:
                raise ProductImageError(f"이미지가 {MAX_ORIGINAL_BYTES}바이트 제한을 초과했습니다.")
            content_sha = _sha256_bytes(payload)
            if content_sha in content_seen:
                duplicates.append(
                    {
                        "candidate_id": row.candidate_id,
                        "source_url": row.source_url,
                        "reason": "content_duplicate",
                        "duplicate_of": content_seen[content_sha],
                    }
                )
                continue
            image, image_format, original_width, original_height, pixel_sha = _decoded_image(payload)
            if pixel_sha in pixel_seen:
                duplicates.append(
                    {
                        "candidate_id": row.candidate_id,
                        "source_url": row.source_url,
                        "reason": "pixel_duplicate",
                        "duplicate_of": pixel_seen[pixel_sha],
                    }
                )
                continue
            prepared, width, height = _prepared_jpeg_bytes(
                image,
                max_side=prepared_max_side,
                quality=jpeg_quality,
            )
            index = len(accepted) + 1
            original_suffix = _original_suffix(str(row.source_url), image_format)
            original_relative = Path("originals") / f"image_{index}{original_suffix}"
            prepared_relative = Path("prepared") / f"image_{index}.jpg"
            (stage / original_relative).write_bytes(payload)
            (stage / prepared_relative).write_bytes(prepared)
            file_label = prepared_relative.as_posix()
            accepted.append(
                {
                    "index": index,
                    "candidate_id": row.candidate_id,
                    "classification": "product",
                    "source_role": row.source_role,
                    "role": row.source_role,
                    "inclusion_reason": f"current_product_{row.source_role}",
                    "source_url": row.source_url,
                    "file": file_label,
                    "sha256": _sha256_bytes(prepared),
                    "width": width,
                    "height": height,
                    "original_file": original_relative.as_posix(),
                    "original_sha256": content_sha,
                    "original_width": original_width,
                    "original_height": original_height,
                    "original_format": image_format,
                    "original_extension": original_suffix,
                    "source_filename": unquote(Path(urlparse(str(row.source_url)).path).name)[:240],
                    "content_type": content_type,
                    "original_bytes": len(payload),
                    "pixel_sha256": pixel_sha,
                    "resized": (width, height) != (original_width, original_height),
                }
            )
            if len(accepted) > effective_cap:
                raise ProductImageError(
                    f"중복 제거 후 고유 업체 이미지가 제한 {effective_cap}개를 초과했습니다."
                )
            content_seen[content_sha] = file_label
            pixel_seen[pixel_sha] = file_label
        if not accepted:
            raise ProductImageError("중복 제거 후 저장할 이미지가 없습니다.")
        manifest: dict[str, Any] = {
            "schema_version": 1,
            "kind": "myrealtrip_product_images",
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source_url": source_url,
            "canonical_url": canonical,
            "product_id": resolved_product_id,
            "image_policy": {
                "seller_only": True,
                "review_images_excluded": True,
                "recommendation_images_excluded": True,
                "hard_cap": HARD_IMAGE_CAP,
                "max_images": effective_cap,
                "preserve_original_bytes": True,
                "prepared_format": "JPEG",
                "prepared_max_side": None,
                "requested_prepared_max_side_ignored": int(prepared_max_side),
                "prepared_upscale": False,
                "prepared_resize": False,
            },
            "candidate_count": len(rows),
            "unique_url_count": len(unique_rows),
            "image_count": len(accepted),
            "accepted": accepted,
            "duplicates": duplicates,
            "rejected": _durable_rejected_images(rejected_candidates),
        }
        if collection_proof is not None:
            manifest["collection_proof"] = json.loads(
                json.dumps(dict(collection_proof), ensure_ascii=False)
            )
        if facts is not None:
            manifest["facts"] = _durable_facts(facts)
        atomic_write_json(stage / "manifest.json", manifest)
        os.replace(stage, target)
        return {**manifest, "manifest": str((target / "manifest.json").resolve())}
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def build_product_bundle(
    rendered_html: str,
    product_url: str,
    output_dir: str | Path,
    *,
    hydrated_rows: Sequence[Mapping[str, Any]] = (),
    collection_proof: Mapping[str, Any] | None = None,
    session: Any | None = None,
    timeout: float = 20,
    max_images: int = HARD_IMAGE_CAP,
    prepared_max_side: int = DEFAULT_PREPARED_MAX_SIDE,
    jpeg_quality: int = 92,
) -> dict[str, Any]:
    """Resolve, parse, hydrate, and atomically write one product bundle."""

    reference = resolve_product_url(product_url, session=session, timeout=timeout)
    if not reference.canonical_url or not reference.product_id:
        raise ProductURLValidationError("상품 URL을 정규화하지 못했습니다.")
    facts = parse_product_html(rendered_html, source_url=reference.canonical_url)
    classification = classify_image_candidates(rendered_html, page_url=reference.canonical_url)
    normalized_proof = validate_collection_proof(
        collection_proof,
        classification,
        hydrated_rows,
        page_url=reference.canonical_url,
    )
    candidates = validate_hydrated_candidates(
        classification,
        hydrated_rows,
        page_url=reference.canonical_url,
        require_all=True,
    )
    result = download_product_images(
        candidates,
        output_dir,
        source_url=product_url,
        canonical_url=reference.canonical_url,
        product_id=reference.product_id,
        facts=facts,
        collection_proof=normalized_proof,
        rejected_candidates=(
            classification.get("rejected")
            if isinstance(classification.get("rejected"), list)
            else []
        ),
        session=session,
        timeout=timeout,
        max_images=max_images,
        prepared_max_side=prepared_max_side,
        jpeg_quality=jpeg_quality,
    )
    result["parsed_facts"] = facts
    result["candidate_summary"] = {
        "candidate_count": classification["candidate_count"],
        "requires_hydration_count": classification["requires_hydration_count"],
        "rejected_count": len(classification["rejected"]),
    }
    result["collection_proof"] = normalized_proof
    return result


def _load_cli_html(path: str | Path) -> tuple[str, str, Mapping[str, Any]]:
    input_path = Path(path).expanduser().resolve()
    payload = json.loads(input_path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, Mapping):
        raise MyRealTripProductError("입력 JSON은 객체여야 합니다.")
    product_url = str(payload.get("product_url") or "").strip()
    rendered_html = str(payload.get("rendered_html") or "")
    html_file = str(payload.get("html_file") or "").strip()
    if bool(rendered_html) == bool(html_file):
        raise MyRealTripProductError("rendered_html과 html_file 중 하나만 필요합니다.")
    if html_file:
        html_path = Path(html_file).expanduser()
        if not html_path.is_absolute():
            html_path = input_path.parent / html_path
        rendered_html = html_path.resolve().read_text(encoding="utf-8-sig")
    return product_url, rendered_html, payload


def _load_cli_input(
    path: str | Path,
) -> tuple[str, str, list[Mapping[str, Any]], Mapping[str, Any] | None]:
    product_url, rendered_html, payload = _load_cli_html(path)
    raw_rows = payload.get("hydrated_rows")
    if not isinstance(raw_rows, list) or not all(isinstance(row, Mapping) for row in raw_rows):
        raise MyRealTripProductError("hydrated_rows는 객체 배열이어야 합니다.")
    raw_proof = payload.get("collection_proof")
    if raw_proof is not None and not isinstance(raw_proof, Mapping):
        raise MyRealTripProductError("collection_proof는 객체여야 합니다.")
    return product_url, rendered_html, list(raw_rows), raw_proof


def _cli_paths(args: argparse.Namespace) -> tuple[Path, Path, Path | None]:
    input_path = Path(args.input).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    facts_path = Path(args.facts_output).expanduser().resolve() if args.facts_output else None
    if not input_path.is_file() or input_path.is_symlink():
        raise MyRealTripProductError("브라우저 staging 입력이 없거나 안전하지 않습니다.")
    if output_dir.exists() or output_dir == input_path or input_path in output_dir.parents:
        raise MyRealTripProductError("출력 번들 경로가 이미 있거나 입력과 충돌합니다.")
    if facts_path is not None:
        staging_root = (googleblog_home() / "mato-blog-codex" / "staging").resolve()
        if (
            facts_path.exists()
            or facts_path == input_path
            or facts_path == output_dir
            or output_dir in facts_path.parents
            or staging_root not in facts_path.parents
        ):
            raise MyRealTripProductError(
                "facts-output은 새 파일이어야 하며 관리 staging 안에서 번들과 분리돼야 합니다."
            )
    if args.purge_input:
        staging_root = (googleblog_home() / "mato-blog-codex" / "staging").resolve()
        if staging_root not in input_path.parents:
            raise MyRealTripProductError("purge-input은 관리 staging 입력에만 사용할 수 있습니다.")
    return input_path, output_dir, facts_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a strict MyRealTrip product bundle.")
    parser.add_argument("--input", required=True, help="staging browser-product.json")
    parser.add_argument("--output-dir")
    parser.add_argument(
        "--classify-output",
        help="write an exact Browser hydration candidate skeleton to managed staging",
    )
    parser.add_argument(
        "--facts-output",
        help="optional staging JSON for parsed facts/reviews; delete after brief generation",
    )
    parser.add_argument(
        "--purge-input",
        action="store_true",
        help="delete the raw rendered-HTML staging input after a successful bundle",
    )
    parser.add_argument("--max-images", type=int, default=HARD_IMAGE_CAP)
    parser.add_argument("--jpeg-quality", type=int, default=92)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cli_stage: Path | None = None
    facts_path: Path | None = None
    try:
        if args.classify_output:
            if args.output_dir or args.facts_output or args.purge_input:
                raise MyRealTripProductError(
                    "classify-output은 bundle 출력 옵션과 함께 사용할 수 없습니다."
                )
            input_path = Path(args.input).expanduser().resolve()
            classify_output = Path(args.classify_output).expanduser().resolve()
            staging_root = (googleblog_home() / "mato-blog-codex" / "staging").resolve()
            if (
                not input_path.is_file()
                or input_path.is_symlink()
                or classify_output.exists()
                or staging_root not in classify_output.parents
            ):
                raise MyRealTripProductError(
                    "분류 입력 또는 managed staging 출력 경로가 안전하지 않습니다."
                )
            product_url, rendered_html, _payload = _load_cli_html(input_path)
            reference = resolve_product_url(product_url)
            if not reference.canonical_url or not reference.product_id:
                raise ProductURLValidationError("상품 URL을 정규화하지 못했습니다.")
            facts = parse_product_html(rendered_html, source_url=reference.canonical_url)
            classification = classify_image_candidates(
                rendered_html, page_url=reference.canonical_url
            )
            candidates = [
                {
                    "candidate_id": row["candidate_id"],
                    "dom_index": row["dom_index"],
                    "document_image_index": int(row["dom_index"]) - 1,
                    "source_role": row["source_role"],
                    "current_src": row.get("source_url") or "",
                    "requires_hydration": bool(row.get("requires_hydration")),
                }
                for row in classification["allowed"]
            ]
            atomic_write_json(
                classify_output,
                {
                    "schema_version": 1,
                    "kind": "myrealtrip_browser_hydration_candidates",
                    "product_url": product_url,
                    "canonical_url": reference.canonical_url,
                    "product_id": reference.product_id,
                    "title": facts.get("title"),
                    "candidate_count": len(candidates),
                    "requires_hydration_count": classification["requires_hydration_count"],
                    "available_sections": classification["available_sections"],
                    "rejected_count": len(classification["rejected"]),
                    "candidates": candidates,
                },
            )
            print(
                json.dumps(
                    {
                        "ok": True,
                        "mode": "classify",
                        "output": str(classify_output),
                        "product_id": reference.product_id,
                        "candidate_count": len(candidates),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if not args.output_dir:
            raise MyRealTripProductError("bundle 생성에는 --output-dir이 필요합니다.")
        input_path, output_dir, facts_path = _cli_paths(args)
        product_url, rendered_html, hydrated_rows, collection_proof = _load_cli_input(input_path)
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        cli_stage = Path(
            tempfile.mkdtemp(prefix=f".{output_dir.name}-cli-", dir=str(output_dir.parent))
        )
        staged_bundle = cli_stage / "bundle"
        result = build_product_bundle(
            rendered_html,
            product_url,
            staged_bundle,
            hydrated_rows=hydrated_rows,
            collection_proof=collection_proof,
            max_images=args.max_images,
            jpeg_quality=args.jpeg_quality,
        )
        facts = result.get("parsed_facts") if isinstance(result.get("parsed_facts"), Mapping) else {}
        facts_output = ""
        if facts_path is not None:
            atomic_write_json(facts_path, dict(facts))
            facts_output = str(facts_path)
        if args.purge_input:
            input_path.unlink()
        os.replace(staged_bundle, output_dir)
        result["manifest"] = str(output_dir / "manifest.json")
        summary = {
            "ok": True,
            "manifest": result.get("manifest"),
            "product_id": result.get("product_id"),
            "title": facts.get("title"),
            "rating": facts.get("rating"),
            "review_count": facts.get("review_count"),
            "price_text": facts.get("price_text"),
            "image_count": result.get("image_count"),
            "facts_output": facts_output,
            "raw_input_removed": bool(args.purge_input),
        }
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    except Exception as exc:
        if facts_path is not None and facts_path.is_file():
            facts_path.unlink(missing_ok=True)
        print(json.dumps({"ok": False, "error": _normalized_text(exc)[:300]}, ensure_ascii=False))
        return 2
    finally:
        if cli_stage is not None:
            shutil.rmtree(cli_stage, ignore_errors=True)


__all__ = [
    "BRIDGE_HOSTS",
    "CANONICAL_HOST",
    "DEFAULT_PREPARED_MAX_SIDE",
    "HARD_IMAGE_CAP",
    "ImageCandidate",
    "MyRealTripProductError",
    "ProductImageError",
    "ProductParseError",
    "ProductReference",
    "ProductURLValidationError",
    "SHORT_HOST",
    "build_product_bundle",
    "build_parser",
    "classify_image_candidates",
    "download_product_images",
    "main",
    "parse_product_html",
    "resolve_product_url",
    "validate_hydrated_candidates",
    "validate_collection_proof",
    "validate_product_url",
]


if __name__ == "__main__":
    raise SystemExit(main())
