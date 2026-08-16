"""Validate Mato draft structure, count, and cross-draft distinctness."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import unquote, urlsplit

try:
    from .history import append_event, update_run
    from .mato_common import load_run
except ImportError:
    from history import append_event, update_run  # type: ignore[no-redef]
    from mato_common import load_run  # type: ignore[no-redef]


TITLE_PREFIX = "제목을입력해주세요1:"
BODY1_MARKER = "본문1:"
INTRO_MARKER = "인트로1:"
BODY_MARKER = "본문2:"
HEADING_PREFIX = "ㅂㅂㅂ"
HEADING_PREFIXES = (HEADING_PREFIX, "소제목")
IMAGE_TAG_RE = re.compile(r"\[(image_([1-9]\d*)\.jpg)\]", re.IGNORECASE)
PRODUCT_IMAGE_RE = re.compile(r"image_([1-9]\d*)\.jpg", re.IGNORECASE)
PRODUCT_IMAGE_LINE_RE = re.compile(r"\[image_[1-9]\d*\.jpg\]", re.IGNORECASE)
TABLE_START_RE = re.compile(r"^표\s+\d+\s*[xX×]\s*\d+\s+시작$")
TABLE_CELL_RE = re.compile(r"^\(\d+\s*,\s*\d+\)\s*.+$")
TABLE_END_RE = re.compile(r"^표\s+\d+\s*[xX×]\s*\d+\s+끝$")
REVIEW_VALUE_RE = re.compile(
    r"(?:^|[/\\?&=_.\-\s])(reviews?|reviewphotos?|reviewimages?|후기사진|후기)"
    r"(?=$|[/\\?&=_.\-\s])",
    re.IGNORECASE,
)
REVIEW_KEY_RE = re.compile(
    r"(?:^|_)(?:is_)?(?:reviews?|reviewphotos?|reviewimages?|후기사진|후기)(?:$|_)",
    re.IGNORECASE,
)
PRODUCT_SECTION_IDS = frozenset(
    {
        "INTRODUCTION",
        "ITINERARIES",
        "INCLUDE_EXCLUDE",
        "USAGE",
        "ESSENTIALS",
        "REFUND",
        "REVIEW",
    }
)
REQUIRED_PRODUCT_SECTION_IDS = frozenset(
    {"INTRODUCTION", "INCLUDE_EXCLUDE", "USAGE", "ESSENTIALS", "REFUND", "REVIEW"}
)
EXPANDABLE_PRODUCT_SECTION_IDS = frozenset(
    {"INTRODUCTION", "ITINERARIES", "ESSENTIALS", "REVIEW"}
)
PRODUCT_SOURCE_TYPES = {"myrealtrip_product", "naver_shopping_product"}


def _is_product_prose_line(line: str, *, link_url: str) -> bool:
    value = str(line or "").strip()
    if not value or value == link_url or PRODUCT_IMAGE_LINE_RE.fullmatch(value):
        return False
    if value.startswith(("ㅂㅂㅂ", "소제목", "!!", "http://", "https://")):
        return False
    if TABLE_START_RE.fullmatch(value) or TABLE_CELL_RE.fullmatch(value) or TABLE_END_RE.fullmatch(value):
        return False
    return bool(re.search(r"[가-힣A-Za-z]", value))


def parse_mato_text(text: str) -> dict[str, Any]:
    """Parse every Mato Helper editor region without flattening its actions.

    The original Naver writer treats text before ``인트로1:``/``본문2:`` as
    ``본문1`` content, then types ``인트로1`` and ``본문2`` into their own
    template placeholders.  Keep those regions separate for upload while
    retaining ``body`` as the validated ``본문2`` payload for compatibility.
    """

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.splitlines()
    first = next((line.strip() for line in lines if line.strip()), "")
    title = first[len(TITLE_PREFIX):].strip() if first.startswith(TITLE_PREFIX) else ""

    headings: list[str] = []
    for line in lines:
        stripped = line.strip()
        for prefix in HEADING_PREFIXES:
            if stripped.startswith(prefix):
                headings.append(stripped[len(prefix):].strip())
                break

    def marker_indexes(marker: str) -> list[int]:
        return [index for index, line in enumerate(lines) if line.strip() == marker]

    body1_indexes = marker_indexes(BODY1_MARKER)
    intro_indexes = marker_indexes(INTRO_MARKER)
    body2_indexes = marker_indexes(BODY_MARKER)
    body2_start = body2_indexes[0] if body2_indexes else len(lines)
    intro_before_body2 = [index for index in intro_indexes if index < body2_start]
    intro_start = intro_before_body2[0] if intro_before_body2 else body2_start
    body1_before_intro = [index for index in body1_indexes if index < intro_start]

    if body1_before_intro:
        body1_lines = lines[body1_before_intro[0] + 1:intro_start]
    else:
        body1_lines = [
            line
            for line in lines[:intro_start]
            if line.strip() and not line.strip().startswith(TITLE_PREFIX)
        ]
    intro_lines = lines[intro_start + 1:body2_start] if intro_before_body2 else []
    body2_lines = lines[body2_start + 1:] if body2_indexes else []

    def trim_trailing_blanks(values: list[str]) -> list[str]:
        cleaned = list(values)
        while cleaned and not cleaned[-1].strip():
            cleaned.pop()
        return cleaned

    body1_lines = trim_trailing_blanks(body1_lines)
    intro_lines = trim_trailing_blanks(intro_lines)
    body2_lines = trim_trailing_blanks(body2_lines)
    marker_count = sum(1 for line in lines if line.strip() == BODY_MARKER)
    body = "\n".join(body2_lines).strip()
    return {
        "title": title,
        "headings": headings,
        "body1_marker_count": len(body1_indexes),
        "intro_marker_count": len(intro_indexes),
        "body_marker_count": marker_count,
        "body1_lines": body1_lines,
        "intro_lines": intro_lines,
        "body2_lines": body2_lines,
        "body": body,
        "text": normalized,
    }


def _normalized_similarity_text(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", value).casefold()


def _posts_root(run_dir: Path) -> Path:
    """Return the physical posts root, rejecting a redirected root directory."""

    directory = run_dir.expanduser().resolve()
    posts_root = directory / "posts"
    resolved_root = posts_root.resolve(strict=False)
    if resolved_root != posts_root:
        raise ValueError("run/posts must not be a symlink or junction")
    return posts_root


def resolve_post_path(run_dir: Path, value: str | Path) -> Path:
    """Resolve one generated post path without allowing it to leave ``run/posts``.

    Relative paths containing ``..`` are rejected even when normalization would
    happen to keep them inside the tree.  Resolving the final candidate also
    catches files and nested directories redirected by symlinks or junctions.
    """

    directory = run_dir.expanduser().resolve()
    posts_root = _posts_root(directory)
    raw = Path(value)
    if not raw.is_absolute():
        if raw.drive or raw.root or ".." in raw.parts:
            raise ValueError(f"generated post path is unsafe: {value}")
        candidate = directory / raw
    else:
        candidate = raw
    resolved = candidate.resolve(strict=False)
    if resolved == posts_root or posts_root not in resolved.parents:
        raise ValueError(f"generated post path escapes run/posts: {value}")
    return resolved


def _relative_post_path(run_dir: Path, path: Path) -> str:
    return str(path.relative_to(run_dir.expanduser().resolve()))


def _manifest_sha256(manifest: Sequence[Mapping[str, str]]) -> str:
    payload = json.dumps(
        list(manifest), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _structured_sha256(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _image_dimensions(path: Path) -> tuple[int, int]:
    try:
        from PIL import Image
    except Exception as exc:  # pragma: no cover - bootstrap guarantees Pillow.
        raise ValueError(f"Pillow is required to validate product images: {exc}") from exc
    try:
        with Image.open(path) as image:
            image.load()
            if image.format != "JPEG":
                raise ValueError("prepared product image must use the JPEG format")
            return int(image.width), int(image.height)
    except Exception as exc:
        raise ValueError(f"product image is not a readable JPEG: {path.name}") from exc


def _run_relative_file(run_dir: Path, value: str | Path, *, label: str) -> Path:
    directory = run_dir.expanduser().resolve()
    raw = Path(value)
    if not raw.is_absolute():
        if raw.drive or raw.root or ".." in raw.parts:
            raise ValueError(f"{label} path is unsafe: {value}")
        candidate = directory / raw
    else:
        candidate = raw
    probe = candidate
    while probe != directory and directory in probe.resolve(strict=False).parents:
        if probe.is_symlink() or bool(getattr(probe, "is_junction", lambda: False)()):
            raise ValueError(f"{label} path must not contain a symlink: {value}")
        probe = probe.parent
    resolved = candidate.resolve(strict=False)
    if resolved == directory or directory not in resolved.parents:
        raise ValueError(f"{label} path escapes the run: {value}")
    return resolved


def _has_review_signal(item: Mapping[str, Any]) -> bool:
    source_url = str(item.get("source_url") or "")
    parsed = urlsplit(source_url)
    url_blob = unquote(f"{parsed.path}?{parsed.query}").replace("-", "_").casefold()
    if any(
        token in url_blob
        for token in ("/review/", "/reviews/", "/review_", "review_photo", "후기사진")
    ):
        return True
    if bool(item.get("review_photo") or item.get("is_review")):
        return True
    for key in (
        "candidate_id",
        "source_id",
        "source_kind",
        "section",
        "source_section",
        "badge",
        "label",
    ):
        signal = re.sub(r"[\s_-]+", "", str(item.get(key) or "")).casefold()
        if "review" in signal or "후기" in signal:
            return True
    signals = item.get("review_signals")
    if isinstance(signals, Mapping):
        return any(bool(value) for value in signals.values())
    if isinstance(signals, list):
        return any(str(value).strip() for value in signals)
    if bool(signals):
        return True

    def walk(value: object) -> bool:
        if isinstance(value, Mapping):
            for raw_key, nested in value.items():
                key = re.sub(r"[\s-]+", "_", str(raw_key)).casefold()
                if REVIEW_KEY_RE.search(key) and bool(nested):
                    return True
                if walk(nested):
                    return True
            return False
        if isinstance(value, list):
            return any(walk(nested) for nested in value)
        return isinstance(value, str) and bool(REVIEW_VALUE_RE.search(unquote(value)))

    return walk(item)


def _load_naver_shopping_manifest(
    run_dir: Path,
    manifest_path: Path,
    raw_bytes: bytes,
    payload: Mapping[str, Any],
    request: Mapping[str, Any] | None,
) -> tuple[str, str, list[dict[str, Any]]]:
    """Validate a gallery-only Naver Shopping original-image manifest."""

    accepted = payload.get("accepted")
    proof = payload.get("collection_proof")
    policy = payload.get("image_policy")
    try:
        declared_count = int(payload.get("image_count") or 0)
        candidate_count = int(payload.get("candidate_count") or 0)
        stable = [int(value) for value in proof.get("stable_image_counts", [])]
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("Naver Shopping product image manifest counts are invalid") from exc
    canonical = str(payload.get("canonical_url") or "")
    canonical_parts = urlsplit(canonical)
    product_id = str(payload.get("product_id") or "")
    if (
        not isinstance(accepted, list)
        or not accepted
        or declared_count != len(accepted)
        or declared_count > 80
        or not re.fullmatch(r"[1-9][0-9]*", product_id)
        or canonical_parts.scheme.lower() != "https"
        or (canonical_parts.hostname or "").lower() != "brand.naver.com"
        or not re.fullmatch(rf"/[^/]+/products/{product_id}", canonical_parts.path.rstrip("/"))
        or not isinstance(policy, Mapping)
        or policy.get("seller_only") is not True
        or policy.get("original_url_without_resize_query") is not True
        or policy.get("review_images_excluded") is not True
        or not isinstance(proof, Mapping)
        or proof.get("review_tab_opened") is not True
        or proof.get("review_more_expanded") is not True
        or str(proof.get("page_url") or "").rstrip("/") != canonical.rstrip("/")
        or stable != [candidate_count, candidate_count]
    ):
        raise ValueError("Naver Shopping product image manifest has incomplete source evidence")
    if request is not None:
        if str(request.get("source_type") or "") != "naver_shopping_product":
            raise ValueError("Naver Shopping manifest used by another product source")
        if str(payload.get("source_url") or "") != str(request.get("product_url") or ""):
            raise ValueError("product image manifest source_url does not match the request")
        request_policy = request.get("image_policy")
        if not isinstance(request_policy, Mapping) or declared_count > int(request_policy.get("max_images") or 0):
            raise ValueError("product image manifest exceeds request max_images")
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(accepted, start=1):
        if not isinstance(raw, Mapping):
            raise ValueError(f"accepted image {index} is invalid")
        source = str(raw.get("source_url") or "")
        parsed_source = urlsplit(source)
        relative_file = str(raw.get("file") or "").replace("\\", "/")
        prepared = _run_relative_file(
            run_dir, manifest_path.parent / relative_file, label=f"accepted image {index} prepared file"
        )
        expected_hash = str(raw.get("sha256") or "").lower()
        if (
            int(raw.get("index") or 0) != index
            or raw.get("classification") != "product"
            or str(raw.get("source_role") or raw.get("role") or "") != "gallery"
            or parsed_source.scheme.lower() != "https"
            or (parsed_source.hostname or "").lower() != "shop-phinf.pstatic.net"
            or bool(parsed_source.query)
            or relative_file != f"prepared/image_{index}.jpg"
            or not prepared.is_file()
            or not re.fullmatch(r"[0-9a-f]{64}", expected_hash)
            or hashlib.sha256(prepared.read_bytes()).hexdigest() != expected_hash
            or _has_review_signal(raw)
        ):
            raise ValueError(f"accepted image {index} is not a valid original seller gallery image")
        width, height = _image_dimensions(prepared)
        if width != int(raw.get("width") or 0) or height != int(raw.get("height") or 0):
            raise ValueError(f"accepted image {index} dimensions do not match")
        normalized.append(
            {
                "index": index,
                "classification": "product",
                "source_role": "gallery",
                "source_url": source,
                "file": relative_file,
                "sha256": expected_hash,
                "width": width,
                "height": height,
            }
        )
    return (
        str(manifest_path.relative_to(run_dir.expanduser().resolve())),
        hashlib.sha256(raw_bytes).hexdigest(),
        normalized,
    )


def _load_product_manifest(
    run_dir: Path,
    manifest_value: object,
    request: Mapping[str, Any] | None = None,
) -> tuple[str, str, list[dict[str, Any]]]:
    manifest_path = _run_relative_file(
        run_dir, str(manifest_value or ""), label="product image manifest"
    )
    if not manifest_path.is_file():
        raise ValueError(f"product image manifest is missing: {manifest_value}")
    raw_bytes = manifest_path.read_bytes()
    payload = json.loads(raw_bytes.decode("utf-8-sig"))
    if not isinstance(payload, Mapping):
        raise ValueError("product image manifest has an invalid kind")
    if payload.get("kind") == "naver_shopping_product_images":
        return _load_naver_shopping_manifest(run_dir, manifest_path, raw_bytes, payload, request)
    if payload.get("kind") != "myrealtrip_product_images":
        raise ValueError("product image manifest has an invalid kind")
    accepted = payload.get("accepted")
    if not isinstance(accepted, list) or not accepted:
        raise ValueError("product image manifest has no accepted images")
    try:
        declared_count = int(payload.get("image_count"))
    except (TypeError, ValueError) as exc:
        raise ValueError("product image manifest has no valid image_count") from exc
    if declared_count != len(accepted) or declared_count > 80:
        raise ValueError("product image manifest image_count does not match accepted images")
    if request is not None:
        source_url = str(payload.get("source_url") or "").strip()
        if source_url != str(request.get("product_url") or "").strip():
            raise ValueError("product image manifest source_url does not match the request")
        product_id = str(payload.get("product_id") or "").strip()
        canonical_url = str(payload.get("canonical_url") or "").strip()
        canonical_parts = urlsplit(canonical_url)
        if (
            not re.fullmatch(r"[1-9][0-9]*", product_id)
            or canonical_parts.scheme.lower() != "https"
            or (canonical_parts.hostname or "").lower() != "experiences.myrealtrip.com"
            or canonical_parts.path.rstrip("/") != f"/products/{product_id}"
        ):
            raise ValueError("product image manifest canonical product identity is invalid")
        request_policy = request.get("image_policy")
        max_images = (
            int(request_policy.get("max_images") or 0)
            if isinstance(request_policy, Mapping)
            else 0
        )
        if max_images <= 0 or declared_count > max_images:
            raise ValueError("product image manifest exceeds request max_images")
        manifest_policy = payload.get("image_policy")
        if not isinstance(manifest_policy, Mapping) or not (
            manifest_policy.get("seller_only") is True
            and manifest_policy.get("review_images_excluded") is True
            and manifest_policy.get("recommendation_images_excluded") is True
        ):
            raise ValueError("product image manifest has no seller-only policy evidence")
        proof = payload.get("collection_proof")
        available_raw = proof.get("available_sections") if isinstance(proof, Mapping) else None
        available = (
            {str(value).strip().upper() for value in available_raw if str(value).strip()}
            if isinstance(available_raw, list)
            else set()
        )
        expanded = (
            {str(value).upper() for value in proof.get("expanded_sections", [])}
            if isinstance(proof, Mapping) and isinstance(proof.get("expanded_sections"), list)
            else set()
        )
        required_expanded = available.intersection(EXPANDABLE_PRODUCT_SECTION_IDS)
        stable = proof.get("stable_candidate_counts") if isinstance(proof, Mapping) else None
        try:
            candidate_count = int(payload.get("candidate_count") or 0)
            unique_url_count = int(payload.get("unique_url_count") or 0)
            proof_count = int(proof.get("candidate_count") or 0) if isinstance(proof, Mapping) else 0
            gallery_visited = (
                int(proof.get("gallery_visited_count") or 0)
                if isinstance(proof, Mapping)
                else 0
            )
            stable_counts = [int(value) for value in stable] if isinstance(stable, list) else []
        except (TypeError, ValueError) as exc:
            raise ValueError("product image manifest collection counts are invalid") from exc
        duplicates = payload.get("duplicates")
        if (
            not isinstance(proof, Mapping)
            or not isinstance(available_raw, list)
            or len(available) != len(available_raw)
            or not available.issubset(PRODUCT_SECTION_IDS)
            or not REQUIRED_PRODUCT_SECTION_IDS.issubset(available)
            or proof.get("page_end_reached") is not True
            or ("REVIEW" in available and proof.get("review_all_opened") is not True)
            or expanded != required_expanded
            or len(stable_counts) != 2
            or stable_counts != [candidate_count, candidate_count]
            or proof_count != candidate_count
            or str(proof.get("page_url") or "").rstrip("/") != canonical_url.rstrip("/")
            or candidate_count <= 0
            or not (0 < declared_count <= unique_url_count <= candidate_count)
            or not isinstance(duplicates, list)
            or len(duplicates) != candidate_count - declared_count
        ):
            raise ValueError("product image manifest has no complete Browser collection proof")

    normalized: list[dict[str, Any]] = []
    allowed_roles = {"gallery", "introduction", "itinerary"}
    for position, raw in enumerate(accepted, start=1):
        if not isinstance(raw, Mapping):
            raise ValueError(f"accepted product image {position} is invalid")
        try:
            index = int(raw.get("index"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"accepted product image {position} has no valid index") from exc
        if index != position:
            raise ValueError("accepted product image indexes must be contiguous from 1")
        classification = str(raw.get("classification") or "").strip().casefold()
        explicit_role = str(raw.get("source_role") or "").strip().casefold()
        alias_role = str(raw.get("role") or "").strip().casefold()
        if explicit_role and alias_role and explicit_role != alias_role:
            raise ValueError(f"accepted image {index} has conflicting product source roles")
        source_role = explicit_role or alias_role
        # Read the brief compatibility shape where the role was temporarily
        # stored in classification, but normalize every durable row to product.
        if classification in allowed_roles:
            if source_role and source_role != classification:
                raise ValueError(f"accepted image {index} has conflicting product source roles")
            source_role = source_role or classification
            classification = "product"
        if classification not in {"product", "seller_product"}:
            raise ValueError(f"accepted image {index} is not classified as a product image")
        if source_role not in allowed_roles:
            raise ValueError(f"accepted image {index} has an invalid product source role")
        required_section = {
            "introduction": "INTRODUCTION",
            "itinerary": "ITINERARIES",
        }.get(source_role)
        if request is not None and required_section and required_section not in available:
            raise ValueError(
                f"accepted image {index} has no matching available product section"
            )
        if _has_review_signal(raw):
            raise ValueError(f"accepted image {index} contains a review-photo signal")
        source_url = str(raw.get("source_url") or "").strip()
        parsed_source = urlsplit(source_url)
        if (
            parsed_source.scheme.lower() != "https"
            or (parsed_source.hostname or "").lower()
            not in {
                "dry7pvlp22cox.cloudfront.net",
                "d2ur7st6jjikze.cloudfront.net",
            }
        ):
            raise ValueError(f"accepted image {index} has no approved seller source URL")
        relative_file = str(raw.get("file") or "").strip()
        expected_suffix = f"prepared/image_{index}.jpg"
        if relative_file.replace("\\", "/") != expected_suffix:
            raise ValueError(f"accepted image {index} has an unexpected prepared file")
        prepared_candidate = manifest_path.parent / Path(relative_file)
        prepared = _run_relative_file(
            run_dir, prepared_candidate, label=f"accepted image {index} prepared file"
        )
        if (
            prepared == manifest_path.parent
            or manifest_path.parent not in prepared.parents
            or not prepared.is_file()
        ):
            raise ValueError(f"accepted image {index} prepared file is missing or unsafe")
        expected_hash = str(raw.get("sha256") or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            raise ValueError(f"accepted image {index} has an invalid SHA256")
        actual_hash = hashlib.sha256(prepared.read_bytes()).hexdigest()
        if actual_hash != expected_hash:
            raise ValueError(f"accepted image {index} prepared SHA256 does not match")
        try:
            width = int(raw.get("width"))
            height = int(raw.get("height"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"accepted image {index} has invalid dimensions") from exc
        if width <= 0 or height <= 0 or _image_dimensions(prepared) != (width, height):
            raise ValueError(f"accepted image {index} prepared dimensions do not match")
        normalized.append(
            {
                "index": index,
                "classification": "product",
                "source_role": source_role,
                "source_url": source_url,
                "file": relative_file.replace("\\", "/"),
                "sha256": expected_hash,
                "width": width,
                "height": height,
            }
        )
    if request is not None and gallery_visited < sum(
        1 for row in normalized if row["source_role"] == "gallery"
    ):
        raise ValueError("product image manifest gallery visit proof is incomplete")
    role_phase = {"gallery": 0, "introduction": 1, "itinerary": 2}
    phases = [role_phase[str(item["source_role"])] for item in normalized]
    if phases != sorted(phases):
        raise ValueError(
            "accepted product image roles must retain gallery/introduction/itinerary order"
        )
    relative_manifest = str(manifest_path.relative_to(run_dir.expanduser().resolve()))
    return relative_manifest, hashlib.sha256(raw_bytes).hexdigest(), normalized


def discover_post_files(run_dir: Path, run: Mapping[str, Any]) -> list[Path]:
    directory = run_dir.expanduser().resolve()
    generation = run.get("generation")
    result: list[Path] = []
    if isinstance(generation, Mapping) and isinstance(generation.get("posts"), list):
        for item in generation["posts"]:
            if not isinstance(item, Mapping) or not item.get("file"):
                continue
            result.append(resolve_post_path(directory, str(item["file"])))
    if result:
        if len(set(result)) != len(result):
            raise ValueError("generation contains duplicate post paths")
        return result
    posts_root = _posts_root(directory)
    if not posts_root.exists():
        return []
    discovered = [resolve_post_path(directory, path) for path in posts_root.rglob("*_함축.txt")]
    if len(set(discovered)) != len(discovered):
        raise ValueError("posts directory contains duplicate resolved post paths")
    return sorted(discovered, key=lambda path: _relative_post_path(directory, path))


def _generation_records_by_path(
    run_dir: Path, run: Mapping[str, Any]
) -> dict[Path, Mapping[str, Any]]:
    generation = run.get("generation")
    raw_posts = generation.get("posts") if isinstance(generation, Mapping) else None
    if not isinstance(raw_posts, list):
        return {}
    records: dict[Path, Mapping[str, Any]] = {}
    for raw in raw_posts:
        if not isinstance(raw, Mapping) or not raw.get("file"):
            continue
        path = resolve_post_path(run_dir, str(raw["file"]))
        if path in records:
            raise ValueError("generation contains duplicate post paths")
        records[path] = raw
    return records


def _product_request(run: Mapping[str, Any]) -> Mapping[str, Any] | None:
    request = run.get("request")
    if isinstance(request, Mapping) and str(request.get("source_type") or "") in PRODUCT_SOURCE_TYPES:
        return request
    return None


def _product_placement_rows(record: Mapping[str, Any]) -> list[tuple[str, int]]:
    raw_placements = record.get("image_placements")
    if raw_placements is None:
        return []
    if not isinstance(raw_placements, list) or not raw_placements:
        raise ValueError("product image_placements must be a non-empty array")
    rows: list[tuple[str, int]] = []
    for position, raw in enumerate(raw_placements, start=1):
        if not isinstance(raw, Mapping):
            raise ValueError(f"product image placement {position} is invalid")
        name = str(raw.get("file") or raw.get("image") or raw.get("name") or "").lower()
        expected_name = f"image_{position}.jpg"
        if name != expected_name:
            raise ValueError("product image placements must use every image_N.jpg in order")
        try:
            manifest_index = int(raw.get("manifest_index") or position)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"product image placement {position} has an invalid manifest index") from exc
        rows.append((name, manifest_index))
    indexes = [manifest_index for _name, manifest_index in rows]
    if sorted(indexes) != list(range(1, len(rows) + 1)) or len(set(indexes)) != len(indexes):
        raise ValueError("product image placement manifest indexes must cover every image once")
    return rows


def _product_placement_names(record: Mapping[str, Any]) -> list[str]:
    return [name for name, _manifest_index in _product_placement_rows(record)]


def _product_layout_signature(body: str, *, link_url: str) -> str:
    rows: list[str] = []
    for line in (value.strip() for value in body.splitlines() if value.strip()):
        if line == link_url:
            rows.append("LINK")
        elif re.fullmatch(r"\[image_[1-9]\d*\.jpg\]", line, re.IGNORECASE):
            rows.append(f"IMAGE:{line.casefold()}")
        elif line.startswith("ㅂㅂㅂ"):
            rows.append("HEADING")
        else:
            rows.append("PARAGRAPH")
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


def _validate_product_title_plan(title: str, record: Mapping[str, Any]) -> None:
    plan = record.get("title_plan")
    if not isinstance(plan, Mapping):
        raise ValueError("product post requires a title_plan")
    if str(plan.get("channel") or "").lower() != "naver":
        raise ValueError("product title_plan channel must be naver")
    if len(title) > 40:
        raise ValueError("product title exceeds the 40-character Naver editor limit")
    exact_title = str(plan.get("exact_title") or "").strip()
    if not exact_title or title != exact_title:
        raise ValueError("product title must exactly match title_plan.exact_title")
    if str(record.get("title") or "").strip() != title:
        raise ValueError("product TXT title no longer matches the generation record")
    raw_main = plan.get("main_terms")
    raw_sub = plan.get("subkeywords")
    if not isinstance(raw_main, list) or not raw_main:
        raise ValueError("product title_plan requires main_terms")
    if not isinstance(raw_sub, list) or not 1 <= len(raw_sub) <= 2:
        raise ValueError("product title_plan requires one or two subkeywords")
    hook = str(plan.get("hook") or "").strip()
    if not hook:
        raise ValueError("product title_plan requires a hook")
    compact_title = re.sub(r"[^0-9A-Za-z가-힣]+", "", title).casefold()
    required = [*raw_main, *raw_sub, hook]
    missing = [
        str(value)
        for value in required
        if re.sub(r"[^0-9A-Za-z가-힣]+", "", str(value)).casefold()
        not in compact_title
    ]
    if missing:
        raise ValueError(f"product title is missing title_plan values: {missing}")


def _product_link_url(record: Mapping[str, Any], request: Mapping[str, Any] | None) -> str:
    value = record.get("link_url")
    if not value and request is not None:
        value = request.get("product_url")
    url = str(value or "").strip()
    if not url or any(character.isspace() for character in url):
        raise ValueError("product link_url is missing or is not one standalone URL")
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or parsed.username
        or parsed.password
        or (
            host not in {"myrealt.rip", "myrealtrip.com", "www.myrealtrip.com", "naver.me", "brand.naver.com"}
            and not host.endswith(".myrealtrip.com")
        )
    ):
        raise ValueError("product link_url must be a supported public product URL")
    if request is not None and url != str(request.get("product_url") or "").strip():
        raise ValueError("product link_url must exactly match request.product_url")
    return url


def _expected_product_asset_keys(
    run_dir: Path, run: Mapping[str, Any]
) -> list[tuple[str, str]]:
    expected: list[tuple[str, str]] = []
    for post_path, record in _generation_records_by_path(run_dir, run).items():
        for name in _product_placement_names(record):
            expected.append(
                (
                    _relative_post_path(run_dir, post_path),
                    str((post_path.parent / name).relative_to(run_dir)),
                )
            )
    return expected


def verify_validation_manifest(
    run_dir: str | Path, run: Mapping[str, Any] | None = None
) -> list[Path]:
    """Verify and return the exact files covered by the last passing validation.

    Upload planning should call this helper instead of trusting mutable
    ``generation.posts`` metadata.  It rejects path-list changes, path escapes,
    and byte changes made after validation.
    """

    directory = Path(run_dir).expanduser().resolve()
    state = dict(run) if run is not None else load_run(directory)
    validation = state.get("validation")
    if not isinstance(validation, Mapping) or validation.get("status") != "passed":
        raise ValueError("drafts must pass validate_posts.py before upload planning")
    raw_manifest = validation.get("file_manifest")
    if not isinstance(raw_manifest, list) or not raw_manifest:
        raise ValueError("passing validation has no file manifest; validate drafts again")

    manifest: list[dict[str, str]] = []
    paths: list[Path] = []
    for index, item in enumerate(raw_manifest, start=1):
        if not isinstance(item, Mapping):
            raise ValueError(f"validation file manifest item {index} is invalid")
        file_value = item.get("file")
        expected_hash = str(item.get("sha256") or "").lower()
        if not isinstance(file_value, str) or not file_value.strip():
            raise ValueError(f"validation file manifest item {index} has no path")
        if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            raise ValueError(f"validation file manifest item {index} has an invalid SHA256")
        path = resolve_post_path(directory, file_value)
        if not path.is_file():
            raise FileNotFoundError(f"validated post is missing: {path}")
        actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual_hash != expected_hash:
            raise ValueError(f"validated post changed after validation: {file_value}")
        manifest.append({"file": _relative_post_path(directory, path), "sha256": expected_hash})
        paths.append(path)

    if len(set(paths)) != len(paths):
        raise ValueError("validation file manifest contains duplicate paths")
    stored_manifest_hash = str(validation.get("manifest_sha256") or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", stored_manifest_hash):
        raise ValueError("passing validation has no valid manifest SHA256; validate drafts again")
    if _manifest_sha256(manifest) != stored_manifest_hash:
        raise ValueError("validation file manifest changed; validate drafts again")

    current_files = discover_post_files(directory, state)
    if current_files != paths:
        raise ValueError("generated post paths changed after validation; validate drafts again")
    if int(validation.get("file_count") or -1) != len(paths):
        raise ValueError("validation file count does not match its manifest")

    expected_asset_keys = _expected_product_asset_keys(directory, state)
    raw_assets = validation.get("asset_manifest")
    raw_asset_hash = str(validation.get("asset_manifest_sha256") or "").lower()
    if raw_assets is None:
        if expected_asset_keys or raw_asset_hash:
            raise ValueError("passing product validation has no asset manifest; validate drafts again")
        return paths
    if not isinstance(raw_assets, list):
        raise ValueError("validation asset manifest is invalid; validate drafts again")
    if not re.fullmatch(r"[0-9a-f]{64}", raw_asset_hash):
        raise ValueError("passing validation has no valid asset manifest SHA256")
    if _structured_sha256(raw_assets) != raw_asset_hash:
        raise ValueError("validation asset manifest changed; validate drafts again")

    manifest_cache: dict[str, tuple[str, str, list[dict[str, Any]]]] = {}
    actual_asset_keys: list[tuple[str, str]] = []
    post_set = set(paths)
    for position, raw in enumerate(raw_assets, start=1):
        if not isinstance(raw, Mapping):
            raise ValueError(f"validation asset manifest item {position} is invalid")
        post_value = str(raw.get("post_file") or "")
        asset_value = str(raw.get("file") or "")
        post_path = resolve_post_path(directory, post_value)
        asset_path = _run_relative_file(
            directory, asset_value, label=f"validation asset {position}"
        )
        if post_path not in post_set or asset_path.parent != post_path.parent:
            raise ValueError(f"validation asset {position} is not bound to its validated post")
        if not asset_path.is_file():
            raise FileNotFoundError(f"validated product image is missing: {asset_path}")
        if not PRODUCT_IMAGE_RE.fullmatch(asset_path.name):
            raise ValueError(f"validation asset {position} has an invalid image name")
        key = (_relative_post_path(directory, post_path), str(asset_path.relative_to(directory)))
        if key in actual_asset_keys:
            raise ValueError("validation asset manifest contains duplicate image paths")
        actual_asset_keys.append(key)

        expected_hash = str(raw.get("sha256") or "").lower()
        if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            raise ValueError(f"validation asset {position} has an invalid SHA256")
        actual_hash = hashlib.sha256(asset_path.read_bytes()).hexdigest()
        if actual_hash != expected_hash:
            raise ValueError(f"validated product image changed after validation: {asset_value}")
        try:
            width, height = int(raw.get("width")), int(raw.get("height"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"validation asset {position} has invalid dimensions") from exc
        if _image_dimensions(asset_path) != (width, height):
            raise ValueError(f"validated product image dimensions changed: {asset_value}")

        source_manifest = str(raw.get("source_manifest") or "")
        loaded = manifest_cache.get(source_manifest)
        if loaded is None:
            loaded = _load_product_manifest(directory, source_manifest, _product_request(state))
            manifest_cache[source_manifest] = loaded
        relative_manifest, manifest_hash, accepted = loaded
        if relative_manifest != source_manifest:
            raise ValueError(f"validation asset {position} source manifest path changed")
        if str(raw.get("source_manifest_sha256") or "").lower() != manifest_hash:
            raise ValueError(f"validation asset {position} source manifest changed")
        try:
            manifest_index = int(raw.get("manifest_index"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"validation asset {position} has an invalid manifest index") from exc
        if not 1 <= manifest_index <= len(accepted):
            raise ValueError(f"validation asset {position} has an out-of-range manifest index")
        source = accepted[manifest_index - 1]
        expected_source_values = {
            "classification": source["classification"],
            "source_role": source["source_role"],
            "source_url": source["source_url"],
            "sha256": source["sha256"],
            "width": source["width"],
            "height": source["height"],
        }
        for field, expected_value in expected_source_values.items():
            if raw.get(field) != expected_value:
                raise ValueError(
                    f"validation asset {position} does not match source manifest field {field}"
                )
    if actual_asset_keys != expected_asset_keys:
        raise ValueError("validation assets no longer match generated product image placements")
    return paths


def validate_run(run_dir: str | Path, expected: int, *, threshold: float = 0.78) -> dict[str, Any]:
    directory = Path(run_dir).expanduser().resolve()
    run = load_run(directory)
    if not run:
        raise FileNotFoundError(f"run.json not found: {directory}")
    if expected <= 0:
        raise ValueError("expected count must be positive")
    if not 0.0 < threshold < 1.0:
        raise ValueError("similarity threshold must be between 0 and 1")

    files = discover_post_files(directory, run)
    errors: list[str] = []
    warnings: list[str] = []
    parsed_posts: list[tuple[Path, dict[str, Any]]] = []
    file_manifest: list[dict[str, str]] = []
    asset_manifest: list[dict[str, Any]] = []
    generation_records = _generation_records_by_path(directory, run)
    product_request = _product_request(run)
    product_manifest_cache: dict[str, tuple[str, str, list[dict[str, Any]]]] = {}
    product_images_required = False
    product_images_forbidden = False
    if product_request is not None:
        if expected != 1:
            errors.append("product v1 requires exactly one post")
        policy = product_request.get("image_policy")
        mode = str(policy.get("mode") or "") if isinstance(policy, Mapping) else ""
        if mode == "all_unique_seller_product_images":
            product_images_required = True
        elif mode == "none":
            product_images_forbidden = True
        else:
            errors.append("product run has an invalid image_policy mode")
    if len(files) != expected:
        errors.append(f"expected {expected} files, found {len(files)}")

    titles: dict[str, Path] = {}
    for path in files:
        if not path.is_file():
            errors.append(f"missing file: {path}")
            continue
        contents = path.read_bytes()
        parsed = parse_mato_text(contents.decode("utf-8-sig"))
        parsed_posts.append((path, parsed))
        file_manifest.append(
            {
                "file": _relative_post_path(directory, path),
                "sha256": hashlib.sha256(contents).hexdigest(),
            }
        )
        if not parsed["title"]:
            errors.append(f"{path.name}: missing {TITLE_PREFIX}")
        title_key = re.sub(r"\s+", "", parsed["title"]).casefold()
        if title_key and title_key in titles:
            errors.append(f"duplicate title: {parsed['title']}")
        elif title_key:
            titles[title_key] = path
        if parsed["body_marker_count"] != 1:
            errors.append(f"{path.name}: {BODY_MARKER} must appear exactly once")
        if not parsed["body"]:
            errors.append(f"{path.name}: body is empty")
        if len(parsed["headings"]) < 3:
            errors.append(f"{path.name}: at least 3 {HEADING_PREFIX} headings are required")
        if any(not heading for heading in parsed["headings"]):
            errors.append(f"{path.name}: empty heading marker")
        if re.search(r"!\[[^\]]*\]\(|<img\b|\[이미지[^\]]*\]", parsed["text"], re.IGNORECASE):
            errors.append(f"{path.name}: only [image_N.jpg] image tags are supported")
        image_matches = IMAGE_TAG_RE.findall(parsed["text"])
        image_names = [match[0] for match in image_matches]
        image_numbers = [int(match[1]) for match in image_matches]
        if image_names:
            expected_numbers = list(range(1, len(image_names) + 1))
            if image_numbers != expected_numbers:
                errors.append(f"{path.name}: image tags must be unique and sequential from image_1.jpg")
            for image_name in image_names:
                image_path = path.parent / image_name
                if not image_path.is_file():
                    errors.append(f"{path.name}: referenced image is missing: {image_name}")
            local_images = sorted(
                item.name for item in path.parent.glob("image_*.jpg") if item.is_file()
            )
            if sorted(image_names) != local_images:
                errors.append(f"{path.name}: local image files and text tags do not match")

        record = generation_records.get(path, {})
        has_product_metadata = any(
            key in record for key in ("link_url", "image_placements", "image_manifest")
        )
        is_product = product_request is not None or has_product_metadata
        if is_product:
            if product_request is not None:
                expected_generation_hash = str(record.get("sha256") or "").lower()
                current_generation_hash = hashlib.sha256(contents).hexdigest()
                if (
                    not re.fullmatch(r"[0-9a-f]{64}", expected_generation_hash)
                    or expected_generation_hash != current_generation_hash
                ):
                    errors.append(
                        f"{path.name}: product draft changed after truthful finalization; "
                        "run the product finalizer and writer again"
                    )
                try:
                    _validate_product_title_plan(parsed["title"], record)
                except ValueError as exc:
                    errors.append(f"{path.name}: {exc}")
            if product_images_required and (
                not record.get("image_manifest") or not record.get("image_placements")
            ):
                errors.append(
                    f"{path.name}: seller-image product post requires "
                    "image_manifest and image_placements"
                )
            if product_images_forbidden and (
                record.get("image_manifest") or record.get("image_placements") or image_names
            ):
                errors.append(f"{path.name}: image_policy none cannot include product images")
            try:
                link_url = _product_link_url(record, product_request)
            except ValueError as exc:
                errors.append(f"{path.name}: {exc}")
            else:
                meaningful_body2 = [
                    line.strip() for line in parsed["body2_lines"] if line.strip()
                ]
                if (
                    not meaningful_body2
                    or meaningful_body2[0] != link_url
                    or meaningful_body2[-1] != link_url
                ):
                    errors.append(
                        f"{path.name}: product link_url must be the first and last "
                        "non-empty body2 line"
                    )
                exact_lines = sum(line.strip() == link_url for line in parsed["body2_lines"])
                if exact_lines != 2 or parsed["body"].count(link_url) != 2:
                    errors.append(
                        f"{path.name}: product link_url must occur exactly twice in body2"
                    )
                if product_images_required or record.get("image_placements"):
                    expected_layout = str(record.get("product_layout_sha256") or "").lower()
                    actual_layout = _product_layout_signature(parsed["body"], link_url=link_url)
                    if (
                        not re.fullmatch(r"[0-9a-f]{64}", expected_layout)
                        or expected_layout != actual_layout
                    ):
                        errors.append(
                            f"{path.name}: product image placement layout changed after generation"
                        )
            meaningful_body2 = [
                line.strip() for line in parsed["body2_lines"] if line.strip()
            ]
            consecutive_images = 0
            for line_index, line in enumerate(meaningful_body2):
                has_image_tag = bool(IMAGE_TAG_RE.search(line))
                is_image_line = bool(
                    re.fullmatch(r"\[image_[1-9]\d*\.jpg\]", line, re.IGNORECASE)
                )
                if has_image_tag and not is_image_line:
                    errors.append(
                        f"{path.name}: product image tags must be standalone lines"
                    )
                if is_image_line:
                    consecutive_images += 1
                    if consecutive_images > 2:
                        errors.append(
                            f"{path.name}: product image layout allows at most two "
                            "consecutive tags"
                        )
                    next_line = (
                        meaningful_body2[line_index + 1]
                        if line_index + 1 < len(meaningful_body2)
                        else ""
                    )
                    if not PRODUCT_IMAGE_LINE_RE.fullmatch(next_line) and not _is_product_prose_line(
                        next_line, link_url=link_url
                    ):
                        errors.append(
                            f"{path.name}: a product image group must be followed immediately "
                            "by a prose paragraph"
                        )
                else:
                    consecutive_images = 0

        raw_placements = record.get("image_placements")
        manifest_value = record.get("image_manifest")
        if manifest_value and raw_placements is None:
            errors.append(f"{path.name}: product image manifest has no image placements")
        if raw_placements is not None:
            try:
                placement_names = _product_placement_names(record)
            except ValueError as exc:
                errors.append(f"{path.name}: {exc}")
                placement_names = []
            normalized_tag_names = [name.lower() for name in image_names]
            if placement_names != normalized_tag_names:
                errors.append(
                    f"{path.name}: product image tags do not match image_placements"
                )
            if not manifest_value:
                errors.append(f"{path.name}: product image placements have no source manifest")
            else:
                manifest_key = str(manifest_value)
                try:
                    loaded_manifest = product_manifest_cache.get(manifest_key)
                    if loaded_manifest is None:
                        loaded_manifest = _load_product_manifest(
                            directory, manifest_key, product_request
                        )
                        product_manifest_cache[manifest_key] = loaded_manifest
                    relative_manifest, source_manifest_hash, accepted = loaded_manifest
                except (OSError, UnicodeError, ValueError) as exc:
                    errors.append(f"{path.name}: {exc}")
                else:
                    provenance = record.get("product_writing_provenance")
                    writing_state = run.get("product_writing")
                    if product_images_required and (
                        not isinstance(provenance, Mapping)
                        or not isinstance(writing_state, Mapping)
                        or writing_state.get("status") != "finalized"
                        or str(provenance.get("image_manifest") or "")
                        != relative_manifest
                        or str(provenance.get("image_manifest_sha256") or "").lower()
                        != source_manifest_hash
                        or str(writing_state.get("image_manifest_sha256") or "").lower()
                        != source_manifest_hash
                        or str(provenance.get("brief_sha256") or "")
                        != str(writing_state.get("brief_sha256") or "")
                        or not re.fullmatch(
                            r"[0-9a-f]{64}",
                            str(provenance.get("finalized_post_sha256") or "").lower(),
                        )
                        or str(provenance.get("finalized_post_sha256") or "").lower()
                        != str(writing_state.get("finalized_post_sha256") or "").lower()
                    ):
                        errors.append(
                            f"{path.name}: product writing or seller image provenance changed"
                        )
                    if len(accepted) != len(placement_names):
                        errors.append(
                            f"{path.name}: accepted product image count does not match placements"
                        )
                    placement_rows = _product_placement_rows(record)
                    for position, (name, manifest_index) in enumerate(
                        placement_rows, start=1
                    ):
                        source = accepted[manifest_index - 1]
                        image_path = path.parent / name
                        if (
                            not image_path.is_file()
                            or image_path.is_symlink()
                            or bool(getattr(image_path, "is_junction", lambda: False)())
                        ):
                            continue
                        local_hash = hashlib.sha256(image_path.read_bytes()).hexdigest()
                        if local_hash != source["sha256"]:
                            errors.append(
                                f"{path.name}: {name} SHA256 does not match its product manifest"
                            )
                            continue
                        try:
                            local_dimensions = _image_dimensions(image_path)
                        except ValueError as exc:
                            errors.append(f"{path.name}: {exc}")
                            continue
                        if local_dimensions != (source["width"], source["height"]):
                            errors.append(
                                f"{path.name}: {name} dimensions do not match its product manifest"
                            )
                            continue
                        asset_manifest.append(
                            {
                                "post_file": _relative_post_path(directory, path),
                                "file": str(image_path.relative_to(directory)),
                                "source_manifest": relative_manifest,
                                "source_manifest_sha256": source_manifest_hash,
                                "manifest_index": manifest_index,
                                "classification": source["classification"],
                                "source_role": source["source_role"],
                                "source_url": source["source_url"],
                                "sha256": local_hash,
                                "width": local_dimensions[0],
                                "height": local_dimensions[1],
                            }
                        )
        if len(parsed["title"]) > 100:
            warnings.append(f"{path.name}: title is longer than 100 characters")

    similarities: list[dict[str, Any]] = []
    for left_index, (left_path, left) in enumerate(parsed_posts):
        for right_path, right in parsed_posts[left_index + 1:]:
            ratio = difflib.SequenceMatcher(
                None,
                _normalized_similarity_text(left["body"]),
                _normalized_similarity_text(right["body"]),
                autojunk=False,
            ).ratio()
            similarities.append({"left": left_path.name, "right": right_path.name, "ratio": round(ratio, 4)})
            if ratio >= threshold:
                errors.append(
                    f"drafts are too similar ({ratio:.3f} >= {threshold:.3f}): "
                    f"{left_path.name} / {right_path.name}"
                )

    ok = not errors
    validation = {
        "status": "passed" if ok else "failed",
        "expected_count": expected,
        "file_count": len(files),
        "similarity_threshold": threshold,
        "errors": errors,
        "warnings": warnings,
        "similarities": similarities,
        "file_manifest": file_manifest,
        "manifest_sha256": _manifest_sha256(file_manifest),
        "asset_manifest": asset_manifest,
        "asset_manifest_sha256": _structured_sha256(asset_manifest),
    }
    update_run(directory, {"status": "validated" if ok else "generation_invalid", "validation": validation})
    append_event(
        directory,
        "validation_completed" if ok else "validation_failed",
        status="validated" if ok else "generation_invalid",
        message="원고 검증을 통과했습니다." if ok else f"원고 검증 오류 {len(errors)}건이 있습니다.",
        details={"files": len(files), "errors": len(errors), "warnings": len(warnings)},
    )
    return {"ok": ok, "run_id": run.get("run_id", directory.name), "run_dir": str(directory), **validation}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate Mato _함축.txt drafts.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--expected", required=True, type=int)
    parser.add_argument("--similarity-threshold", type=float, default=0.78)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = validate_run(args.run_dir, args.expected, threshold=args.similarity_threshold)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
