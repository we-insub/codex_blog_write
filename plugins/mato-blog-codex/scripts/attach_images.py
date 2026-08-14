"""Attach locally downloaded source images to generated Mato post folders."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from .history import append_event, update_run
    from .mato_common import load_run
except ImportError:
    from history import append_event, update_run  # type: ignore[no-redef]
    from mato_common import load_run  # type: ignore[no-redef]


IMAGE_NAME_RE = re.compile(r"image_([1-9]\d*)\.jpg", re.IGNORECASE)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inside(path: Path, root: Path) -> bool:
    resolved = path.resolve(strict=False)
    base = root.resolve(strict=False)
    return resolved == base or base in resolved.parents


def _assert_tree_has_no_links(root: Path) -> None:
    if root.is_symlink():
        raise ValueError(f"image target folder must not be a symlink: {root}")
    for candidate in root.rglob("*"):
        if candidate.is_symlink():
            raise ValueError(f"image target folder contains a symlink: {candidate}")


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
    plans: list[dict[str, Any]] = []
    seen_indexes: set[int] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        index = int(row.get("index") or 0)
        if index <= 0 or index in seen_indexes:
            raise ValueError(f"duplicate or invalid generated post index: {index}")
        seen_indexes.add(index)
        generated_item = by_index.get(index)
        if generated_item is None:
            raise ValueError(f"unknown generated post index: {index}")
        text_path = (directory / str(generated_item.get("file") or "")).resolve()
        if not _inside(text_path, posts_root) or not text_path.is_file():
            raise ValueError(f"unsafe or missing generated post path: {text_path}")
        post_dir = text_path.parent
        _assert_tree_has_no_links(post_dir)
        images = row.get("images")
        if not isinstance(images, list) or not images:
            raise ValueError(f"post {index} has no image mappings")
        copied: list[str] = []
        source_rows: list[dict[str, Any]] = []
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
            expected_sha = str(raw.get("sha256") or "").strip().lower()
            if expected_sha and (
                not re.fullmatch(r"[0-9a-f]{64}", expected_sha)
                or _sha256(source) != expected_sha
            ):
                raise ValueError(f"post {index} source image hash mismatch: {source_value}")
            copied.append(target_name)
            source_rows.append(
                {"source": source, "target": target_name, "sha256": expected_sha}
            )

        if len(set(copied)) != len(copied):
            raise ValueError(f"post {index} has duplicate target image names")

        text = text_path.read_text(encoding="utf-8-sig")
        references = re.findall(r"\[(image_[1-9]\d*\.jpg)\]", text, flags=re.IGNORECASE)
        if references != copied:
            raise ValueError(
                f"post {index} image tags do not match manifest order: tags={references}, files={copied}"
            )
        plans.append(
            {
                "index": index,
                "post_dir": post_dir,
                "sources": source_rows,
                "images": copied,
            }
        )

    if len(plans) != len(generated):
        raise ValueError("image manifest must map every generated post exactly once")

    # Build every complete post folder first.  Only after all copies and image
    # cleanup pass do we swap any live folder, so a late corrupt image cannot
    # leave a half-attached dataset behind.
    staging_root = Path(tempfile.mkdtemp(prefix=".mato-image-attach-", dir=directory))
    staged_rows: list[dict[str, Any]] = []
    attached: list[dict[str, Any]] = []
    committed: list[tuple[Path, Path]] = []
    try:
        for plan in plans:
            index = int(plan["index"])
            post_dir = Path(plan["post_dir"])
            staged_post = staging_root / "staged" / f"post-{index}"
            staged_post.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(post_dir, staged_post)
            for stale in staged_post.glob("image_*.jpg"):
                if stale.is_file() and not stale.is_symlink():
                    stale.unlink()
            (staged_post / "image-processing.json").unlink(missing_ok=True)
            for source_row in plan["sources"]:
                shutil.copy2(
                    Path(source_row["source"]),
                    staged_post / str(source_row["target"]),
                )
                expected_sha = str(source_row.get("sha256") or "")
                if expected_sha and _sha256(staged_post / str(source_row["target"])) != expected_sha:
                    raise ValueError(f"post {index} staged image hash mismatch")

            processing_manifest = staged_post / "image-processing.json"
            if sanitize_per_post:
                try:
                    import sanitize_images

                    processing = sanitize_images.sanitize_path(
                        staged_post,
                        recursive=False,
                        manifest_path=processing_manifest,
                    )
                except Exception as exc:
                    raise ValueError(f"post {index} image cleanup failed: {exc}") from exc
                if not processing.get("ok") or int(processing.get("processed") or 0) != len(
                    plan["images"]
                ):
                    raise ValueError(f"post {index} image cleanup was incomplete")

            actual = sorted(
                item.name for item in staged_post.glob("image_*.jpg") if item.is_file()
            )
            if actual != sorted(plan["images"]):
                raise ValueError(f"post {index} staged image set is incomplete")
            staged_rows.append({**plan, "staged_post": staged_post})
            attached.append(
                {
                    "index": index,
                    "folder": str(post_dir),
                    "images": list(plan["images"]),
                    "image_cleanup": {
                        "enabled": bool(sanitize_per_post),
                        "manifest": str(post_dir / "image-processing.json")
                        if sanitize_per_post
                        else "",
                        "processed": len(plan["images"]) if sanitize_per_post else 0,
                    },
                }
            )

        backup_root = staging_root / "backups"
        backup_root.mkdir(parents=True, exist_ok=True)
        for staged in staged_rows:
            post_dir = Path(staged["post_dir"])
            backup = backup_root / f"post-{staged['index']}"
            post_dir.replace(backup)
            try:
                Path(staged["staged_post"]).replace(post_dir)
            except Exception:
                backup.replace(post_dir)
                raise
            committed.append((post_dir, backup))

        def update(state_value: dict[str, Any]) -> None:
            state_value["status"] = "generated"
            state_value.pop("validation", None)
            state_value.pop("upload_plan", None)
            state_value.pop("uploads", None)
            generation_value = state_value.get("generation")
            if isinstance(generation_value, dict):
                generation_value["image_attachments"] = attached
                source_manifest = payload.get("source_manifest")
                if isinstance(source_manifest, str) and source_manifest.strip():
                    generation_value["image_source_manifest"] = source_manifest

        update_run(directory, update)
    except Exception:
        for post_dir, backup in reversed(committed):
            if post_dir.exists():
                shutil.rmtree(post_dir)
            if backup.exists():
                backup.replace(post_dir)
        raise
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)

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
