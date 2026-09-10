from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import profile_runtime  # noqa: E402


class ProfileRuntimeTests(unittest.TestCase):
    def test_round_trip_contains_status_but_no_authentication_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            profile = Path(temporary) / "browser_profiles" / "naver_1"
            profile.mkdir(parents=True)

            profile_runtime.write_profile_state(profile, status="ready", pid=27)
            state = profile_runtime.read_profile_state(profile)

            self.assertIsNotNone(state)
            self.assertEqual(state["status"], "ready")
            self.assertEqual(state["pid"], 27)
            self.assertEqual(state["blog_id"], "")
            raw = profile_runtime.profile_runtime_path(profile).read_text(encoding="utf-8")
            self.assertNotIn("cookie", raw.casefold())
            self.assertNotIn("password", raw.casefold())

    def test_ready_state_identifies_its_public_blog_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            profile = Path(temporary) / "browser_profiles" / "naver_1"
            profile.mkdir(parents=True)
            profile_runtime.write_profile_state(profile, status="ready", pid=27, blog_id="owner_1")
            state = profile_runtime.read_profile_state(profile)
            self.assertEqual(state["blog_id"], "owner_1")

    def test_invalid_blog_id_is_rejected_before_writing(self) -> None:
        profile = Path("/local/browser_profiles/naver_1")
        for blog_id in ["https://blog.naver.com/owner1", " owner1 ", "owner1?token=fake", "한글", 27]:
            with self.subTest(blog_id=blog_id), patch.object(Path, "mkdir") as mkdir:
                with self.assertRaises(ValueError):
                    profile_runtime.write_profile_state(profile, status="ready", blog_id=blog_id)
                mkdir.assert_not_called()

    def test_invalid_stored_blog_id_invalidates_runtime_state(self) -> None:
        profile = Path("/local/browser_profiles/naver_1")
        valid = {"profile": "naver_1", "pid": 27, "status": "ready", "updated_at": time.time()}
        for blog_id in [None, 27, "https://blog.naver.com/owner1", "owner1?token=fake", " owner1 "]:
            with self.subTest(blog_id=blog_id), patch.object(
                Path, "read_text", return_value=json.dumps({**valid, "blog_id": blog_id})
            ):
                self.assertIsNone(profile_runtime.read_profile_state(profile))

    def test_stale_state_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            profile = Path(temporary) / "browser_profiles" / "naver_2"
            profile.mkdir(parents=True)
            profile_runtime.write_profile_state(profile, status="needs_login")

            self.assertIsNone(profile_runtime.read_profile_state(profile, max_age_seconds=-1))

    def test_logged_in_state_round_trips_without_claiming_editor_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            profile = Path(temporary) / "browser_profiles" / "naver_1"
            profile.mkdir(parents=True)
            profile_runtime.write_profile_state(profile, status="logged_in", pid=27)
            self.assertEqual(profile_runtime.read_profile_state(profile)["status"], "logged_in")

    def test_mismatched_profile_and_invalid_pid_are_rejected(self) -> None:
        profile = Path("/local/browser_profiles/naver_1")
        valid = {"profile": "naver_1", "pid": 27, "status": "ready", "updated_at": time.time()}
        invalid_rows = [
            {**valid, "profile": "naver_2"},
            {**valid, "pid": None},
            {**valid, "pid": True},
            {**valid, "pid": "27"},
            {**valid, "pid": 0},
            {**valid, "pid": -1},
            {**valid, "pid": 2_147_483_648},
        ]
        for row in invalid_rows:
            with self.subTest(row=row), patch.object(Path, "read_text", return_value=json.dumps(row)):
                self.assertIsNone(profile_runtime.read_profile_state(profile))

    def test_future_nonfinite_and_stale_timestamps_are_rejected(self) -> None:
        profile = Path("/local/browser_profiles/naver_1")
        now = time.time()
        for timestamp in [now + 1, now - 16, float("nan"), float("inf"), float("-inf")]:
            row = {"profile": "naver_1", "pid": 27, "status": "ready", "updated_at": timestamp}
            with self.subTest(timestamp=timestamp), patch.object(
                Path, "read_text", return_value=json.dumps(row)
            ), patch.object(profile_runtime.time, "time", return_value=now):
                self.assertIsNone(profile_runtime.read_profile_state(profile))

    def test_nonfinite_max_age_cannot_accept_a_runtime_state(self) -> None:
        profile = Path("/local/browser_profiles/naver_1")
        row = {"profile": "naver_1", "pid": 27, "status": "ready", "updated_at": time.time()}
        for max_age in [float("nan"), float("inf"), float("-inf")]:
            with self.subTest(max_age=max_age), patch.object(Path, "read_text", return_value=json.dumps(row)):
                self.assertIsNone(profile_runtime.read_profile_state(profile, max_age_seconds=max_age))

    def test_clear_removes_only_the_selected_profile_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "browser_profiles"
            first = root / "naver_1"
            second = root / "naver_2"
            first.mkdir(parents=True)
            second.mkdir(parents=True)
            profile_runtime.write_profile_state(first, status="ready")
            profile_runtime.write_profile_state(second, status="ready")

            profile_runtime.clear_profile_state(first)

            self.assertFalse(profile_runtime.profile_runtime_path(first).exists())
            self.assertTrue(profile_runtime.profile_runtime_path(second).exists())


if __name__ == "__main__":
    unittest.main()
