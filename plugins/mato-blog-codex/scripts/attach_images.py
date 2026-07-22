"""Attach locally downloaded source images to generated Mato post folders."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from .history import append_event, update_run
    from .mato_common import load_run
except ImportError:
    from history import append_event, update_run  # type: ignore[no-redef]
    from mato_common import load_run  # type: ignore[no-redef]


IMAGE_NAME_RE = re.compile(r"image_([1-9]\d*)\.jpg", re.IGNORECASE)


def _inside(path: Path, root: Path) -> bool:
    resolved = path.resolve(strict=False)
    base = root.resolve(strict=False)
    return resolved == base or base in resolved.parents


def attach_images(
    run_dir: str | Path,
    manifest_path: str | Path,
    *,
    sanitize_per_post: bool = True,
) -> dict[str, Any]:
    directory = Path(run_dir).expanduser().resolve()
    state = load_run(directory)
    if not state:
        raise FileNotFoundError(f"run.json not found: {directory}")
    generation = state.get("generation")
    generated = generation.get("posts") if isinstance(generation, Mapping) else None
    if not isinstance(generated, list) or not generated:
        raise ValueError("generate posts before attaching images")

    payload = json.loads(Path(manifest_path).expanduser().read_text(encoding="utf-8-sig"))
    rows = payload.get("posts") if isinstance(payload, Mapping) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError("image manifest must contain a non-empty posts array")

    by_index = {
        int(item.get("index")): item
        for item in generated
        if isinstance(item, Mapping) and str(item.get("index") or "").isdigit()
    }
    sources_root = directory / "sources"
    posts_root = directory / "posts"
    attached: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        index = int(row.get("index") or 0)
        generated_item = by_index.get(index)
        if generated_item is None:
            raise ValueError(f"unknown generated post index: {index}")
        text_path = (directory / str(generated_item.get("file") or "")).resolve()
        if not _inside(text_path, posts_root) or not text_path.is_file():
            raise ValueError(f"unsafe or missing generated post path: {text_path}")
        post_dir = text_path.parent
        images = row.get("images")
        if not isinstance(images, list) or not images:
            raise ValueError(f"post {index} has no image mappings")
        copied: list[str] = []
        for position, raw in enumerate(images, start=1):
            if not isinstance(raw, Mapping):
                raise ValueError(f"post {index} image {position} is invalid")
            source_value = str(raw.get("source") or "")
            target_name = str(raw.get("target") or f"image_{position}.jpg")
            if not IMAGE_NAME_RE.fullmatch(target_name):
                raise ValueError(f"post {index} has invalid target image name: {target_name}")
            source = (directory / source_value).resolve()
            if not _inside(source, sources_root) or not source.is_file() or source.is_symlink():
                raise ValueError(f"post {index} has unsafe or missing source image: {source_value}")
            target = post_dir / target_name
            shutil.copy2(source, target)
            copied.append(target_name)

        text = text_path.read_text(encoding="utf-8-sig")
        references = re.findall(r"\[(image_[1-9]\d*\.jpg)\]", text, flags=re.IGNORECASE)
        if references != copied:
            raise ValueError(
                f"post {index} image tags do not match manifest order: tags={references}, files={copied}"
            )
        processing_manifest = post_dir / "image-processing.json"
        if sanitize_per_post:
            try:
                import sanitize_images

                processing = sanitize_images.sanitize_path(
                    post_dir,
                    recursive=False,
                    manifest_path=processing_manifest,
                )
            except Exception as exc:
                raise ValueError(f"post {index} image cleanup failed: {exc}") from exc
            if not processing.get("ok") or int(processing.get("processed") or 0) != len(copied):
                raise ValueError(f"post {index} image cleanup was incomplete")
        attached.append(
            {
                "index": index,
                "folder": str(post_dir),
                "images": copied,
                "image_cleanup": {
                    "enabled": bool(sanitize_per_post),
                    "manifest": str(processing_manifest) if sanitize_per_post else "",
                    "processed": len(copied) if sanitize_per_post else 0,
                },
            }
        )

    def update(state_value: dict[str, Any]) -> None:
        state_value["status"] = "generated"
        state_value.pop("validation", None)
        state_value.pop("upload_plan", None)
        state_value.pop("uploads", None)
        generation_value = state_value.get("generation")
        if isinstance(generation_value, dict):
            generation_value["image_attachments"] = attached

    update_run(directory, update)
    append_event(
        directory,
        "generated_images_attached",
        status="generated",
        message=f"새 원고 {len(attached)}개에 로컬 이미지 파일과 태그를 연결했습니다.",
        details={
            "posts": len(attached),
            "images": sum(len(item["images"]) for item in attached),
            "per_post_cleanup": bool(sanitize_per_post),
        },
    )
    return {
        "ok": True,
        "run_dir": str(directory),
        "post_count": len(attached),
        "image_count": sum(len(item["images"]) for item in attached),
        "posts": attached,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Attach run-local images to Mato post folders.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--skip-image-sanitize", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = attach_images(
            args.run_dir,
            args.manifest,
            sanitize_per_post=not args.skip_image_sanitize,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
