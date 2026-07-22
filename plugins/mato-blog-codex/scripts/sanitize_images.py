"""Remove private image metadata and normalize image encoding.

The processor is intentionally platform-neutral.  It applies EXIF orientation,
removes EXIF/GPS/XMP/ICC payloads by omission, and atomically re-encodes the
image while preserving its filename.  A local JSON manifest records what was
processed without copying metadata values.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable


SUPPORTED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


class ImageSanitizeError(RuntimeError):
    """Raised when an image cannot be normalized safely."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _metadata_flags(image: Any) -> dict[str, bool]:
    exif = image.getexif()
    return {
        "exif": bool(exif),
        "gps": bool(exif.get(34853)) if exif else False,
        "xmp": any(str(key).lower() in {"xmp", "xml"} for key in image.info),
        "icc": bool(image.info.get("icc_profile")),
    }


def sanitize_image(path: str | Path, *, jpeg_quality: int = 92) -> dict[str, Any]:
    """Normalize one image in place and return a credential-free audit row."""

    try:
        from PIL import Image, ImageOps
    except Exception as exc:  # pragma: no cover - dependency failure
        raise ImageSanitizeError(f"Pillow를 불러올 수 없습니다: {exc}") from exc

    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise ImageSanitizeError(f"이미지 파일을 찾을 수 없습니다: {source}")
    suffix = source.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ImageSanitizeError(f"지원하지 않는 이미지 형식입니다: {source.suffix}")

    quality = max(70, min(100, int(jpeg_quality)))
    before_hash = _sha256(source)
    temporary_path: Path | None = None
    try:
        with Image.open(source) as opened:
            opened.load()
            before_metadata = _metadata_flags(opened)
            normalized = ImageOps.exif_transpose(opened)
            width, height = normalized.size

            handle, temporary_name = tempfile.mkstemp(
                prefix=f".{source.stem}-sanitize-",
                suffix=source.suffix,
                dir=source.parent,
            )
            os.close(handle)
            temporary_path = Path(temporary_name)

            if suffix in {".jpg", ".jpeg"}:
                normalized.convert("RGB").save(
                    temporary_path,
                    format="JPEG",
                    quality=quality,
                    optimize=True,
                )
            elif suffix == ".png":
                normalized.save(temporary_path, format="PNG", optimize=True)
            else:
                webp_mode = "RGBA" if "A" in normalized.getbands() else "RGB"
                normalized.convert(webp_mode).save(
                    temporary_path,
                    format="WEBP",
                    quality=quality,
                    method=6,
                )

        with Image.open(temporary_path) as verified:
            verified.load()
            after_metadata = _metadata_flags(verified)
            if any(after_metadata.values()):
                raise ImageSanitizeError(f"메타데이터 제거 검증에 실패했습니다: {source.name}")
            output_width, output_height = verified.size

        os.replace(temporary_path, source)
        temporary_path = None
    except ImageSanitizeError:
        raise
    except Exception as exc:
        raise ImageSanitizeError(f"이미지 정리에 실패했습니다 ({source.name}): {exc}") from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    return {
        "file": source.name,
        "path": str(source),
        "width": output_width,
        "height": output_height,
        "sha256_before": before_hash,
        "sha256_after": _sha256(source),
        "metadata_removed": before_metadata,
        "metadata_present_after": after_metadata,
    }


def _iter_images(path: Path, *, recursive: bool) -> Iterable[Path]:
    if path.is_file():
        if path.suffix.lower() in SUPPORTED_SUFFIXES:
            yield path
        return
    iterator = path.rglob("*") if recursive else path.iterdir()
    yield from sorted(
        item for item in iterator if item.is_file() and item.suffix.lower() in SUPPORTED_SUFFIXES
    )


def sanitize_path(
    path: str | Path,
    *,
    recursive: bool = False,
    jpeg_quality: int = 92,
    manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    """Normalize a file or directory and optionally write a JSON manifest."""

    target = Path(path).expanduser().resolve()
    if not target.exists():
        raise ImageSanitizeError(f"경로를 찾을 수 없습니다: {target}")

    entries: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for image_path in _iter_images(target, recursive=recursive):
        try:
            entries.append(sanitize_image(image_path, jpeg_quality=jpeg_quality))
        except Exception as exc:
            failures.append({"file": str(image_path), "error": str(exc)[:240]})

    result: dict[str, Any] = {
        "ok": bool(entries) and not failures,
        "kind": "image_metadata_sanitize",
        "target": str(target),
        "processed": len(entries),
        "failed": len(failures),
        "entries": entries,
        "failures": failures,
    }
    if manifest_path:
        manifest = Path(manifest_path).expanduser().resolve()
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        result["manifest"] = str(manifest)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Remove GPS/EXIF/XMP/ICC data and normalize owned images."
    )
    parser.add_argument("path", help="Image file or directory to process in place.")
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--jpeg-quality", type=int, default=92)
    parser.add_argument("--manifest", default="")
    args = parser.parse_args(argv)

    try:
        result = sanitize_path(
            args.path,
            recursive=args.recursive,
            jpeg_quality=args.jpeg_quality,
            manifest_path=args.manifest or None,
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
