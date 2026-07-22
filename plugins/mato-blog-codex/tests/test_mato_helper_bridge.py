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


class FakeStore:
    def __init__(self, profile_path: Path) -> None:
        self.profile_path = profile_path

    def get_playwright_profile(self, _name: str) -> str:
        return str(self.profile_path)

    def ensure_playwright_profile(self, name: str, path: str) -> dict[str, object]:
        selected = Path(path)
        selected.mkdir(parents=True, exist_ok=True)
        self.profile_path = selected
        return {"name": name, "path": str(selected), "exists": True}

    def list_playwright_profiles(self) -> list[dict[str, object]]:
        return [{"name": "naver_1", "path": str(self.profile_path), "exists": self.profile_path.exists()}]


class FakeWriter:
    last: "FakeWriter | None" = None

    def __init__(self, update_status_func=None, profile_dir=None):  # type: ignore[no-untyped-def]
        self.update_status_func = update_status_func
        self.profile_dir = profile_dir
        self.stopped = False
        self.login_args = None
        FakeWriter.last = self

    def login(self, naver_id, naver_pw, target_url=None, login_timeout_sec=300):  # type: ignore[no-untyped-def]
        self.login_args = (naver_id, naver_pw, target_url, login_timeout_sec)
        return True

    def stop(self) -> None:
        self.stopped = True


class MatoHelperBridgeTests(unittest.TestCase):
    def test_profile_one_maps_to_existing_naver_one(self) -> None:
        payload = bridge.profile_check_payload(
            1,
            "https://blog.naver.com/intp_kr?Redirect=Write",
            interactive_login=True,
        )
        self.assertEqual(payload["profile_name"], "naver_1")
        self.assertEqual(payload["mode"], "create_login")
        self.assertTrue(payload["interactive_login"])

    def test_profile_slots_have_no_fixed_upper_limit(self) -> None:
        self.assertEqual(bridge.profile_name(999_999), "naver_999999")

    def test_unregistered_profile_one_adopts_existing_mato_legacy_profile(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = root / "legacy-naver-browser"
            legacy.mkdir()
            store = FakeStore(root / "unregistered")
            store.get_playwright_profile = lambda _name: ""  # type: ignore[method-assign]
            expected_home = root / "home"
            with (
                mock.patch.object(bridge, "_load_store", return_value=store),
                mock.patch.object(bridge.Path, "home", return_value=expected_home),
            ):
                (expected_home / ".googleblog").mkdir(parents=True)
                adopted = expected_home / ".googleblog" / "naver_browser"
                adopted.mkdir()
                result = bridge.resolve_profile_path(root, 1, create=False)
        self.assertEqual(result, adopted.resolve())

    def test_draft_payload_uses_existing_profile_and_defaults_to_draft(self) -> None:
        with TemporaryDirectory() as temporary:
            payload = bridge.draft_payload(
                1,
                temporary,
                "https://blog.naver.com/intp_kr?Redirect=Write",
            )
        self.assertEqual(payload["profile_name"], "naver_1")
        self.assertEqual(payload["publish_mode"], "draft")
        self.assertTrue(payload["is_draft"])
        self.assertEqual(payload["max_count"], 1)

    def test_direct_profile_check_uses_existing_writer_without_credentials(self) -> None:
        with TemporaryDirectory() as temporary:
            profile = Path(temporary) / "naver_1"
            store = FakeStore(profile)
            writer_module = SimpleNamespace(NaverPlaywright=FakeWriter)
            with (
                mock.patch.object(bridge, "_load_store", return_value=store),
                mock.patch.object(bridge.importlib, "import_module", return_value=writer_module),
                mock.patch.object(bridge, "_record_profile_status") as record_status,
            ):
                result = bridge.run_direct_profile_check(
                    Path(temporary),
                    1,
                    "https://blog.naver.com/intp_kr?Redirect=Write",
                    timeout_seconds=120,
                )
        self.assertTrue(result["login_ready"])
        self.assertEqual(result["profile_name"], "naver_1")
        self.assertEqual(FakeWriter.last.login_args[:2], ("", ""))
        self.assertTrue(FakeWriter.last.stopped)
        record_status.assert_called_once_with(1, True)

    def test_direct_draft_returns_safe_result_without_session_data(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile = root / "naver_1"
            profile.mkdir()
            work = root / "posts"
            work.mkdir()
            store = FakeStore(profile)

            def run_naver_draft_upload(**_kwargs):  # type: ignore[no-untyped-def]
                return {
                    "mode": "draft",
                    "processed": 1,
                    "succeeded": 1,
                    "failed": 0,
                    "skipped": 0,
                    "elapsed_sec": 2,
                    "cookie": "must-not-return",
                    "entries": [
                        {
                            "folder": "post-1",
                            "title": "테스트",
                            "ok": True,
                            "save_ok": True,
                            "completed": 4,
                            "session": "must-not-return",
                        }
                    ],
                }

            draft_module = SimpleNamespace(run_naver_draft_upload=run_naver_draft_upload)
            with (
                mock.patch.object(bridge, "_load_store", return_value=store),
                mock.patch.object(bridge.importlib, "import_module", return_value=draft_module),
            ):
                result = bridge.run_direct_draft(
                    root,
                    1,
                    str(work),
                    "https://blog.naver.com/intp_kr?Redirect=Write",
                )
        self.assertTrue(result["ok"])
        self.assertNotIn("cookie", str(result))
        self.assertNotIn("session", str(result))

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
                result = bridge.run_direct_url_download(Path(temporary), str(run_dir))

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

    def test_doctor_discovers_filesystem_profile(self) -> None:
        with TemporaryDirectory() as temporary:
            profile_root = Path(temporary) / "browser_profiles"
            profile = profile_root / "naver_2"
            profile.mkdir(parents=True)
            store = FakeStore(Path(temporary) / "missing")
            with (
                mock.patch.object(bridge, "DEFAULT_PROFILE_ROOT", profile_root),
                mock.patch.object(bridge, "_load_store", return_value=store),
            ):
                result = bridge.doctor(Path(temporary))
        self.assertEqual(result["transport"], "direct-local")
        self.assertIn("naver_2", [item["name"] for item in result["naver_profiles"]])

    def test_helper_root_requires_existing_bridge_files(self) -> None:
        with TemporaryDirectory() as temporary:
            with self.assertRaises(bridge.BridgeError):
                bridge.helper_root(temporary)


if __name__ == "__main__":
    unittest.main()
