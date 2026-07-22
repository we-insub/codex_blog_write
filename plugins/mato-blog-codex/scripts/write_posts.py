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


def render_mato_post(post: Mapping[str, Any]) -> tuple[str, str]:
    title = _clean_text(post.get("title"), field="title").replace("\n", " ")
    intro = _string_list(post.get("intro", []), field="intro")
    sections = post.get("sections")
    if not isinstance(sections, list) or not sections:
        raise ValueError(f"post '{title}' must contain a non-empty sections array")

    body_lines: list[str] = []
    body_lines.extend(intro)
    for section_index, section in enumerate(sections, start=1):
        if not isinstance(section, Mapping):
            raise ValueError(f"post '{title}' section {section_index} must be an object")
        heading = _clean_text(section.get("heading"), field=f"section {section_index} heading")
        heading = re.sub(r"^ㅂㅂㅂ\s*", "", heading).replace("\n", " ").strip()
        paragraphs = _string_list(
            section.get("paragraphs", []), field=f"section {section_index} paragraphs"
        )
        body_lines.extend(["", f"ㅂㅂㅂ{heading}"])
        body_lines.extend(paragraphs)

    body = "\n".join(body_lines).strip()
    rendered = f"제목을입력해주세요1: {title}\n\n본문2:\n{body}\n"
    return title, rendered


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
        title, rendered = render_mato_post(item)
        normalized_title = re.sub(r"\s+", "", title).casefold()
        if normalized_title in seen_titles:
            raise ValueError(f"duplicate title: {title}")
        seen_titles.add(normalized_title)
        safe_title = slugify(title, fallback=f"post-{index:02d}")[:80]
        folder = Path("posts") / f"v{index:02d}_{safe_title}"
        filename = f"{safe_title}_함축.txt"
        destination = folder / filename
        rendered_posts.append((destination, rendered))
        records.append(
            {
                "index": index,
                "title": title,
                "file": str(destination),
                "sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
                "status": "generated",
            }
        )

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
