"""Editable local prompt packs for the standalone OneQ platform workflow."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Sequence

try:
    from .integrations import integrations_home
    from .mato_common import atomic_write_text
except ImportError:
    from integrations import integrations_home  # type: ignore[no-redef]
    from mato_common import atomic_write_text  # type: ignore[no-redef]


DEFAULT_WORDPRESS_PROMPT = """# WordPress SEO/AEO 글쓰기 프롬프트

역할: WordPress 메인 도메인에 올릴 장기 자산형 글을 작성한다.
목표: 검색자가 바로 답을 얻고, 검색과 AI 답변 시스템이 핵심 정보를 쉽게 이해하도록 구조화한다.

출력 규칙:
- HTML 본문 조각만 작성한다. html, head, body 전체 문서 태그는 쓰지 않는다.
- 제목은 핵심 키워드와 검색 의도가 드러나게 작성한다.
- 첫 문단은 질문에 대한 직접 답변 2~3문장으로 시작한다.
- H2 단위로 나누고, 판단에 유용한 표와 FAQ를 필요한 위치에 넣는다.
- 출처 또는 관련 링크는 라벨을 반드시 `출처:`로 표시한다.
- 작성자 항목, 번역 안내문, 이미지 자리표시자, Naver 제목/본문 라벨은 만들지 않는다.
- 타인의 문장·후기·독특한 표현을 옮기지 말고, 확인 가능한 정보만 새 구조와 문장으로 작성한다.
- 허위 방문담을 쓰지 말고 이동 시간, 동선, 비용, 대상, 예약 전 확인사항 같은 결정 정보를 우선한다.
- 과장된 광고문과 번역투를 피하고, 자료를 검토한 전문 작성자의 담백한 설명체를 사용한다.
"""

DEFAULT_BLOGSPOT_PROMPT = """# Blogspot HTML 글쓰기 프롬프트

역할: WordPress 공개 글을 참고해 Blogspot 독자를 위한 별도의 HTML 글을 작성한다.

출력 규칙:
- HTML body에 바로 넣을 수 있는 본문 조각만 작성한다.
- 문단, H2 소제목, 필요한 표와 FAQ 구조를 사용한다.
- WordPress 원문을 문장 단위로 바꾸지 말고 제목, 도입, 소제목 순서, 설명 방식을 새롭게 구성한다.
- 사실은 확인 가능한 범위에서만 쓰고, 방문·구매·테스트를 하지 않았다면 체험한 것처럼 쓰지 않는다.
- 맨 아래에 WordPress 공개 글을 `출처:` 라벨 링크로 넣을 수 있는 자리를 남긴다.
- Naver 템플릿 라벨과 이미지 파일명 토큰은 출력하지 않는다.
"""

DEFAULT_NAVER_FROM_WORDPRESS_PROMPT = """# WordPress → Naver 다중 원고 프롬프트

역할: 공개된 WordPress 글의 사실을 바탕으로 각 Naver 프로필에 올릴 서로 다른 원고를 작성한다.

출력 규칙:
- 지정된 개수만큼 제목·도입·소제목 순서·설명 관점을 서로 다르게 만든다.
- Mato 형식: 제목을입력해주세요1:, 본문1:, 인트로1:, 본문2:, ㅂㅂㅂ소제목을 사용한다.
- 표는 `표 N x M 시작`, `(r,c) 값`, `표 N x M 끝` 형식만 사용한다.
- 문장·구성·목록을 WordPress나 다른 원고에서 재사용하지 않는다.
- WordPress URL과 링크 문구는 본문에 쓰지 않는다. 프로그램이 SmartEditor 텍스트 링크로 별도 삽입한다.
- 확인하지 않은 체험, 가격, 일정, 인용을 만들지 않는다.
"""

DEFAULTS = {
    "wordpress": DEFAULT_WORDPRESS_PROMPT,
    "blogspot": DEFAULT_BLOGSPOT_PROMPT,
    "naver-from-wordpress": DEFAULT_NAVER_FROM_WORDPRESS_PROMPT,
}


def _safe_key(value: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z가-힣_-]+", "_", str(value or "").strip()).strip("._-")
    if normalized not in DEFAULTS:
        raise ValueError(f"unsupported prompt key: {value}")
    return normalized


def prompt_path(key: str) -> Path:
    return integrations_home() / "prompts" / f"{_safe_key(key)}.txt"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def init_prompt(key: str) -> dict[str, Any]:
    safe_key = _safe_key(key)
    path = prompt_path(safe_key)
    created = not path.is_file()
    if created:
        atomic_write_text(path, DEFAULTS[safe_key].rstrip() + "\n")
    text = path.read_text(encoding="utf-8-sig")
    return {"ok": True, "key": safe_key, "path": str(path), "created": created, "sha256": _sha(text)}


def get_prompt(key: str) -> str:
    record = init_prompt(key)
    return Path(str(record["path"])).read_text(encoding="utf-8-sig").strip()


def status_prompt(key: str) -> dict[str, Any]:
    safe_key = _safe_key(key)
    path = prompt_path(safe_key)
    text = path.read_text(encoding="utf-8-sig") if path.is_file() else ""
    return {"ok": True, "key": safe_key, "path": str(path), "exists": path.is_file(), "sha256": _sha(text) if text else ""}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage local platform prompt packs.")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("init", "status"):
        item = commands.add_parser(command)
        item.add_argument("--key", choices=sorted(DEFAULTS), required=True)
    show = commands.add_parser("show")
    show.add_argument("--key", choices=sorted(DEFAULTS), required=True)
    args = parser.parse_args(argv)
    if args.command == "init":
        result: Any = init_prompt(args.key)
    elif args.command == "status":
        result = status_prompt(args.key)
    else:
        result = {"ok": True, "key": args.key, "prompt": get_prompt(args.key)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
