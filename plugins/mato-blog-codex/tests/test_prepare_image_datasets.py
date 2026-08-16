from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from _support import IsolatedMatoEnvironment, SCRIPTS_DIR, create_run, generated_payload, write_json

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import history
import attach_images
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

    def test_product_dataset_requires_preplaced_image_tags(self) -> None:
        from PIL import Image

        run_dir = self.env.root / "product-images"
        product_url = "https://myrealt.rip/iZRp3d"
        history.create_run(
            "시장",
            "blog",
            1,
            "상품 이미지 태그 검증",
            run_dir=run_dir,
            request_fields={
                "source_type": "myrealtrip_product",
                "channel": "naver",
                "product_url": product_url,
                "main_keyword": "시장",
                "image_policy": {
                    "mode": "none",
                    "permission_confirmed": False,
                    "max_images": 80,
                },
            },
        )
        generated = self.env.root / "product-generated.json"
        payload = generated_payload(1)
        payload["posts"][0].update(  # type: ignore[index]
            {
                "title_plan": {
                    "channel": "naver",
                    "main_keyword": "시장",
                    "main_terms": ["시장"],
                    "subkeywords": ["안내"],
                    "hook": "",
                },
                "link_url": product_url,
            }
        )
        write_json(generated, payload)
        result = write_posts.write_posts(run_dir, generated)
        text_path = run_dir / str(result["posts"][0]["file"])
        before = text_path.read_bytes()

        source_dir = run_dir / "sources" / "myrealtrip-product" / "prepared"
        source_dir.mkdir(parents=True)
        Image.new("RGB", (800, 600), "blue").save(source_dir / "image_1.jpg")

        with self.assertRaisesRegex(ValueError, "must place every image tag"):
            prepare_image_datasets.prepare_post_image_datasets(run_dir, source_dir)
        self.assertEqual(text_path.read_bytes(), before)
        self.assertFalse((text_path.parent / "image_1.jpg").exists())

    def test_product_dataset_remaps_relevant_manifest_image_to_first_sequential_tag(self) -> None:
        from PIL import Image

        run_dir = self.env.root / "product-remap"
        product_url = "https://myrealt.rip/iZRp3d"
        history.create_run(
            "시장",
            "product",
            1,
            "상품 대표 이미지 재배치",
            run_dir=run_dir,
            request_fields={
                "source_type": "myrealtrip_product",
                "channel": "naver",
                "product_url": product_url,
                "main_keyword": "시장",
                "image_policy": {
                    "mode": "all_unique_seller_product_images",
                    "permission_confirmed": True,
                    "max_images": 80,
                },
            },
        )
        bundle = run_dir / "sources" / "myrealtrip-product"
        prepared = bundle / "prepared"
        prepared.mkdir(parents=True)
        colors = ("red", "green", "blue")
        for index, color in enumerate(colors, start=1):
            Image.new("RGB", (40 + index, 30 + index), color).save(
                prepared / f"image_{index}.jpg", format="JPEG"
            )
        roles = ("gallery", "introduction", "itinerary")
        accepted = [
            {
                "index": index,
                "classification": "product",
                "source_role": roles[index - 1],
                "role": roles[index - 1],
                "source_url": f"https://dry7pvlp22cox.cloudfront.net/mrt-images-prod/test/{index}.jpg",
                "file": f"prepared/image_{index}.jpg",
                "sha256": hashlib.sha256(
                    (prepared / f"image_{index}.jpg").read_bytes()
                ).hexdigest(),
                "width": 40 + index,
                "height": 30 + index,
            }
            for index in range(1, 4)
        ]
        manifest = bundle / "manifest.json"
        write_json(
            manifest,
            {
                "kind": "myrealtrip_product_images",
                "source_url": product_url,
                "canonical_url": "https://experiences.myrealtrip.com/products/3795277",
                "product_id": "3795277",
                "image_count": 3,
                "candidate_count": 3,
                "unique_url_count": 3,
                "accepted": accepted,
                "duplicates": [],
            },
        )
        brief = run_dir / "analysis" / "product-writing-brief.json"
        write_json(brief, {"kind": "fixture"})
        manifest_sha = hashlib.sha256(manifest.read_bytes()).hexdigest()
        history.update_run(
            run_dir,
            {
                "product_writing": {
                    "status": "briefed",
                    "brief_file": str(brief.relative_to(run_dir)),
                    "brief_file_sha256": hashlib.sha256(brief.read_bytes()).hexdigest(),
                    "brief_sha256": "b" * 64,
                    "image_manifest": str(manifest.relative_to(run_dir)),
                    "image_manifest_sha256": manifest_sha,
                }
            },
        )
        provenance = {
            "brief_sha256": "b" * 64,
            "image_manifest": str(manifest.relative_to(run_dir)),
            "image_manifest_sha256": manifest_sha,
        }
        payload = generated_payload(1)
        payload["posts"][0].update(  # type: ignore[index]
            {
                "title_plan": {
                    "channel": "naver",
                    "main_keyword": "시장",
                    "main_terms": ["시장"],
                    "subkeywords": ["안내"],
                    "hook": "1",
                    "exact_title": "시장 안내 1",
                },
                "link_url": product_url,
                "image_manifest": str(manifest.relative_to(run_dir)),
                "product_writing_provenance": provenance,
                "image_placements": [
                    {"file": "image_1.jpg", "manifest_index": 3, "region": "intro", "after_paragraph": 0},
                    {"file": "image_2.jpg", "manifest_index": 1, "region": "intro", "after_paragraph": 0},
                    {"file": "image_3.jpg", "manifest_index": 2, "region": "section", "section_index": 1, "after_paragraph": 0},
                ],
            }
        )
        semantic_post = dict(payload["posts"][0])  # type: ignore[index]
        semantic_post.pop("product_writing_provenance", None)
        finalized_hash = hashlib.sha256(
            json.dumps(
                semantic_post,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        provenance["finalized_post_sha256"] = finalized_hash
        product_state = dict(history.load_run(run_dir)["product_writing"])
        product_state.update(
            {"status": "finalized", "finalized_post_sha256": finalized_hash}
        )
        history.update_run(run_dir, {"product_writing": product_state})
        generated = self.env.root / "product-remap.json"
        write_json(generated, payload)
        result = write_posts.write_posts(run_dir, generated)
        prepare_image_datasets.prepare_post_image_datasets(run_dir, prepared)
        post_dir = (run_dir / str(result["posts"][0]["file"])).parent
        self.assertEqual(
            hashlib.sha256((post_dir / "image_1.jpg").read_bytes()).hexdigest(),
            accepted[2]["sha256"],
        )
        self.assertEqual(
            hashlib.sha256((post_dir / "image_2.jpg").read_bytes()).hexdigest(),
            accepted[0]["sha256"],
        )

    def test_attach_is_atomic_when_a_later_post_cleanup_fails(self) -> None:
        from PIL import Image

        run_dir = self.env.root / "atomic-attach"
        create_run(run_dir, versions=2)
        generated = self.env.root / "atomic-generated.json"
        write_json(generated, generated_payload(2))
        result = write_posts.write_posts(run_dir, generated)
        originals: dict[Path, bytes] = {}
        for item in result["posts"]:
            text_path = run_dir / str(item["file"])
            text_path.write_text(
                text_path.read_text(encoding="utf-8").rstrip()
                + "\n\n[image_1.jpg]\n",
                encoding="utf-8",
            )
            originals[text_path] = text_path.read_bytes()

        source = run_dir / "sources" / "items" / "source"
        source.mkdir(parents=True)
        Image.new("RGB", (600, 400), "green").save(source / "image_1.jpg")
        manifest = run_dir / "analysis" / "attach.json"
        write_json(
            manifest,
            {
                "posts": [
                    {
                        "index": index,
                        "images": [
                            {
                                "source": "sources/items/source/image_1.jpg",
                                "target": "image_1.jpg",
                            }
                        ],
                    }
                    for index in (1, 2)
                ]
            },
        )

        with patch(
            "sanitize_images.sanitize_path",
            side_effect=[
                {"ok": True, "processed": 1},
                ValueError("second image failed"),
            ],
        ):
            with self.assertRaisesRegex(ValueError, "second image failed"):
                attach_images.attach_images(run_dir, manifest)

        for text_path, expected in originals.items():
            self.assertEqual(text_path.read_bytes(), expected)
            self.assertFalse((text_path.parent / "image_1.jpg").exists())


if __name__ == "__main__":
    unittest.main()
