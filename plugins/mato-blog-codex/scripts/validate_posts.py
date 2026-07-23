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
