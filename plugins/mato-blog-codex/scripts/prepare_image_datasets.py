"""Create one owned-image dataset for every generated Mato draft."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from . import attach_images, sanitize_images
    from .history import append_event, update_run
    from .mato_common import atomic_write_json, atomic_write_text, load_run
except ImportError:
    import attach_images  # type: ignore[no-redef]
    import sanitize_images  # type: ignore[no-redef]
    from history import append_event, update_run  # type: ignore[no-redef]
    from mato_common import atomic_write_json, atomic_write_text, load_run  # type: ignore[no-redef]


IMAGE_NAME_RE = re.compile(r"image_([1-9]\d*)\.jpg", re.IGNORECASE)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _product_manifest_contract(
    directory: Path,
    generation: Mapping[str, Any],
    source: Path,
    images: Sequence[Path],
) -> tuple[str, list[str]]:
    """Bind the prepared source set to the collector's seller-only manifest."""

    posts = generation.get("posts")
    if not isinstance(posts, list) or not posts:
        raise ValueError("product generation has no post records")
    values = {
        str(item.get("image_manifest") or "").strip()
        for item in posts
        if isinstance(item, Mapping)
    }
    if len(values) != 1 or not next(iter(values), ""):
        raise ValueError("every product post must reference the same image manifest")
    relative = next(iter(values))
    manifest_path = (directory / relative).resolve()
    if not _inside(manifest_path, directory) or not manifest_path.is_file() or manifest_path.is_symlink():
        raise ValueError("product image manifest is missing or unsafe")
    manifest_sha256 = _sha256(manifest_path)
    provenances = [
        item.get("product_writing_provenance")
        for item in posts
        if isinstance(item, Mapping)
    ]
    if len(provenances) != len(posts) or any(
        not isinstance(value, Mapping)
        or str(value.get("image_manifest") or "") != relative
        or str(value.get("image_manifest_sha256") or "").lower() != manifest_sha256
        for value in provenances
    ):
        raise ValueError("product image manifest changed after the writing brief")
    if source != (manifest_path.parent / "prepared").resolve():
        raise ValueError("product source directory must be the manifest's prepared folder")
    payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, Mapping) or payload.get("kind") != "myrealtrip_product_images":
        raise ValueError("product image manifest kind is invalid")
    accepted = payload.get("accepted")
    if not isinstance(accepted, list) or len(accepted) != len(images):
        raise ValueError("product image manifest count does not match prepared files")
    hashes: list[str] = []
    for index, (raw, image) in enumerate(zip(accepted, images), start=1):
        if not isinstance(raw, Mapping):
            raise ValueError("product image manifest row is invalid")
        role = str(raw.get("source_role") or raw.get("role") or "")
        source_url = str(raw.get("source_url") or "")
        if (
            int(raw.get("index") or 0) != index
            or raw.get("classification") != "product"
            or role not in {"gallery", "introduction", "itinerary"}
            or Path(str(raw.get("file") or "")).name.lower() != image.name.lower()
            or re.search(r"/(?:reviews?|review_images)/", source_url, re.IGNORECASE)
        ):
            raise ValueError("product image manifest contains an unsafe or mismatched row")
        expected_sha = str(raw.get("sha256") or "").lower()
        actual_sha = _sha256(image)
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha) or actual_sha != expected_sha:
            raise ValueError(f"product prepared image hash mismatch: {image.name}")
        hashes.append(expected_sha)
    return str(manifest_path.relative_to(directory)), hashes


def _inside(path: Path, root: Path) -> bool:
    resolved = path.resolve(strict=False)
    base = root.resolve(strict=False)
    return resolved == base or base in resolved.parents


def _source_images(source_dir: Path, sources_root: Path) -> list[Path]:
    if not _inside(source_dir, sources_root) or not source_dir.is_dir():
        raise ValueError("source directory must be a run-local sources folder")
    images = sorted(
        (
            path
            for path in source_dir.iterdir()
            if path.is_file()
            and not path.is_symlink()
            and IMAGE_NAME_RE.fullmatch(path.name)
        ),
        key=lambda path: int(IMAGE_NAME_RE.fullmatch(path.name).group(1)),  # type: ignore[union-attr]
    )
    if not images:
        raise ValueError("source directory has no image_N.jpg files")
    expected = [f"image_{index}.jpg" for index in range(1, len(images) + 1)]
    if [path.name.lower() for path in images] != expected:
        raise ValueError("source images must use a contiguous image_1.jpg sequence")
    return images


def _append_or_validate_tags(
    text_path: Path,
    names: list[str],
    *,
    require_existing: bool = False,
) -> None:
    content = text_path.read_text(encoding="utf-8-sig")
    tags = re.findall(r"\[(image_[1-9]\d*\.jpg)\]", content, flags=re.IGNORECASE)
    if tags:
        if [tag.lower() for tag in tags] != names:
            raise ValueError(f"post tags do not match the owned image dataset: {text_path}")
        return
    if require_existing:
        raise ValueError(
            f"product posts must place every image tag before dataset attachment: {text_path}"
        )
    atomic_write_text(text_path, content.rstrip() + "\n\n" + "\n".join(f"[{name}]" for name in names) + "\n")


def prepare_post_image_datasets(
    run_dir: str | Path,
    source_dir: str | Path,
    *,
    sanitize_source: bool = True,
    sanitize_per_post: bool = True,
) -> dict[str, Any]:
    """Create and clean a distinct local image dataset for every generated post.

    The selected source image folder is cleaned once immediately after download.
    Then every generated post receives its own copied image files and cleanup
    pass.  This keeps every post folder self-contained and auditable.
    """

    directory = Path(run_dir).expanduser().resolve()
    state = load_run(directory)
    generation = state.get("generation") if isinstance(state, Mapping) else None
    generated = generation.get("posts") if isinstance(generation, Mapping) else None
    if not isinstance(generated, list) or not generated:
        raise ValueError("generate posts before preparing image datasets")

    sources_root = directory / "sources"
    source = Path(source_dir).expanduser().resolve()
    images = _source_images(source, sources_root)
    request = state.get("request") if isinstance(state, Mapping) else None
    product_mode = bool(
        isinstance(request, Mapping)
        and str(request.get("source_type") or "") == "myrealtrip_product"
    )
    source_manifest = ""
    source_hashes: list[str] = []
    if product_mode:
        if not isinstance(generation, Mapping):
            raise ValueError("product generation record is missing")
        names = [path.name.lower() for path in images]
        posts_root = directory / "posts"
        for raw in generated:
            if not isinstance(raw, Mapping):
                raise ValueError("product generation record is invalid")
            text_path = (directory / str(raw.get("file") or "")).resolve()
            if not _inside(text_path, posts_root) or not text_path.is_file():
                raise ValueError("product generated post is missing or unsafe")
            _append_or_validate_tags(text_path, names, require_existing=True)
        source_manifest, source_hashes = _product_manifest_contract(
            directory,
            generation,
            source,
            images,
        )
    # The product collector already creates metadata-clean upload copies and
    # separately retains the raw originals.  Re-encoding those prepared JPEGs
    # here (and again per post) would needlessly reduce quality and invalidate
    # their provenance hashes.
    effective_sanitize_source = bool(sanitize_source and not product_mode)
    effective_sanitize_per_post = bool(sanitize_per_post and not product_mode)
    if effective_sanitize_source:
        source_processing = sanitize_images.sanitize_path(
            source,
            recursive=False,
            manifest_path=source / "image-processing.json",
        )
        if not source_processing.get("ok") or int(source_processing.get("processed") or 0) != len(images):
            raise ValueError("source image cleanup was incomplete")
    else:
        source_processing = {"enabled": False, "processed": 0}

    posts_root = directory / "posts"
    names = [path.name.lower() for path in images]
    rows: list[dict[str, Any]] = []
    for raw in sorted(
        (item for item in generated if isinstance(item, Mapping)),
        key=lambda item: int(item.get("index") or 0),
    ):
        index = int(raw.get("index") or 0)
        text_path = (directory / str(raw.get("file") or "")).resolve()
        if index <= 0 or not _inside(text_path, posts_root) or not text_path.is_file():
            raise ValueError(f"unsafe or missing generated post: {raw}")
        _append_or_validate_tags(text_path, names, require_existing=product_mode)
        if product_mode:
            placements = raw.get("image_placements")
            if not isinstance(placements, list) or len(placements) != len(images):
                raise ValueError("product image placements do not cover the source dataset")
            dataset_rows: list[dict[str, Any]] = []
            used_manifest_indexes: set[int] = set()
            for target_index, placement in enumerate(placements, start=1):
                if not isinstance(placement, Mapping):
                    raise ValueError("product image placement is invalid")
                target_name = str(placement.get("file") or "").lower()
                manifest_index = int(placement.get("manifest_index") or 0)
                if (
                    target_name != f"image_{target_index}.jpg"
                    or not 1 <= manifest_index <= len(images)
                    or manifest_index in used_manifest_indexes
                ):
                    raise ValueError("product image placement mapping is incomplete or duplicated")
                used_manifest_indexes.add(manifest_index)
                dataset_rows.append(
                    {
                        "source": str(images[manifest_index - 1].relative_to(directory)),
                        "target": target_name,
                        "sha256": source_hashes[manifest_index - 1],
                        "manifest_index": manifest_index,
                    }
                )
        else:
            dataset_rows = [
                {
                    "source": str(path.relative_to(directory)),
                    "target": path.name.lower(),
                }
                for path in images
            ]
        rows.append(
            {
                "index": index,
                "images": dataset_rows,
            }
        )
    if len(rows) != len(generated):
        raise ValueError("one or more generated posts have invalid image dataset targets")

    manifest = directory / "analysis" / "owned-image-datasets.json"
    atomic_write_json(
        manifest,
        {
            "kind": "owned_image_datasets",
            "posts": rows,
            **({"source_manifest": source_manifest} if source_manifest else {}),
        },
    )
    attached = attach_images.attach_images(
        directory,
        manifest,
        sanitize_per_post=effective_sanitize_per_post,
    )

    def update(state_value: dict[str, Any]) -> None:
        request = state_value.get("request")
        if isinstance(request, dict):
            request["image_mode"] = (
                "myrealtrip_product_images" if product_mode else "owned_dataset_per_post"
            )
        state_value["owned_image_datasets"] = {
            "source_dir": str(source),
            "source_images": len(images),
            "post_datasets": len(rows),
            "manifest": str(manifest),
            "source_manifest": source_manifest,
            "source_cleanup": {
                "enabled": effective_sanitize_source,
                "manifest": (
                    str(source / "image-processing.json") if effective_sanitize_source else ""
                ),
            },
            "per_post_cleanup": effective_sanitize_per_post,
        }

    update_run(directory, update)
    append_event(
        directory,
        "owned_image_datasets_prepared",
        status="generated",
        message=f"생성 원고 {len(rows)}개에 이미지 데이터셋을 각각 연결했습니다.",
        details={
            "source_images": len(images),
            "post_datasets": len(rows),
            "source_cleanup": effective_sanitize_source,
            "per_post_cleanup": effective_sanitize_per_post,
        },
    )
    return {
        "ok": True,
        "run_dir": str(directory),
        "source_dir": str(source),
        "source_images": len(images),
        "post_datasets": len(rows),
        "manifest": str(manifest),
        "source_cleanup": source_processing,
        "attachments": attached,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create a cleaned owned-image dataset for every generated Mato post."
    )
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--skip-source-sanitize", action="store_true")
    parser.add_argument("--skip-post-sanitize", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = prepare_post_image_datasets(
            args.run_dir,
            args.source_dir,
            sanitize_source=not args.skip_source_sanitize,
            sanitize_per_post=not args.skip_post_sanitize,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
