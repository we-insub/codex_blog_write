"""Safe local run journaling for Mato Blog Codex.

Every event is appended to both ``<run>/HISTORY.md`` and ``<run>/run.json``.
A compact copy is also appended to ``~/.googleblog/mato-blog-codex/HISTORY.md``.
All timestamps are KST, sensitive values are redacted, and scraped article
bodies are intentionally excluded from the journal.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, MutableMapping, Sequence
from urllib.parse import urlparse

try:
    from .mato_common import (
        SCHEMA_VERSION,
        atomic_append_text,
        atomic_write_json,
        compact_json,
        default_runs_root,
        display_timestamp_kst,
        global_history_path,
        load_run as common_load_run,
        now_kst,
        redact_text,
        sanitize_for_history,
        save_run,
        slugify,
        timestamp_kst,
    )
except ImportError:  # Direct execution: ``python scripts/history.py``.
    from mato_common import (  # type: ignore[no-redef]
        SCHEMA_VERSION,
        atomic_append_text,
        atomic_write_json,
        compact_json,
        default_runs_root,
        display_timestamp_kst,
        global_history_path,
        load_run as common_load_run,
        now_kst,
        redact_text,
        sanitize_for_history,
        save_run,
        slugify,
        timestamp_kst,
    )


TERMINAL_STATUSES = {"completed", "complete", "failed", "cancelled", "blocked"}
PRODUCT_SOURCE_TYPE = "myrealtrip_product"
PRODUCT_IMAGE_MODE = "all_unique_seller_product_images"
PRODUCT_MAX_IMAGES = 80
PRODUCT_REQUEST_FIELDS = {
    "source_type",
    "channel",
    "product_url",
    "main_keyword",
    "subkeywords",
    "hook",
    "companions",
    "experience_notes",
    "image_policy",
    "link_wait_ms",
}


def _safe_mapping(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a recursively sanitized plain dictionary."""

    sanitized = sanitize_for_history(dict(value or {}))
    if not isinstance(sanitized, dict):  # Defensive; mappings always sanitize to dict.
        return {}
    return sanitized


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return redact_text(value).strip()


def _clean_text_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise ValueError(f"request_fields.{field_name}는 문자열 배열이어야 합니다.")
    result: list[str] = []
    for raw in value:
        cleaned = _clean_text(raw)
        if cleaned and cleaned not in result:
            result.append(cleaned)
    return result


def _permission_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    if value is None:
        return False
    raise ValueError("image_policy.permission_confirmed는 boolean이어야 합니다.")


def _valid_product_url(value: str) -> bool:
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower().rstrip(".")
    try:
        port = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme.lower() != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        return False
    if host == "experiences.myrealtrip.com":
        return bool(re.fullmatch(r"/products/[1-9][0-9]*/?", parsed.path))
    if host == "myrealt.rip":
        return bool(
            re.fullmatch(r"/[A-Za-z0-9_-]{4,64}/?", parsed.path)
            and not parsed.query
        )
    return bool(
        host in {"myrealtrip.com", "www.myrealtrip.com"}
        and parsed.path.rstrip("/") == "/main/bridge/marketing"
        and re.search(r"(?:^|&)return_url=", parsed.query, re.IGNORECASE)
    )


def _normalize_product_image_policy(value: Any) -> dict[str, Any]:
    if value is None:
        raw: Mapping[str, Any] = {}
    elif isinstance(value, Mapping):
        raw = value
    else:
        raise ValueError("request_fields.image_policy는 객체여야 합니다.")

    mode = _clean_text(raw.get("mode") or PRODUCT_IMAGE_MODE)
    if mode not in {PRODUCT_IMAGE_MODE, "none"}:
        raise ValueError(
            "image_policy.mode은 all_unique_seller_product_images 또는 none이어야 합니다."
        )
    try:
        max_images = int(raw.get("max_images", PRODUCT_MAX_IMAGES))
    except (TypeError, ValueError):
        raise ValueError("image_policy.max_images는 정수여야 합니다.") from None
    if not 1 <= max_images <= PRODUCT_MAX_IMAGES:
        raise ValueError("image_policy.max_images는 1에서 80 사이여야 합니다.")
    return {
        "mode": mode,
        "permission_confirmed": _permission_flag(raw.get("permission_confirmed")),
        "max_images": max_images,
    }


def _build_run_request(
    keyword: str,
    surface: str,
    versions: int,
    command: str,
    request_fields: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], str]:
    """Build a backwards-compatible request and its safe run-folder label."""

    fields = {
        key: value
        for key, value in dict(request_fields or {}).items()
        if key in PRODUCT_REQUEST_FIELDS
    }
    source_type = _clean_text(fields.get("source_type"))
    is_product = source_type == PRODUCT_SOURCE_TYPE
    normalized_surface = "product" if is_product else str(surface).strip()
    if normalized_surface not in {"통합검색", "블로그탭", "integrated", "blog", "product"}:
        raise ValueError("검색 영역은 통합검색, 블로그탭 또는 product여야 합니다.")
    if normalized_surface == "product" and not is_product:
        raise ValueError("product 영역은 myrealtrip_product 요청에서만 사용할 수 있습니다.")

    cleaned_keyword = _clean_text(keyword)
    if is_product:
        cleaned_keyword = _clean_text(fields.get("main_keyword", cleaned_keyword))
        if re.search(r"https?://", cleaned_keyword, re.IGNORECASE):
            cleaned_keyword = ""
    elif not cleaned_keyword:
        raise ValueError("검색어가 비어 있습니다.")

    request: dict[str, Any] = {
        "keyword": cleaned_keyword,
        "surface": normalized_surface,
        "versions": versions,
        "command": _clean_text(command),
        "image_mode": "none",
    }
    if is_product:
        product_url = _clean_text(fields.get("product_url"))
        if not _valid_product_url(product_url):
            raise ValueError("myrealtrip_product 요청에 안전한 product_url이 필요합니다.")
        channel = _clean_text(fields.get("channel") or "naver").lower()
        if channel != "naver":
            raise ValueError("myrealtrip_product channel은 v1에서 naver만 허용됩니다.")
        image_policy = _normalize_product_image_policy(fields.get("image_policy"))
        try:
            link_wait_ms = int(fields.get("link_wait_ms", 2_000))
        except (TypeError, ValueError):
            raise ValueError("link_wait_ms는 정수여야 합니다.") from None
        if link_wait_ms != 2_000:
            raise ValueError("myrealtrip_product link_wait_ms는 정확히 2000이어야 합니다.")
        request.update(
            {
                "source_type": PRODUCT_SOURCE_TYPE,
                "channel": channel,
                "product_url": product_url,
                "main_keyword": cleaned_keyword,
                "subkeywords": _clean_text_list(fields.get("subkeywords"), "subkeywords"),
                "hook": _clean_text(fields.get("hook")),
                "companions": _clean_text_list(fields.get("companions"), "companions"),
                "experience_notes": _clean_text_list(
                    fields.get("experience_notes"), "experience_notes"
                ),
                "image_policy": image_policy,
                "image_mode": image_policy["mode"],
                "link_wait_ms": link_wait_ms,
            }
        )
    folder_label = cleaned_keyword or ("myrealtrip-product" if is_product else "run")
    return request, folder_label


def _elapsed_seconds(started_at: str, ended_at: str) -> int | None:
    """Calculate elapsed whole seconds from ISO timestamps when possible."""

    try:
        start = datetime.fromisoformat(started_at)
        end = datetime.fromisoformat(ended_at)
    except (TypeError, ValueError):
        return None
    return max(0, int((end - start).total_seconds()))


def _markdown_scalar(value: Any) -> str:
    """Render a safe one-line Markdown value."""

    if isinstance(value, (dict, list)):
        rendered = compact_json(value)
    elif value is None:
        rendered = "-"
    else:
        rendered = str(value)
    return rendered.replace("\r", " ").replace("\n", " ").replace("`", "\\`")


def _event_markdown(event: Mapping[str, Any], *, run_dir: Path | None = None) -> str:
    """Create a concise Markdown block for a sanitized event."""

    event_type = _markdown_scalar(event.get("event_type", "event"))
    lines = [f"## {display_timestamp_kst(datetime.fromisoformat(str(event['timestamp'])))} — {event_type}"]
    if run_dir is not None:
        lines.append(f"- 실행 폴더: `{_markdown_scalar(str(run_dir.resolve()))}`")
    if event.get("status"):
        lines.append(f"- 상태: `{_markdown_scalar(event['status'])}`")
    if event.get("message"):
        lines.append(f"- 내용: {_markdown_scalar(event['message'])}")
    if event.get("command"):
        lines.append(f"- 사용자 명령: `{_markdown_scalar(event['command'])}`")
    details = event.get("details")
    if isinstance(details, Mapping) and details:
        lines.append("- 세부 정보:")
        for key, value in details.items():
            lines.append(f"  - {_markdown_scalar(key)}: `{_markdown_scalar(value)}`")
    return "\n".join(lines) + "\n\n"


def load_run(run_dir: str | Path) -> dict[str, Any]:
    """Load a run journal, returning an empty initialized state if absent."""

    directory = Path(run_dir).expanduser()
    path = directory / "run.json"
    state = common_load_run(directory)
    events = state.get("events", [])
    if not isinstance(events, list):
        raise ValueError(f"run.json의 events는 배열이어야 합니다: {path}")
    return state


def append_event(
    run_dir: str | Path,
    stage: str,
    status: str | None = None,
    message: str = "",
    details: Mapping[str, Any] | None = None,
    *,
    command: str | None = None,
    run_updates: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Append one event and return its sanitized dictionary.

    Existing events are retained, so calling this function after an interrupted
    run naturally records a new resume segment. ``run_updates`` is for compact
    state such as parsed inputs, assignments, output paths, and upload results;
    raw source text/HTML keys are replaced with ``[원문 미기록]``.
    """

    directory = Path(run_dir).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    state = load_run(directory)
    now = timestamp_kst()
    events = list(state.get("events", []))

    safe_event: dict[str, Any] = {
        "sequence": len(events) + 1,
        "timestamp": now,
        "stage": redact_text(stage).strip() or "event",
        "event_type": redact_text(stage).strip() or "event",
        "message": redact_text(message).strip(),
        "details": _safe_mapping(details),
    }
    if status:
        safe_event["status"] = redact_text(status).strip()
    if command:
        safe_event["command"] = redact_text(command).strip()

    if not state:
        state = {
            "schema_version": SCHEMA_VERSION,
            "run_id": directory.name,
            "run_dir": str(directory.resolve()),
            "started_at": now,
            "status": status or "in_progress",
            "events": [],
        }
    if run_updates:
        for key, value in _safe_mapping(run_updates).items():
            if key in {"events", "schema_version", "run_id", "run_dir", "started_at"}:
                continue
            state[key] = value

    events.append(safe_event)
    state["schema_version"] = SCHEMA_VERSION
    state["run_id"] = state.get("run_id") or directory.name
    state["run_dir"] = str(directory.resolve())
    state["events"] = events
    state["updated_at"] = now
    if status:
        state["status"] = safe_event["status"]
    else:
        state.setdefault("status", "in_progress")

    normalized_status = str(state.get("status", "")).lower()
    if normalized_status in TERMINAL_STATUSES:
        state["completed_at"] = now
        elapsed = _elapsed_seconds(str(state.get("started_at", "")), now)
        if elapsed is not None:
            state["elapsed_seconds"] = elapsed
            safe_event["details"].setdefault("elapsed_seconds", elapsed)
    elif status:
        state.pop("completed_at", None)
        state.pop("elapsed_seconds", None)

    # Sanitize the entire object as a last line of defence if an older caller
    # wrote unsafe fields before this module took over the run journal.
    sanitized_state = sanitize_for_history(state)
    if not isinstance(sanitized_state, dict):
        raise RuntimeError("실행 기록을 안전한 JSON 객체로 변환하지 못했습니다.")
    atomic_write_json(directory / "run.json", sanitized_state)

    block = _event_markdown(safe_event)
    atomic_append_text(
        directory / "HISTORY.md",
        block,
        heading=f"# Mato Blog Codex 실행 기록 — {directory.name}",
    )
    global_block = _event_markdown(safe_event, run_dir=directory)
    atomic_append_text(
        global_history_path(),
        global_block,
        heading="# Mato Blog Codex 전체 실행 기록",
    )
    return safe_event


def redact_sensitive(value: Any) -> Any:
    """Public history redaction helper for strings and structured data."""

    return redact_text(value) if isinstance(value, str) else sanitize_for_history(value)


def update_run(
    run_dir: str | Path,
    mutator_or_fields: Mapping[str, Any]
    | Callable[[MutableMapping[str, Any]], Mapping[str, Any] | None],
) -> dict[str, Any]:
    """Atomically update sanitized ``run.json`` state.

    A mapping is merged into the top level. A callable receives a mutable copy
    and may either modify it in place and return ``None``, or return a complete
    replacement mapping. Identity and event fields remain caller-controlled so
    the collection/generation/upload stages can maintain their compact state.
    """

    directory = Path(run_dir).expanduser()
    state: MutableMapping[str, Any] = dict(load_run(directory))
    if callable(mutator_or_fields):
        result = mutator_or_fields(state)
        if result is not None:
            state = dict(result)
    elif isinstance(mutator_or_fields, Mapping):
        state.update(mutator_or_fields)
    else:  # pragma: no cover - type checkers catch this for normal callers.
        raise TypeError("mutator_or_fields는 매핑 또는 함수여야 합니다.")
    state["updated_at"] = timestamp_kst()
    safe_state = sanitize_for_history(state)
    if not isinstance(safe_state, dict):
        raise RuntimeError("실행 상태를 안전한 JSON 객체로 변환하지 못했습니다.")
    return save_run(directory, safe_state)


def create_run(
    keyword: str,
    surface: str,
    versions: int,
    command: str,
    run_dir: str | Path | None = None,
    *,
    request_fields: Mapping[str, Any] | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Create and journal a workflow run without breaking legacy callers.

    When *run_dir* is omitted a unique ``YYYYMMDD_HHMMSS_<keyword>`` directory
    is created under today's KST date directory on the user's Desktop. Existing
    explicit run folders are treated as resumes and are never overwritten.
    ``request_fields`` carries the whitelisted MyRealTrip product contract; its
    product URL is never substituted for a missing search/main keyword.
    """

    if versions <= 0:
        raise ValueError("생성 개수는 1 이상이어야 합니다.")
    if request_fields is not None and not isinstance(request_fields, Mapping):
        raise ValueError("request_fields는 객체여야 합니다.")
    request, folder_label = _build_run_request(
        keyword,
        surface,
        versions,
        command,
        request_fields,
    )
    normalized_surface = str(request["surface"])
    normalized_keyword = str(request["keyword"])

    if run_dir is None:
        timestamp_segment = now_kst().strftime("%Y%m%d_%H%M%S")
        root = default_runs_root()
        base = root / f"{timestamp_segment}_{slugify(folder_label)}"
        directory = base
        suffix = 2
        while directory.exists():
            directory = Path(f"{base}_{suffix}")
            suffix += 1
    else:
        directory = Path(run_dir).expanduser()

    existing = load_run(directory)
    if existing:
        if not isinstance(existing.get("request"), Mapping):
            update_run(
                directory,
                {"request": request, "input": dict(request)},
            )
        event = append_event(
            directory,
            "resumed",
            "in_progress",
            "기존 실행을 재개했습니다.",
            {
                "keyword": normalized_keyword,
                "surface": normalized_surface,
                "versions": versions,
                "source_type": request.get("source_type", "naver_search"),
            },
            command=command,
        )
        del event
        return directory, load_run(directory)

    now = timestamp_kst()
    initial = {
        "schema_version": SCHEMA_VERSION,
        "run_id": directory.name,
        "run_dir": str(directory.resolve()),
        "started_at": now,
        "updated_at": now,
        "status": "in_progress",
        "request": request,
        "input": dict(request),
        "events": [],
    }
    safe_initial = sanitize_for_history(initial)
    if not isinstance(safe_initial, dict):
        raise RuntimeError("초기 실행 상태를 안전한 JSON 객체로 변환하지 못했습니다.")
    save_run(directory, safe_initial)
    append_event(
        directory,
        "created",
        "in_progress",
        "실행을 생성했습니다.",
        {
            "keyword": normalized_keyword,
            "surface": normalized_surface,
            "versions": versions,
            "source_type": request.get("source_type", "naver_search"),
        },
        command=command,
    )
    return directory, load_run(directory)


def show_history(run_dir: str | Path | None = None) -> str:
    """Return per-run history, or the local global history when omitted."""

    path = (
        Path(run_dir).expanduser() / "HISTORY.md"
        if run_dir is not None
        else global_history_path()
    )
    if not path.exists():
        return "아직 기록된 작업 히스토리가 없습니다.\n"
    return path.read_text(encoding="utf-8")


def _parse_json_object(raw: str | None, option_name: str) -> dict[str, Any]:
    """Parse a CLI JSON object with a useful Korean error message."""

    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{option_name} 값이 올바른 JSON이 아닙니다: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{option_name} 값은 JSON 객체여야 합니다.")
    return value


def build_parser() -> argparse.ArgumentParser:
    """Build the history command-line parser."""

    parser = argparse.ArgumentParser(description="Mato Blog Codex 작업 히스토리 관리")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    show_parser = subparsers.add_parser("show", help="전체 또는 실행별 HISTORY.md 표시")
    show_parser.add_argument("--run-dir", help="특정 실행 폴더; 생략하면 전체 히스토리")

    event_parser = subparsers.add_parser("event", help="실행 이벤트를 안전하게 추가")
    event_parser.add_argument("--run-dir", required=True, help="실행 폴더")
    event_parser.add_argument(
        "--type",
        "--event-type",
        "--stage",
        dest="event_type",
        required=True,
        help="예: search_started, generated, upload_failed",
    )
    event_parser.add_argument("--message", default="", help="사람이 읽을 수 있는 짧은 설명")
    event_parser.add_argument("--status", help="in_progress, completed, failed 등")
    event_parser.add_argument("--command", help="사용자가 입력한 원래 명령")
    event_parser.add_argument("--details-json", help="원문을 제외한 세부 정보 JSON 객체")
    event_parser.add_argument("--run-update-json", help="run.json 최상위 상태 갱신 JSON 객체")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the history command-line interface."""

    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.subcommand == "show":
            sys.stdout.write(show_history(args.run_dir))
            return 0
        details = _parse_json_object(args.details_json, "--details-json")
        updates = _parse_json_object(args.run_update_json, "--run-update-json")
        event = append_event(
            args.run_dir,
            args.event_type,
            args.status,
            args.message,
            details,
            command=args.command,
            run_updates=updates,
        )
        print(json.dumps(event, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(2, f"오류: {exc}\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
