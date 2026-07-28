from __future__ import annotations

import sys
import unittest
from unittest.mock import patch

from _support import IsolatedMatoEnvironment, SCRIPTS_DIR

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import naver_profile_runtime


class _Chromium:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def launch_persistent_context(self, path: str, **kwargs: object) -> object:
        self.calls.append((path, kwargs))
        if kwargs.get("channel") == "chrome":
            raise RuntimeError("chrome unavailable")
        return object()


class _Playwright:
    def __init__(self) -> None:
        self.chromium = _Chromium()


class MatoProfileRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = IsolatedMatoEnvironment()
        self.env = self.environment.__enter__()

    def tearDown(self) -> None:
        self.environment.__exit__(None, None, None)

    def test_uses_mato_options_and_falls_back_to_edge(self) -> None:
        playwright = _Playwright()
        profile_path = self.env.root / "profile"
        with patch.object(naver_profile_runtime.platform, "system", return_value="Darwin"):
            naver_profile_runtime.launch_mato_profile_context(playwright, profile_path)

        self.assertTrue(profile_path.is_dir())
        self.assertEqual([call[1].get("channel") for call in playwright.chromium.calls], ["chrome", "msedge"])
        options = playwright.chromium.calls[0][1]
        self.assertIn("--disable-blink-features=AutomationControlled", options["args"])
        self.assertEqual(options["locale"], "ko-KR")
        self.assertEqual(options["timeout"], 45_000)


if __name__ == "__main__":
    unittest.main()
