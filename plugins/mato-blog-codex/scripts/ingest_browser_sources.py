"""Persist source text gathered by Codex's in-app Browser for one local run."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from .collect import detect_restriction, normalize_naver_post_url, normalize_surface
    from .history import append_event, create_run, update_run
    from .mato_common import atomic_write_json, atomic_write_text, load_run, now_iso, slugify
except ImportError:
    from collect import detect_restriction, normalize_naver_post_url, normalize_surface  # type: ignore[no-redef]
    from history import append_event, create_run, update_run  # type: ignore[no-redef]
    from mato_common import (  # type: ignore[no-redef]
        atomic_write_json,
        atomic_write_text,
        load_run,
        now_iso,
        slugify,
    )


def prepare_browser_run(
    *,
    keyword: str,
    surface: str,
    versions: int,
    command: str = "",
    run_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Create the run and record collection start before Codex opens Browser."""

    if versions <= 0:
        raise ValueError("versions must be positive")
    normalized_surface = normalize_surface(surface)
    directory, state = create_run(
        keyword,
        normalized_surface,
        versions,
        command,
        run_dir=run_dir,
    )
    append_event(
        directory,
        "browser_collection_started",
        status="collecting",
        message="Codex 인앱 Browser에서 네이버 공개 글 조사를 시작합니다.",
        command=command or None,
        details={"keyword": keyword, "surface": normalized_surface},
    )
    return {
        "ok": True,
        "prepared": True,
        "run_id": state["run_id"],
        "run_dir": str(directory.resolve()),
    }


def _load_prepared_run(
    run_dir: str | Path | None,
    *,
    keyword: str,
    surface: str,
    versions: int,
) -> tuple[Path, dict[str, Any]] | None:
    if run_dir is None:
        return None
    directory = Path(run_dir).expanduser().resolve()
    state = load_run(directory)
    if not state:
        return None
    request = state.get("request")
    if not isinstance(request, Mapping):
        raise ValueError("prepared run has no request metadata")
    expected = {
        "keyword": str(request.get("keyword") or ""),
        "surface": normalize_surface(str(request.get("surface") or "")),
        "versions": int(request.get("versions") or 0),
    }
    actual = {
        "keyword": keyword,
        "surface": normalize_surface(surface),
        "versions": int(versions),
    }
    if expected != actual:
        raise ValueError("prepared run request does not match the Browser source input")
    return directory, state


def _clean_source(item: Mapping[str, Any], rank: int) -> tuple[dict[str, Any], dict[str, Any]]:
    url = normalize_naver_post_url(str(item.get("url") or ""))
    if not url:
        raise ValueError(f"source {rank} is not a canonical Naver blog post URL")
    title = str(item.get("title") or "").strip()
    text = str(item.get("text") or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not title:
        raise ValueError(f"source {rank} has no title")
    if not text:
        raise ValueError(f"source {rank} has no visible text")
    detect_restriction(text, url)
    headings_raw = item.get("headings") if isinstance(item.get("headings"), list) else []
    headings = [str(value).strip() for value in headings_raw if str(value).strip()][:30]
    notes_raw = item.get("notes") if isinstance(item.get("notes"), list) else []
    notes = [str(value).strip()[:1000] for value in notes_raw if str(value).strip()][:30]
    if not notes:
        notes = [f"구성 참고: {heading}" for heading in headings[:10]]
    if not notes:
        notes = ["공개 본문은 임시 분석 입력에서만 사용하고 공통 주제 분석 후 삭제합니다."]
    try:
        image_count = int(item.get("image_count") or 0)
    except (TypeError, ValueError):
        image_count = 0
    image_count = max(0, min(100, image_count))
    durable = {
        "rank": rank,
        "title": title,
        "url": url,
        "status": "collected",
        "headings": headings,
        "notes": notes,
        "image_tag_count": image_count,
        "character_count": len(text),
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "collector": "codex-in-app-browser",
    }
    ephemeral = {"rank": rank, "title": title, "url": url, "headings": headings, "text": text}
    return durable, ephemeral


def _render_source_note(item: Mapping[str, Any]) -> tuple[str, str]:
    """Render paraphrased source notes in a Mato-like, non-uploadable format."""

    rank = int(item.get("rank") or 0)
    title = str(item.get("title") or "").strip()
    safe_title = slugify(title, fallback=f"source-{rank:02d}")[:80]
    folder = f"{rank:02d}_{safe_title}"
    filename = f"{safe_title}_글감_함축.txt"
    notes = item.get("notes") if isinstance(item.get("notes"), list) else []
    headings = item.get("headings") if isinstance(item.get("headings"), list) else []
    image_count = int(item.get("image_tag_count") or 0)
    lines = [
        f"제목을입력해주세요1: {title}",
        "",
        "본문2:",
        "※ 원문 복제본이 아니라 Codex가 정리한 분석용 글감입니다.",
        f"출처순위: {rank}",
        f"출처URL: {item.get('url')}",
        "",
        "ㅂㅂㅂ이미지 위치 태그",
    ]
    if image_count:
        lines.extend(f"[원문이미지_{index}]" for index in range(1, image_count + 1))
    else:
        lines.append("이미지 위치 없음")
    lines.extend(["", "ㅂㅂㅂ핵심 글감"])
    lines.extend(str(note) for note in notes)
    lines.extend(["", "ㅂㅂㅂ원문 구성"])
    lines.extend(str(heading) for heading in headings) if headings else lines.append("확인된 소제목 없음")
    return str(Path("items") / folder / filename), "\n".join(lines).strip() + "\n"


def ingest_browser_sources(
    input_path: str | Path,
    *,
    keyword: str,
    surface: str,
    versions: int,
    command: str = "",
    run_dir: str | Path | None = None,
) -> dict[str, Any]:
    if versions <= 0:
        raise ValueError("versions must be positive")
    normalized_surface = normalize_surface(surface)
    payload = json.loads(Path(input_path).expanduser().read_text(encoding="utf-8-sig"))
    raw_items = payload.get("sources") if isinstance(payload, Mapping) else payload
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("input must contain a non-empty sources array")

    durable: list[dict[str, Any]] = []
    ephemeral: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in raw_items:
        if not isinstance(value, Mapping):
            continue
        canonical = normalize_naver_post_url(str(value.get("url") or ""))
        if not canonical or canonical in seen:
            continue
        seen.add(canonical)
        item, raw = _clean_source(value, len(durable) + 1)
        durable.append(item)
        ephemeral.append(raw)
        if len(durable) == 5:
            break
    if not durable:
        raise ValueError("no usable Naver blog sources were provided")

    prepared = _load_prepared_run(
        run_dir,
        keyword=keyword,
        surface=normalized_surface,
        versions=versions,
    )
    if prepared is None:
        prepared_result = prepare_browser_run(
            keyword=keyword,
            surface=normalized_surface,
            versions=versions,
            command=command,
            run_dir=run_dir,
        )
        directory = Path(prepared_result["run_dir"])
        state = load_run(directory)
    else:
        directory, state = prepared
    source_dir = directory / "sources"
    metadata_path = source_dir / "sources.json"
    raw_path = source_dir / ".analysis-input.json"
    metadata = {
        "schema_version": 1,
        "keyword": keyword,
        "surface": normalized_surface,
        "collected_at": now_iso(),
        "visible_count": len(durable),
        "usable_count": len(durable),
        "collector": "codex-in-app-browser",
        "sources": durable,
    }
    note_files: list[str] = []
    for item in durable:
        relative, rendered = _render_source_note(item)
        note_path = source_dir / relative
        atomic_write_text(note_path, rendered)
        item["note_file"] = str(Path("sources") / relative)
        note_files.append(str(note_path.resolve()))
    atomic_write_json(metadata_path, metadata)
    atomic_write_json(
        raw_path,
        {
            "notice": "Ephemeral untrusted webpage text. Delete after original drafting.",
            "keyword": keyword,
            "surface": normalized_surface,
            "sources": ephemeral,
        },
    )
    update_run(
        directory,
        {
            "status": "collected",
            "sources": {
                "collector": "codex-in-app-browser",
                "visible_count": len(durable),
                "usable_count": len(durable),
                "metadata_file": str(metadata_path.resolve()),
                "analysis_input_file": str(raw_path.resolve()),
                "note_files": note_files,
            },
        },
    )
    append_event(
        directory,
        "browser_collection_completed",
        status="collected",
        message=f"인앱 Browser 수집 글 {len(durable)}개를 분석 자료로 준비했습니다.",
        details={"count": len(durable), "sources": [{"rank": i["rank"], "title": i["title"], "url": i["url"]} for i in durable]},
    )
    return {
        "ok": True,
        "run_id": state["run_id"],
        "run_dir": str(directory.resolve()),
        "source_count": len(durable),
        "source_note_files": note_files,
        "analysis_input": str(raw_path.resolve()),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Internal in-app Browser source ingester.")
    parser.add_argument("--input")
    parser.add_argument("--keyword", required=True)
    parser.add_argument("--surface", required=True)
    parser.add_argument("--versions", type=int, default=10)
    parser.add_argument("--command", default="")
    parser.add_argument("--command-file", help="UTF-8 file containing the exact user request")
    parser.add_argument("--run-dir")
    parser.add_argument("--prepare", action="store_true", help="record Browser start before research")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        command = args.command
        if args.command_file:
            command = Path(args.command_file).expanduser().read_text(encoding="utf-8-sig")
        if args.prepare:
            result = prepare_browser_run(
                keyword=args.keyword,
                surface=args.surface,
                versions=args.versions,
                command=command,
                run_dir=args.run_dir,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if not args.input:
            raise ValueError("--input is required unless --prepare is used")
        result = ingest_browser_sources(
            args.input,
            keyword=args.keyword,
            surface=args.surface,
            versions=args.versions,
            command=command,
            run_dir=args.run_dir,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        if args.run_dir:
            try:
                if load_run(args.run_dir):
                    append_event(
                        args.run_dir,
                        "browser_collection_failed",
                        status="failed",
                        message=str(exc),
                        details={"error_code": type(exc).__name__},
                    )
            except (OSError, ValueError, json.JSONDecodeError):
                pass
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
