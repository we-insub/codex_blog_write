from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from _support import SCRIPTS_DIR

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import sanitize_images


class SanitizeImagesTests(unittest.TestCase):
    def test_sanitize_jpeg_removes_exif_and_preserves_pixels_and_name(self) -> None:
        from PIL import Image

        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "image_1.jpg"
            image = Image.new("RGB", (640, 480), "blue")
            exif = Image.Exif()
            exif[270] = "private description"
            exif[274] = 1
            image.save(path, format="JPEG", quality=95, exif=exif)
            before = path.read_bytes()

            result = sanitize_images.sanitize_image(path)

            self.assertEqual(result["file"], "image_1.jpg")
            self.assertEqual((result["width"], result["height"]), (640, 480))
            self.assertNotEqual(path.read_bytes(), before)
            self.assertTrue(result["metadata_removed"]["exif"])
            with Image.open(path) as cleaned:
                self.assertFalse(cleaned.getexif())
                self.assertEqual(cleaned.size, (640, 480))

    def test_folder_run_writes_manifest_without_metadata_values(self) -> None:
        from PIL import Image

        with TemporaryDirectory() as temporary:
            folder = Path(temporary)
            Image.new("RGB", (320, 320), "red").save(folder / "image_1.jpg")
            manifest = folder / "image-processing.json"

            result = sanitize_images.sanitize_path(folder, manifest_path=manifest)

            self.assertTrue(result["ok"])
            self.assertEqual(result["processed"], 1)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(payload["entries"][0]["file"], "image_1.jpg")
            self.assertNotIn("private description", manifest.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
