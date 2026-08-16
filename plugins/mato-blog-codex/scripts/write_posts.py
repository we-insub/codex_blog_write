"""Render Codex-generated JSON into deterministic Mato ``_함축.txt`` files."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

try:
    from .collect import purge_raw
    from .history import append_event, update_run
    from .mato_common import atomic_write_json, atomic_write_text, load_run, sanitize_for_history, slugify
except ImportError:
    from collect import purge_raw  # type: ignore[no-redef]
    from history import append_event, update_run  # type: ignore[no-redef]
    from mato_common import (  # type: ignore[no-redef]
        atomic_write_json,
        atomic_write_text,
        load_run,
        sanitize_for_history,
        slugify,
    )


def _clean_text(value: object, *, field: str) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise ValueError(f"{field} must not be empty")
    if "\x00" in text:
        raise ValueError(f"{field} contains a NUL character")
    return text


def _string_list(value: object, *, field: str) -> list[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = value
    else:
        raise ValueError(f"{field} must be a string or array of strings")
    return [_clean_text(item, field=field) for item in values if str(item or "").strip()]


PRODUCT_IMAGE_RE = re.compile(r"image_([1-9]\d*)\.jpg", re.IGNORECASE)
PRODUCT_IMAGE_LINE_RE = re.compile(r"\[image_([1-9]\d*)\.jpg\]", re.IGNORECASE)
TABLE_START_RE = re.compile(r"^표\s+\d+\s*[xX×]\s*\d+\s+시작$")
TABLE_CELL_RE = re.compile(r"^\(\d+\s*,\s*\d+\)\s*.+$")
TABLE_END_RE = re.compile(r"^표\s+\d+\s*[xX×]\s*\d+\s+끝$")
PRODUCT_LINK_HOSTS = {
    "myrealt.rip",
    "myrealtrip.com",
    "www.myrealtrip.com",
    "experiences.myrealtrip.com",
    "naver.me",
    "brand.naver.com",
}
PRODUCT_SOURCE_TYPES = {"myrealtrip_product", "naver_shopping_product"}


def _finalized_product_post_sha256(post: Mapping[str, Any]) -> str:
    semantic = dict(post)
    semantic.pop("product_writing_provenance", None)
    encoded = json.dumps(
        semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalized_keyword(value: object) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", str(value or "")).casefold()


def _validate_title_plan(title: str, value: object) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("title_plan must be an object")
    channel = str(value.get("channel") or "naver").strip().lower()
    if channel != "naver":
        raise ValueError("title_plan.channel must be naver")
    if len(title) > 40:
        raise ValueError("product title must not exceed the 40-character Naver editor limit")
    exact_title = str(value.get("exact_title") or "").strip()
    if exact_title and title != exact_title:
        raise ValueError("title does not match title_plan.exact_title")

    main_keyword = str(value.get("main_keyword") or "").strip()
    raw_terms = value.get("main_terms")
    if raw_terms is not None and not isinstance(raw_terms, list):
        raise ValueError("title_plan.main_terms must be an array")
    main_terms = (
        [str(item).strip() for item in raw_terms if str(item).strip()]
        if isinstance(raw_terms, list)
        else [item for item in re.split(r"\s+", main_keyword) if item]
    )
    raw_subkeywords = value.get("subkeywords")
    if raw_subkeywords is None:
        subkeywords: list[str] = []
    elif isinstance(raw_subkeywords, list):
        subkeywords = [str(item).strip() for item in raw_subkeywords if str(item).strip()]
    else:
        raise ValueError("title_plan.subkeywords must be an array")
    hook = str(value.get("hook") or "").strip()
    if subkeywords and not 1 <= len(subkeywords) <= 2:
        raise ValueError("title_plan.subkeywords must select one or two values")
    title_key = _normalized_keyword(title)
    missing = [
        item
        for item in [*main_terms, *subkeywords, hook]
        if item and _normalized_keyword(item) not in title_key
    ]
    if missing:
        raise ValueError(f"title is missing title_plan values: {', '.join(missing)}")
    return {
        "channel": channel,
        "main_keyword": main_keyword,
        "main_terms": main_terms,
        "subkeywords": subkeywords,
        "hook": hook,
        **({"exact_title": exact_title} if exact_title else {}),
    }


def _clean_link_url(value: object) -> str:
    url = str(value or "").strip()
    if not url:
        return ""
    if "\n" in url or "\r" in url or any(character.isspace() for character in url):
        raise ValueError("link_url must be one standalone URL")
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not host
        or parsed.username
        or parsed.password
    ):
        raise ValueError("link_url must be a public HTTP(S) URL without credentials")
    if host not in PRODUCT_LINK_HOSTS and not host.endswith(".myrealtrip.com"):
        raise ValueError("link_url must be a supported product URL")
    return url


def _normalize_image_placements(
    value: object,
    *,
    intro_count: int,
    section_paragraph_counts: Sequence[int],
) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("image_placements must be an array")
    normalized: list[dict[str, Any]] = []
    for position, raw in enumerate(value, start=1):
        if not isinstance(raw, Mapping):
            raise ValueError(f"image_placements[{position - 1}] must be an object")
        image_value = raw.get("file") or raw.get("image") or raw.get("name")
        image_name = str(image_value or f"image_{position}.jpg").strip().lower()
        match = PRODUCT_IMAGE_RE.fullmatch(image_name)
        if match is None:
            raise ValueError(f"invalid product image filename: {image_name}")
        region = str(raw.get("region") or raw.get("location") or "").strip().lower()
        if region not in {"intro", "section"}:
            raise ValueError(f"image placement region must be intro or section: {image_name}")
        try:
            after_paragraph = int(raw.get("after_paragraph") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid after_paragraph for {image_name}") from exc
        section_index = 0
        paragraph_count = intro_count
        if region == "section":
            try:
                section_index = int(raw.get("section_index") or 0)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid section_index for {image_name}") from exc
            if not 1 <= section_index <= len(section_paragraph_counts):
                raise ValueError(f"section_index is out of range for {image_name}")
            paragraph_count = int(section_paragraph_counts[section_index - 1])
        if not 0 <= after_paragraph <= paragraph_count:
            raise ValueError(f"after_paragraph is out of range for {image_name}")
        normalized.append(
            {
                "file": image_name,
                "region": region,
                "section_index": section_index,
                "after_paragraph": after_paragraph,
                "manifest_index": int(raw.get("manifest_index") or match.group(1)),
            }
        )
    image_numbers = [
        int(PRODUCT_IMAGE_RE.fullmatch(item["file"]).group(1))  # type: ignore[union-attr]
        for item in normalized
    ]
    if image_numbers != list(range(1, len(normalized) + 1)):
        raise ValueError(
            "image_placements must list each image_N.jpg exactly once in sequence"
        )
    manifest_indexes = [int(item["manifest_index"]) for item in normalized]
    if sorted(manifest_indexes) != list(range(1, len(normalized) + 1)) or len(
        set(manifest_indexes)
    ) != len(manifest_indexes):
        raise ValueError("image_placements manifest_index values must cover every image once")
    return normalized


def _region_lines(
    paragraphs: Sequence[str], placements: Sequence[Mapping[str, Any]]
) -> list[str]:
    by_position: dict[int, list[str]] = {}
    for placement in placements:
        by_position.setdefault(int(placement["after_paragraph"]), []).append(
            f"[{placement['file']}]"
        )
    lines = list(by_position.get(0, []))
    for index, paragraph in enumerate(paragraphs, start=1):
        lines.append(paragraph)
        lines.extend(by_position.get(index, []))
    return lines


def _is_product_prose_line(line: str, *, link_url: str) -> bool:
    """Return true only for a real reader-facing paragraph after an image group.

    Product images must never spill into a heading, URL card, table instruction,
    or another editor component.  Keeping this deliberately narrow makes the
    rendered Mato instructions match the visible ``image → prose`` rhythm.
    """

    value = str(line or "").strip()
    if not value or value == link_url or PRODUCT_IMAGE_LINE_RE.fullmatch(value):
        return False
    if value.startswith(("ㅂㅂㅂ", "소제목", "!!", "http://", "https://")):
        return False
    if TABLE_START_RE.fullmatch(value) or TABLE_CELL_RE.fullmatch(value) or TABLE_END_RE.fullmatch(value):
        return False
    return bool(re.search(r"[가-힣A-Za-z]", value))


def _validate_product_image_layout(body: str, *, link_url: str) -> None:
    meaningful = [line.strip() for line in body.splitlines() if line.strip()]
    consecutive = 0
    for index, line in enumerate(meaningful):
        if PRODUCT_IMAGE_LINE_RE.fullmatch(line):
            consecutive += 1
            if consecutive > 2:
                raise ValueError("product image layout allows at most two consecutive tags")
            next_line = meaningful[index + 1] if index + 1 < len(meaningful) else ""
            if not PRODUCT_IMAGE_LINE_RE.fullmatch(next_line) and not _is_product_prose_line(
                next_line, link_url=link_url
            ):
                raise ValueError(
                    "a product image group must be followed immediately by a prose paragraph"
                )
        else:
            consecutive = 0
    if link_url and (not meaningful or meaningful[-1] != link_url):
        raise ValueError("no image or text may appear after the final product link_url")


def _product_layout_signature(body: str, *, link_url: str) -> str:
    rows: list[str] = []
    for line in (value.strip() for value in body.splitlines() if value.strip()):
        if line == link_url:
            rows.append("LINK")
        elif PRODUCT_IMAGE_LINE_RE.fullmatch(line):
            rows.append(f"IMAGE:{line.casefold()}")
        elif line.startswith("ㅂㅂㅂ"):
            rows.append("HEADING")
        else:
            rows.append("PARAGRAPH")
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


def _render_mato_post_details(post: Mapping[str, Any]) -> tuple[str, str, dict[str, Any]]:
    title = _clean_text(post.get("title"), field="title").replace("\n", " ")
    intro = _string_list(post.get("intro", []), field="intro")
    sections = post.get("sections")
    if not isinstance(sections, list) or not sections:
        raise ValueError(f"post '{title}' must contain a non-empty sections array")

    clean_sections: list[tuple[str, list[str]]] = []
    for section_index, section in enumerate(sections, start=1):
        if not isinstance(section, Mapping):
            raise ValueError(f"post '{title}' section {section_index} must be an object")
        heading = _clean_text(section.get("heading"), field=f"section {section_index} heading")
        heading = re.sub(r"^ㅂㅂㅂ\s*", "", heading).replace("\n", " ").strip()
        paragraphs = _string_list(
            section.get("paragraphs", []), field=f"section {section_index} paragraphs"
        )
        clean_sections.append((heading, paragraphs))

    title_plan = _validate_title_plan(title, post.get("title_plan"))
    link_url = _clean_link_url(post.get("link_url"))
    placements = _normalize_image_placements(
        post.get("image_placements"),
        intro_count=len(intro),
        section_paragraph_counts=[len(paragraphs) for _heading, paragraphs in clean_sections],
    )
    if placements:
        raw_content = "\n".join(
            [
                *intro,
                *(paragraph for _heading, values in clean_sections for paragraph in values),
            ]
        )
        if re.search(r"\[image_[1-9]\d*\.jpg\]", raw_content, re.IGNORECASE):
            raise ValueError("image tags must come from image_placements in product posts")

    body_lines: list[str] = []
    if link_url:
        body_lines.extend([link_url, ""])
    body_lines.extend(
        _region_lines(intro, [item for item in placements if item["region"] == "intro"])
    )
    for section_index, (heading, paragraphs) in enumerate(clean_sections, start=1):
        body_lines.extend(["", f"ㅂㅂㅂ{heading}"])
        section_placements = [
            item
            for item in placements
            if item["region"] == "section" and int(item["section_index"]) == section_index
        ]
        body_lines.extend(_region_lines(paragraphs, section_placements))
    if link_url:
        body_lines.extend(["", link_url])

    body = "\n".join(body_lines).strip()
    rendered_images = [
        match[0].lower()
        for match in re.findall(r"\[(image_([1-9]\d*)\.jpg)\]", body, re.IGNORECASE)
    ]
    expected_images = [item["file"] for item in placements]
    if rendered_images != expected_images:
        raise ValueError("rendered product image tags do not match deterministic placements")
    if placements:
        _validate_product_image_layout(body, link_url=link_url)
    if link_url:
        meaningful = [line.strip() for line in body.splitlines() if line.strip()]
        if (
            meaningful[0] != link_url
            or meaningful[-1] != link_url
            or meaningful.count(link_url) != 2
            or body.count(link_url) != 2
        ):
            raise ValueError(
                "link_url must be the first and last non-empty body2 line exactly once each"
            )
    rendered = f"제목을입력해주세요1: {title}\n\n본문2:\n{body}\n"
    return title, rendered, {
        "link_url": link_url,
        "title_plan": title_plan,
        "image_placements": placements,
        "product_layout_sha256": (
            _product_layout_signature(body, link_url=link_url) if placements else ""
        ),
    }


def render_mato_post(post: Mapping[str, Any]) -> tuple[str, str]:
    title, rendered, _details = _render_mato_post_details(post)
    return title, rendered


def _effective_product_post(
    payload: Mapping[str, Any],
    post: Mapping[str, Any],
    run: Mapping[str, Any],
) -> dict[str, Any]:
    effective = dict(post)
    for key in (
        "link_url",
        "title_plan",
        "image_placements",
        "image_manifest",
        "product_writing_provenance",
    ):
        if key not in effective and key in payload:
            effective[key] = payload[key]
    request = run.get("request")
    if (
        "link_url" not in effective
        and isinstance(request, Mapping)
        and str(request.get("source_type") or "") in PRODUCT_SOURCE_TYPES
        and request.get("product_url")
    ):
        effective["link_url"] = request["product_url"]
    return effective


def _enforce_product_run_contract(
    run: Mapping[str, Any],
    details: Mapping[str, Any],
    image_manifest: str,
    provenance: object,
    run_dir: Path,
    effective_post: Mapping[str, Any],
) -> None:
    request = run.get("request")
    if not isinstance(request, Mapping) or str(request.get("source_type") or "") not in PRODUCT_SOURCE_TYPES:
        return
    if details.get("title_plan") is None:
        raise ValueError("product posts require a title_plan")
    link_url = str(details.get("link_url") or "")
    if link_url != str(request.get("product_url") or ""):
        raise ValueError("product link_url must exactly match request.product_url")
    policy = request.get("image_policy")
    mode = str(policy.get("mode") or "") if isinstance(policy, Mapping) else ""
    if mode == "all_unique_seller_product_images":
        if not details.get("image_placements") or not image_manifest:
            raise ValueError(
                "seller-image product posts require image_placements and image_manifest"
            )
        writing_state = run.get("product_writing")
        if not isinstance(provenance, Mapping) or not isinstance(writing_state, Mapping):
            raise ValueError("seller-image product posts require finalized writing provenance")
        brief_file = _run_relative_manifest(run_dir, writing_state.get("brief_file"))
        manifest_path = _run_relative_manifest(run_dir, image_manifest)
        finalized_hash = str(provenance.get("finalized_post_sha256") or "").lower()
        if (
            writing_state.get("status") != "finalized"
            or str(provenance.get("brief_sha256") or "")
            != str(writing_state.get("brief_sha256") or "")
            or str(provenance.get("image_manifest_sha256") or "")
            != str(writing_state.get("image_manifest_sha256") or "")
            or str(provenance.get("image_manifest") or "") != image_manifest
            or hashlib.sha256((run_dir / brief_file).read_bytes()).hexdigest()
            != str(writing_state.get("brief_file_sha256") or "")
            or hashlib.sha256((run_dir / manifest_path).read_bytes()).hexdigest()
            != str(writing_state.get("image_manifest_sha256") or "")
            or not re.fullmatch(r"[0-9a-f]{64}", finalized_hash)
            or finalized_hash
            != str(writing_state.get("finalized_post_sha256") or "").lower()
            or finalized_hash != _finalized_product_post_sha256(effective_post)
        ):
            raise ValueError(
                "product writing brief, finalized article, or seller image provenance changed"
            )
    elif mode == "none":
        if details.get("image_placements") or image_manifest:
            raise ValueError("image_policy none cannot include product images")
    else:
        raise ValueError("product run has an invalid image_policy mode")


def _run_relative_manifest(directory: Path, value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    path = Path(raw).expanduser()
    unresolved = path if path.is_absolute() else directory / path
    candidate = unresolved.resolve(strict=False)
    if candidate == directory or directory not in candidate.parents or ".." in path.parts:
        raise ValueError("image_manifest must stay inside the run directory")
    probe = unresolved
    while probe != directory and directory in probe.resolve(strict=False).parents:
        if _is_link_or_junction(probe):
            raise ValueError(f"image_manifest is missing or unsafe: {raw}")
        probe = probe.parent
    if not candidate.is_file():
        raise ValueError(f"image_manifest is missing or unsafe: {raw}")
    return str(candidate.relative_to(directory))


def _expected_versions(run: Mapping[str, Any]) -> int:
    request = run.get("request")
    candidates = []
    if isinstance(request, Mapping):
        candidates.extend([request.get("versions"), request.get("version_count")])
    candidates.extend([run.get("versions"), run.get("version_count")])
    for value in candidates:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    raise ValueError("run.json does not contain a positive requested version count")


def _is_link_or_junction(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def _assert_direct_output_root(path: Path, *, label: str) -> None:
    """Reject redirected output roots before any replacement or cleanup."""

    if not path.exists() and not path.is_symlink():
        return
    if _is_link_or_junction(path) or path.resolve(strict=False) != path:
        raise ValueError(f"{label} output directory must not be a symlink or junction")
    if not path.is_dir():
        raise ValueError(f"{label} output path must be a directory")


def _assert_tree_has_no_links(root: Path) -> None:
    if not root.exists():
        return
    for candidate in root.rglob("*"):
        if _is_link_or_junction(candidate):
            raise ValueError(f"existing generated output contains a symlink or junction: {candidate}")


def _remove_scoped_path(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if _is_link_or_junction(path) or path.is_file():
        path.unlink()
    else:
        shutil.rmtree(path)


def _replace_path_with_retry(source: Path, destination: Path, *, attempts: int = 6) -> None:
    """Handle short-lived Windows scanner/indexer locks during atomic swaps."""

    for attempt in range(attempts):
        try:
            source.replace(destination)
            return
        except PermissionError:
            if attempt + 1 >= attempts:
                raise
            time.sleep(0.05 * (attempt + 1))


def _replace_generated_outputs(
    directory: Path,
    staged_posts: Path,
    staged_analysis: Path,
    generation_state: Mapping[str, Any],
    previous_run: Mapping[str, Any],
) -> Path:
    """Swap staged outputs into place and roll back on any commit failure."""

    posts_root = directory / "posts"
    analysis_root = directory / "analysis"
    analysis_path = analysis_root / "common-patterns.json"
    _assert_direct_output_root(posts_root, label="posts")
    _assert_tree_has_no_links(posts_root)
    _assert_direct_output_root(analysis_root, label="analysis")
    if analysis_path.exists() and _is_link_or_junction(analysis_path):
        raise ValueError("analysis output file must not be a symlink or junction")

    token = uuid.uuid4().hex
    posts_backup = directory / f".mato-posts-backup-{token}"
    analysis_backup = directory / f".mato-analysis-backup-{token}.json"
    had_posts = posts_root.exists()
    had_analysis = analysis_path.exists()
    analysis_root_existed = analysis_root.exists()
    installed_posts = False
    installed_analysis = False
    try:
        if had_posts:
            _replace_path_with_retry(posts_root, posts_backup)
        if had_analysis:
            _replace_path_with_retry(analysis_path, analysis_backup)
        analysis_root.mkdir(parents=True, exist_ok=True)
        _replace_path_with_retry(staged_posts, posts_root)
        installed_posts = True
        _replace_path_with_retry(staged_analysis, analysis_path)
        installed_analysis = True

        def commit_state(state: dict[str, Any]) -> None:
            state["status"] = "generated"
            state["generation"] = dict(generation_state)
            # A regenerated byte set must never inherit approval or upload
            # state bound to the previous files.
            state.pop("validation", None)
            state.pop("upload_plan", None)
            state.pop("uploads", None)

        update_run(directory, commit_state)
    except Exception:
        try:
            if installed_analysis and analysis_path.exists():
                analysis_path.unlink()
            if analysis_backup.exists():
                _replace_path_with_retry(analysis_backup, analysis_path)
            if installed_posts and posts_root.exists():
                _remove_scoped_path(posts_root)
            if posts_backup.exists():
                _replace_path_with_retry(posts_backup, posts_root)
            if not analysis_root_existed and analysis_root.exists() and not any(analysis_root.iterdir()):
                analysis_root.rmdir()
            # ``update_run`` uses atomic replacement.  Rewriting the prior
            # snapshot also covers an injected failure raised just after it.
            atomic_write_json(directory / "run.json", dict(previous_run))
        except Exception as rollback_error:
            raise RuntimeError(
                f"generation commit failed and rollback was incomplete: {rollback_error}"
            ) from rollback_error
        raise
    else:
        # Backups contain only a previously verified direct tree.  Cleanup is
        # best-effort after state and outputs agree, so cleanup failure cannot
        # turn a successful commit into a misleading failed regeneration.
        try:
            _remove_scoped_path(posts_backup)
            _remove_scoped_path(analysis_backup)
        except OSError:
            pass
    return analysis_path


def write_posts(run_dir: str | Path, input_path: str | Path, *, remove_raw: bool = False) -> dict[str, Any]:
    directory = Path(run_dir).expanduser().resolve()
    run = load_run(directory)
    if not run:
        raise FileNotFoundError(f"run.json not found: {directory}")
    source = Path(input_path).expanduser()
    payload = json.loads(source.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, Mapping):
        raise ValueError("generated post input must be a JSON object")
    posts = payload.get("posts")
    if not isinstance(posts, list):
        raise ValueError("generated post input must contain a posts array")
    expected = _expected_versions(run)
    if len(posts) != expected:
        raise ValueError(f"expected {expected} posts, received {len(posts)}")

    # Render and validate the complete payload in memory first.  A malformed
    # later post must not touch a previously valid generation or run state.
    records: list[dict[str, Any]] = []
    rendered_posts: list[tuple[Path, str]] = []
    seen_titles: set[str] = set()
    for index, item in enumerate(posts, start=1):
        if not isinstance(item, Mapping):
            raise ValueError(f"posts[{index - 1}] must be an object")
        effective = _effective_product_post(payload, item, run)
        title, rendered, details = _render_mato_post_details(effective)
        image_manifest = _run_relative_manifest(directory, effective.get("image_manifest"))
        if details["image_placements"] and not image_manifest:
            raise ValueError("product image_placements require an image_manifest")
        _enforce_product_run_contract(
            run,
            details,
            image_manifest,
            effective.get("product_writing_provenance"),
            directory,
            effective,
        )
        normalized_title = re.sub(r"\s+", "", title).casefold()
        if normalized_title in seen_titles:
            raise ValueError(f"duplicate title: {title}")
        seen_titles.add(normalized_title)
        safe_title = slugify(title, fallback=f"post-{index:02d}")[:80]
        folder = Path("posts") / f"v{index:02d}_{safe_title}"
        filename = f"{safe_title}_함축.txt"
        destination = folder / filename
        rendered_posts.append((destination, rendered))
        record: dict[str, Any] = {
            "index": index,
            "title": title,
            "file": str(destination),
            "sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
            "status": "generated",
        }
        if details["link_url"]:
            record["link_url"] = details["link_url"]
        if details["title_plan"] is not None:
            record["title_plan"] = details["title_plan"]
        if details["image_placements"]:
            record["image_placements"] = details["image_placements"]
            record["image_manifest"] = image_manifest
            record["product_layout_sha256"] = details["product_layout_sha256"]
            record["product_writing_provenance"] = dict(
                effective.get("product_writing_provenance") or {}
            )
        records.append(record)

    analysis = payload.get("analysis", {})
    safe_analysis = sanitize_for_history(analysis)
    if not isinstance(safe_analysis, (dict, list)):
        raise ValueError("analysis must be a JSON object or array")
    analysis_relative = Path("analysis") / "common-patterns.json"
    generation_state = {
        "status": "generated",
        "expected_count": expected,
        "generated_count": len(records),
        "analysis_file": str(analysis_relative),
        "posts": records,
    }

    staging_root = Path(tempfile.mkdtemp(prefix=".mato-generation-", dir=directory))
    try:
        staged_posts = staging_root / "posts"
        for relative_path, rendered in rendered_posts:
            atomic_write_text(staging_root / relative_path, rendered)
        staged_analysis = staging_root / analysis_relative
        atomic_write_json(staged_analysis, safe_analysis)
        analysis_path = _replace_generated_outputs(
            directory,
            staged_posts,
            staged_analysis,
            generation_state,
            run,
        )
    finally:
        try:
            _remove_scoped_path(staging_root)
        except OSError:
            pass

    # Journal only after the transactional file/state commit.  Validation
    # errors and staging failures therefore leave the prior run untouched.
    append_event(
        directory,
        "generation_started",
        message=f"Codex 원고 {expected}개를 Mato 형식으로 저장합니다.",
    )
    append_event(
        directory,
        "generation_completed",
        status="generated",
        message=f"서로 다른 Mato 원고 {len(records)}개를 저장했습니다.",
        details={
            "count": len(records),
            "analysis_file": str(analysis_path),
            "posts": [
                {"index": item["index"], "title": item["title"], "file": item["file"]}
                for item in records
            ],
        },
    )
    raw_removed = purge_raw(directory) if remove_raw else False
    return {
        "ok": True,
        "run_id": run.get("run_id", directory.name),
        "run_dir": str(directory),
        "generated_count": len(records),
        "analysis_file": str(analysis_path),
        "posts": records,
        "raw_source_removed": raw_removed,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Write Codex-generated JSON as Mato text files.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--input", required=True, help="UTF-8 generated-posts.json")
    parser.add_argument("--purge-raw", action="store_true", help="delete ephemeral competitor text")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = write_posts(args.run_dir, args.input, remove_raw=args.purge_raw)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
