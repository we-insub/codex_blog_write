from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from _support import IsolatedMatoEnvironment, SCRIPTS_DIR

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import bootstrap


class BootstrapPlatformTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = IsolatedMatoEnvironment()
        self.env = self.environment.__enter__()

    def tearDown(self) -> None:
        self.environment.__exit__(None, None, None)

    def test_runtime_uses_platform_specific_virtualenv_python(self) -> None:
        with patch.object(bootstrap, "is_windows", return_value=True):
            self.assertEqual(bootstrap.runtime_python().name, "python.exe")
            self.assertEqual(bootstrap.runtime_python().parent.name, "Scripts")
        with patch.object(bootstrap, "is_windows", return_value=False):
            self.assertEqual(bootstrap.runtime_python().name, "python")
            self.assertEqual(bootstrap.runtime_python().parent.name, "bin")

    def test_macos_chrome_candidates_include_system_and_user_applications(self) -> None:
        with patch.object(bootstrap.sys, "platform", "darwin"):
            candidates = bootstrap.chrome_candidates()
        self.assertEqual(
            candidates[0],
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        )
        self.assertIn(
            "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            candidates[1].as_posix(),
        )

    def test_windows_chrome_candidates_cover_standard_install_roots(self) -> None:
        environment = {
            "PROGRAMFILES": r"C:\Program Files",
            "PROGRAMFILES(X86)": r"C:\Program Files (x86)",
            "LOCALAPPDATA": r"C:\Users\student\AppData\Local",
        }
        with patch.object(bootstrap.sys, "platform", "win32"), patch.object(
            bootstrap, "is_windows", return_value=True
        ), patch.dict(os.environ, environment, clear=False):
            candidates = bootstrap.chrome_candidates()

        self.assertEqual(len(candidates), 3)
        self.assertTrue(all(path.name == "chrome.exe" for path in candidates))
        self.assertTrue(all("Google" in path.parts for path in candidates))

    def test_old_python_relaunches_bootstrap_with_supported_interpreter(self) -> None:
        probe = subprocess.CompletedProcess([], 0)
        launched = subprocess.CompletedProcess([], 7)
        with patch.object(bootstrap.sys, "version_info", (3, 8, 10)), patch.object(
            bootstrap, "bootstrap_python_candidates", return_value=[["python3.12"]]
        ), patch.object(bootstrap.subprocess, "run", side_effect=[probe, launched]) as run:
            result = bootstrap.main(["--check"])

        self.assertEqual(result, 7)
        self.assertEqual(run.call_args_list[0].args[0][:2], ["python3.12", "-c"])
        self.assertEqual(run.call_args_list[1].args[0][-1], "--check")


if __name__ == "__main__":
    unittest.main()
