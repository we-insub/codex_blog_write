from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path


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
            raw = profile_runtime.profile_runtime_path(profile).read_text(encoding="utf-8")
            self.assertNotIn("cookie", raw.casefold())
            self.assertNotIn("password", raw.casefold())

    def test_stale_state_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            profile = Path(temporary) / "browser_profiles" / "naver_2"
            profile.mkdir(parents=True)
            profile_runtime.write_profile_state(profile, status="needs_login")

            self.assertIsNone(profile_runtime.read_profile_state(profile, max_age_seconds=-1))

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
