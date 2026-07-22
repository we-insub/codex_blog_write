"""Shared fixtures for the dependency-free Mato Blog Codex test suite."""

from __future__ import annotations

import json
import os
import sys
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


TESTS_DIR = Path(__file__).resolve().parent
PLUGIN_DIR = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


class IsolatedMatoEnvironment:
    """Put all profile, history, and run data below one temporary directory."""

    def __init__(self) -> None:
        self._stack = ExitStack()
        self._temporary = TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.googleblog_home = self.root / "googleblog-home"
        self.runs_root = self.root / "runs"

    def __enter__(self) -> "IsolatedMatoEnvironment":
        self._stack.enter_context(
            patch.dict(
                os.environ,
                {
                    "MATO_GOOGLEBLOG_HOME": str(self.googleblog_home),
                    "MATO_RUNS_ROOT": str(self.runs_root),
                },
                clear=False,
            )
        )
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:  # type: ignore[no-untyped-def]
        self._stack.close()
        self._temporary.cleanup()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def generated_payload(count: int) -> dict[str, object]:
    """Return deliberately distinct posts with three Mato headings each."""

    vocabularies = (
        ("시장", "골목", "메뉴", "예약"),
        ("공원", "산책", "교통", "계절"),
        ("박물관", "전시", "관람", "휴관"),
        ("도서관", "자료", "열람", "좌석"),
        ("공연장", "무대", "예매", "좌석"),
    )
    posts: list[dict[str, object]] = []
    for index in range(count):
        words = vocabularies[index % len(vocabularies)]
        unique = f"버전{index + 1}-{words[0]}"
        posts.append(
            {
                "title": f"{words[0]} 안내 {index + 1}",
                "intro": [f"{unique} 정보를 한눈에 정리합니다."],
                "sections": [
                    {
                        "heading": f"{words[1]} 핵심",
                        "paragraphs": [f"{unique} {words[1]} 동선을 차분히 살펴봅니다. " + words[1] * 20],
                    },
                    {
                        "heading": f"{words[2]} 기준",
                        "paragraphs": [f"{unique} {words[2]} 선택 기준을 구체적으로 확인합니다. " + words[2] * 20],
                    },
                    {
                        "heading": f"{words[3]} 주의점",
                        "paragraphs": [f"{unique} {words[3]} 전에 확인할 내용을 정리합니다. " + words[3] * 20],
                    },
                ],
            }
        )
    return {"analysis": {"summary": "공통 검색 의도만 기록"}, "posts": posts}


def create_run(directory: Path, *, versions: int = 1) -> dict[str, object]:
    import history

    _, state = history.create_run(
        "서울 맛집",
        "blog",
        versions,
        "서울 맛집 / 블로그탭",
        run_dir=directory,
    )
    return state
