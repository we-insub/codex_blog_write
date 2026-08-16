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
from urllib.parse import urlparse


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
_WEB_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_PRODUCT_FIELD_RE = re.compile(
    r"(?:^|[;|])\s*(?:[-*•]\s*)?"
    r"(?P<label>"
    r"메인\s*키워드|main\s*keyword|"
    r"서브\s*키워드|sub\s*keywords?|"
    r"후킹\s*문구|후킹|hook|"
    r"동행자|동행|companions?|"
    r"경험(?:\s*(?:노트|메모))?|experience\s*notes?"
    r")\s*(?:(?:[:=：])|(?:은|는))?\s*",
    re.IGNORECASE,
)

_PRODUCT_FIELD_NAMES = {
    "메인키워드": "main_keyword",
    "mainkeyword": "main_keyword",
    "서브키워드": "subkeywords",
    "subkeyword": "subkeywords",
    "subkeywords": "subkeywords",
    "후킹": "hook",
    "후킹문구": "hook",
    "hook": "hook",
    "동행": "companions",
    "동행자": "companions",
    "companion": "companions",
    "companions": "companions",
    "경험": "experience_notes",
    "경험노트": "experience_notes",
    "경험메모": "experience_notes",
    "experiencenote": "experience_notes",
    "experiencenotes": "experience_notes",
}

_PRODUCT_IMAGE_MODE = "all_unique_seller_product_images"
_PRODUCT_MAX_IMAGES = 80


def _parse_profiles(command: str) -> list[int]:
    # A Naver post URL ends in a numeric log number.  Strip URLs before
    # recognizing profile slots so ``.../224360981794 + 프로필2`` can never
    # turn the post number into a profile number.
    command = _without_urls(str(command or ""))
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


def _trim_url(value: str) -> str:
    """Remove Markdown/sentence punctuation that cannot be part of a URL."""

    return value.rstrip(".,;:!?。,)]}〉》」』”’")


def _is_myrealtrip_product_url(value: str) -> bool:
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower().rstrip(".")
    path = parsed.path.rstrip("/")
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
    if host == "myrealt.rip":
        return bool(re.fullmatch(r"/[A-Za-z0-9_-]{4,64}", path) and not parsed.query)
    if host == "experiences.myrealtrip.com":
        return bool(re.fullmatch(r"/products/[^/]+", path, re.IGNORECASE))
    if host in {"myrealtrip.com", "www.myrealtrip.com"}:
        return bool(
            path == "/main/bridge/marketing"
            and re.search(r"(?:^|&)return_url=", parsed.query, re.IGNORECASE)
        )
    return False


def _is_naver_shopping_product_url(value: str) -> bool:
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower().rstrip(".")
    path = parsed.path.rstrip("/")
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
    if host == "naver.me":
        return bool(re.fullmatch(r"/[A-Za-z0-9_-]{4,64}", path) and not parsed.query)
    return bool(
        host == "brand.naver.com"
        and re.fullmatch(r"/[^/]+/products/[1-9][0-9]*", path)
    )


def extract_product_url(command: str) -> str:
    """Return the first MyRealTrip product/short URL, without Markdown syntax."""

    for match in _WEB_URL_RE.finditer(str(command or "")):
        candidate = _trim_url(match.group(0))
        if _is_myrealtrip_product_url(candidate):
            return candidate
    return ""


def extract_naver_shopping_product_url(command: str) -> str:
    """Return the first supported Naver Shopping short/product URL."""

    for match in _WEB_URL_RE.finditer(str(command or "")):
        candidate = _trim_url(match.group(0))
        if _is_naver_shopping_product_url(candidate):
            return candidate
    return ""


def _without_urls(value: str) -> str:
    return _WEB_URL_RE.sub(lambda match: " " * len(match.group(0)), value)


def _field_key(label: str) -> str:
    normalized = re.sub(r"\s+", "", label).lower()
    return _PRODUCT_FIELD_NAMES.get(normalized, "")


def _extract_product_fields(command: str) -> dict[str, list[str]]:
    """Parse labelled product inputs while leaving free-form prose untouched."""

    result: dict[str, list[str]] = {}
    for raw_line in str(command or "").splitlines():
        line = _without_urls(raw_line)
        matches = list(_PRODUCT_FIELD_RE.finditer(line))
        for index, match in enumerate(matches):
            key = _field_key(match.group("label"))
            if not key:
                continue
            end = matches[index + 1].start() if index + 1 < len(matches) else len(line)
            value = line[match.end() : end].strip(" \t,;|:=：-")
            if value:
                result.setdefault(key, []).append(value)
    return result


def _split_list_fields(values: Sequence[str], *, split_commas: bool = True) -> list[str]:
    pattern = r"\s*(?:,|/|\||\+)\s*" if split_commas else r"\s*(?:\||\+)\s*"
    result: list[str] = []
    for value in values:
        for item in re.split(pattern, value):
            cleaned = re.sub(r"\s+", " ", item).strip(" ,;|+-")
            if cleaned and cleaned not in result:
                result.append(cleaned)
    return result


def _parse_product_max_images(option_text: str) -> int:
    patterns = (
        r"(?:이미지|사진)\s*(?:은|는|을|를)?\s*(?:최대\s*)?(\d+)\s*(?:개|장)",
        r"최대\s*(\d+)\s*(?:개|장)\s*(?:의\s*)?(?:이미지|사진)",
        r"max_images\s*[:=]\s*(\d+)",
        r"max(?:imum)?\s*images?\s*[:=]?\s*(\d+)",
    )
    for pattern in patterns:
        match = re.search(pattern, option_text, re.IGNORECASE)
        if match:
            value = int(match.group(1))
            if not 1 <= value <= _PRODUCT_MAX_IMAGES:
                raise ValueError("상품 이미지는 1장 이상 80장까지만 허용됩니다.")
            return value
    return _PRODUCT_MAX_IMAGES


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


def _parse_product_request(
    text: str,
    product_url: str,
    *,
    source_type: str = "myrealtrip_product",
) -> dict[str, Any]:
    quoted_keyword, quoted_safe_options = _split_keyword_and_options(text)
    option_text = _without_urls(quoted_safe_options)
    fields = _extract_product_fields(text)

    main_values = fields.get("main_keyword", [])
    main_keyword = re.sub(r"\s+", " ", main_values[0]).strip() if main_values else ""
    if (
        not main_keyword
        and _QUOTED_TEXT_RE.search(text)
        and quoted_keyword
        and not extract_product_url(quoted_keyword)
    ):
        main_keyword = re.sub(r"\s+", " ", quoted_keyword).strip()

    subkeywords = _split_list_fields(fields.get("subkeywords", []))
    companions = _split_list_fields(fields.get("companions", []))
    experience_notes = _split_list_fields(
        fields.get("experience_notes", []), split_commas=False
    )
    hook_values = fields.get("hook", [])
    hook = re.sub(r"\s+", " ", hook_values[0]).strip() if hook_values else ""

    channel_text = "\n".join(
        line
        for line in option_text.splitlines()
        if not _PRODUCT_FIELD_RE.match(_without_urls(line))
    )
    compact = re.sub(r"\s+", "", channel_text).lower()
    upload_prohibited = bool(_UPLOAD_PROHIBITION_RE.search(option_text))
    explicit_publish = bool(
        re.search(r"(?:자동\s*|바로\s*)?발행", option_text, re.IGNORECASE)
    )
    mode = "publish" if not upload_prohibited and explicit_publish else "draft"
    version_text = re.sub(
        r"(?:이미지|사진)\s*(?:은|는|을|를)?\s*(?:최대\s*)?\d+\s*(?:개|장)",
        " ",
        option_text,
        flags=re.IGNORECASE,
    )
    version_text = re.sub(
        r"최대\s*\d+\s*(?:개|장)\s*(?:의\s*)?(?:이미지|사진)",
        " ",
        version_text,
        flags=re.IGNORECASE,
    )
    version_text = re.sub(
        r"(?:\d+(?:\s*[,/]\s*\d+)*\s*번?\s*프로필|"
        r"프로필\s*\d+(?:\s*[,/]\s*\d+)*)",
        " ",
        version_text,
        flags=re.IGNORECASE,
    )
    versions_match = re.search(r"(\d+)\s*(?:개|가지|버전)", version_text)
    versions = int(versions_match.group(1)) if versions_match else 1
    if versions <= 0:
        raise ValueError("versions must be positive")
    profiles = _parse_profiles(option_text)
    if re.search(r"(?:구글|google)", compact, re.IGNORECASE):
        raise ValueError("상품 작성 v1은 네이버 채널만 지원합니다.")
    channel = "naver"

    # Seller-image use is covered by the standing approval configured for this
    # workflow. Keep the field in each run for compatibility and audit data,
    # but do not derive it from one-off wording in the request.
    permission_confirmed = True
    image_mode = _PRODUCT_IMAGE_MODE
    if re.search(
        r"(?:이미지|사진)\s*(?:사용|첨부|처리)\s*"
        r"(?:안\s*해|하지\s*마|없이|없음)",
        option_text,
        re.IGNORECASE,
    ):
        image_mode = "none"

    return {
        "source_type": source_type,
        "channel": channel,
        "product_url": product_url,
        # Kept for older run helpers; the URL is deliberately never used here.
        "keyword": main_keyword,
        "main_keyword": main_keyword,
        "subkeywords": subkeywords,
        "hook": hook,
        "companions": companions,
        "experience_notes": experience_notes,
        "surface": "product",
        "versions": versions,
        "profiles": profiles,
        "mode": mode,
        "upload_requested": bool(profiles) and not upload_prohibited,
        "publish_confirmation_required": False,
        "publish_authorized": mode == "publish",
        "image_policy": {
            "mode": image_mode,
            "permission_confirmed": permission_confirmed,
            "max_images": _parse_product_max_images(option_text),
        },
        "link_wait_ms": 2_000,
        "original_command": text,
    }


def parse_request(command: str) -> dict[str, Any]:
    text = str(command or "").strip()
    if not text:
        raise ValueError("command is empty")
    product_url = extract_product_url(text)
    if product_url:
        return _parse_product_request(text, product_url)
    shopping_product_url = extract_naver_shopping_product_url(text)
    if shopping_product_url:
        return _parse_product_request(
            text,
            shopping_product_url,
            source_type="naver_shopping_product",
        )
    keyword, option_text = _split_keyword_and_options(text)
    compact = re.sub(r"\s+", "", option_text).lower()
    upload_prohibited = bool(_UPLOAD_PROHIBITION_RE.search(option_text))
    surface = "blog" if "블로그탭" in compact and "통합검색" not in compact else "integrated"
    explicit_publish = bool(re.search(r"(?:자동\s*|바로\s*)?발행", option_text, re.IGNORECASE))
    mode = "publish" if not upload_prohibited and explicit_publish else "draft"
    versions_match = re.search(r"(?<!프로필\s)(\d+)\s*(?:개|가지|버전)", option_text)
    requested_versions = int(versions_match.group(1)) if versions_match else 10
    profiles = _parse_profiles(option_text)
    # A profile list is an assignment request, not just an upload target.
    # Generate one independent post per requested profile so profile 1,2,3
    # always receives exactly three drafts, even if a different post count was
    # also written in the compact command.
    versions = len(profiles) if profiles else requested_versions
    if versions <= 0:
        raise ValueError("versions must be positive")
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
