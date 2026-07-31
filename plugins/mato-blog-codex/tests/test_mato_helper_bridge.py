from __future__ import annotations

import sys
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

from _support import SCRIPTS_DIR

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import mato_helper_bridge as bridge


class BundledSourceDownloadTests(unittest.TestCase):
    def test_public_cli_rejects_removed_helper_commands(self) -> None:
        for command in ("doctor", "profile-check", "draft", "prompt-export"):
            with self.subTest(command=command), mock.patch.object(sys, "stderr"):
                with self.assertRaises(SystemExit) as raised:
                    bridge.main([command])
            self.assertEqual(raised.exception.code, 2)

    def test_source_has_no_external_helper_runtime_hooks(self) -> None:
        source = Path(bridge.__file__).read_text(encoding="utf-8")
        forbidden = (
            "MATO_HELPER_ROOT",
            "google-blog-auto",
            "local_agent.local_store",
            "local_agent.naver_draft",
            "naver_playwright",
        )
        for marker in forbidden:
            with self.subTest(marker=marker):
                self.assertNotIn(marker, source)

    def test_direct_url_download_creates_jpg_and_structured_original_hamchuk(self) -> None:
        with TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "run"
            source_folder = run_dir / "sources" / "items" / "01_서울맛집"
            source_folder.mkdir(parents=True)
            metadata = {
                "sources": [
                    {
                        "rank": 1,
                        "title": "서울맛집",
                        "url": "https://m.blog.naver.com/owner/1001",
                        "note_file": "sources/items/01_서울맛집/서울맛집_글감_함축.txt",
                    }
                ]
            }
            (run_dir / "sources" / "sources.json").write_text(
                json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
            )

            def fake_download(**kwargs):  # type: ignore[no-untyped-def]
                from PIL import Image

                pairs = Path(kwargs["txt_path"]).read_text(encoding="utf-8").splitlines()
                self.assertEqual(
                    pairs[0],
                    "https://blog.naver.com/PostView.naver?blogId=owner&logNo=1001",
                )
                output = Path(kwargs["output_dir"])
                folder = output / "01_서울맛집"
                folder.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (400, 400), "red").save(folder / "image_1.png", format="PNG")
                text_file = "01_서울맛집_원본.txt"
                (folder / text_file).write_text(
                    "원본 URL: https://blog.naver.com/owner/1001\n본문\n[image_1.png]\n",
                    encoding="utf-8",
                )
                return {
                    "processed": 1,
                    "succeeded": 1,
                    "failed": 0,
                    "entries": [
                        {
                            "folder": "01_서울맛집",
                            "url": "https://blog.naver.com/owner/1001",
                            "ok": True,
                            "images": 1,
                            "text_file": text_file,
                        }
                    ],
                }

            fake_history = SimpleNamespace(append_event=lambda *_args, **_kwargs: None)
            real_import = bridge.importlib.import_module

            def import_module(name):  # type: ignore[no-untyped-def]
                return fake_history if name == "history" else real_import(name)

            def fake_structured(folder, **_kwargs):  # type: ignore[no-untyped-def]
                output = Path(folder) / "원문제목_원본_함축.txt"
                output.write_text(
                    "제목을입력해주세요1: 원문제목\n\n인트로1:\n도입\n\n본문2:\n[image_1.jpg]\n",
                    encoding="utf-8",
                )
                return output, "원문제목"

            with (
                mock.patch.object(bridge, "local_url_download", SimpleNamespace(run_naver_url_download=fake_download)),
                mock.patch.object(bridge.importlib, "import_module", side_effect=import_module),
                mock.patch.object(bridge, "_write_structured_original_hamchuk", side_effect=fake_structured),
            ):
                result = bridge.run_direct_url_download(str(run_dir))

            hamchuk = source_folder / "원문제목_원본_함축.txt"
            self.assertTrue(result["ok"])
            self.assertTrue((source_folder / "image_1.jpg").is_file())
            self.assertFalse((source_folder / "image_1.png").exists())
            self.assertTrue(hamchuk.is_file())
            text = hamchuk.read_text(encoding="utf-8")
            self.assertIn("제목을입력해주세요1: 원문제목", text)
            self.assertIn("인트로1:", text)
            self.assertIn("본문2:", text)
            self.assertIn("[image_1.jpg]", text)
            self.assertEqual(result["entries"][0]["title"], "원문제목")

    def test_structured_original_preserves_intro_heading_table_and_image(self) -> None:
        html = """
        <div class="se-title-text">원문 제목</div>
        <div class="se-main-container">
          <div class="se-component se-text"><p class="se-text-paragraph">첫 도입</p></div>
          <div class="se-component se-quotation"><p class="se-text-paragraph">소제목</p></div>
          <div class="se-component se-table"><table><tr><td>항목</td><td>내용</td></tr><tr><td>A</td><td>B</td></tr></table></div>
          <div class="se-component se-image"><img src="https://example.test/image.jpg?type=w80" /></div>
        </div>
        """
        title, text = bridge._structured_original_hamchuk(
            html,
            source_url="https://blog.naver.com/PostView.naver?blogId=test&logNo=1",
            fallback_title="대체 제목",
            image_names_by_url={"https://example.test/image.jpg": "image_1.jpg"},
        )
        self.assertEqual(title, "원문 제목")
        self.assertIn("제목을입력해주세요1: 원문 제목", text)
        self.assertIn("인트로1:\n첫 도입", text)
        self.assertIn("본문2:", text)
        self.assertIn("ㅂㅂㅂ 소제목", text)
        self.assertIn("표 2 x 2 시작", text)
        self.assertIn("(1,1) B", text)
        self.assertIn("표 2 x 2 끝", text)
        self.assertIn("[image_1.jpg]", text)

    def test_local_image_size_filter_drops_small_and_renumbers(self) -> None:
        from PIL import Image

        with TemporaryDirectory() as temporary:
            folder = Path(temporary)
            Image.new("RGB", (100, 50), "white").save(folder / "image_1.jpg")
            Image.new("RGB", (1200, 800), "blue").save(folder / "image_2.jpg")
            Image.new("RGB", (299, 900), "green").save(folder / "image_3.jpg")
            result = bridge._filter_local_images_by_size(
                folder, min_width=300, min_height=300
            )

            self.assertEqual(result["image_files"], ["image_1.jpg"])
            self.assertEqual(result["old_to_new"]["image_1.jpg"], "")
            self.assertEqual(result["old_to_new"]["image_2.jpg"], "image_1.jpg")
            self.assertEqual(result["old_to_new"]["image_3.jpg"], "")
            self.assertEqual(result["dropped"], 2)
            with Image.open(folder / "image_1.jpg") as image:
                self.assertEqual(image.size, (1200, 800))

    def test_naver_image_url_keeps_required_size_query(self) -> None:
        url = "https://postfiles.pstatic.net/example.png?type=w966"
        self.assertEqual(bridge._original_image_url(url), url)

    def test_normalize_images_removes_stale_same_index_representation(self) -> None:
        from PIL import Image

        with TemporaryDirectory() as temporary:
            folder = Path(temporary)
            Image.new("RGB", (400, 400), "red").save(folder / "image_1.jpg")
            Image.new("RGB", (400, 400), "blue").save(folder / "image_1.png")
            files, _replacements = bridge._normalize_downloaded_images(folder)
            self.assertEqual(files, ["image_1.jpg"])
            self.assertTrue((folder / "image_1.jpg").is_file())
            self.assertFalse((folder / "image_1.png").exists())

if __name__ == "__main__":
    unittest.main()
