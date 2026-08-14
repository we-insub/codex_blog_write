"""Manage the editable, local Mato common prompt without Mato Helper runtime access."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any

try:
    from .mato_common import googleblog_home
except ImportError:
    from mato_common import googleblog_home  # type: ignore[no-redef]


DEFAULT_COMMON_PROMPT = """# Mato 공통 글작성 규칙

- 최종 글은 독자가 바로 읽는 독립 글로 작성하고 원문·출처·작성자를 언급하지 않습니다.
- 확인되지 않은 방문·구매·사용 경험, 날짜, 가격, 수치, 인용을 만들지 않습니다.
- 내 글 URL로 제공된 경험은 사용자가 제공한 사실로 보고 자연스러운 1인칭 글로 씁니다.
- 제목은 사용자가 지정한 경우 한 글자도 바꾸지 않습니다.
- 모바일에서 읽기 좋게 문장을 짧게 나누고 Markdown 기호를 쓰지 않습니다.
- Mato 형식 `제목을입력해주세요1:`, `본문2:`, `ㅂㅂㅂ소제목`을 지킵니다.
- 이미지 태그는 제공된 `[image_N.jpg]` 순서와 위치를 바꾸거나 추가·삭제하지 않습니다.
"""


def prompt_path(prompt_key: str = "공통") -> Path:
    safe = re.sub(r"[^0-9A-Za-z가-힣_-]+", "_", str(prompt_key or "공통").strip()).strip("._")
    return googleblog_home() / "mato-blog-codex" / "prompts" / f"{safe or '공통'}.txt"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def init_prompt(prompt_key: str = "공통") -> dict[str, Any]:
    path = prompt_path(prompt_key)
    existed = path.is_file()
    if not existed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_COMMON_PROMPT.rstrip() + "\n", encoding="utf-8", newline="\n")
    text = path.read_text(encoding="utf-8-sig")
    return {
        "ok": True,
        "prompt_key": prompt_key or "공통",
        "path": str(path),
        "created": not existed,
        "sha256": _sha(text),
        "character_count": len(text),
    }


def status_prompt(prompt_key: str = "공통") -> dict[str, Any]:
    path = prompt_path(prompt_key)
    text = path.read_text(encoding="utf-8-sig") if path.is_file() else ""
    return {
        "ok": True,
        "prompt_key": prompt_key or "공통",
        "path": str(path),
        "exists": path.is_file(),
        "sha256": _sha(text) if text else "",
        "character_count": len(text),
    }


def export_prompt(
    output: str | Path,
    prompt_key: str = "공통",
    *,
    overlay: str | Path | None = None,
) -> dict[str, Any]:
    """Export the managed prompt with an optional run-scoped overlay.

    The overlay is read-only input.  It is appended to the exported staging
    file and never written back to the user's persistent ``공통.txt``.
    """

    managed = init_prompt(prompt_key)
    body = Path(str(managed["path"])).read_text(encoding="utf-8-sig").rstrip()
    today = dt.date.today()
    context = (
        f"# 현재 날짜\n오늘: {today:%Y-%m-%d}\n\n"
        "날짜가 자료에 없으면 날짜를 꾸며내지 않습니다.\n\n"
    )
    destination = Path(output).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    effective = context + body + "\n"
    overlay_path = ""
    if overlay:
        source = Path(overlay).expanduser().resolve()
        if not source.is_file() or source.is_symlink():
            raise ValueError(f"prompt overlay is missing or unsafe: {source}")
        overlay_body = source.read_text(encoding="utf-8-sig").strip()
        if not overlay_body:
            raise ValueError("prompt overlay must not be empty")
        effective += "\n# 작업별 오버레이\n" + overlay_body + "\n"
        overlay_path = str(source)
    destination.write_text(effective, encoding="utf-8", newline="\n")
    return {
        "ok": True,
        "prompt_key": prompt_key or "공통",
        "managed_prompt": managed["path"],
        "output": str(destination),
        "overlay": overlay_path,
        "sha256": _sha(effective),
        "character_count": len(effective),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage Mato Blog Codex local prompts.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "status"):
        command = commands.add_parser(name)
        command.add_argument("--prompt-key", default="공통")
    export = commands.add_parser("export")
    export.add_argument("--prompt-key", default="공통")
    export.add_argument("--output", required=True)
    export.add_argument("--overlay")
    args = parser.parse_args()
    if args.command == "init":
        result = init_prompt(args.prompt_key)
    elif args.command == "status":
        result = status_prompt(args.prompt_key)
    else:
        result = export_prompt(args.output, args.prompt_key, overlay=args.overlay)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
