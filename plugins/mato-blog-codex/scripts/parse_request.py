"""Parse the compact Korean prompts used by the Mato Codex skill.

This is an internal helper. Normal users speak to Codex and never run it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Sequence


_QUOTED_TEXT_RE = re.compile(
    r'"(?P<double>[^\"]*)"'
    r"|'(?P<single>[^']*)'"
    r"|“(?P<curly_double>[^”]*)”"
    r"|‘(?P<curly_single>[^’]*)’"
)
_UPLOAD_PROHIBITION_RE = re.compile(
    r"(?:업로드|저장|발행)\s*(?:(?:은|는|을|를)\s*)?"
    r"(?:하지\s*마|하지\s*말|금지|안\s*(?:해|해주세요|할래))",
    re.IGNORECASE,
)


def _parse_profiles(command: str) -> list[int]:
    patterns = (
        r"(?P<slots>\d+(?:\s*[,/]\s*\d+)*)\s*번?\s*프로필",
        r"프로필\s*(?P<slots>\d+(?:\s*[,/]\s*\d+)*)",
    )
    for pattern in patterns:
        match = re.search(pattern, command, re.IGNORECASE)
        if not match:
            continue
        slots: list[int] = []
        for value in re.split(r"\s*[,/]\s*", match.group("slots")):
            slot = int(value)
            if slot > 0 and slot not in slots:
                slots.append(slot)
        return slots
    return []


def _extract_unquoted_keyword(command: str) -> str:
    remainder = command
    removals = (
        r"\d+(?:\s*[,/]\s*\d+)*\s*번?\s*프로필",
        r"프로필\s*\d+(?:\s*[,/]\s*\d+)*",
        r"\d+\s*(?:개|가지|버전)",
        r"블로그\s*탭|블로그탭|통합\s*검색|통합검색",
        r"(?:업로드|저장|발행)\s*(?:(?:은|는|을|를)\s*)?"
        r"(?:하지\s*마(?:세요)?|하지\s*말\S*|금지|안\s*(?:해|해주세요|할래))",
        r"자동\s*발행|바로\s*발행|발행|임시\s*저장|임시저장|초안",
        r"새\s*원고|업로드",
        r"(?:글|원고)\s*(?:작성|생성|만들기|만들어줘|써줘)",
    )
    for pattern in removals:
        remainder = re.sub(pattern, " ", remainder, flags=re.IGNORECASE)
    remainder = re.sub(r"[,/|]+", " ", remainder)
    return re.sub(r"\s+", " ", remainder).strip()


def _split_keyword_and_options(command: str) -> tuple[str, str]:
    """Return the exact first quoted keyword and text safe for option parsing.

    Every quoted segment is blanked from the option text.  This prevents words
    such as ``통합검색`` or ``자동발행`` and numbers such as ``10개`` that are
    legitimately part of a search keyword from changing request options.
    """

    matches = list(_QUOTED_TEXT_RE.finditer(command))
    if not matches:
        return _extract_unquoted_keyword(command), command

    first = matches[0]
    keyword = next(
        value
        for name in ("double", "single", "curly_double", "curly_single")
        if (value := first.group(name)) is not None
    )
    option_text = _QUOTED_TEXT_RE.sub(lambda match: " " * len(match.group(0)), command)
    return keyword, option_text


def _extract_keyword(command: str) -> str:
    """Compatibility wrapper returning the parsed keyword only."""

    keyword, _ = _split_keyword_and_options(command)
    return keyword


def parse_request(command: str) -> dict[str, Any]:
    text = str(command or "").strip()
    if not text:
        raise ValueError("command is empty")
    keyword, option_text = _split_keyword_and_options(text)
    compact = re.sub(r"\s+", "", option_text).lower()
    upload_prohibited = bool(_UPLOAD_PROHIBITION_RE.search(option_text))
    surface = "blog" if "블로그탭" in compact and "통합검색" not in compact else "integrated"
    explicit_publish = bool(re.search(r"(?:자동\s*|바로\s*)?발행", option_text, re.IGNORECASE))
    mode = "publish" if not upload_prohibited and explicit_publish else "draft"
    versions_match = re.search(r"(?<!프로필\s)(\d+)\s*(?:개|가지|버전)", option_text)
    versions = int(versions_match.group(1)) if versions_match else 10
    if versions <= 0:
        raise ValueError("versions must be positive")
    profiles = _parse_profiles(option_text)
    if not keyword.strip():
        raise ValueError("검색 키워드를 따옴표로 감싸서 입력하세요.")
    return {
        "keyword": keyword,
        "surface": surface,
        "versions": versions,
        "profiles": profiles,
        "mode": mode,
        "upload_requested": bool(profiles) and not upload_prohibited,
        # An explicit publish phrase is the user's action-time authorization.
        # The uploader still checks its saved run ID and signature internally;
        # Codex does not ask the user to repeat that ID.
        "publish_confirmation_required": False,
        "publish_authorized": mode == "publish",
        "original_command": text,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Internal Mato natural-language request parser.")
    parser.add_argument("command", nargs="?", help="compact Korean request")
    parser.add_argument("--command", dest="command_option", help="compact Korean request")
    parser.add_argument("--command-file", help="UTF-8 file containing the exact user request")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        command = args.command_option or args.command or ""
        if args.command_file:
            command = Path(args.command_file).expanduser().read_text(encoding="utf-8-sig")
        print(json.dumps(parse_request(command), ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
