from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest

from _support import IsolatedMatoEnvironment, SCRIPTS_DIR

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import convert


class MatoConvertTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = IsolatedMatoEnvironment()
        self.env = self.environment.__enter__()
        self.folder = self.env.root / "서울 맛집"
        self.folder.mkdir()

    def tearDown(self) -> None:
        self.environment.__exit__(None, None, None)

    def test_converts_source_without_modifying_it(self) -> None:
        source = self.folder / "원문.txt"
        source.write_text("소개 문장\n[사진1]", encoding="utf-8")
        (self.folder / "사진1.jpg").write_bytes(b"not-an-image")

        result = convert.convert_folder(self.folder, fix_image_tags=True)

        self.assertTrue(result["ok"])
        self.assertEqual(source.read_text(encoding="utf-8"), "소개 문장\n[사진1]")
        output = self.folder / "서울 맛집_함축.txt"
        text = output.read_text(encoding="utf-8")
        self.assertIn("제목을입력해주세요1: 서울 맛집", text)
        self.assertIn("본문2:", text)
        self.assertIn("[사진1.jpg]", text)

    def test_existing_output_requires_explicit_overwrite(self) -> None:
        (self.folder / "원문.txt").write_text("본문", encoding="utf-8")
        output = self.folder / "서울 맛집_함축.txt"
        output.write_text("기존 결과", encoding="utf-8")

        blocked = convert.convert_folder(self.folder)
        replaced = convert.convert_folder(self.folder, overwrite=True)

        self.assertFalse(blocked["ok"])
        self.assertTrue(replaced["ok"])
        self.assertIn("본문2:", output.read_text(encoding="utf-8"))

    def test_batch_limit_and_failures_are_reported(self) -> None:
        result = convert.convert_rows([{"folder_path": str(self.folder)}])
        self.assertEqual(result["succeeded"], 0)
        self.assertEqual(result["failed"], 1)
        with self.assertRaises(ValueError):
            convert.convert_rows([{"folder_path": str(self.folder)}] * 51)

    def test_cli_entrypoint_returns_json_summary(self) -> None:
        (self.folder / "원문.txt").write_text("본문", encoding="utf-8")
        request = self.env.root / "convert-rows.json"
        request.write_text(json.dumps([{"folder_path": str(self.folder)}]), encoding="utf-8")
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = convert.main(["--input", str(request)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stream.getvalue())["succeeded"], 1)


if __name__ == "__main__":
    unittest.main()
