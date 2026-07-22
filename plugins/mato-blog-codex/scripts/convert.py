"""Convert local text folders to Mato ``_함축.txt`` post files.

The source TXT and any image files are preserved. Image-tag repair, when
requested, changes only the output text and never downloads or renames files.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
WINDOWS_INVALID_NAME = re.compile(r'[\\/:*?"<>|]+')


def safe_title(value: str, fallback: str) -> str:
    """Return a filename-safe post title while retaining Korean text."""

    title = WINDOWS_INVALID_NAME.sub("_", str(value or "").strip()).strip(" .")
    return title or fallback


def read_text(path: Path) -> str:
    """Read common Korean text encodings without modifying the source file."""

    for encoding in ("utf-8", "utf-8-sig", "cp949"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("unknown", b"", 0, 0, f"텍스트 인코딩을 읽지 못했습니다: {path.name}")


def _source_file(folder: Path, output: Path) -> Path | None:
    candidates = sorted(
        item for item in folder.iterdir()
        if item.is_file() and item.suffix.lower() == ".txt" and item != output
    )
    return candidates[0] if candidates else (output if output.exists() else None)


def _image_name_index(folder: Path) -> dict[str, str]:
    return {
        item.stem.casefold(): item.name
        for item in folder.iterdir()
        if item.is_file() and item.suffix.lower() in IMAGE_EXTENSIONS
    }


def repair_image_tags(content: str, folder: Path) -> str:
    """Resolve extension-less image tags against files in the same folder."""

    image_index = _image_name_index(folder)

    def replace(match: re.Match[str]) -> str:
        inner = match.group(1).strip()
        path = Path(inner)
        if path.suffix.lower() in IMAGE_EXTENSIONS:
            return f"[{inner}]"
        actual = image_index.get(inner.casefold()) or image_index.get(path.stem.casefold())
        return f"[{actual}]" if actual else f"[{inner}.png]"

    return re.sub(r"\[([^\]\r\n]+)\]", replace, content)


def ensure_mato_headers(content: str, title: str) -> str:
    """Add missing title and body markers without rewriting the article body."""

    lines = content.splitlines()
    has_title = any(line.startswith("제목을입력해주세요1:") for line in lines[:5])
    has_body = any(line.strip() == "본문2:" for line in lines[:50])
    if not has_title:
        prefix = f"제목을입력해주세요1: {title}\n"
        if not has_body:
            prefix += "\n본문2:\n"
        return prefix + content
    if has_body:
        return content

    rewritten: list[str] = []
    inserted = False
    for line in lines:
        rewritten.append(line)
        if not inserted and line.startswith("제목을입력해주세요1:"):
            rewritten.extend(("", "본문2:"))
            inserted = True
    return "\n".join(rewritten) + ("\n" if content.endswith(("\n", "\r")) else "")


def convert_folder(
    folder_path: str | Path,
    *,
    title: str = "",
    overwrite: bool = False,
    fix_image_tags: bool = False,
) -> dict[str, Any]:
    """Convert one user-selected folder and return a non-sensitive summary."""

    folder = Path(folder_path).expanduser().resolve()
    if not folder.is_dir():
        raise FileNotFoundError(f"변환 폴더를 찾을 수 없습니다: {folder}")
    post_title = safe_title(title, folder.name)
    output = folder / f"{post_title}_함축.txt"
    source = _source_file(folder, output)
    if source is None:
        return {"folder": folder.name, "title": post_title, "ok": False, "reason": "TXT 파일 없음"}
    if output.exists() and source != output and not overwrite:
        return {
            "folder": folder.name,
            "title": post_title,
            "ok": False,
            "reason": "동일한 _함축.txt가 이미 있습니다. 덮어쓰기를 명시하세요.",
            "output": output.name,
        }

    content = ensure_mato_headers(read_text(source), post_title)
    if fix_image_tags:
        content = repair_image_tags(content, folder)
    output.write_text(content, encoding="utf-8", newline="\n")
    return {
        "folder": folder.name,
        "title": post_title,
        "ok": True,
        "source": source.name,
        "output": output.name,
        "char_count": len(content),
        "image_tags_repaired": fix_image_tags,
    }


def convert_rows(
    rows: Iterable[Mapping[str, Any]], *, overwrite: bool = False, fix_image_tags: bool = False
) -> dict[str, Any]:
    """Convert a bounded list of folders, retaining each failure in the result."""

    clean_rows = [row for row in rows if str(row.get("folder_path") or "").strip()]
    if not clean_rows:
        raise ValueError("변환할 폴더를 하나 이상 지정하세요.")
    if len(clean_rows) > 50:
        raise ValueError("한 번에 변환할 수 있는 폴더는 최대 50개입니다.")

    entries: list[dict[str, Any]] = []
    for row in clean_rows:
        try:
            entries.append(
                convert_folder(
                    str(row["folder_path"]),
                    title=str(row.get("title") or row.get("name") or ""),
                    overwrite=overwrite,
                    fix_image_tags=fix_image_tags,
                )
            )
        except Exception as exc:
            entries.append(
                {
                    "folder": Path(str(row["folder_path"])).name,
                    "title": str(row.get("title") or row.get("name") or ""),
                    "ok": False,
                    "reason": str(exc)[:240],
                }
            )
    return {
        "kind": "mato_convert",
        "processed": len(entries),
        "succeeded": sum(bool(item.get("ok")) for item in entries),
        "failed": sum(not bool(item.get("ok")) for item in entries),
        "entries": entries,
    }


def _load_rows(path: Path) -> list[Mapping[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("rows") if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or not all(isinstance(row, Mapping) for row in rows):
        raise ValueError("입력 JSON은 rows 배열 또는 행 배열이어야 합니다.")
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convert local TXT folders to Mato _함축.txt files.")
    parser.add_argument("--input", required=True, help="JSON rows: [{folder_path, title?}]")
    parser.add_argument("--overwrite", action="store_true", help="Allow replacing an existing output file.")
    parser.add_argument("--fix-image-tags", action="store_true", help="Repair text image tags only; never modify images.")
    args = parser.parse_args(argv)
    try:
        result = convert_rows(
            _load_rows(Path(args.input)),
            overwrite=args.overwrite,
            fix_image_tags=args.fix_image_tags,
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
