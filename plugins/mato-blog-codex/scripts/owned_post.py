"""Prepare and download one user-owned Naver post as a Mato run source."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import parse_qs, urlparse

try:
    from . import mato_helper_bridge as bridge
    from .history import append_event, create_run, update_run
    from .mato_common import atomic_write_json, atomic_write_text, slugify
except ImportError:
    import mato_helper_bridge as bridge  # type: ignore[no-redef]
    from history import append_event, create_run, update_run  # type: ignore[no-redef]
    from mato_common import atomic_write_json, atomic_write_text, slugify  # type: ignore[no-redef]


def _owned_post_identity(url: str) -> tuple[str, str]:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in {"https", "http"} or parsed.netloc.lower() not in {
        "blog.naver.com",
        "m.blog.naver.com",
    }:
        raise ValueError("a public Naver blog post URL is required")
    query = parse_qs(parsed.query)
    blog_id = str((query.get("blogId") or [""])[0]).strip()
    log_no = str((query.get("logNo") or [""])[0]).strip()
    if not (blog_id and log_no):
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and re.fullmatch(r"\d+", parts[1]):
            blog_id, log_no = parts[0], parts[1]
    if not re.fullmatch(r"[A-Za-z0-9._-]{2,64}", blog_id) or not re.fullmatch(r"\d{1,24}", log_no):
        raise ValueError("the URL must include a Naver blog id and post number")
    return blog_id, log_no


def prepare_owned_post_download(
    url: str,
    *,
    versions: int,
    command: str,
    run_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Create a run for one owned post and download its local source assets."""

    if versions <= 0:
        raise ValueError("versions must be positive")
    blog_id, log_no = _owned_post_identity(url)
    keyword = f"내 글 {blog_id}-{log_no}"
    directory, _state = create_run(
        keyword,
        "integrated",
        int(versions),
        command or url,
        run_dir=run_dir,
    )
    safe_title = f"내 글 {blog_id} {log_no}"
    folder_name = f"01_{slugify(safe_title, fallback='owned-post')[:80]}"
    note_name = f"{slugify(safe_title, fallback='owned-post')[:80]}_글감_함축.txt"
    note_relative = Path("sources") / "items" / folder_name / note_name
    source_note = (
        f"제목을입력해주세요1: {safe_title}\n\n"
        "본문2:\n"
        "사용자가 소유한다고 지정한 글 URL입니다. 원문은 다운로드 후 원고 생성에만 사용합니다.\n"
    )
    atomic_write_text(directory / note_relative, source_note)
    metadata = {
        "schema_version": 1,
        "keyword": keyword,
        "surface": "integrated",
        "collector": "owned-post-url",
        "visible_count": 1,
        "usable_count": 1,
        "sources": [
            {
                "rank": 1,
                "title": safe_title,
                "url": str(url).strip(),
                "status": "owned_by_user",
                "note_file": str(note_relative),
            }
        ],
    }
    metadata_path = directory / "sources" / "sources.json"
    atomic_write_json(metadata_path, metadata)
    append_event(
        directory,
        "owned_post_download_started",
        status="collecting",
        message="사용자가 지정한 내 글 URL의 본문과 이미지를 내려받기 시작했습니다.",
        command=command or url,
        details={"url": str(url).strip(), "versions": int(versions)},
    )
    # The content downloader is bundled with this plugin.  The URL workflow
    # therefore does not require a Mato Helper checkout on the user's PC.
    result = bridge.run_direct_url_download(None, str(directory))
    if not result.get("ok"):
        raise bridge.BridgeError("owned post download did not complete")
    entry = next((item for item in result.get("entries", []) if item.get("ok")), None)
    if not isinstance(entry, dict):
        raise bridge.BridgeError("owned post download returned no usable source folder")

    def update(state: dict[str, Any]) -> None:
        state["status"] = "collected"
        state["sources"] = {
            "collector": "owned-post-url",
            "metadata_file": str(metadata_path),
            "owned_source_url": str(url).strip(),
            "owned_source_folder": str(entry.get("folder") or folder_name),
            "source_images": int(entry.get("images") or 0),
        }

    update_run(directory, update)
    append_event(
        directory,
        "owned_post_download_completed",
        status="collected",
        message="내 글 원본 데이터셋 다운로드와 초기 이미지 정리가 완료됐습니다.",
        details={"images": int(entry.get("images") or 0), "folder": entry.get("folder")},
    )
    return {
        "ok": True,
        "run_dir": str(directory.resolve()),
        "source_folder": str((directory / "sources" / "items" / str(entry.get("folder") or folder_name)).resolve()),
        "source": entry,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download one user-owned Naver post into a Mato run.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--versions", type=int, required=True)
    parser.add_argument("--command", default="")
    parser.add_argument("--run-dir", default="")
    args = parser.parse_args(argv)
    try:
        result = prepare_owned_post_download(
            args.url,
            versions=args.versions,
            command=args.command,
            run_dir=args.run_dir or None,
        )
    except (OSError, ValueError, bridge.BridgeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
