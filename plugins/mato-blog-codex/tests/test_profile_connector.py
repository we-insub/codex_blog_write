from __future__ import annotations

import subprocess
import socket
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from _support import SCRIPTS_DIR

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import profile_connector
import profile_runtime


class ProfileConnectorTests(unittest.TestCase):
    def test_cdp_attach_preserves_user_browser_defaults(self) -> None:
        playwright = MagicMock()
        browser = MagicMock()
        context = MagicMock()
        browser.contexts = [context]
        playwright.chromium.connect_over_cdp.return_value = browser

        with patch.object(
            profile_connector,
            "connector_endpoint",
            return_value="http://127.0.0.1:43123",
        ):
            attached = profile_connector.connect_profile_context(playwright, "/tmp/naver_1")

        self.assertEqual(attached, (browser, context))
        playwright.chromium.connect_over_cdp.assert_called_once_with(
            "http://127.0.0.1:43123",
            is_local=True,
            no_defaults=True,
        )

    def test_reads_only_a_live_loopback_devtools_endpoint(self) -> None:
        with TemporaryDirectory() as temporary:
            profile = Path(temporary) / "naver_1"
            profile.mkdir()
            (profile / profile_connector.DEVTOOLS_ACTIVE_PORT).write_text(
                "43123\n/devtools/browser/example\n",
                encoding="utf-8",
            )
            connection = MagicMock()
            connection.__enter__.return_value = connection
            response = MagicMock()
            response.__enter__.return_value = response
            response.read.return_value = (
                b'{"webSocketDebuggerUrl":"ws://127.0.0.1:43123/devtools/browser/example"}'
            )
            with patch.object(
                profile_connector.socket,
                "create_connection",
                return_value=connection,
            ) as create_connection, patch.object(
                profile_connector,
                "urlopen",
                return_value=response,
            ):
                endpoint = profile_connector.connector_endpoint(profile)

        self.assertEqual(endpoint, "http://127.0.0.1:43123")
        create_connection.assert_called_once_with(("127.0.0.1", 43123), timeout=0.5)

    def test_stale_devtools_port_owned_by_another_process_is_rejected(self) -> None:
        with TemporaryDirectory() as temporary:
            profile = Path(temporary) / "naver_1"
            profile.mkdir()
            (profile / profile_connector.DEVTOOLS_ACTIVE_PORT).write_text(
                "43123\n/devtools/browser/expected\n",
                encoding="utf-8",
            )
            connection = MagicMock()
            connection.__enter__.return_value = connection
            response = MagicMock()
            response.__enter__.return_value = response
            response.read.return_value = (
                b'{"webSocketDebuggerUrl":"ws://127.0.0.1:43123/devtools/browser/different"}'
            )
            with patch.object(
                profile_connector.socket,
                "create_connection",
                return_value=connection,
            ), patch.object(profile_connector, "urlopen", return_value=response):
                self.assertIsNone(profile_connector.connector_endpoint(profile))

    def test_macos_launch_is_detached_and_keeps_exact_profile_path(self) -> None:
        self._assert_launch_arguments(os_name="posix", expected_flags=0, start_new_session=True)

    def test_windows_launch_uses_detached_process_flags_and_exact_profile_path(self) -> None:
        with patch.object(subprocess, "CREATE_NEW_PROCESS_GROUP", 8, create=True), patch.object(
            subprocess, "DETACHED_PROCESS", 16, create=True
        ):
            self._assert_launch_arguments(
                os_name="nt",
                expected_flags=24,
                start_new_session=False,
            )

    def test_windows_stale_lockfile_does_not_block_profile_launch(self) -> None:
        with TemporaryDirectory() as temporary:
            profile = Path(temporary) / "naver_1"
            profile.mkdir()
            (profile / "lockfile").write_text("stale", encoding="utf-8")
            with patch.object(profile_connector, "is_windows", return_value=True):
                self.assertFalse(profile_connector.profile_lock_exists(profile))

    def test_windows_active_lockfile_blocks_a_second_unmanaged_profile_launch(self) -> None:
        with TemporaryDirectory() as temporary:
            profile = Path(temporary) / "naver_1"
            profile.mkdir()
            (profile / "lockfile").write_text("locked", encoding="utf-8")
            with patch.object(profile_connector, "connector_endpoint", return_value=None), patch.object(
                profile_connector,
                "find_chrome_executable",
                side_effect=AssertionError("Chrome must not be launched for a locked profile"),
            ), patch.object(
                profile_connector,
                "is_windows",
                return_value=True,
            ), patch.object(
                profile_connector.os,
                "open",
                side_effect=PermissionError(13, "sharing violation"),
            ):
                with self.assertRaisesRegex(RuntimeError, "연결기 없이 이미 열려"):
                    profile_connector.open_profile_browser(
                        {"slot": 1, "profile_path": str(profile), "write_url": ""}
                    )

    def test_macos_stale_singleton_symlink_does_not_block_profile(self) -> None:
        with TemporaryDirectory() as temporary:
            profile = Path(temporary) / "naver_1"
            profile.mkdir()
            lock = profile / "SingletonLock"
            try:
                lock.symlink_to(f"{socket.gethostname()}-12345")
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            self.assertFalse(lock.exists())
            with patch.object(profile_connector, "is_windows", return_value=False), patch.object(
                profile_connector,
                "_pid_is_alive",
                return_value=False,
            ):
                self.assertFalse(profile_connector.profile_lock_exists(profile))

    def test_macos_active_singleton_symlink_blocks_profile_launch(self) -> None:
        with TemporaryDirectory() as temporary:
            profile = Path(temporary) / "naver_1"
            profile.mkdir()
            lock = profile / "SingletonLock"
            try:
                lock.symlink_to(f"{socket.gethostname()}-12345")
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            with patch.object(profile_connector, "connector_endpoint", return_value=None), patch.object(
                profile_connector,
                "find_chrome_executable",
                side_effect=AssertionError("Chrome must not be launched for a locked profile"),
            ), patch.object(
                profile_connector,
                "is_windows",
                return_value=False,
            ), patch.object(
                profile_connector,
                "_pid_is_alive",
                return_value=True,
            ):
                with self.assertRaisesRegex(RuntimeError, "연결기 없이 이미 열려"):
                    profile_connector.open_profile_browser(
                        {"slot": 1, "profile_path": str(profile), "write_url": ""}
                    )

    def test_macos_foreign_host_singleton_lock_remains_protected(self) -> None:
        with TemporaryDirectory() as temporary:
            profile = Path(temporary) / "naver_1"
            profile.mkdir()
            lock = profile / "SingletonLock"
            try:
                lock.symlink_to("different-host-12345")
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            with patch.object(profile_connector, "is_windows", return_value=False):
                self.assertTrue(profile_connector.profile_lock_exists(profile))

    def test_fresh_ready_status_without_endpoint_does_not_skip_failed_launch(self) -> None:
        with TemporaryDirectory() as temporary:
            profile = Path(temporary) / "browser_profiles" / "naver_1"
            profile.mkdir(parents=True)
            profile_runtime.write_profile_state(profile, status="ready", pid=12345)
            process = MagicMock(pid=23456)
            process.poll.return_value = 1
            with patch.object(profile_connector, "connector_endpoint", return_value=None), patch.object(
                profile_connector, "profile_lock_exists", return_value=False
            ), patch.object(profile_connector.subprocess, "Popen", return_value=process) as popen:
                with self.assertRaisesRegex(RuntimeError, "연결기가 준비되지 않았습니다"):
                    profile_connector.open_profile_browser({"slot": 1, "profile_path": str(profile)})
            popen.assert_called_once()

    def test_starting_status_waits_for_a_verified_endpoint(self) -> None:
        with TemporaryDirectory() as temporary:
            profile = Path(temporary) / "browser_profiles" / "naver_1"
            profile.mkdir(parents=True)
            profile_runtime.write_profile_state(profile, status="starting", pid=12345)
            process = MagicMock(pid=12345)
            process.poll.return_value = None
            with patch.object(
                profile_connector,
                "connector_endpoint",
                side_effect=[None, None, "http://127.0.0.1:43123"],
            ) as endpoint, patch.object(
                profile_connector, "profile_lock_exists", return_value=False
            ), patch.object(profile_connector.subprocess, "Popen", return_value=process), patch.object(
                profile_connector.time, "sleep"
            ) as sleep:
                result = profile_connector.open_profile_browser({"slot": 1, "profile_path": str(profile)})
            self.assertTrue(result["connector_ready"])
            self.assertEqual(endpoint.call_count, 3)
            sleep.assert_called_once_with(0.2)

    def test_host_exit_during_endpoint_probe_is_not_reported_as_ready(self) -> None:
        with TemporaryDirectory() as temporary:
            profile = Path(temporary) / "browser_profiles" / "naver_1"
            profile.mkdir(parents=True)
            process = MagicMock(pid=12345)
            process.poll.side_effect = [None, 1, 1]
            with patch.object(
                profile_connector,
                "connector_endpoint",
                side_effect=[None, "http://127.0.0.1:43123"],
            ), patch.object(
                profile_connector, "profile_lock_exists", return_value=False
            ), patch.object(profile_connector.subprocess, "Popen", return_value=process):
                with self.assertRaisesRegex(RuntimeError, "연결기가 준비되지 않았습니다"):
                    profile_connector.open_profile_browser({"slot": 1, "profile_path": str(profile)})

    def test_verified_existing_endpoint_does_not_launch_another_host(self) -> None:
        with TemporaryDirectory() as temporary:
            profile = Path(temporary) / "browser_profiles" / "naver_1"
            profile.mkdir(parents=True)
            with patch.object(
                profile_connector, "connector_endpoint", return_value="http://127.0.0.1:43123"
            ), patch.object(profile_connector.subprocess, "Popen") as popen:
                result = profile_connector.open_profile_browser({"slot": 1, "profile_path": str(profile)})
            self.assertEqual(result["status"], "already_open")
            self.assertTrue(result["connector_ready"])
            popen.assert_not_called()

    def _assert_launch_arguments(
        self,
        *,
        os_name: str,
        expected_flags: int,
        start_new_session: bool,
    ) -> None:
        with TemporaryDirectory() as temporary:
            profile = Path(temporary) / "browser_profiles" / "naver_27"
            profile.mkdir(parents=True)
            process = MagicMock(pid=27123)
            process.poll.return_value = None
            connector_results = [None, "http://127.0.0.1:43127"]
            with patch.object(
                profile_connector,
                "connector_endpoint",
                side_effect=connector_results,
            ), patch.object(
                profile_connector.subprocess,
                "Popen",
                return_value=process,
            ) as popen, patch.object(
                profile_connector,
                "is_windows",
                return_value=os_name == "nt",
            ):
                result = profile_connector.open_profile_browser(
                    {
                        "slot": 27,
                        "profile_path": str(profile),
                        "write_url": "https://blog.naver.com/owner27?Redirect=Write&",
                    }
                )

        command = popen.call_args.args[0]
        options = popen.call_args.kwargs
        self.assertEqual(command[0], sys.executable)
        self.assertEqual(Path(command[1]).name, "profile_host.py")
        self.assertIn("--profile-path", command)
        self.assertIn(str(profile.resolve(strict=False)), command)
        self.assertIn("--target-url", command)
        self.assertIn("https://blog.naver.com/owner27?Redirect=Write&", command)
        self.assertEqual(options["creationflags"], expected_flags)
        self.assertEqual(options["start_new_session"], start_new_session)
        self.assertEqual(result["status"], "opened")


if __name__ == "__main__":
    unittest.main()
