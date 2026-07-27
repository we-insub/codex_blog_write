from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from _support import IsolatedMatoEnvironment, SCRIPTS_DIR

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import mato_common
import profiles
import profile_connector


class ProfileCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = IsolatedMatoEnvironment()
        self.env = self.environment.__enter__()

    def tearDown(self) -> None:
        self.environment.__exit__(None, None, None)

    def test_discovers_only_numbered_profile_directories_in_numeric_order(self) -> None:
        root = mato_common.browser_profiles_dir()
        for name in ("naver_10", "naver_2", "naver_1", "naver_zero", "other_3"):
            (root / name).mkdir(parents=True, exist_ok=True)
        catalog = profiles.load_catalog()
        self.assertEqual([item["slot"] for item in catalog["profiles"]], [1, 2, 10])
        self.assertTrue(all(item["source"] == "discovered" for item in catalog["profiles"]))
        persisted = json.loads(mato_common.profile_catalog_path().read_text(encoding="utf-8"))
        self.assertEqual([item["slot"] for item in persisted["profiles"]], [1, 2, 10])

    def test_ignores_browser_folders_not_created_by_the_plugin(self) -> None:
        legacy = mato_common.googleblog_home() / "naver_browser"
        legacy.mkdir(parents=True, exist_ok=True)

        catalog = profiles.load_catalog()

        self.assertEqual(catalog["profiles"], [])

    def test_add_and_edit_profile_store_only_derived_local_metadata(self) -> None:
        added = profiles.add_profile(3, alias="업무용", blog_url="https://m.blog.naver.com/my.blog")
        self.assertEqual(added["slot"], 3)
        self.assertEqual(added["blog_url"], "https://blog.naver.com/my.blog")
        self.assertEqual(added["write_url"], "https://blog.naver.com/my.blog?Redirect=Write&")
        self.assertTrue(Path(added["profile_path"]).is_dir())

        edited = profiles.edit_profile(3, alias="수정 계정", blog_url="second-id")
        self.assertEqual(edited["account_alias"], "수정 계정")
        self.assertEqual(edited["blog_url"], "https://blog.naver.com/second-id")
        catalog_text = mato_common.profile_catalog_path().read_text(encoding="utf-8")
        self.assertNotIn("password", catalog_text.casefold())
        self.assertNotIn("cookie", catalog_text.casefold())

    def test_discovered_slot_can_be_enriched_without_replace(self) -> None:
        path = mato_common.browser_profiles_dir() / "naver_4"
        path.mkdir(parents=True)
        profiles.load_catalog()
        enriched = profiles.add_profile(4, alias="기존 계정", blog_url="owner4")
        self.assertEqual(enriched["account_alias"], "기존 계정")
        self.assertEqual(enriched["source"], "created")

    def test_replace_keeps_the_plugin_managed_profile_path(self) -> None:
        profiles.add_profile(1, alias="기존", blog_url="owner1")

        replaced = profiles.add_profile(1, alias="새 프로필", blog_url="owner2", replace=True)

        self.assertEqual(Path(replaced["profile_path"]), profiles.profile_path_for_slot(1))
        self.assertEqual(replaced["source"], "created")

    def test_macos_chrome_candidate_is_supported(self) -> None:
        candidates = profile_connector.chrome_executable_candidates("Darwin")
        self.assertIn(Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"), candidates)

    def test_macos_profile_launch_uses_the_app_launcher(self) -> None:
        profile = {"slot": 1, "profile_path": str(self.env.root / "naver_1"), "blog_url": "https://blog.naver.com/owner1"}
        Path(profile["profile_path"]).mkdir()
        with patch.object(profile_connector, "find_chrome_executable"), patch.object(
            profile_connector, "connector_endpoint", side_effect=(None, "http://127.0.0.1:9222")
        ), patch.object(profile_connector.platform, "system", return_value="Darwin"), patch.object(
            profile_connector.subprocess, "Popen"
        ) as popen:
            popen.return_value.pid = 1234
            result = profile_connector.open_profile_browser(profile)

        command = popen.call_args.args[0]
        self.assertEqual(command[:4], ["/usr/bin/open", "-na", "Google Chrome", "--args"])
        self.assertIn("--remote-debugging-port=0", command)
        self.assertEqual(result["status"], "opened")

    def test_macos_profile_launch_recovers_a_dead_instance_lock(self) -> None:
        profile = {"slot": 1, "profile_path": str(self.env.root / "naver_1")}
        profile_path = Path(profile["profile_path"])
        profile_path.mkdir()
        os.symlink("local-machine-999999", profile_path / "SingletonLock")
        with patch.object(profile_connector, "find_chrome_executable"), patch.object(
            profile_connector, "connector_endpoint", side_effect=(None, "http://127.0.0.1:9222")
        ), patch.object(profile_connector.platform, "system", return_value="Darwin"), patch.object(
            profile_connector.os, "kill", side_effect=ProcessLookupError
        ), patch.object(profile_connector.subprocess, "Popen") as popen:
            popen.return_value.pid = 1234
            result = profile_connector.open_profile_browser(profile)

        self.assertEqual(result["status"], "opened")
        self.assertFalse(os.path.lexists(profile_path / "SingletonLock"))

    def test_round_robin_preserves_requested_profile_order_and_deduplicates_slots(self) -> None:
        for slot in (1, 2, 3):
            profiles.add_profile(slot, alias=f"계정{slot}", blog_url=f"owner{slot}")
        assignments = profiles.round_robin_assign(
            ["원고1", "원고2", "원고3", "원고4", "원고5"], "3,1,3,2"
        )
        self.assertEqual([row["profile_slot"] for row in assignments], [3, 1, 2, 3, 1])
        self.assertEqual([row["item"] for row in assignments], ["원고1", "원고2", "원고3", "원고4", "원고5"])
        with self.assertRaises(ValueError):
            profiles.round_robin_assign(["원고"], "99")

    def test_check_without_login_never_calls_browser_boundary(self) -> None:
        profiles.add_profile(1, alias="계정", blog_url="owner1")
        with patch.object(
            profiles,
            "_interactive_login_check",
            side_effect=AssertionError("browser must not be used"),
        ) as browser_check:
            result = profiles.check_profile(1, login=False)
        browser_check.assert_not_called()
        self.assertFalse(result["login_check_performed"])
        self.assertTrue(result["profile_path_exists"])

    def test_configured_profile_check_opens_naver_home_before_writer(self) -> None:
        profile = profiles.add_profile(1, alias="계정", blog_url="owner1")
        self.assertEqual(
            profiles._profile_check_urls(profile),
            ("https://www.naver.com", "https://blog.naver.com/owner1?Redirect=Write&"),
        )

    def test_profile_without_blog_url_restores_the_naver_home_session(self) -> None:
        profile = profiles.add_profile(1, alias="계정")
        self.assertEqual(
            profiles._profile_check_urls(profile),
            ("https://www.naver.com", None),
        )

    def test_profile_check_navigation_timeout_never_exceeds_remaining_time(self) -> None:
        deadline = profiles.time.monotonic() + 0.01
        self.assertEqual(profiles._remaining_navigation_timeout_ms(deadline), 3_000)
        with self.assertRaises(TimeoutError):
            profiles._remaining_navigation_timeout_ms(profiles.time.monotonic() - 0.01)

    def test_login_check_uses_bundled_persistent_session_flow(self) -> None:
        profiles.add_profile(1, alias="계정", blog_url="owner1")
        with patch.object(
            profiles,
            "_interactive_login_check",
            return_value="ready",
        ) as bundled:
            result = profiles.check_profile(1, login=True)

        bundled.assert_called_once()
        self.assertEqual(result["login_status"], "ready")
        self.assertEqual(result["session_source"], "plugin")

    def test_voice_metadata_is_saved_without_browser_and_redacts_secrets(self) -> None:
        profiles.add_profile(1, alias="계정", blog_url="owner1")
        with patch.object(
            profiles,
            "_interactive_login_check",
            side_effect=AssertionError("browser must not be used"),
        ) as browser_check:
            result = profiles.set_voice(
                1,
                status="ready",
                summary="짧은 문장 위주 password=never-store",
                sample_count=4,
            )
            status = profiles.voice_status(1)
        browser_check.assert_not_called()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["sample_count"], 4)
        self.assertNotIn("never-store", result["summary"])
        self.assertEqual(status[0]["status"], "ready")

    def test_symlink_escape_is_ignored_when_platform_allows_symlinks(self) -> None:
        profile_root = mato_common.browser_profiles_dir()
        profile_root.mkdir(parents=True)
        outside = self.env.root / "outside-profile"
        outside.mkdir()
        link = profile_root / "naver_9"
        try:
            os.symlink(outside, link, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"directory symlinks unavailable: {exc}")
        self.assertFalse(profiles._is_safe_profile_directory(link))
        catalog = profiles.load_catalog()
        self.assertNotIn(9, [item["slot"] for item in catalog["profiles"]])


if __name__ == "__main__":
    unittest.main()
