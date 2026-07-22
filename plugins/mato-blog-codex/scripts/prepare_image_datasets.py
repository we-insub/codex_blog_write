"""Create one owned-image dataset for every generated Mato draft."""

from __future__ import annotations

import argparse
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


def _append_or_validate_tags(text_path: Path, names: list[str]) -> None:
    content = text_path.read_text(encoding="utf-8-sig")
    tags = re.findall(r"\[(image_[1-9]\d*\.jpg)\]", content, flags=re.IGNORECASE)
    if tags:
        if [tag.lower() for tag in tags] != names:
            raise ValueError(f"post tags do not match the owned image dataset: {text_path}")
        return
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
    if sanitize_source:
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
        _append_or_validate_tags(text_path, names)
        rows.append(
            {
                "index": index,
                "images": [
                    {
                        "source": str(path.relative_to(directory)),
                        "target": path.name.lower(),
                    }
                    for path in images
                ],
            }
        )
    if len(rows) != len(generated):
        raise ValueError("one or more generated posts have invalid image dataset targets")

    manifest = directory / "analysis" / "owned-image-datasets.json"
    atomic_write_json(manifest, {"kind": "owned_image_datasets", "posts": rows})
    attached = attach_images.attach_images(
        directory,
        manifest,
        sanitize_per_post=sanitize_per_post,
    )

    def update(state_value: dict[str, Any]) -> None:
        request = state_value.get("request")
        if isinstance(request, dict):
            request["image_mode"] = "owned_dataset_per_post"
        state_value["owned_image_datasets"] = {
            "source_dir": str(source),
            "source_images": len(images),
            "post_datasets": len(rows),
            "manifest": str(manifest),
            "source_cleanup": {
                "enabled": bool(sanitize_source),
                "manifest": str(source / "image-processing.json") if sanitize_source else "",
            },
            "per_post_cleanup": bool(sanitize_per_post),
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
            "source_cleanup": bool(sanitize_source),
            "per_post_cleanup": bool(sanitize_per_post),
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
