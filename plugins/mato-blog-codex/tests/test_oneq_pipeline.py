from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

from _support import IsolatedMatoEnvironment, SCRIPTS_DIR, generated_payload, write_json

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import history
import oneq_pipeline
import upload


class OneQPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = IsolatedMatoEnvironment()
        self.env = self.environment.__enter__()

    def tearDown(self) -> None:
        self.environment.__exit__(None, None, None)

    def _fake_source(self, url: str, *, versions: int, command: str, **_kwargs):  # type: ignore[no-untyped-def]
        run_dir = self.env.root / "oneq-run"
        directory, _state = history.create_run("내 글", "integrated", versions, command or url, run_dir=run_dir)
        source = directory / "sources" / "items" / "01_owned"
        source.mkdir(parents=True, exist_ok=True)
        history.update_run(
            directory,
            {
                "status": "collected",
                "sources": {"owned_source_folder": "01_owned", "owned_source_url": url, "source_images": 0},
            },
        )
        return {"ok": True, "run_dir": str(directory), "source_folder": str(source)}

    def test_naver_only_skips_wordpress_and_blogspot_settings(self) -> None:
        with mock.patch.object(oneq_pipeline, "prepare_owned_web_post", side_effect=self._fake_source):
            prepared = oneq_pipeline.prepare_oneq_run(
                "https://example.test/my-post",
                targets="naver",
                naver_versions=2,
                profiles="1,2",
                mode="publish",
                command="내 글을 네이버 두 곳에 발행",
            )
        run_dir = Path(str(prepared["run_dir"]))
        payload_path = self.env.root / "platforms.json"
        write_json(payload_path, {"analysis": {"summary": "test"}, "naver_posts": generated_payload(2)["posts"]})
        rendered = oneq_pipeline.render_oneq_payload(run_dir, payload_path)
        self.assertEqual(rendered["rendered"]["naver"]["generated_count"], 2)

        finalized = oneq_pipeline.finalize_downstream(run_dir)
        self.assertEqual(finalized["platforms"], ["naver"])
        state = history.load_run(run_dir)
        self.assertEqual(state["oneq"]["targets"], ["naver"])
        self.assertEqual(state["oneq"]["stages"]["wordpress"], "skipped")
        self.assertEqual(state["oneq"]["stages"]["blogspot"], "skipped")
        for path in (run_dir / "posts").rglob("*_함축.txt"):
            self.assertIn("[[MATO_TEXT_LINK|", path.read_text(encoding="utf-8"))
        self.assertEqual(state["validation"]["status"], "passed")

    def test_text_link_marker_is_validated_before_editor_input(self) -> None:
        self.assertEqual(
            upload._parse_naver_text_link_line("[[MATO_TEXT_LINK|자세히%20보기|https%3A%2F%2Fexample.test%2Fpost]]"),
            ("자세히 보기", "https://example.test/post"),
        )
        self.assertIsNone(upload._parse_naver_text_link_line("[[MATO_TEXT_LINK|x|javascript%3Aalert(1)]]"))

    def test_wordpress_is_published_before_downstream_link_insertion(self) -> None:
        with mock.patch.object(oneq_pipeline, "prepare_owned_web_post", side_effect=self._fake_source):
            prepared = oneq_pipeline.prepare_oneq_run(
                "https://example.test/my-post",
                targets="wordpress,blogspot,naver",
                naver_versions=2,
                profiles="1,2",
                mode="publish",
                command="WordPress 먼저 발행",
            )
        run_dir = Path(str(prepared["run_dir"]))
        payload_path = self.env.root / "all-platforms.json"
        write_json(
            payload_path,
            {
                "analysis": {"summary": "test"},
                "wordpress": {"title": "워드프레스 제목", "html": "<p>독립 WordPress 본문</p>"},
                "blogspot": {"title": "블로그스팟 제목", "html": "<p>독립 Blogspot 본문</p>"},
                "naver_posts": generated_payload(2)["posts"],
            },
        )
        oneq_pipeline.render_oneq_payload(run_dir, payload_path)

        class FakeWordPressClient:
            def __init__(self, *_args, **_kwargs) -> None:
                self.created = []

            def upload_image(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
                raise AssertionError("the fixture has no images")

            def create_post(self, **kwargs):  # type: ignore[no-untyped-def]
                self.created.append(kwargs)
                return {"id": 77, "link": "https://example.test/wordpress-public"}

        with (
            mock.patch.object(oneq_pipeline, "_require_platform_config", return_value={"site_url": "https://example.test", "username": "writer", "categories": [], "tags": [], "default_status": "draft"}),
            mock.patch.object(oneq_pipeline, "get_secret", return_value="application-password"),
            mock.patch.object(oneq_pipeline, "WordPressClient", FakeWordPressClient),
        ):
            wordpress = oneq_pipeline.publish_wordpress(run_dir)
        self.assertEqual(wordpress["status"], "publish")
        self.assertEqual(wordpress["url"], "https://example.test/wordpress-public")

        finalized = oneq_pipeline.finalize_downstream(run_dir)
        self.assertEqual(finalized["wordpress_url"], wordpress["url"])
        blogspot_html = (run_dir / "platforms" / "02_blogspot" / "post.html").read_text(encoding="utf-8")
        self.assertIn('href="https://example.test/wordpress-public"', blogspot_html)
        for path in (run_dir / "posts").rglob("*_함축.txt"):
            self.assertIn("https%3A%2F%2Fexample.test%2Fwordpress-public", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
