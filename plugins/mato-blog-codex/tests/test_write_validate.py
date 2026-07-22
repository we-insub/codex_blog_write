from __future__ import annotations

import hashlib
import json
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


if __name__ == "__main__":
    unittest.main()
