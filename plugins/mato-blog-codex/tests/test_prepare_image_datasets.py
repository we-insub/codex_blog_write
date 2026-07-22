from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

from _support import IsolatedMatoEnvironment, SCRIPTS_DIR, create_run, generated_payload, write_json

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import history
import prepare_image_datasets
import write_posts


class PrepareImageDatasetsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = IsolatedMatoEnvironment()
        self.env = self.environment.__enter__()

    def tearDown(self) -> None:
        self.environment.__exit__(None, None, None)

    def test_creates_and_cleans_one_dataset_for_each_generated_post(self) -> None:
        from PIL import Image

        run_dir = self.env.root / "owned-images"
        create_run(run_dir, versions=3)
        generated = self.env.root / "generated.json"
        write_json(generated, generated_payload(3))
        write_posts.write_posts(run_dir, generated)

        source_dir = run_dir / "sources" / "items" / "my-post"
        source_dir.mkdir(parents=True)
        image = Image.new("RGB", (600, 400), "green")
        exif = Image.Exif()
        exif[270] = "private source note"
        image.save(source_dir / "image_1.jpg", exif=exif)
        Image.new("RGB", (800, 600), "blue").save(source_dir / "image_2.jpg")

        result = prepare_image_datasets.prepare_post_image_datasets(run_dir, source_dir)

        self.assertTrue(result["ok"])
        self.assertEqual(result["post_datasets"], 3)
        self.assertTrue((source_dir / "image-processing.json").is_file())
        self.assertEqual(result["attachments"]["post_count"], 3)
        for post in result["attachments"]["posts"]:
            post_dir = Path(post["folder"])
            self.assertTrue((post_dir / "image_1.jpg").is_file())
            self.assertTrue((post_dir / "image_2.jpg").is_file())
            self.assertTrue((post_dir / "image-processing.json").is_file())
            with Image.open(post_dir / "image_1.jpg") as copied:
                self.assertFalse(copied.getexif())
            text = next(post_dir.glob("*_함축.txt")).read_text(encoding="utf-8")
            self.assertIn("[image_1.jpg]", text)
            self.assertIn("[image_2.jpg]", text)

        state = history.load_run(run_dir)
        self.assertEqual(state["owned_image_datasets"]["post_datasets"], 3)
        self.assertTrue(state["owned_image_datasets"]["per_post_cleanup"])
        manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["posts"]), 3)


if __name__ == "__main__":
    unittest.main()
