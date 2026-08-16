from __future__ import annotations

import sys
import unittest
from pathlib import Path

from _support import IsolatedMatoEnvironment, SCRIPTS_DIR

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import prompt_manager


class PromptManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = IsolatedMatoEnvironment()
        self.env = self.environment.__enter__()

    def tearDown(self) -> None:
        self.environment.__exit__(None, None, None)

    def test_export_appends_overlay_without_changing_managed_prompt(self) -> None:
        managed = prompt_manager.init_prompt("공통")
        managed_path = Path(str(managed["path"]))
        managed_path.write_text("사용자 공통 규칙\n", encoding="utf-8")
        before = managed_path.read_bytes()
        overlay = self.env.root / "product-overlay.txt"
        overlay.write_text("마이리얼트립 상품 팩트\n", encoding="utf-8")
        output = self.env.root / "effective.txt"

        result = prompt_manager.export_prompt(output, "공통", overlay=overlay)

        effective = output.read_text(encoding="utf-8")
        self.assertLess(effective.index("사용자 공통 규칙"), effective.index("작업별 오버레이"))
        self.assertIn("마이리얼트립 상품 팩트", effective)
        self.assertEqual(managed_path.read_bytes(), before)
        self.assertEqual(result["overlay"], str(overlay.resolve()))


if __name__ == "__main__":
    unittest.main()
