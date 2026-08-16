from __future__ import annotations

import hashlib
import json
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

from _support import (
    IsolatedMatoEnvironment,
    SCRIPTS_DIR,
    create_run,
    generated_payload,
    write_json,
)

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import history
import validate_posts
import write_posts


def _post_snapshot(run_dir: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(run_dir)): path.read_bytes()
        for path in sorted((run_dir / "posts").rglob("*"))
        if path.is_file()
    }


PRODUCT_LINK = "https://myrealt.rip/iZRp3d"


def _write_product_manifest(run_dir: Path, *, count: int = 3) -> Path:
    from PIL import Image

    bundle = run_dir / "sources" / "myrealtrip" / "product-3510284"
    prepared = bundle / "prepared"
    prepared.mkdir(parents=True, exist_ok=True)
    roles = ("gallery", "introduction", "itinerary")
    accepted: list[dict[str, object]] = []
    for index in range(1, count + 1):
        path = prepared / f"image_{index}.jpg"
        Image.new(
            "RGB",
            (32 + index, 24 + index),
            color=(30 * index, 40 * index, 50 * index),
        ).save(path, format="JPEG", quality=91)
        width, height = validate_posts._image_dimensions(path)
        accepted.append(
            {
                "index": index,
                "classification": "product",
                "source_role": roles[(index - 1) % len(roles)],
                "role": roles[(index - 1) % len(roles)],
                "source_url": (
                    "https://dry7pvlp22cox.cloudfront.net/"
                    f"mrt-images-prod/test/image_{index}.jpg"
                ),
                "file": f"prepared/image_{index}.jpg",
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "width": width,
                "height": height,
            }
        )
    manifest = bundle / "manifest.json"
    write_json(
        manifest,
        {
            "schema_version": 1,
            "kind": "myrealtrip_product_images",
            "source_url": PRODUCT_LINK,
            "canonical_url": "https://experiences.myrealtrip.com/products/3510284",
            "product_id": "3510284",
            "image_policy": {
                "seller_only": True,
                "review_images_excluded": True,
                "recommendation_images_excluded": True,
            },
            "collection_proof": {
                "page_url": "https://experiences.myrealtrip.com/products/3510284",
                "candidate_count": count,
                "gallery_visited_count": sum(
                    1 for row in accepted if row["source_role"] == "gallery"
                ),
                "page_end_reached": True,
                "review_all_opened": True,
                "available_sections": [
                    "INTRODUCTION",
                    "ITINERARIES",
                    "INCLUDE_EXCLUDE",
                    "USAGE",
                    "ESSENTIALS",
                    "REFUND",
                    "REVIEW",
                ],
                "expanded_sections": ["INTRODUCTION", "ITINERARIES", "ESSENTIALS", "REVIEW"],
                "stable_candidate_counts": [count, count],
            },
            "image_count": count,
            "candidate_count": count,
            "unique_url_count": count,
            "accepted": accepted,
            "duplicates": [],
            "rejected": [],
        },
    )
    brief = run_dir / "analysis" / "product-writing-brief.json"
    write_json(brief, {"kind": "test-product-writing-brief"})
    manifest_sha256 = hashlib.sha256(manifest.read_bytes()).hexdigest()
    history.update_run(
        run_dir,
        {
            "product_writing": {
                "status": "briefed",
                "brief_file": str(brief.relative_to(run_dir)),
                "brief_file_sha256": hashlib.sha256(brief.read_bytes()).hexdigest(),
                "brief_sha256": "b" * 64,
                "image_manifest": str(manifest.relative_to(run_dir)),
                "image_manifest_sha256": manifest_sha256,
            }
        },
    )
    return manifest


def _product_payload(run_dir: Path, manifest: Path) -> dict[str, object]:
    state = history.load_run(run_dir)["product_writing"]
    provenance = {
        "brief_sha256": state["brief_sha256"],
        "image_manifest_sha256": state["image_manifest_sha256"],
        "image_manifest": str(manifest.relative_to(run_dir)),
    }
    payload: dict[str, object] = {
        "analysis": {"summary": "상품 사실과 가족 여행 검색 의도를 반영"},
        "product_writing_provenance": provenance,
        "posts": [
            {
                "title": "나트랑 여행 보트투어 솔직후기",
                "title_plan": {
                    "channel": "naver",
                    "main_keyword": "나트랑 여행",
                    "main_terms": ["나트랑", "여행"],
                    "subkeywords": ["보트투어"],
                    "hook": "솔직후기",
                    "exact_title": "나트랑 여행 보트투어 솔직후기",
                },
                "link_url": PRODUCT_LINK,
                "image_manifest": str(manifest.relative_to(run_dir)),
                "product_writing_provenance": provenance,
                "image_placements": [
                    {
                        "file": "image_1.jpg",
                        "manifest_index": 1,
                        "region": "intro",
                        "after_paragraph": 0,
                    },
                    {
                        "file": "image_2.jpg",
                        "manifest_index": 2,
                        "region": "intro",
                        "after_paragraph": 0,
                    },
                    {
                        "file": "image_3.jpg",
                        "manifest_index": 3,
                        "region": "section",
                        "section_index": 2,
                        "after_paragraph": 0,
                    },
                ],
                "intro": [
                    "시부모님과 아이 둘을 함께 데리고 가는 일정이라 이동이 편한지를 먼저 봤어요.",
                    "배를 타는 시간과 쉬는 시간을 같이 확인하니 가족 일정으로 고르기 수월했어요.",
                ],
                "sections": [
                    {
                        "heading": "가족 여행으로 고른 이유",
                        "paragraphs": [
                            "아이들은 바다를 즐기고 부모님은 무리 없이 쉴 수 있는 구성이 마음에 들었어요."
                        ],
                    },
                    {
                        "heading": "코스와 이동 동선",
                        "paragraphs": [
                            "정해진 코스를 따라 움직여 따로 교통편을 맞출 일이 적다는 점이 편했어요."
                        ],
                    },
                    {
                        "heading": "예약 전에 확인한 내용",
                        "paragraphs": [
                            "출발 시간과 포함 사항을 미리 맞춰 두니 여러 세대가 함께 움직여도 덜 복잡했어요."
                        ],
                    },
                ],
            }
        ],
    }
    post = payload["posts"][0]  # type: ignore[index]
    semantic_post = dict(post)
    semantic_post.pop("product_writing_provenance", None)
    finalized_hash = hashlib.sha256(
        json.dumps(
            semantic_post, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    provenance["finalized_post_sha256"] = finalized_hash
    next_state = dict(state)
    next_state.update(
        {"status": "finalized", "finalized_post_sha256": finalized_hash}
    )
    history.update_run(run_dir, {"product_writing": next_state})
    return payload


def _generated_product_run(
    root: Path, name: str, *, attach_images: bool = True
) -> tuple[Path, Path, Path]:
    run_dir = root / name
    history.create_run(
        "나트랑 여행",
        "blog",
        1,
        "마이리얼트립 상품 원고",
        run_dir=run_dir,
        request_fields={
            "source_type": "myrealtrip_product",
            "channel": "naver",
            "product_url": PRODUCT_LINK,
            "main_keyword": "나트랑 여행",
            "subkeywords": ["보트투어"],
            "hook": "솔직후기",
            "companions": ["시부모님", "아이 둘"],
            "image_policy": {
                "mode": "all_unique_seller_product_images",
                "permission_confirmed": True,
                "max_images": 80,
            },
            "link_wait_ms": 2000,
        },
    )
    manifest = _write_product_manifest(run_dir)
    generated = root / f"{name}-generated.json"
    write_json(generated, _product_payload(run_dir, manifest))
    result = write_posts.write_posts(run_dir, generated)
    post_path = run_dir / result["posts"][0]["file"]
    if attach_images:
        for source in sorted((manifest.parent / "prepared").glob("image_*.jpg")):
            shutil.copy2(source, post_path.parent / source.name)
    return run_dir, manifest, post_path


class WritePostsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = IsolatedMatoEnvironment()
        self.env = self.environment.__enter__()

    def tearDown(self) -> None:
        self.environment.__exit__(None, None, None)

    def _write(self, count: int, *, remove_raw: bool = False):  # type: ignore[no-untyped-def]
        run_dir = self.env.root / f"write-{count}-{remove_raw}"
        create_run(run_dir, versions=count)
        input_path = self.env.root / f"generated-{count}-{remove_raw}.json"
        write_json(input_path, generated_payload(count))
        return run_dir, write_posts.write_posts(run_dir, input_path, remove_raw=remove_raw)

    def test_writes_exact_requested_count_in_mato_format(self) -> None:
        run_dir, result = self._write(3)
        self.assertEqual(result["generated_count"], 3)
        files = sorted((run_dir / "posts").rglob("*_함축.txt"))
        self.assertEqual(len(files), 3)
        for path in files:
            text = path.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("제목을입력해주세요1: "))
            self.assertEqual(text.count("본문2:"), 1)
            self.assertEqual(sum(line.startswith("ㅂㅂㅂ") for line in text.splitlines()), 3)
            self.assertNotIn("![", text)
            self.assertNotIn("<img", text.casefold())
        state = history.load_run(run_dir)
        self.assertEqual(state["generation"]["generated_count"], 3)
        self.assertEqual(len(state["generation"]["posts"]), 3)

    def test_rejects_count_mismatch_before_writing_posts(self) -> None:
        run_dir = self.env.root / "count-mismatch"
        create_run(run_dir, versions=2)
        input_path = self.env.root / "one.json"
        write_json(input_path, generated_payload(1))
        with self.assertRaisesRegex(ValueError, "expected 2 posts, received 1"):
            write_posts.write_posts(run_dir, input_path)
        self.assertFalse((run_dir / "posts").exists())

    def test_rejects_duplicate_titles(self) -> None:
        run_dir = self.env.root / "duplicate-title"
        create_run(run_dir, versions=2)
        payload = generated_payload(2)
        payload["posts"][1]["title"] = payload["posts"][0]["title"]  # type: ignore[index]
        input_path = self.env.root / "duplicates.json"
        write_json(input_path, payload)
        with self.assertRaisesRegex(ValueError, "duplicate title"):
            write_posts.write_posts(run_dir, input_path)
        self.assertFalse((run_dir / "posts").exists())

    def test_invalid_later_post_leaves_existing_generation_and_state_unchanged(self) -> None:
        run_dir, _ = self._write(2)
        before_files = _post_snapshot(run_dir)
        before_state = history.load_run(run_dir)
        payload = generated_payload(2)
        second = payload["posts"][1]  # type: ignore[index]
        second["sections"][2]["heading"] = ""  # type: ignore[index]
        input_path = self.env.root / "invalid-later.json"
        write_json(input_path, payload)

        with self.assertRaisesRegex(ValueError, "heading must not be empty"):
            write_posts.write_posts(run_dir, input_path)

        self.assertEqual(_post_snapshot(run_dir), before_files)
        self.assertEqual(history.load_run(run_dir), before_state)
        self.assertFalse(any(run_dir.glob(".mato-generation-*")))

    def test_staging_failure_leaves_existing_generation_and_state_unchanged(self) -> None:
        run_dir, _ = self._write(2)
        before_files = _post_snapshot(run_dir)
        before_state = history.load_run(run_dir)
        input_path = self.env.root / "staging-failure.json"
        write_json(input_path, generated_payload(2))
        real_write = write_posts.atomic_write_text
        staged_post_writes = 0

        def fail_second_staged_post(path: Path, text: str) -> None:
            nonlocal staged_post_writes
            candidate = Path(path)
            if ".mato-generation-" in str(candidate) and candidate.name.endswith("_함축.txt"):
                staged_post_writes += 1
                if staged_post_writes == 2:
                    raise OSError("injected staging failure")
            real_write(candidate, text)

        with mock.patch.object(write_posts, "atomic_write_text", side_effect=fail_second_staged_post):
            with self.assertRaisesRegex(OSError, "injected staging failure"):
                write_posts.write_posts(run_dir, input_path)

        self.assertEqual(_post_snapshot(run_dir), before_files)
        self.assertEqual(history.load_run(run_dir), before_state)
        self.assertFalse(any(run_dir.glob(".mato-generation-*")))

    def test_state_commit_failure_rolls_back_replaced_files(self) -> None:
        run_dir, _ = self._write(1)
        before_files = _post_snapshot(run_dir)
        before_state = history.load_run(run_dir)
        payload = generated_payload(1)
        payload["posts"][0]["title"] = "교체 예정 제목"  # type: ignore[index]
        input_path = self.env.root / "commit-failure.json"
        write_json(input_path, payload)

        with mock.patch.object(write_posts, "update_run", side_effect=OSError("state write failed")):
            with self.assertRaisesRegex(OSError, "state write failed"):
                write_posts.write_posts(run_dir, input_path)

        self.assertEqual(_post_snapshot(run_dir), before_files)
        self.assertEqual(history.load_run(run_dir), before_state)
        self.assertFalse(any(run_dir.glob(".mato-*-backup-*")))

    def test_purge_raw_removes_only_ephemeral_source_file(self) -> None:
        run_dir = self.env.root / "purge"
        create_run(run_dir, versions=1)
        raw = run_dir / "sources" / ".analysis-input.json"
        durable = run_dir / "sources" / "sources.json"
        write_json(raw, {"sources": [{"text": "경쟁 글 전체 원문"}]})
        write_json(durable, {"sources": [{"title": "제목", "url": "https://example.test"}]})
        input_path = self.env.root / "purge-input.json"
        write_json(input_path, generated_payload(1))
        result = write_posts.write_posts(run_dir, input_path, remove_raw=True)
        self.assertTrue(result["raw_source_removed"])
        self.assertFalse(raw.exists())
        self.assertTrue(durable.exists())
        self.assertEqual(history.load_run(run_dir)["events"][-1]["stage"], "source_raw_purged")

    def test_product_writer_preserves_title_link_and_deterministic_image_placements(self) -> None:
        run_dir, manifest, post_path = _generated_product_run(
            self.env.root, "product-writer", attach_images=False
        )
        parsed = validate_posts.parse_mato_text(post_path.read_text(encoding="utf-8"))
        meaningful = [line.strip() for line in parsed["body2_lines"] if line.strip()]
        self.assertEqual(meaningful[0], PRODUCT_LINK)
        self.assertEqual(meaningful[-1], PRODUCT_LINK)
        self.assertEqual(parsed["body"].count(PRODUCT_LINK), 2)
        self.assertEqual(
            [match[0] for match in validate_posts.IMAGE_TAG_RE.findall(parsed["body"])],
            ["image_1.jpg", "image_2.jpg", "image_3.jpg"],
        )
        self.assertEqual(
            meaningful[1:4],
            [
                "[image_1.jpg]",
                "[image_2.jpg]",
                "시부모님과 아이 둘을 함께 데리고 가는 일정이라 이동이 편한지를 먼저 봤어요.",
            ],
        )
        for image_name in ("image_1.jpg", "image_2.jpg", "image_3.jpg"):
            self.assertEqual(meaningful.count(f"[{image_name}]"), 1)

        state = history.load_run(run_dir)
        record = state["generation"]["posts"][0]
        self.assertEqual(record["link_url"], PRODUCT_LINK)
        self.assertEqual(record["title_plan"]["main_terms"], ["나트랑", "여행"])
        self.assertEqual(record["title_plan"]["subkeywords"], ["보트투어"])
        self.assertEqual(record["image_manifest"], str(manifest.relative_to(run_dir)))
        self.assertEqual(
            [item["file"] for item in record["image_placements"]],
            ["image_1.jpg", "image_2.jpg", "image_3.jpg"],
        )

    def test_product_run_cannot_bypass_title_and_seller_image_contract(self) -> None:
        run_dir = self.env.root / "product-generic-bypass"
        history.create_run(
            "나트랑 여행",
            "product",
            1,
            "상품 원고",
            run_dir=run_dir,
            request_fields={
                "source_type": "myrealtrip_product",
                "channel": "naver",
                "product_url": PRODUCT_LINK,
                "main_keyword": "나트랑 여행",
                "image_policy": {
                    "mode": "all_unique_seller_product_images",
                    "permission_confirmed": True,
                    "max_images": 80,
                },
            },
        )
        source = self.env.root / "product-generic-bypass.json"
        write_json(source, generated_payload(1))
        with self.assertRaisesRegex(ValueError, "require a title_plan"):
            write_posts.write_posts(run_dir, source)
        self.assertFalse((run_dir / "posts").exists())

    def test_product_writer_rejects_title_and_image_layout_contract_violations(self) -> None:
        cases = (
            "missing-title-hook",
            "nonsequential-images",
            "three-image-group",
            "image-before-final-url",
        )
        for case in cases:
            with self.subTest(case=case):
                run_dir = self.env.root / case
                history.create_run(
                    "나트랑 여행",
                    "blog",
                    1,
                    "상품 원고",
                    run_dir=run_dir,
                    request_fields={
                        "source_type": "myrealtrip_product",
                        "channel": "naver",
                        "product_url": PRODUCT_LINK,
                        "main_keyword": "나트랑 여행",
                    },
                )
                manifest = _write_product_manifest(run_dir)
                payload = _product_payload(run_dir, manifest)
                post = payload["posts"][0]  # type: ignore[index]
                if case == "missing-title-hook":
                    post["title"] = "나트랑 여행 보트투어"  # type: ignore[index]
                    expected_error = "does not match title_plan.exact_title"
                elif case == "nonsequential-images":
                    placements = post["image_placements"]  # type: ignore[index]
                    placements[0]["file"] = "image_2.jpg"  # type: ignore[index]
                    expected_error = "exactly once in sequence"
                elif case == "three-image-group":
                    placements = post["image_placements"]  # type: ignore[index]
                    for placement in placements:  # type: ignore[union-attr]
                        placement.update(
                            {
                                "region": "intro",
                                "section_index": 0,
                                "after_paragraph": 1,
                            }
                        )
                    expected_error = "at most two consecutive"
                else:
                    placements = post["image_placements"]  # type: ignore[index]
                    placements[2].update(  # type: ignore[index]
                        {
                            "region": "section",
                            "section_index": 3,
                            "after_paragraph": 1,
                        }
                    )
                    expected_error = "must be followed immediately by a prose paragraph"
                source = self.env.root / f"{case}.json"
                write_json(source, payload)
                with self.assertRaisesRegex(ValueError, expected_error):
                    write_posts.write_posts(run_dir, source)
                self.assertFalse((run_dir / "posts").exists())

    def test_product_writer_rejects_an_extra_inline_link_occurrence(self) -> None:
        run_dir = self.env.root / "extra-inline-link"
        history.create_run(
            "나트랑 여행",
            "blog",
            1,
            "상품 원고",
            run_dir=run_dir,
            request_fields={
                "source_type": "myrealtrip_product",
                "channel": "naver",
                "product_url": PRODUCT_LINK,
                "main_keyword": "나트랑 여행",
            },
        )
        manifest = _write_product_manifest(run_dir)
        payload = _product_payload(run_dir, manifest)
        post = payload["posts"][0]  # type: ignore[index]
        post["intro"][0] += f" 예약 주소는 {PRODUCT_LINK}입니다."  # type: ignore[index]
        source = self.env.root / "extra-inline-link.json"
        write_json(source, payload)
        with self.assertRaisesRegex(ValueError, "first and last non-empty"):
            write_posts.write_posts(run_dir, source)


class ValidatePostsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = IsolatedMatoEnvironment()
        self.env = self.environment.__enter__()

    def tearDown(self) -> None:
        self.environment.__exit__(None, None, None)

    def _generated_run(self, name: str, count: int) -> Path:
        run_dir = self.env.root / name
        create_run(run_dir, versions=count)
        source = self.env.root / f"{name}.json"
        write_json(source, generated_payload(count))
        write_posts.write_posts(run_dir, source)
        return run_dir

    def test_validation_rejects_a_generic_generation_relabelled_as_product(self) -> None:
        run_dir = self._generated_run("product-validation-bypass", 1)

        def relabel(state: dict[str, object]) -> None:
            request = state["request"]  # type: ignore[index]
            request.update(  # type: ignore[union-attr]
                {
                    "source_type": "myrealtrip_product",
                    "channel": "naver",
                    "product_url": PRODUCT_LINK,
                    "image_policy": {
                        "mode": "all_unique_seller_product_images",
                        "permission_confirmed": True,
                        "max_images": 80,
                    },
                }
            )

        history.update_run(run_dir, relabel)
        result = validate_posts.validate_run(run_dir, 1)
        self.assertFalse(result["ok"])
        self.assertTrue(any("requires a title_plan" in error for error in result["errors"]))
        self.assertTrue(
            any(
                "requires image_manifest and image_placements" in error
                for error in result["errors"]
            )
        )

    def test_valid_distinct_posts_pass_and_update_run(self) -> None:
        run_dir = self._generated_run("valid", 3)
        result = validate_posts.validate_run(run_dir, 3)
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(result["file_count"], 3)
        self.assertEqual(len(result["similarities"]), 3)
        self.assertEqual(len(result["file_manifest"]), 3)
        for item in result["file_manifest"]:
            path = run_dir / item["file"]
            self.assertEqual(item["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        state = history.load_run(run_dir)
        self.assertEqual(state["validation"]["status"], "passed")
        self.assertEqual(
            validate_posts.verify_validation_manifest(run_dir, state),
            validate_posts.discover_post_files(run_dir, state),
        )

    def test_product_validation_binds_url_manifest_and_every_local_image(self) -> None:
        run_dir, manifest, post_path = _generated_product_run(
            self.env.root, "valid-product"
        )
        result = validate_posts.validate_run(run_dir, 1)
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(len(result["asset_manifest"]), 3)
        self.assertEqual(
            result["asset_manifest_sha256"],
            validate_posts._structured_sha256(result["asset_manifest"]),
        )
        for index, item in enumerate(result["asset_manifest"], start=1):
            self.assertEqual(item["post_file"], str(post_path.relative_to(run_dir)))
            self.assertEqual(item["file"], str((post_path.parent / f"image_{index}.jpg").relative_to(run_dir)))
            self.assertEqual(item["source_manifest"], str(manifest.relative_to(run_dir)))
            self.assertEqual(item["manifest_index"], index)
            self.assertEqual(item["classification"], "product")
            self.assertIn(item["source_role"], {"gallery", "introduction", "itinerary"})

        state = history.load_run(run_dir)
        self.assertEqual(
            validate_posts.verify_validation_manifest(run_dir, state),
            [post_path.resolve()],
        )

    def test_product_validation_rejects_txt_title_changed_after_generation(self) -> None:
        run_dir, _manifest, post_path = _generated_product_run(
            self.env.root, "product-title-tamper"
        )
        text = post_path.read_text(encoding="utf-8")
        post_path.write_text(
            text.replace(
                "제목을입력해주세요1: 나트랑 여행 보트투어 솔직후기",
                "제목을입력해주세요1: 나트랑 여행 보트투어 임의변경",
                1,
            ),
            encoding="utf-8",
        )
        result = validate_posts.validate_run(run_dir, 1)
        self.assertFalse(result["ok"])
        self.assertTrue(any("title_plan.exact_title" in error for error in result["errors"]))

    def test_product_manifest_validation_allows_only_a_genuinely_absent_itinerary(self) -> None:
        run_dir, manifest, _post_path = _generated_product_run(
            self.env.root, "product-no-itinerary"
        )
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        payload["accepted"] = payload["accepted"][:2]
        payload["image_count"] = 2
        payload["candidate_count"] = 2
        payload["unique_url_count"] = 2
        proof = payload["collection_proof"]
        proof["candidate_count"] = 2
        proof["stable_candidate_counts"] = [2, 2]
        proof["available_sections"].remove("ITINERARIES")
        proof["expanded_sections"].remove("ITINERARIES")
        write_json(manifest, payload)
        request = history.load_run(run_dir)["request"]
        _relative, _digest, rows = validate_posts._load_product_manifest(
            run_dir, manifest.relative_to(run_dir), request
        )
        self.assertEqual(
            [row["source_role"] for row in rows], ["gallery", "introduction"]
        )

        payload["accepted"][1]["source_role"] = "itinerary"
        payload["accepted"][1]["role"] = "itinerary"
        write_json(manifest, payload)
        with self.assertRaisesRegex(ValueError, "no matching available"):
            validate_posts._load_product_manifest(
                run_dir, manifest.relative_to(run_dir), request
            )

    def test_product_validation_rejects_body_changed_after_truthful_finalization(self) -> None:
        run_dir, _manifest, post_path = _generated_product_run(
            self.env.root, "product-body-tamper"
        )
        text = post_path.read_text(encoding="utf-8")
        post_path.write_text(
            text.replace(
                "정해진 코스를 따라 움직여 따로 교통편을 맞출 일이 적다는 점이 편했어요.",
                "제가 직접 구매해 다녀왔고 아이 둘과 시부모님 모두 100% 만족했어요.",
                1,
            ),
            encoding="utf-8",
        )
        result = validate_posts.validate_run(run_dir, 1)
        self.assertFalse(result["ok"])
        self.assertTrue(
            any("changed after truthful finalization" in error for error in result["errors"]),
            result["errors"],
        )

    def test_product_validation_rejects_missing_extra_or_misplaced_links(self) -> None:
        mutations = {
            "missing-first": lambda text: text.replace(
                f"본문2:\n{PRODUCT_LINK}\n", "본문2:\n", 1
            ),
            "extra-inline": lambda text: text.replace(
                "가족 일정으로 고르기 수월했어요.",
                f"가족 일정으로 고르기 수월했어요. {PRODUCT_LINK}",
                1,
            ),
            "after-final": lambda text: text.rstrip() + "\n마지막 링크 뒤의 문장\n",
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                run_dir, _manifest, post_path = _generated_product_run(
                    self.env.root, f"product-link-{name}"
                )
                text = post_path.read_text(encoding="utf-8")
                post_path.write_text(mutate(text), encoding="utf-8")
                result = validate_posts.validate_run(run_dir, 1)
                self.assertFalse(result["ok"])
                self.assertTrue(
                    any("product link_url" in error for error in result["errors"]),
                    result["errors"],
                )

    def test_product_validation_rejects_image_layout_contract_violations(self) -> None:
        mutations = ("embedded", "three-consecutive", "moved-with-order-preserved")
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                run_dir, _manifest, post_path = _generated_product_run(
                    self.env.root, f"product-layout-{mutation}"
                )
                text = post_path.read_text(encoding="utf-8")
                if mutation == "embedded":
                    text = text.replace("[image_1.jpg]", "[image_1.jpg] 사진 설명", 1)
                    expected = "standalone lines"
                elif mutation == "three-consecutive":
                    for image_name in ("image_1.jpg", "image_2.jpg", "image_3.jpg"):
                        text = text.replace(f"\n[{image_name}]", "", 1)
                    anchor = "가족 일정으로 고르기 수월했어요."
                    text = text.replace(
                        anchor,
                        anchor
                        + "\n[image_1.jpg]\n[image_2.jpg]\n[image_3.jpg]",
                        1,
                    )
                    expected = "at most two consecutive"
                else:
                    text = text.replace("\n[image_3.jpg]", "", 1)
                    text = text.replace(
                        "ㅂㅂㅂ가족 여행으로 고른 이유",
                        "ㅂㅂㅂ가족 여행으로 고른 이유\n[image_3.jpg]",
                        1,
                    )
                    expected = "placement layout changed"
                post_path.write_text(text, encoding="utf-8")
                result = validate_posts.validate_run(run_dir, 1)
                self.assertFalse(result["ok"])
                self.assertTrue(
                    any(expected in error for error in result["errors"]),
                    result["errors"],
                )

    def test_product_validation_rejects_review_signals_and_manifest_count_mismatch(self) -> None:
        mutations = ("review-path", "review-field", "count")
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                run_dir, manifest, _post_path = _generated_product_run(
                    self.env.root, f"product-manifest-{mutation}"
                )
                payload = json.loads(manifest.read_text(encoding="utf-8"))
                if mutation == "review-path":
                    payload["accepted"][0]["source_url"] = (
                        "https://cdn.example.test/review/image_1.jpg"
                    )
                    expected = "review-photo signal"
                elif mutation == "review-field":
                    payload["accepted"][0]["review_origin"] = "customer-photo"
                    expected = "review-photo signal"
                else:
                    payload["image_count"] = 4
                    expected = "image_count does not match"
                write_json(manifest, payload)
                result = validate_posts.validate_run(run_dir, 1)
                self.assertFalse(result["ok"])
                self.assertTrue(
                    any(expected in error for error in result["errors"]),
                    result["errors"],
                )

    def test_product_validation_rejects_local_or_source_image_integrity_changes(self) -> None:
        from PIL import Image

        mutations = ("local-hash", "source-hash", "declared-dimensions")
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                run_dir, manifest, post_path = _generated_product_run(
                    self.env.root, f"product-integrity-{mutation}"
                )
                if mutation == "local-hash":
                    Image.new("RGB", (33, 25), color=(255, 0, 0)).save(
                        post_path.parent / "image_1.jpg", format="JPEG", quality=91
                    )
                    expected = "SHA256 does not match"
                elif mutation == "source-hash":
                    Image.new("RGB", (33, 25), color=(0, 255, 0)).save(
                        manifest.parent / "prepared" / "image_1.jpg",
                        format="JPEG",
                        quality=91,
                    )
                    expected = "prepared SHA256 does not match"
                else:
                    payload = json.loads(manifest.read_text(encoding="utf-8"))
                    payload["accepted"][0]["width"] += 1
                    write_json(manifest, payload)
                    expected = "prepared dimensions do not match"
                result = validate_posts.validate_run(run_dir, 1)
                self.assertFalse(result["ok"])
                self.assertTrue(
                    any(expected in error for error in result["errors"]),
                    result["errors"],
                )

    def test_product_asset_manifest_rejects_image_changed_after_validation(self) -> None:
        from PIL import Image

        run_dir, _manifest, post_path = _generated_product_run(
            self.env.root, "product-changed-after-validation"
        )
        result = validate_posts.validate_run(run_dir, 1)
        self.assertTrue(result["ok"], result["errors"])
        state = history.load_run(run_dir)
        Image.new("RGB", (33, 25), color=(240, 20, 20)).save(
            post_path.parent / "image_1.jpg", format="JPEG", quality=91
        )
        with self.assertRaisesRegex(ValueError, "changed after validation"):
            validate_posts.verify_validation_manifest(run_dir, state)

    def test_validation_manifest_rejects_post_changed_after_validation(self) -> None:
        run_dir = self._generated_run("changed-after-validation", 1)
        validate_posts.validate_run(run_dir, 1)
        state = history.load_run(run_dir)
        path = validate_posts.discover_post_files(run_dir, state)[0]
        path.write_text(path.read_text(encoding="utf-8") + "\n수정된 내용\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "changed after validation"):
            validate_posts.verify_validation_manifest(run_dir, state)

    def test_relative_parent_path_in_generation_is_rejected(self) -> None:
        run_dir = self._generated_run("parent-path", 1)
        state = history.load_run(run_dir)
        generation = dict(state["generation"])
        posts = [dict(item) for item in generation["posts"]]
        posts[0]["file"] = str(Path("posts") / ".." / "outside_함축.txt")
        generation["posts"] = posts
        history.update_run(run_dir, {"generation": generation})

        with self.assertRaisesRegex(ValueError, "unsafe"):
            validate_posts.validate_run(run_dir, 1)

    def test_symlink_escape_from_posts_is_rejected(self) -> None:
        run_dir = self._generated_run("symlink-escape", 1)
        state = history.load_run(run_dir)
        path = validate_posts.discover_post_files(run_dir, state)[0]
        outside = self.env.root / "outside_함축.txt"
        outside.write_bytes(path.read_bytes())
        path.unlink()
        try:
            path.symlink_to(outside)
        except OSError as exc:
            self.skipTest(f"file symlinks are unavailable: {exc}")

        with self.assertRaisesRegex(ValueError, "escapes run/posts"):
            validate_posts.validate_run(run_dir, 1)

    def test_expected_count_mismatch_fails(self) -> None:
        run_dir = self._generated_run("count", 1)
        result = validate_posts.validate_run(run_dir, 2)
        self.assertFalse(result["ok"])
        self.assertTrue(any("expected 2 files, found 1" in error for error in result["errors"]))

    def test_missing_headings_fail(self) -> None:
        run_dir = self._generated_run("heading", 1)
        path = next((run_dir / "posts").rglob("*_함축.txt"))
        text = path.read_text(encoding="utf-8").replace("ㅂㅂㅂ", "", 1)
        path.write_text(text, encoding="utf-8")
        result = validate_posts.validate_run(run_dir, 1)
        self.assertFalse(result["ok"])
        self.assertTrue(any("at least 3" in error for error in result["errors"]))

    def test_similar_bodies_fail_at_configured_threshold(self) -> None:
        run_dir = self._generated_run("similarity", 2)
        paths = sorted((run_dir / "posts").rglob("*_함축.txt"))
        first = validate_posts.parse_mato_text(paths[0].read_text(encoding="utf-8"))
        second = validate_posts.parse_mato_text(paths[1].read_text(encoding="utf-8"))
        paths[1].write_text(
            f"제목을입력해주세요1: {second['title']}\n\n본문2:\n{first['body']}\n",
            encoding="utf-8",
        )
        result = validate_posts.validate_run(run_dir, 2, threshold=0.78)
        self.assertFalse(result["ok"])
        self.assertTrue(any("drafts are too similar" in error for error in result["errors"]))

    def test_non_mato_image_tag_forms_are_rejected(self) -> None:
        image_fragments = ("![사진](photo.jpg)", '<img src="photo.jpg">', "[이미지1]")
        for index, fragment in enumerate(image_fragments):
            with self.subTest(fragment=fragment):
                run_dir = self._generated_run(f"image-{index}", 1)
                path = next((run_dir / "posts").rglob("*_함축.txt"))
                path.write_text(path.read_text(encoding="utf-8") + f"\n{fragment}\n", encoding="utf-8")
                result = validate_posts.validate_run(run_dir, 1)
                self.assertFalse(result["ok"])
                self.assertTrue(any("only [image_N.jpg]" in error for error in result["errors"]))

    def test_mato_image_tags_require_matching_sequential_files(self) -> None:
        run_dir = self._generated_run("mato-images", 1)
        path = next((run_dir / "posts").rglob("*_함축.txt"))
        path.write_text(
            path.read_text(encoding="utf-8") + "\n[image_1.jpg]\n[image_2.jpg]\n",
            encoding="utf-8",
        )
        (path.parent / "image_1.jpg").write_bytes(b"one")
        failed = validate_posts.validate_run(run_dir, 1)
        self.assertFalse(failed["ok"])
        self.assertTrue(any("referenced image is missing" in error for error in failed["errors"]))

        (path.parent / "image_2.jpg").write_bytes(b"two")
        passed = validate_posts.validate_run(run_dir, 1)
        self.assertTrue(passed["ok"])

    def test_parser_requires_exact_markers(self) -> None:
        parsed = validate_posts.parse_mato_text(
            "제목을입력해주세요1: 테스트\n\n본문2:\nㅂㅂㅂ첫째\n내용"
        )
        self.assertEqual(parsed["title"], "테스트")
        self.assertEqual(parsed["body_marker_count"], 1)
        self.assertEqual(parsed["headings"], ["첫째"])

    def test_parser_preserves_body1_intro_and_body2_for_helper_upload(self) -> None:
        parsed = validate_posts.parse_mato_text(
            "제목을입력해주세요1: 영역 테스트\n\n"
            "본문1:\n첫 번째 영역\n\n"
            "인트로1:\n도입 영역\n\n"
            "본문2:\n소제목본문 제목\n본문 영역"
        )
        self.assertEqual(parsed["body1_lines"], ["첫 번째 영역"])
        self.assertEqual(parsed["intro_lines"], ["도입 영역"])
        self.assertEqual(parsed["body2_lines"], ["소제목본문 제목", "본문 영역"])
        self.assertEqual(parsed["body"], "소제목본문 제목\n본문 영역")
        self.assertEqual(parsed["headings"], ["본문 제목"])


if __name__ == "__main__":
    unittest.main()
