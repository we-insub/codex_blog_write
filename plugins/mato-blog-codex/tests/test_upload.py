from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch

from _support import (
    IsolatedMatoEnvironment,
    SCRIPTS_DIR,
    create_run,
    generated_payload,
    write_json,
)

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import history
import profiles
import upload
import validate_posts
import write_posts


class _FakePlaywrightManager:
    def __enter__(self):
        return SimpleNamespace(chromium=object())

    def __exit__(self, exc_type, exc, traceback):  # type: ignore[no-untyped-def]
        return False


class _BodyOnlyLocator:
    def __init__(self, selector: str, body_text: str) -> None:
        self.selector = selector
        self.body_text = body_text

    def count(self) -> int:
        return 1 if self.selector == "body" else 0

    def inner_text(self, timeout: int = 0) -> str:
        return self.body_text if self.selector == "body" else ""


class _BodyOnlyPage:
    url = "https://blog.naver.com/owner1?Redirect=Write&"

    def __init__(self, body_text: str) -> None:
        self.body_text = body_text

    def locator(self, selector: str) -> _BodyOnlyLocator:
        return _BodyOnlyLocator(selector, self.body_text)

    def wait_for_timeout(self, _milliseconds: int) -> None:
        return None


def _fake_playwright_modules() -> dict[str, types.ModuleType]:
    root = types.ModuleType("playwright")
    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.sync_playwright = lambda: _FakePlaywrightManager()  # type: ignore[attr-defined]
    root.sync_api = sync_api  # type: ignore[attr-defined]
    return {"playwright": root, "playwright.sync_api": sync_api}


class UploadPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = IsolatedMatoEnvironment()
        self.env = self.environment.__enter__()

    def tearDown(self) -> None:
        self.environment.__exit__(None, None, None)

    def _ready_run(self, name: str = "upload-run", count: int = 3) -> Path:
        run_dir = self.env.root / name
        create_run(run_dir, versions=count)
        generated = self.env.root / f"{name}.json"
        write_json(generated, generated_payload(count))
        write_posts.write_posts(run_dir, generated)
        validation = validate_posts.validate_run(run_dir, count)
        self.assertTrue(validation["ok"], validation["errors"])
        return run_dir

    def _add_profiles(self, *slots: int) -> None:
        for slot in slots:
            profiles.add_profile(slot, alias=f"계정{slot}", blog_url=f"owner{slot}")

    def test_editor_lines_keep_mato_actions_until_smart_editor_typing(self) -> None:
        lines = upload._editor_lines({"body": "ㅂㅂㅂ소제목\n본문\n[image_1.jpg]"})
        self.assertEqual(lines, ["ㅂㅂㅂ소제목", "본문", "[image_1.jpg]"])

    def test_mato_template_title_and_table_markers_match_helper_contract(self) -> None:
        self.assertEqual(upload.NAVER_TEMPLATE_NAME, "제목을입력해주세요1:")
        self.assertEqual(upload.BODY_TYPING_DELAY_MS, 20)
        self.assertEqual(
            upload.NaverTextUploader._clean_title(
                "제목을입력해주세요1: 사십 자보다 짧은 제목"
            ),
            "사십 자보다 짧은 제목",
        )
        self.assertIsNotNone(upload.TABLE_START_RE.match("표 5 x 2 시작"))
        self.assertIsNotNone(upload.TABLE_CELL_RE.match("(4,1) 배송 조건 확인"))
        self.assertIsNotNone(upload.TABLE_END_RE.match("표 5 x 2 끝"))

    def test_editor_sections_preserve_helper_region_order(self) -> None:
        parsed = {
            "body1_lines": ["본문1 문장"],
            "intro_lines": ["인트로 문장"],
            "body2_lines": ["ㅂㅂㅂ소제목", "본문 문장"],
        }
        self.assertEqual(
            upload._editor_sections(parsed),
            [
                ("본문1:", ["본문1 문장"]),
                ("인트로1:", ["인트로 문장"]),
                ("본문2:", ["ㅂㅂㅂ소제목", "본문 문장"]),
            ],
        )

    def test_product_runs_use_two_second_link_card_wait(self) -> None:
        self.assertEqual(
            upload._link_card_wait_ms(
                {"request": {"source_type": "myrealtrip_product"}}
            ),
            2_000,
        )
        self.assertEqual(upload._link_card_wait_ms({"request": {}}), 4_000)
        with self.assertRaisesRegex(ValueError, "exactly 2000"):
            upload._link_card_wait_ms(
                {
                    "request": {
                        "source_type": "myrealtrip_product",
                        "link_wait_ms": 1_250,
                    }
                }
            )

    def test_legacy_write_urls_are_normalized_like_helper(self) -> None:
        self.assertEqual(
            upload._normalize_naver_write_url(
                "https://blog.naver.com/PostWriteForm.naver?categoryNo=7&blogId=owner1"
            ),
            "https://blog.naver.com/owner1?Redirect=Write&categoryNo=7",
        )
        self.assertEqual(
            upload._normalize_naver_write_url(
                "https://blog.naver.com/owner1/postwrite?categoryNo=7"
            ),
            "https://blog.naver.com/owner1?Redirect=Write&categoryNo=7",
        )

    def test_image_resolution_matches_helper_filename_fallbacks(self) -> None:
        image_dir = self.env.root / "images"
        image_dir.mkdir()
        bracketed = image_dir / "[IMAGE_1.JPG]"
        bracketed.write_bytes(b"image")
        resolved = upload._resolve_image_path(image_dir, "image_1.jpg")
        self.assertIsNotNone(resolved)
        self.assertTrue(resolved.is_file())
        self.assertTrue(resolved.samefile(bracketed))
        self.assertIsNotNone(upload.IMAGE_TAG_RE.fullmatch("[image_2.webp]"))

    def test_process_lines_uses_toolbar_actions_url_paste_images_and_tables(self) -> None:
        page = MagicMock()
        uploader = upload.NaverTextUploader(page)
        frame = MagicMock()
        placeholder = MagicMock()
        image_path = self.env.root / "image_1.jpg"
        image_path.write_bytes(b"image")
        lines = [
            "!!",
            "ㅂㅂㅂ제목형 소제목",
            "표 1 x 1 시작",
            "(0,0) 셀 내용",
            "표 1 x 1 끝",
            "[image_1.jpg]",
            "https://example.com/link",
            "일반 본문",
        ]
        with patch.object(uploader, "_exact_text", return_value=placeholder), patch.object(
            uploader, "_clear_region_placeholder"
        ) as clear_placeholder, patch.object(
            uploader, "_apply_toolbar_token", return_value=True
        ) as toolbar_action, patch.object(
            uploader, "_insert_table"
        ) as insert_table, patch.object(
            uploader, "_upload_image"
        ) as upload_image, patch.object(
            upload, "_copy_to_clipboard", return_value=True
        ):
            first_text = uploader._process_lines(
                frame, lines, upload.NAVER_BODY_PLACEHOLDER, self.env.root
            )

        clear_placeholder.assert_called_once_with(placeholder)
        self.assertEqual(
            [call.args[1:3] for call in toolbar_action.call_args_list],
            [("!!", ""), ("ㅂㅂㅂ", "제목형 소제목")],
        )
        insert_table.assert_called_once_with(frame, 1, 1, {(0, 0): "셀 내용"})
        upload_image.assert_called_once_with(image_path)
        paste_key = "Meta+V" if upload.platform.system() == "Darwin" else "Control+V"
        page.keyboard.press.assert_any_call(paste_key)
        page.keyboard.type.assert_called_once_with(
            "일반 본문", delay=upload.BODY_TYPING_DELAY_MS
        )
        self.assertEqual(first_text, "제목형 소제목")

    def test_image_upload_waits_for_one_loaded_editor_component(self) -> None:
        page = MagicMock()
        uploader = upload.NaverTextUploader(page)
        image_path = self.env.root / "image_1.jpg"
        image_path.write_bytes(b"image")
        button = MagicMock()
        frame = MagicMock()
        components = MagicMock()
        components.count.side_effect = [0, 1, 1]
        frame.locator.return_value = components
        latest = components.nth.return_value
        error_locator = MagicMock()
        error_locator.count.return_value = 0
        busy_locator = MagicMock()
        busy_locator.count.return_value = 0
        image_locator = MagicMock()
        image_locator.count.return_value = 1
        image_locator.last.evaluate.return_value = {
            "src": "https://blogthumb.pstatic.net/editor/image.jpg",
            "complete": True,
            "width": 1200,
            "height": 800,
        }
        latest.locator.side_effect = lambda selector: (
            image_locator
            if selector == "img"
            else busy_locator
            if selector == upload.EDITOR_IMAGE_BUSY_SELECTOR
            else error_locator
        )
        chooser = page.expect_file_chooser.return_value.__enter__.return_value
        with patch.object(upload, "_find_visible", return_value=button), patch.object(
            uploader, "_editor_frame", return_value=frame
        ):
            uploader._upload_image(image_path)

        chooser.value.set_files.assert_called_once_with(str(image_path))
        self.assertEqual(components.nth.call_count, 2)
        self.assertEqual(image_locator.last.evaluate.call_count, 2)

    def test_image_upload_waits_through_blob_preview_for_stable_remote_image(self) -> None:
        page = MagicMock()
        uploader = upload.NaverTextUploader(page)
        image_path = self.env.root / "image_1.jpg"
        image_path.write_bytes(b"image")
        frame = MagicMock()
        components = MagicMock()
        components.count.side_effect = [0, 1, 1, 1, 1]
        frame.locator.return_value = components
        latest = components.nth.return_value
        error_locator = MagicMock()
        error_locator.count.return_value = 0
        busy_locator = MagicMock()
        busy_locator.count.side_effect = [1, 0, 0, 0]
        image_locator = MagicMock()
        image_locator.count.return_value = 1
        remote_state = {
            "src": "https://blogthumb.pstatic.net/editor/final.jpg",
            "complete": True,
            "width": 1200,
            "height": 800,
        }
        image_locator.last.evaluate.side_effect = [
            {
                "src": "blob:https://blog.naver.com/preview-id",
                "complete": True,
                "width": 1200,
                "height": 800,
            },
            remote_state,
            dict(remote_state),
        ]
        latest.locator.side_effect = lambda selector: (
            image_locator
            if selector == "img"
            else busy_locator
            if selector == upload.EDITOR_IMAGE_BUSY_SELECTOR
            else error_locator
        )

        with patch.object(upload, "_find_visible", return_value=MagicMock()), patch.object(
            uploader, "_editor_frame", return_value=frame
        ):
            uploader._upload_image(image_path)

        self.assertEqual(image_locator.last.evaluate.call_count, 3)
        self.assertEqual(busy_locator.count.call_count, 4)
        self.assertGreaterEqual(page.wait_for_timeout.call_count, 3)

    def test_image_upload_blob_preview_followed_by_late_error_is_fatal(self) -> None:
        page = MagicMock()
        uploader = upload.NaverTextUploader(page)
        image_path = self.env.root / "image_1.jpg"
        image_path.write_bytes(b"image")
        frame = MagicMock()
        components = MagicMock()
        components.count.side_effect = [0, 1, 1]
        frame.locator.return_value = components
        latest = components.nth.return_value
        error_locator = MagicMock()
        error_locator.count.side_effect = [0, 1]
        busy_locator = MagicMock()
        busy_locator.count.return_value = 0
        image_locator = MagicMock()
        image_locator.count.return_value = 1
        image_locator.last.evaluate.return_value = {
            "src": "blob:https://blog.naver.com/preview-id",
            "complete": True,
            "width": 1200,
            "height": 800,
        }
        latest.locator.side_effect = lambda selector: (
            image_locator
            if selector == "img"
            else busy_locator
            if selector == upload.EDITOR_IMAGE_BUSY_SELECTOR
            else error_locator
        )

        with patch.object(upload, "_find_visible", return_value=MagicMock()), patch.object(
            uploader, "_editor_frame", return_value=frame
        ):
            with self.assertRaises(upload.UploadError) as raised:
                uploader._upload_image(image_path)

        self.assertEqual(raised.exception.code, "image_upload_failed")
        self.assertEqual(image_locator.last.evaluate.call_count, 1)

    def test_image_upload_timeout_is_fatal_before_the_next_editor_action(self) -> None:
        page = MagicMock()
        uploader = upload.NaverTextUploader(page)
        image_path = self.env.root / "image_1.jpg"
        image_path.write_bytes(b"image")
        frame = MagicMock()
        components = MagicMock()
        components.count.return_value = 0
        frame.locator.return_value = components
        with patch.object(upload, "_find_visible", return_value=MagicMock()), patch.object(
            uploader, "_editor_frame", return_value=frame
        ), patch.object(upload.time, "monotonic", side_effect=[0.0, 61.0]):
            with self.assertRaises(upload.UploadError) as raised:
                uploader._upload_image(image_path)
        self.assertEqual(raised.exception.code, "image_upload_failed")

    def test_image_preflight_rejects_missing_empty_and_non_image_assets(self) -> None:
        for name, content in (("empty.jpg", b""), ("not-image.jpg", b"plain text")):
            with self.subTest(name=name):
                path = self.env.root / name
                path.write_bytes(content)
                with self.assertRaises(upload.UploadError) as raised:
                    upload._preflight_image_assets([f"[{name}]"], self.env.root)
                self.assertEqual(raised.exception.code, "image_upload_failed")
        with self.assertRaises(upload.UploadError) as raised:
            upload._preflight_image_assets(["[missing.jpg]"], self.env.root)
        self.assertEqual(raised.exception.code, "image_upload_failed")

    def test_image_group_creates_one_text_block_after_last_image_only(self) -> None:
        page = MagicMock()
        uploader = upload.NaverTextUploader(page)
        frame = MagicMock()
        placeholder = MagicMock()
        for index in (1, 2):
            (self.env.root / f"image_{index}.jpg").write_bytes(b"\xff\xd8\xffimage")
        with patch.object(uploader, "_exact_text", return_value=placeholder), patch.object(
            uploader, "_clear_region_placeholder"
        ), patch.object(uploader, "_upload_image"), patch.object(
            uploader, "_create_text_block_after_image"
        ) as create_text_block:
            uploader._process_lines(
                frame,
                ["[image_1.jpg]", "[image_2.jpg]", "사진 뒤 본문 문장입니다."],
                upload.NAVER_BODY_PLACEHOLDER,
                self.env.root,
            )
        create_text_block.assert_called_once_with()

    def test_process_lines_waits_two_seconds_after_each_product_url(self) -> None:
        page = MagicMock()
        uploader = upload.NaverTextUploader(page, link_card_wait_ms=2_000)
        frame = MagicMock()
        placeholder = MagicMock()
        with patch.object(uploader, "_exact_text", return_value=placeholder), patch.object(
            uploader, "_clear_region_placeholder"
        ), patch.object(upload, "_copy_to_clipboard", return_value=True):
            uploader._process_lines(
                frame,
                [
                    "https://myrealt.rip/iZRp3d",
                    "본문",
                    "https://myrealt.rip/iZRp3d",
                ],
                upload.NAVER_BODY_PLACEHOLDER,
                self.env.root,
            )

        waits = [call.args[0] for call in page.wait_for_timeout.call_args_list]
        self.assertEqual(waits.count(2_000), 2)

    def test_write_falls_back_to_default_editor_when_template_is_missing(self) -> None:
        page = MagicMock()
        uploader = upload.NaverTextUploader(page)
        uploader.open_editor_fields = [MagicMock(), MagicMock(), False]
        parsed = {
            "body1_lines": ["본문1"],
            "intro_lines": ["인트로"],
            "body2_lines": ["본문2"],
        }
        with patch.object(uploader, "_replace_line"), patch.object(
            uploader, "_exact_text", return_value=None
        ), patch.object(
            uploader, "_process_lines", return_value="본문1"
        ) as process_lines, patch.object(
            upload, "_verify_input", return_value=True
        ), patch.object(
            uploader, "_editor_contains", return_value=True
        ):
            uploader.write("제목", parsed, self.env.root)

        process_lines.assert_called_once_with(
            uploader.open_editor_fields[0],
            ["본문1", "인트로", "본문2"],
            upload.NAVER_FALLBACK_PLACEHOLDER,
            self.env.root,
        )

    def test_missing_template_enters_fallback_mode(self) -> None:
        uploader = upload.NaverTextUploader(MagicMock())
        with patch.object(upload, "_find_visible", return_value=None):
            self.assertFalse(uploader._apply_mato_template(MagicMock()))

    def test_draft_save_retries_three_times_before_uncertain(self) -> None:
        page = MagicMock()
        uploader = upload.NaverTextUploader(page)
        button = MagicMock()
        find_results = [button, None, button, None, button, None]
        with patch.object(upload, "_scopes", return_value=[page]), patch.object(
            upload, "_find_visible", side_effect=find_results
        ), patch.object(upload, "_wait_for_visible_feedback", return_value=False):
            with self.assertRaises(upload.UploadError) as raised:
                uploader.save_draft()

        self.assertEqual(raised.exception.code, "save_unverified")
        self.assertEqual(button.click.call_count, 3)

    def test_open_profile_context_reuses_live_connector(self) -> None:
        browser = object()
        context = object()
        profile = {"slot": 1, "profile_path": "C:/safe/naver_1"}
        with patch.object(
            upload,
            "connect_profile_context",
            return_value=(browser, context),
        ), patch.object(
            upload,
            "open_profile_browser",
        ), patch.object(
            upload,
            "_launch_context",
            side_effect=AssertionError("must reuse open connector"),
        ):
            opened, owns_context, retained_browser = upload._open_profile_context(
                object(), profile, headless=False
            )

        self.assertIs(opened, context)
        self.assertFalse(owns_context)
        self.assertIs(retained_browser, browser)

    def test_open_profile_context_starts_retained_host_instead_of_owned_context(self) -> None:
        browser = object()
        context = object()
        profile = {"slot": 2, "profile_path": "C:/safe/naver_2"}
        with patch.object(
            upload,
            "open_profile_browser",
            return_value={"status": "opened", "connector_ready": True},
        ) as open_browser, patch.object(
            upload,
            "connect_profile_context",
            return_value=(browser, context),
        ), patch.object(
            upload,
            "_launch_context",
            side_effect=AssertionError("visible uploads must use the retained host"),
        ):
            opened, owns_context, retained_browser = upload._open_profile_context(
                object(), profile, headless=False
            )

        open_browser.assert_called_once_with(profile)
        self.assertIs(opened, context)
        self.assertFalse(owns_context)
        self.assertIs(retained_browser, browser)

    def test_open_profile_context_keeps_explicit_headless_compatibility(self) -> None:
        context = object()
        profile = {"slot": 3, "profile_path": "C:/safe/naver_3"}
        with patch.object(
            upload,
            "open_profile_browser",
            side_effect=AssertionError("headless mode must not open a visible host"),
        ), patch.object(
            upload,
            "connect_profile_context",
            return_value=None,
        ), patch.object(
            upload,
            "_launch_context",
            return_value=context,
        ) as launch:
            opened, owns_context, retained_browser = upload._open_profile_context(
                object(), profile, headless=True
            )

        launch.assert_called_once_with(ANY, "C:/safe/naver_3", headless=True)
        self.assertIs(opened, context)
        self.assertTrue(owns_context)
        self.assertIsNone(retained_browser)

    def test_unfinished_draft_prompt_is_cancelled_before_new_editor_entry(self) -> None:
        page = MagicMock()
        prompt = MagicMock()
        cancel = MagicMock()
        with patch.object(upload, "_find_visible", side_effect=[prompt, cancel, None]) as find_visible:
            dismissed = upload._dismiss_unfinished_draft_prompt(page)
        self.assertTrue(dismissed)
        cancel.click.assert_called_once()
        page.wait_for_timeout.assert_called_once_with(1_000)
        initial_selectors = find_visible.call_args_list[0].args[1]
        verification_selectors = find_visible.call_args_list[2].args[1]
        current_popup = "[data-group='popupLayer'][data-name*='se-popup-alert-confirm']"
        self.assertIn(current_popup, initial_selectors)
        self.assertIn(current_popup, verification_selectors)

    def test_build_plan_contains_profile_table_data_and_round_robin_assignment(self) -> None:
        run_dir = self._ready_run(count=4)
        self._add_profiles(1, 2, 3)
        plan = upload.build_upload_plan(run_dir, "1,2,3", "draft")
        self.assertEqual(plan["mode"], "draft")
        self.assertEqual(plan["profiles"], [1, 2, 3])
        self.assertEqual([row["profile_slot"] for row in plan["assignments"]], [1, 2, 3, 1])
        self.assertEqual([row["account_alias"] for row in plan["assignments"]], ["계정1", "계정2", "계정3", "계정1"])
        self.assertTrue(all(row["blog_url"].startswith("https://blog.naver.com/") for row in plan["assignments"]))
        rendered = upload._render_plan(plan)
        self.assertIn("No browser action has been performed.", rendered)
        self.assertIn(f"`{plan['run_id']}`", rendered)

    def test_plan_requires_passed_validation_and_complete_profile_metadata(self) -> None:
        run_dir = self.env.root / "not-validated"
        create_run(run_dir, versions=1)
        self._add_profiles(1)
        with self.assertRaisesRegex(ValueError, "must pass"):
            upload.build_upload_plan(run_dir, "1", "draft")

        ready = self._ready_run("missing-blog", 1)
        profiles.add_profile(2, alias="URL 없음")
        with self.assertRaisesRegex(ValueError, "needs an alias/blog URL"):
            upload.build_upload_plan(ready, "2", "draft")

        profiles.add_profile(3, blog_url="owner3")
        with self.assertRaisesRegex(ValueError, "needs an alias/blog URL"):
            upload.build_upload_plan(ready, "3", "draft")

    def test_signature_is_stable_and_changes_with_mode_or_file_hash(self) -> None:
        run_dir = self._ready_run(count=1)
        self._add_profiles(1)
        first = upload.build_upload_plan(run_dir, "1", "draft")
        repeated = upload.build_upload_plan(run_dir, "1", "draft")
        published = upload.build_upload_plan(run_dir, "1", "publish")
        self.assertEqual(first["signature"], repeated["signature"])
        self.assertNotEqual(first["signature"], published["signature"])

        post_path = run_dir / first["assignments"][0]["file"]
        post_path.write_text(post_path.read_text(encoding="utf-8") + "\n추가 내용", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "changed after validation"):
            upload.build_upload_plan(run_dir, "1", "draft")
        self.assertTrue(validate_posts.validate_run(run_dir, 1)["ok"])
        changed = upload.build_upload_plan(run_dir, "1", "draft")
        self.assertNotEqual(first["signature"], changed["signature"])

        profiles.edit_profile(1, blog_url="owner2")
        retargeted = upload.build_upload_plan(run_dir, "1", "draft")
        self.assertNotEqual(changed["signature"], retargeted["signature"])

    def test_save_plan_persists_signature_and_confirmation_contract(self) -> None:
        run_dir = self._ready_run(count=1)
        self._add_profiles(1)
        plan = upload.build_upload_plan(run_dir, "1", "draft")
        upload.save_upload_plan(plan)
        state = history.load_run(run_dir)
        self.assertEqual(state["upload_plan"]["signature"], plan["signature"])
        with self.assertRaisesRegex(ValueError, "exactly match"):
            upload.execute_upload(plan, confirm="wrong-run-id")

        tampered = dict(plan)
        tampered["signature"] = "tampered"
        with self.assertRaisesRegex(ValueError, "plan changed"):
            upload.execute_upload(tampered, confirm=plan["run_id"])

    def test_editor_body_success_phrases_are_not_upload_evidence(self) -> None:
        page = _BodyOnlyPage("본문에 저장되었습니다 그리고 발행되었습니다 라고 적혀 있습니다.")
        self.assertFalse(
            upload._wait_for_visible_feedback(page, upload.DRAFT_SUCCESS_SELECTORS, 2)
        )
        self.assertFalse(
            upload._wait_for_visible_feedback(page, upload.PUBLISH_SUCCESS_SELECTORS, 2)
        )


class UploadResumeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = IsolatedMatoEnvironment()
        self.env = self.environment.__enter__()

    def tearDown(self) -> None:
        self.environment.__exit__(None, None, None)

    def _ready_plan(self, count: int = 2):  # type: ignore[no-untyped-def]
        run_dir = self.env.root / "resume-upload"
        create_run(run_dir, versions=count)
        source = self.env.root / "resume-generated.json"
        write_json(source, generated_payload(count))
        write_posts.write_posts(run_dir, source)
        self.assertTrue(validate_posts.validate_run(run_dir, count)["ok"])
        profile = profiles.add_profile(1, alias="업무", blog_url="owner1")
        plan = upload.build_upload_plan(run_dir, "1", "draft")
        upload.save_upload_plan(plan)
        return run_dir, profile, plan

    def test_entry_key_captures_file_hash_profile_and_mode(self) -> None:
        row = {"file": "posts/a.txt", "file_sha256": "abc", "profile_slot": 1}
        key = upload._entry_key(row, "draft")
        self.assertEqual(key, "posts/a.txt|abc|1|draft")
        self.assertNotEqual(key, upload._entry_key(row, "publish"))
        asset_row = {**row, "asset_manifest_sha256": "def"}
        self.assertEqual(
            upload._entry_key(asset_row, "draft"),
            "posts/a.txt|abc|def|1|draft",
        )

    def test_resume_skips_verified_success_and_uploads_only_pending_item(self) -> None:
        run_dir, profile, plan = self._ready_plan(2)
        first = dict(plan["assignments"][0])
        first_entry = {
            **first,
            "key": upload._entry_key(first, "draft"),
            "mode": "draft",
            "status": "success",
            "verified_url": "https://blog.naver.com/owner1/9999",
        }
        upload._save_upload_state(run_dir, "draft", [first_entry], "failed")

        context = SimpleNamespace(pages=[object()], close=MagicMock())
        connector_browser = object()
        uploader_instance = MagicMock()
        uploader_instance.upload.return_value = {
            "status": "success",
            "verified_url": "https://blog.naver.com/owner1/10000",
        }
        with patch.dict(sys.modules, _fake_playwright_modules()), patch.object(
            upload,
            "_open_profile_context",
            return_value=(context, False, connector_browser),
        ) as open_context, patch.object(
            upload, "get_profile", return_value=profile
        ), patch.object(
            upload, "NaverTextUploader", return_value=uploader_instance
        ):
            result = upload.execute_upload(plan, confirm=plan["run_id"], delay=2)

        open_context.assert_called_once()
        uploader_instance.upload.assert_called_once()
        uploaded_title = uploader_instance.upload.call_args.args[1]
        self.assertEqual(uploaded_title, plan["assignments"][1]["title"])
        context.close.assert_not_called()
        self.assertEqual(result["success_count"], 2)
        self.assertEqual([entry["status"] for entry in result["entries"]], ["success", "success"])
        state = history.load_run(run_dir)
        self.assertEqual(state["uploads"]["status"], "completed")
        self.assertEqual(state["uploads"]["success_count"], 2)

    def test_multiple_profiles_are_processed_in_order_and_left_open(self) -> None:
        run_dir, profile_one, _single_plan = self._ready_plan(2)
        profile_two = profiles.add_profile(2, alias="계정2", blog_url="owner2")
        plan = upload.build_upload_plan(run_dir, "1,2", "draft")
        upload.save_upload_plan(plan)

        context_one = SimpleNamespace(pages=[object()], close=MagicMock())
        context_two = SimpleNamespace(pages=[object()], close=MagicMock())
        uploader_one = MagicMock()
        uploader_two = MagicMock()
        uploader_one.upload.return_value = {"status": "success", "verified_url": None}
        uploader_two.upload.return_value = {"status": "success", "verified_url": None}
        profile_by_slot = {1: profile_one, 2: profile_two}

        with patch.dict(sys.modules, _fake_playwright_modules()), patch.object(
            upload,
            "_open_profile_context",
            side_effect=[
                (context_one, False, object()),
                (context_two, False, object()),
            ],
        ) as open_context, patch.object(
            upload,
            "get_profile",
            side_effect=lambda slot: profile_by_slot[int(slot)],
        ), patch.object(
            upload,
            "NaverTextUploader",
            side_effect=[uploader_one, uploader_two],
        ):
            result = upload.execute_upload(plan, confirm=plan["run_id"], delay=2)

        self.assertEqual(
            [int(call.args[1]["slot"]) for call in open_context.call_args_list],
            [1, 2],
        )
        uploader_one.upload.assert_called_once()
        uploader_two.upload.assert_called_once()
        context_one.close.assert_not_called()
        context_two.close.assert_not_called()
        self.assertEqual(result["success_count"], 2)

    def test_uncertain_previous_upload_blocks_retry_before_browser_launch(self) -> None:
        run_dir, _profile, plan = self._ready_plan(1)
        row = dict(plan["assignments"][0])
        uncertain = {
            **row,
            "key": upload._entry_key(row, "draft"),
            "mode": "draft",
            "status": "uncertain",
        }
        upload._save_upload_state(run_dir, "draft", [uncertain], "failed")
        with patch.dict(sys.modules, _fake_playwright_modules()), patch.object(
            upload, "_open_profile_context", side_effect=AssertionError("must not open")
        ) as open_context:
            with self.assertRaisesRegex(ValueError, "prior upload is unverified"):
                upload.execute_upload(plan, confirm=plan["run_id"])
        open_context.assert_not_called()

    def test_manual_not_uploaded_resolution_returns_uncertain_entry_to_pending(self) -> None:
        run_dir, _profile, plan = self._ready_plan(1)
        row = dict(plan["assignments"][0])
        entry_key = upload._entry_key(row, "draft")
        upload._save_upload_state(
            run_dir,
            "draft",
            [{**row, "key": entry_key, "mode": "draft", "status": "uncertain"}],
            "failed",
        )

        result = upload.resolve_uncertain_upload(
            run_dir,
            index=1,
            entry_key=entry_key,
            resolution="not-uploaded",
            confirm=plan["run_id"],
        )

        self.assertEqual(result["status"], "pending")
        state = history.load_run(run_dir)
        self.assertEqual(state["status"], "upload_planned")
        self.assertEqual(state["uploads"]["entries"][0]["manual_resolution"], "not-uploaded")
        self.assertEqual(state["events"][-1]["stage"], "upload_uncertain_resolved")

    def test_manual_publish_success_requires_direct_verified_url(self) -> None:
        run_dir, _profile, draft_plan = self._ready_plan(1)
        publish_plan = upload.build_upload_plan(run_dir, "1", "publish")
        upload.save_upload_plan(publish_plan)
        row = dict(publish_plan["assignments"][0])
        entry_key = upload._entry_key(row, "publish")
        upload._save_upload_state(
            run_dir,
            "publish",
            [{**row, "key": entry_key, "mode": "publish", "status": "uncertain"}],
            "failed",
        )
        with self.assertRaisesRegex(ValueError, "requires --verified-url"):
            upload.resolve_uncertain_upload(
                run_dir,
                index=1,
                entry_key=entry_key,
                resolution="success",
                confirm=draft_plan["run_id"],
            )

        result = upload.resolve_uncertain_upload(
            run_dir,
            index=1,
            entry_key=entry_key,
            resolution="success",
            confirm=draft_plan["run_id"],
            verified_url="https://blog.naver.com/owner1/12345",
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["run_status"], "completed")

    def test_switching_from_draft_to_publish_does_not_skip_prior_draft_success(self) -> None:
        run_dir, profile, draft_plan = self._ready_plan(1)
        draft_row = dict(draft_plan["assignments"][0])
        upload._save_upload_state(
            run_dir,
            "draft",
            [
                {
                    **draft_row,
                    "key": upload._entry_key(draft_row, "draft"),
                    "mode": "draft",
                    "status": "success",
                }
            ],
            "completed",
        )

        publish_plan = upload.build_upload_plan(run_dir, "1", "publish")
        upload.save_upload_plan(publish_plan)
        context = SimpleNamespace(pages=[object()], close=MagicMock())
        connector_browser = object()
        uploader_instance = MagicMock()
        uploader_instance.upload.return_value = {
            "status": "success",
            "verified_url": "https://blog.naver.com/owner1/10001",
        }
        with patch.dict(sys.modules, _fake_playwright_modules()), patch.object(
            upload,
            "_open_profile_context",
            return_value=(context, False, connector_browser),
        ), patch.object(
            upload, "get_profile", return_value=profile
        ), patch.object(
            upload, "NaverTextUploader", return_value=uploader_instance
        ):
            result = upload.execute_upload(
                publish_plan, confirm=publish_plan["run_id"], delay=2
            )

        uploader_instance.upload.assert_called_once()
        self.assertEqual(uploader_instance.upload.call_args.args[3], "publish")
        context.close.assert_not_called()
        self.assertEqual(result["success_count"], 1)
        self.assertEqual([entry["mode"] for entry in result["entries"]], ["publish"])

    def test_profile_target_change_after_plan_blocks_before_browser_launch(self) -> None:
        _run_dir, _profile, plan = self._ready_plan(1)
        profiles.edit_profile(1, blog_url="different-owner")
        with patch.dict(sys.modules, _fake_playwright_modules()), patch.object(
            upload, "_open_profile_context", side_effect=AssertionError("must not open")
        ) as open_context:
            with self.assertRaisesRegex(ValueError, "changed after confirmation"):
                upload.execute_upload(plan, confirm=plan["run_id"])
        open_context.assert_not_called()

    def test_browser_launch_failure_appends_upload_failed_event(self) -> None:
        run_dir, _profile, plan = self._ready_plan(1)
        with patch.dict(sys.modules, _fake_playwright_modules()), patch.object(
            upload,
            "_open_profile_context",
            side_effect=upload.UploadError("profile busy", code="profile_in_use"),
        ):
            with self.assertRaisesRegex(upload.UploadError, "profile busy"):
                upload.execute_upload(plan, confirm=plan["run_id"])

        state = history.load_run(run_dir)
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["events"][-1]["stage"], "upload_failed")
        self.assertEqual(state["events"][-1]["details"]["error_code"], "profile_in_use")


if __name__ == "__main__":
    unittest.main()
