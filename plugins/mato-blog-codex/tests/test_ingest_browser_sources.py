from __future__ import annotations

import json
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from _support import IsolatedMatoEnvironment, SCRIPTS_DIR, write_json

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import history
import collect
import ingest_browser_sources
import parse_request


class BrowserSourceIngestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = IsolatedMatoEnvironment()
        self.env = self.environment.__enter__()

    def tearDown(self) -> None:
        self.environment.__exit__(None, None, None)

    def test_ingest_preserves_visible_order_deduplicates_and_caps_at_five(self) -> None:
        source_rows = [
            {
                "title": "첫째",
                "url": "https://m.blog.naver.com/owner1/1001",
                "text": "첫 원문",
                "headings": ["A"],
                "notes": ["첫 글의 핵심을 재서술한 글감"],
                "image_count": 2,
            },
            {"title": "중복", "url": "https://blog.naver.com/owner1/1001", "text": "중복 원문"},
            {"title": "외부", "url": "https://example.com/owner/1002", "text": "외부 원문"},
        ]
        source_rows.extend(
            {"title": f"글 {index}", "url": f"https://blog.naver.com/owner{index}/{1000 + index}", "text": f"원문 {index}"}
            for index in range(2, 8)
        )
        input_path = self.env.root / "browser-sources.json"
        write_json(input_path, {"sources": source_rows})
        run_dir = self.env.root / "browser-run"

        result = ingest_browser_sources.ingest_browser_sources(
            input_path,
            keyword="서울 맛집",
            surface="블로그탭",
            versions=4,
            command="서울 맛집 4개",
            run_dir=run_dir,
        )

        self.assertEqual(result["source_count"], 5)
        metadata = json.loads((run_dir / "sources" / "sources.json").read_text(encoding="utf-8"))
        raw = json.loads((run_dir / "sources" / ".analysis-input.json").read_text(encoding="utf-8"))
        self.assertEqual([row["rank"] for row in metadata["sources"]], [1, 2, 3, 4, 5])
        self.assertEqual([row["title"] for row in metadata["sources"]], ["첫째", "글 2", "글 3", "글 4", "글 5"])
        self.assertEqual(metadata["sources"][0]["url"], "https://blog.naver.com/owner1/1001")
        self.assertTrue(all(row["collector"] == "codex-in-app-browser" for row in metadata["sources"]))
        self.assertTrue(all("text" not in row for row in metadata["sources"]))
        self.assertEqual([row["text"] for row in raw["sources"]], ["첫 원문", "원문 2", "원문 3", "원문 4", "원문 5"])
        first_note = run_dir / metadata["sources"][0]["note_file"]
        self.assertTrue(first_note.is_file())
        note_text = first_note.read_text(encoding="utf-8")
        self.assertIn("제목을입력해주세요1: 첫째", note_text)
        self.assertIn("[원문이미지_1]", note_text)
        self.assertIn("[원문이미지_2]", note_text)
        self.assertIn("첫 글의 핵심을 재서술한 글감", note_text)
        self.assertNotIn("첫 원문", note_text)

        state = history.load_run(run_dir)
        self.assertEqual(state["sources"]["collector"], "codex-in-app-browser")
        self.assertEqual(state["request"]["versions"], 4)
        self.assertEqual(state["request"]["surface"], "blog")
        self.assertEqual(state["events"][-1]["stage"], "browser_collection_completed")
        journal = (run_dir / "HISTORY.md").read_text(encoding="utf-8")
        self.assertNotIn("첫 원문", journal)

    def test_restricted_browser_text_is_rejected_before_run_creation(self) -> None:
        input_path = self.env.root / "blocked.json"
        write_json(
            input_path,
            {"sources": [{"title": "차단", "url": "https://blog.naver.com/owner/1000", "text": "자동입력 방지"}]},
        )
        run_dir = self.env.root / "blocked-run"
        with self.assertRaises(collect.AccessRestricted):
            ingest_browser_sources.ingest_browser_sources(
                input_path,
                keyword="키워드",
                surface="blog",
                versions=1,
                run_dir=run_dir,
            )
        self.assertFalse(run_dir.exists())

    def test_empty_or_unusable_input_is_rejected(self) -> None:
        for index, payload in enumerate(({}, {"sources": []}, {"sources": [{"url": "https://example.com", "text": "x"}]})):
            with self.subTest(payload=payload):
                input_path = self.env.root / f"invalid-{index}.json"
                write_json(input_path, payload)
                with self.assertRaises(ValueError):
                    ingest_browser_sources.ingest_browser_sources(
                        input_path,
                        keyword="키워드",
                        surface="blog",
                        versions=1,
                        run_dir=self.env.root / f"invalid-run-{index}",
                    )

    def test_prepare_records_browser_start_before_sources_are_ingested(self) -> None:
        run_dir = self.env.root / "prepared-browser-run"
        prepared = ingest_browser_sources.prepare_browser_run(
            keyword="서울 맛집",
            surface="blog",
            versions=2,
            command='"서울 맛집" 2개',
            run_dir=run_dir,
        )
        self.assertTrue(prepared["prepared"])
        before = history.load_run(run_dir)
        self.assertEqual(before["events"][-1]["stage"], "browser_collection_started")

        input_path = self.env.root / "prepared-sources.json"
        write_json(
            input_path,
            {
                "sources": [
                    {
                        "title": "첫 글",
                        "url": "https://blog.naver.com/owner1/1001",
                        "text": "공개 본문",
                    }
                ]
            },
        )
        ingest_browser_sources.ingest_browser_sources(
            input_path,
            keyword="서울 맛집",
            surface="blog",
            versions=2,
            run_dir=run_dir,
        )

        after = history.load_run(run_dir)
        stages = [event["stage"] for event in after["events"]]
        self.assertEqual(stages.count("browser_collection_started"), 1)
        self.assertLess(
            stages.index("browser_collection_started"),
            stages.index("browser_collection_completed"),
        )

    def test_prepared_request_mismatch_is_rejected(self) -> None:
        run_dir = self.env.root / "mismatched-browser-run"
        ingest_browser_sources.prepare_browser_run(
            keyword="서울 맛집", surface="blog", versions=1, run_dir=run_dir
        )
        input_path = self.env.root / "mismatched-sources.json"
        write_json(
            input_path,
            {
                "sources": [
                    {
                        "title": "첫 글",
                        "url": "https://blog.naver.com/owner1/1001",
                        "text": "공개 본문",
                    }
                ]
            },
        )
        with self.assertRaisesRegex(ValueError, "does not match"):
            ingest_browser_sources.ingest_browser_sources(
                input_path,
                keyword="다른 키워드",
                surface="blog",
                versions=1,
                run_dir=run_dir,
            )


class NaturalLanguageRequestTests(unittest.TestCase):
    def test_myrealtrip_url_is_separate_from_keyword_and_uses_product_defaults(self) -> None:
        result = parse_request.parse_request(
            "[상품](https://experiences.myrealtrip.com/products/3510284)"
        )
        self.assertEqual(result["source_type"], "myrealtrip_product")
        self.assertEqual(
            result["product_url"],
            "https://experiences.myrealtrip.com/products/3510284",
        )
        self.assertEqual(result["keyword"], "")
        self.assertEqual(result["main_keyword"], "")
        self.assertEqual(result["surface"], "product")
        self.assertEqual(result["channel"], "naver")
        self.assertEqual(result["versions"], 1)
        self.assertEqual(
            result["image_policy"],
            {
                "mode": "all_unique_seller_product_images",
                "permission_confirmed": True,
                "max_images": 80,
            },
        )
        self.assertEqual(result["link_wait_ms"], 2_000)

    def test_myrealtrip_request_parses_labelled_audience_and_image_contract(self) -> None:
        result = parse_request.parse_request(
            "https://myrealt.rip/iZRp3d\n"
            "메인키워드: 베트남 나트랑 투어\n"
            "서브키워드: 아이랑, 부모님이랑, 비용\n"
            "후킹 문구: 솔직후기\n"
            "동행자: 시부모 + 아이 둘\n"
            "경험노트: 이동이 편했고, 가족 모두 만족함\n"
            "업체 제공 사진 사용 가능 이미지 최대 24장\n"
            "프로필 2 임시저장"
        )
        self.assertEqual(result["keyword"], "베트남 나트랑 투어")
        self.assertNotIn("myrealt", result["keyword"])
        self.assertEqual(result["subkeywords"], ["아이랑", "부모님이랑", "비용"])
        self.assertEqual(result["hook"], "솔직후기")
        self.assertEqual(result["companions"], ["시부모", "아이 둘"])
        self.assertEqual(result["experience_notes"], ["이동이 편했고, 가족 모두 만족함"])
        self.assertEqual(result["profiles"], [2])
        self.assertTrue(result["upload_requested"])
        self.assertEqual(result["image_policy"]["max_images"], 24)
        self.assertTrue(result["image_policy"]["permission_confirmed"])

    def test_cpa_oneq_link_and_profile_requests_a_single_draft_upload(self) -> None:
        result = parse_request.parse_request(
            "CPA 링크 https://myrealt.rip/iZRp3d 프로필1 작업"
        )
        self.assertEqual(result["source_type"], "myrealtrip_product")
        self.assertTrue(result["cpa_oneq"])
        self.assertEqual(result["product_url"], "https://myrealt.rip/iZRp3d")
        self.assertEqual(result["profiles"], [1])
        self.assertEqual(result["versions"], 1)
        self.assertEqual(result["mode"], "draft")
        self.assertTrue(result["upload_requested"])
        self.assertTrue(result["image_policy"]["permission_confirmed"])

    def test_product_images_use_standing_approval(self) -> None:
        unconfirmed = parse_request.parse_request(
            'https://myrealt.rip/iZRp3d "나트랑 투어" 업체 이미지 모두 사용'
        )
        confirmed = parse_request.parse_request(
            'https://myrealt.rip/iZRp3d "나트랑 투어" 사용권한 있음'
        )
        self.assertTrue(unconfirmed["image_policy"]["permission_confirmed"])
        self.assertTrue(confirmed["image_policy"]["permission_confirmed"])

    def test_product_rejects_google_channel_and_out_of_range_image_count(self) -> None:
        with self.assertRaisesRegex(ValueError, "네이버 채널만"):
            parse_request.parse_request(
                'https://myrealt.rip/iZRp3d "나트랑 투어" 구글'
            )
        for count in (0, 81):
            with self.subTest(count=count):
                with self.assertRaisesRegex(ValueError, "80장"):
                    parse_request.parse_request(
                        f'https://myrealt.rip/iZRp3d "나트랑 투어" 이미지 {count}장'
                    )

    def test_parses_multiline_profile_count_upload_and_keyword(self) -> None:
        result = parse_request.parse_request(
            "프로필1 새원고 3개\n업로드 임시저장\n염창 맛집"
        )
        self.assertEqual(result["keyword"], "염창 맛집")
        self.assertEqual(result["surface"], "integrated")
        self.assertEqual(result["versions"], 1)
        self.assertEqual(result["profiles"], [1])
        self.assertEqual(result["mode"], "draft")
        self.assertTrue(result["upload_requested"])

    def test_named_profile_defaults_to_one_manuscript(self) -> None:
        result = parse_request.parse_request('프로필1 "서울맛집"')
        self.assertEqual(result["keyword"], "서울맛집")
        self.assertEqual(result["surface"], "integrated")
        self.assertEqual(result["versions"], 1)
        self.assertEqual(result["profiles"], [1])

    def test_no_profiles_defaults_to_ten(self) -> None:
        result = parse_request.parse_request('"서울맛집"')
        self.assertEqual(result["profiles"], [])
        self.assertEqual(result["versions"], 10)
        self.assertFalse(result["upload_requested"])

    def test_parses_mobile_friendly_compact_request(self) -> None:
        result = parse_request.parse_request(
            '"서울 맛집" 블로그탭 10개 프로필 1,2,3 임시저장'
        )
        self.assertEqual(result["keyword"], "서울 맛집")
        self.assertEqual(result["surface"], "blog")
        self.assertEqual(result["versions"], 3)
        self.assertEqual(result["profiles"], [1, 2, 3])
        self.assertEqual(result["mode"], "draft")
        self.assertTrue(result["upload_requested"])

    def test_publish_request_is_authorized_without_repeated_confirmation(self) -> None:
        result = parse_request.parse_request(
            '"제주 렌터카" 통합검색 3버전 2/4번 프로필 자동발행'
        )
        self.assertEqual(result["surface"], "integrated")
        self.assertEqual(result["profiles"], [2, 4])
        self.assertEqual(result["mode"], "publish")
        self.assertFalse(result["publish_confirmation_required"])
        self.assertTrue(result["publish_authorized"])

    def test_plain_publish_phrase_authorizes_public_publish(self) -> None:
        result = parse_request.parse_request(
            '"제주 갈치" 2개 프로필 1,2 발행'
        )
        self.assertEqual(result["keyword"], "제주 갈치")
        self.assertEqual(result["profiles"], [1, 2])
        self.assertEqual(result["mode"], "publish")
        self.assertTrue(result["publish_authorized"])
        self.assertFalse(result["publish_confirmation_required"])

    def test_quoted_keyword_is_exact_and_does_not_supply_options(self) -> None:
        result = parse_request.parse_request(
            '"  통합검색 자동발행 9개 7번프로필  " 블로그탭 2개 1번프로필 임시저장'
        )

        self.assertEqual(result["keyword"], "  통합검색 자동발행 9개 7번프로필  ")
        self.assertEqual(result["surface"], "blog")
        self.assertEqual(result["versions"], 1)
        self.assertEqual(result["profiles"], [1])
        self.assertEqual(result["mode"], "draft")
        self.assertTrue(result["upload_requested"])

    def test_explicit_upload_prohibition_overrides_profile_and_publish_words(self) -> None:
        commands = (
            '"서울 맛집" 1번프로필 업로드하지 마',
            '"서울 맛집" 1번프로필 저장하지 마',
            '"서울 맛집" 1번프로필 자동발행하지 마',
            '"서울 맛집" 1번프로필 바로발행하지 말아줘',
        )
        for command in commands:
            with self.subTest(command=command):
                result = parse_request.parse_request(command)
                self.assertEqual(result["profiles"], [1])
                self.assertEqual(result["mode"], "draft")
                self.assertFalse(result["upload_requested"])
                self.assertFalse(result["publish_confirmation_required"])
                self.assertFalse(result["publish_authorized"])

    def test_prohibition_words_inside_keyword_do_not_disable_upload(self) -> None:
        result = parse_request.parse_request('"업로드하지 마" 1번프로필')
        self.assertEqual(result["keyword"], "업로드하지 마")
        self.assertTrue(result["upload_requested"])

    def test_missing_keyword_is_rejected_instead_of_guessing(self) -> None:
        with self.assertRaises(ValueError):
            parse_request.parse_request("블로그탭 10개 프로필 1,2,3 임시저장")

    def test_reads_exact_request_from_utf8_command_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            command_file = Path(temp_dir) / "request.txt"
            command_file.write_text('"서울 맛집" 1번프로필, 블로그탭', encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = parse_request.main(["--command-file", str(command_file)])

        self.assertEqual(exit_code, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["keyword"], "서울 맛집")
        self.assertEqual(result["profiles"], [1])
        self.assertEqual(result["surface"], "blog")
        self.assertEqual(result["versions"], 1)
        self.assertEqual(result["mode"], "draft")


if __name__ == "__main__":
    unittest.main()
