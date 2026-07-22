from __future__ import annotations

import sys
import types
import unittest
from urllib.parse import parse_qs, quote, urlparse
from unittest.mock import patch

from _support import SCRIPTS_DIR

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import collect


class CollectUrlTests(unittest.TestCase):
    def test_surface_aliases_and_search_urls_are_distinct(self) -> None:
        self.assertEqual(collect.normalize_surface(" 블로그 탭 "), "blog")
        self.assertEqual(collect.normalize_surface("통합검색"), "integrated")
        with self.assertRaises(ValueError):
            collect.normalize_surface("뉴스")

        blog_url = urlparse(collect.build_search_url("서울 맛집", "blog"))
        integrated_url = urlparse(collect.build_search_url("서울 맛집", "integrated"))
        self.assertEqual(parse_qs(blog_url.query)["ssc"], ["tab.blog.all"])
        self.assertNotIn("where", parse_qs(blog_url.query))
        self.assertEqual(parse_qs(integrated_url.query)["where"], ["nexearch"])
        self.assertNotIn("ssc", parse_qs(integrated_url.query))
        self.assertEqual(parse_qs(blog_url.query)["query"], ["서울 맛집"])

    def test_normalizes_supported_post_shapes(self) -> None:
        expected = "https://blog.naver.com/my.blog-1/123456"
        values = (
            "https://blog.naver.com/my.blog-1/123456?from=search",
            "https://m.blog.naver.com/my.blog-1/123456",
            "https://blog.naver.com/PostView.naver?blogId=my.blog-1&logNo=123456",
        )
        for value in values:
            with self.subTest(value=value):
                self.assertEqual(collect.normalize_naver_post_url(value), expected)

        redirected = "https://search.naver.com/redirect?url=" + quote(values[2], safe="")
        self.assertEqual(collect.normalize_naver_post_url(redirected), expected)

    def test_rejects_non_post_and_untrusted_urls(self) -> None:
        rejected = (
            "https://example.com/user/123456",
            "https://blog.naver.com/myblog",
            "https://blog.naver.com/myblog/not-a-number",
            "javascript:alert(1)",
            "",
        )
        for value in rejected:
            with self.subTest(value=value):
                self.assertIsNone(collect.normalize_naver_post_url(value))

    def test_search_result_order_deduplication_and_five_item_cap(self) -> None:
        html = """
        <a href="https://blog.naver.com/first/1000"><span>첫 번째</span></a>
        <a href="https://m.blog.naver.com/first/1000">중복 제목</a>
        <a href="https://example.com/not-blog/2000">외부 링크</a>
        <a href="https://blog.naver.com/second/2000">두 번째</a>
        <a href="https://blog.naver.com/third/3000">세 번째</a>
        <a href="https://blog.naver.com/fourth/4000">네 번째</a>
        <a href="https://blog.naver.com/fifth/5000">다섯 번째</a>
        <a href="https://blog.naver.com/sixth/6000">여섯 번째</a>
        """
        results = collect.extract_search_results(html, limit=99)
        self.assertEqual([item["blog_id"] for item in results], ["first", "second", "third", "fourth", "fifth"])
        self.assertEqual([item["rank"] for item in results], [1, 2, 3, 4, 5])
        self.assertEqual(results[0]["title"], "첫 번째")


class CollectExtractionTests(unittest.TestCase):
    def test_extracts_title_headings_and_body_without_script(self) -> None:
        document = """
        <html><head><meta property="og:title" content="샘플 제목 : 네이버 블로그"></head>
        <body><div class="se-main-container">
          <h2>첫 소제목</h2><p>첫 문단입니다.</p>
          <script>비밀 스크립트 본문</script>
          <h3>둘째 소제목</h3><p>둘째 문단입니다.</p>
        </div></body></html>
        """
        parsed = collect.extract_post(document, fallback_title="대체 제목")
        self.assertEqual(parsed["title"], "샘플 제목")
        self.assertEqual(parsed["headings"], ["첫 소제목", "둘째 소제목"])
        self.assertIn("첫 문단입니다.", parsed["text"])
        self.assertNotIn("비밀 스크립트", parsed["text"])

    def test_uses_fallback_title_and_meta_description_when_body_missing(self) -> None:
        parsed = collect.extract_post(
            '<meta name="description" content="요약 설명">', fallback_title="검색 제목"
        )
        self.assertEqual(parsed["title"], "검색 제목")
        self.assertEqual(parsed["text"], "요약 설명")

    def test_detects_login_captcha_and_access_restrictions(self) -> None:
        for document, url in (
            ("정상 페이지", "https://nid.naver.com/nidlogin.login"),
            ("CAPTCHA verification", "https://blog.naver.com/a/1000"),
            ("자동입력 방지 안내", "https://blog.naver.com/a/1000"),
        ):
            with self.subTest(document=document, url=url):
                with self.assertRaises(collect.AccessRestricted):
                    collect.detect_restriction(document, url)
        collect.detect_restriction("정상 블로그 내용", "https://blog.naver.com/a/1000")

    def test_fetch_stops_immediately_when_search_page_is_restricted(self) -> None:
        class FakeResponse:
            url = "https://search.naver.com/search.naver"
            body = "자동입력 방지"

            def css(self, *args, **kwargs):  # type: ignore[no-untyped-def]
                return []

        class FakeSession:
            fetch_calls = 0

            def __init__(self, **kwargs):  # type: ignore[no-untyped-def]
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):  # type: ignore[no-untyped-def]
                return None

            def fetch(self, *args, **kwargs):  # type: ignore[no-untyped-def]
                type(self).fetch_calls += 1
                return FakeResponse()

        scrapling = types.ModuleType("scrapling")
        fetchers = types.ModuleType("scrapling.fetchers")
        fetchers.DynamicSession = FakeSession  # type: ignore[attr-defined]
        scrapling.fetchers = fetchers  # type: ignore[attr-defined]
        with patch.dict(sys.modules, {"scrapling": scrapling, "scrapling.fetchers": fetchers}):
            with self.assertRaises(collect.AccessRestricted):
                collect.fetch_naver_sources("서울 맛집", "blog", delay=1)
        self.assertEqual(FakeSession.fetch_calls, 1)


if __name__ == "__main__":
    unittest.main()
