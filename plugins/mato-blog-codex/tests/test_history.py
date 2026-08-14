from __future__ import annotations

import json
import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from _support import IsolatedMatoEnvironment, SCRIPTS_DIR

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import history
import mato_common


class HistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = IsolatedMatoEnvironment()
        self.env = self.environment.__enter__()

    def tearDown(self) -> None:
        self.environment.__exit__(None, None, None)

    def test_default_run_is_created_under_desktop_kst_date(self) -> None:
        fixed = datetime(2026, 7, 22, 14, 5, 6, tzinfo=mato_common.KST)
        with TemporaryDirectory() as temporary:
            desktop = Path(temporary) / "Desktop"
            with (
                mock.patch.dict(
                    os.environ,
                    {"MATO_RUNS_ROOT": "", "MATO_DESKTOP_ROOT": str(desktop)},
                    clear=False,
                ),
                mock.patch.object(mato_common, "now_kst", return_value=fixed),
                mock.patch.object(history, "now_kst", return_value=fixed),
            ):
                directory, _state = history.create_run(
                    "서울맛집", "blog", 1, '프로필1 "서울맛집"'
                )
            self.assertEqual(directory.parent, desktop / "2026-07-22")
            self.assertEqual(directory.name, "20260722_140506_서울맛집")
            self.assertTrue((directory / "run.json").is_file())

    def test_runs_root_override_takes_priority_over_desktop_detection(self) -> None:
        with TemporaryDirectory() as temporary:
            override = Path(temporary) / "explicit-runs"
            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "MATO_RUNS_ROOT": str(override),
                        "MATO_DESKTOP_ROOT": str(Path(temporary) / "ignored-desktop"),
                    },
                    clear=False,
                ),
                mock.patch.object(
                    mato_common,
                    "_windows_desktop_path",
                    side_effect=AssertionError("Desktop detection must not run for an override"),
                ),
            ):
                self.assertEqual(mato_common.default_runs_root(), override)

    def test_windows_known_folder_desktop_is_used_before_home_fallback(self) -> None:
        fixed = datetime(2026, 7, 22, 14, 5, 6, tzinfo=mato_common.KST)
        with TemporaryDirectory() as temporary:
            known_desktop = Path(temporary) / "OneDrive" / "Desktop"
            with (
                mock.patch.dict(
                    os.environ,
                    {"MATO_RUNS_ROOT": "", "MATO_DESKTOP_ROOT": ""},
                    clear=False,
                ),
                mock.patch.object(
                    mato_common,
                    "_windows_desktop_path",
                    return_value=known_desktop,
                ),
                mock.patch.object(mato_common, "now_kst", return_value=fixed),
            ):
                self.assertEqual(
                    mato_common.default_runs_root(),
                    known_desktop / "2026-07-22",
                )

    def test_desktop_detection_failure_falls_back_to_home_desktop(self) -> None:
        fixed = datetime(2026, 7, 22, 14, 5, 6, tzinfo=mato_common.KST)
        with TemporaryDirectory() as temporary:
            home = Path(temporary) / "home"
            with (
                mock.patch.dict(
                    os.environ,
                    {"MATO_RUNS_ROOT": "", "MATO_DESKTOP_ROOT": ""},
                    clear=False,
                ),
                mock.patch.object(mato_common, "_windows_desktop_path", return_value=None),
                mock.patch.object(mato_common.Path, "home", return_value=home),
                mock.patch.object(mato_common, "now_kst", return_value=fixed),
            ):
                self.assertEqual(
                    mato_common.default_runs_root(),
                    home / "Desktop" / "2026-07-22",
                )

    @unittest.skipUnless(os.name == "nt", "Windows Known Folder API test")
    def test_windows_known_folder_helper_returns_an_absolute_path(self) -> None:
        desktop = mato_common._windows_desktop_path()
        self.assertIsNotNone(desktop)
        assert desktop is not None
        self.assertTrue(desktop.is_absolute())

    def test_free_form_and_structured_secrets_are_redacted(self) -> None:
        command = (
            "작업 password=hunter2 token=abc123 cookie: session-value "
            "Bearer abc.def.ghi sk-abcdefghijklmnop"
        )
        redacted = history.redact_sensitive(command)
        for secret in ("hunter2", "abc123", "session-value", "abc.def.ghi", "sk-abcdefghijklmnop"):
            self.assertNotIn(secret, redacted)
        self.assertIn("[민감정보 삭제]", redacted)

        structured = history.redact_sensitive(
            {
                "password": "secret-password",
                "api_key": "secret-api-key",
                "body": "경쟁 글 전체 원문",
                "nested": {"html": "<p>raw</p>", "safe": "유지"},
            }
        )
        serialized = json.dumps(structured, ensure_ascii=False)
        self.assertNotIn("secret-password", serialized)
        self.assertNotIn("secret-api-key", serialized)
        self.assertNotIn("경쟁 글 전체 원문", serialized)
        self.assertNotIn("<p>raw</p>", serialized)
        self.assertEqual(structured["nested"]["safe"], "유지")

    def test_composite_korean_and_basic_authorization_secrets_are_redacted(self) -> None:
        command = (
            "client_secret=client-value id_token=id-value auth_token=auth-value "
            "pw=pw-value 비밀번호=한글암호 Authorization: Basic dXNlcjpwYXNz"
        )
        redacted = history.redact_sensitive(command)
        for secret in (
            "client-value",
            "id-value",
            "auth-value",
            "pw-value",
            "한글암호",
            "dXNlcjpwYXNz",
        ):
            self.assertNotIn(secret, redacted)
        self.assertIn("Authorization: Basic [민감정보 삭제]", redacted)

        structured = history.redact_sensitive(
            {
                "oauth_client_secret_backup": "one",
                "openid_id_token": "two",
                "naver_auth_token": "three",
                "account_pw": "four",
                "비밀번호": "five",
                "oauthClientSecret": "six",
                "safe_label": "keep",
            }
        )
        serialized = json.dumps(structured, ensure_ascii=False)
        for secret in ("one", "two", "three", "four", "five", "six"):
            self.assertNotIn(f'"{secret}"', serialized)
        self.assertEqual(structured["safe_label"], "keep")

    def test_create_run_uses_kst_local_paths_and_writes_both_histories(self) -> None:
        run_dir = self.env.root / "explicit-run"
        directory, state = history.create_run(
            "서울 맛집", "blog", 3, "서울 맛집 token=do-not-store", run_dir=run_dir
        )
        self.assertEqual(directory, run_dir)
        self.assertTrue((run_dir / "run.json").is_file())
        self.assertTrue((run_dir / "HISTORY.md").is_file())
        self.assertEqual(mato_common.global_history_path(), self.env.googleblog_home / "mato-blog-codex" / "HISTORY.md")
        self.assertTrue(mato_common.global_history_path().is_file())
        timestamp = datetime.fromisoformat(str(state["started_at"]))
        self.assertEqual(timestamp.utcoffset().total_seconds(), 9 * 60 * 60)
        self.assertEqual(state["request"]["image_mode"], "none")
        contents = (run_dir / "HISTORY.md").read_text(encoding="utf-8")
        self.assertIn("created", contents)
        self.assertNotIn("do-not-store", contents)

    def test_product_run_preserves_optional_request_fields_without_url_keyword(self) -> None:
        run_dir = self.env.root / "product-run"
        request_fields = {
            "source_type": "myrealtrip_product",
            "channel": "naver",
            "product_url": "https://myrealt.rip/iZRp3d",
            "main_keyword": "",
            "subkeywords": ["아이랑", "부모님이랑"],
            "hook": "솔직후기",
            "companions": ["시부모", "아이 둘"],
            "experience_notes": ["이동 동선이 편했음"],
            "image_policy": {
                "mode": "all_unique_seller_product_images",
                "permission_confirmed": True,
                "max_images": 80,
            },
        }
        _directory, state = history.create_run(
            "https://myrealt.rip/iZRp3d",
            "product",
            1,
            "상품 글 작성",
            run_dir=run_dir,
            request_fields=request_fields,
        )
        request = state["request"]
        self.assertEqual(request["source_type"], "myrealtrip_product")
        self.assertEqual(request["channel"], "naver")
        self.assertEqual(request["keyword"], "")
        self.assertEqual(request["main_keyword"], "")
        self.assertEqual(request["product_url"], "https://myrealt.rip/iZRp3d")
        self.assertEqual(request["subkeywords"], ["아이랑", "부모님이랑"])
        self.assertEqual(request["companions"], ["시부모", "아이 둘"])
        self.assertEqual(request["experience_notes"], ["이동 동선이 편했음"])
        self.assertEqual(request["image_policy"]["max_images"], 80)
        self.assertEqual(request["link_wait_ms"], 2_000)
        self.assertEqual(state["input"], request)

    def test_product_run_defaults_permission_to_false_and_validates_v1_contract(self) -> None:
        base = {
            "source_type": "myrealtrip_product",
            "channel": "naver",
            "product_url": "https://experiences.myrealtrip.com/products/3510284",
            "main_keyword": "나트랑 투어",
        }
        _directory, state = history.create_run(
            "나트랑 투어",
            "integrated",
            1,
            "상품 글",
            run_dir=self.env.root / "product-defaults",
            request_fields=base,
        )
        self.assertEqual(state["request"]["surface"], "product")
        self.assertFalse(state["request"]["image_policy"]["permission_confirmed"])
        self.assertEqual(state["request"]["image_policy"]["max_images"], 80)

        for field_updates in (
            {"channel": "google"},
            {"product_url": "https://example.com/products/3510284"},
            {"image_policy": {"max_images": 0}},
            {"image_policy": {"max_images": 81}},
        ):
            with self.subTest(field_updates=field_updates):
                invalid = dict(base)
                invalid.update(field_updates)
                with self.assertRaises(ValueError):
                    history.create_run(
                        "나트랑 투어",
                        "product",
                        1,
                        "상품 글",
                        run_dir=self.env.root / f"invalid-{len(field_updates)}-{field_updates!s}",
                        request_fields=invalid,
                    )

    def test_append_event_is_append_only_and_sanitizes_run_updates(self) -> None:
        run_dir = self.env.root / "append-run"
        history.create_run("키워드", "integrated", 1, "첫 명령", run_dir=run_dir)
        first_text = (run_dir / "HISTORY.md").read_text(encoding="utf-8")
        event = history.append_event(
            run_dir,
            "analysis_completed",
            status="generated",
            message="분석 token=hidden",
            command="두 번째 password=hidden-password",
            details={"count": 5, "article_text": "경쟁 원문"},
            run_updates={"safe_field": "보존", "source_body": "원문 본문"},
        )
        second_text = (run_dir / "HISTORY.md").read_text(encoding="utf-8")
        self.assertTrue(second_text.startswith(first_text))
        self.assertEqual(event["sequence"], 2)
        state = history.load_run(run_dir)
        self.assertEqual(len(state["events"]), 2)
        self.assertEqual(state["safe_field"], "보존")
        serialized = json.dumps(state, ensure_ascii=False)
        for secret in ("hidden-password", "hidden", "경쟁 원문", "원문 본문"):
            self.assertNotIn(secret, serialized)

    def test_reopening_explicit_run_adds_resume_event_without_overwrite(self) -> None:
        run_dir = self.env.root / "resume-run"
        history.create_run("키워드", "blog", 2, "처음", run_dir=run_dir)
        _, resumed = history.create_run("다른 키워드", "blog", 9, "재개", run_dir=run_dir)
        self.assertEqual([event["stage"] for event in resumed["events"]], ["created", "resumed"])
        self.assertEqual(resumed["request"]["keyword"], "키워드")
        self.assertEqual(resumed["request"]["versions"], 2)

    def test_terminal_event_records_completion_and_elapsed_time(self) -> None:
        run_dir = self.env.root / "complete-run"
        history.create_run("키워드", "blog", 1, "명령", run_dir=run_dir)
        history.append_event(run_dir, "finished", status="completed")
        state = history.load_run(run_dir)
        self.assertIn("completed_at", state)
        self.assertGreaterEqual(state["elapsed_seconds"], 0)
        self.assertIn("elapsed_seconds", state["events"][-1]["details"])


if __name__ == "__main__":
    unittest.main()
