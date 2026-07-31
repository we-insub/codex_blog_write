from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

from _support import IsolatedMatoEnvironment, SCRIPTS_DIR

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import owned_post


class OwnedPostTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = IsolatedMatoEnvironment()
        self.env = self.environment.__enter__()

    def tearDown(self) -> None:
        self.environment.__exit__(None, None, None)

    def test_prepares_owned_url_source_with_bundled_downloader(self) -> None:
        run_dir = self.env.root / "owned"

        def fake_download(target):  # type: ignore[no-untyped-def]
            directory = Path(target)
            folder = directory / "sources" / "items" / "01_내-글-intp_kr-224348902627"
            folder.mkdir(parents=True, exist_ok=True)
            return {
                "ok": True,
                "entries": [{"ok": True, "folder": folder.name, "images": 2}],
            }

        with (
            mock.patch.object(owned_post.bridge, "run_direct_url_download", side_effect=fake_download),
        ):
            result = owned_post.prepare_owned_post_download(
                "https://blog.naver.com/intp_kr/224348902627",
                versions=3,
                command="내 글 URL 원고 3개",
                run_dir=run_dir,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["source"]["images"], 2)
        metadata = (run_dir / "sources" / "sources.json").read_text(encoding="utf-8")
        self.assertIn("224348902627", metadata)
        self.assertTrue((run_dir / "sources" / "items" / "01_내-글-intp_kr-224348902627").is_dir())

    def test_rejects_non_naver_or_incomplete_url(self) -> None:
        with self.assertRaisesRegex(ValueError, "Naver"):
            owned_post._owned_post_identity("https://example.com/post")
        with self.assertRaisesRegex(ValueError, "post number"):
            owned_post._owned_post_identity("https://blog.naver.com/intp_kr")


if __name__ == "__main__":
    unittest.main()
