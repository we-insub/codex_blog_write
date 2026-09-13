from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from _support import IsolatedMatoEnvironment, write_json
import history
import parse_request
import product_batch


class ProductBatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = IsolatedMatoEnvironment()
        self.env = self.environment.__enter__()

    def tearDown(self) -> None:
        self.environment.__exit__(None, None, None)

    def prepare(self, suffix: str = "프로필 2,1 작업") -> dict:
        return product_batch.prepare_batch(
            "CPA 링크 https://myrealt.rip/iZRp3d " + suffix,
            self.env.root / "batch",
        )

    def plans(self, batch: dict, *, duplicate: bool = False) -> dict:
        plans = {}
        for row in batch["children"]:
            path = Path(row["run_dir"])
            slot = row["profile_slot"]
            file = path / "article.txt"
            text = "공통 본문" if duplicate else f"프로필별 독립 원고 {slot}"
            file.write_text("제목을입력해주세요1: 제목\n본문2:\n" + text, encoding="utf-8")
            plans[str(path)] = {
                "run_dir": str(path), "run_id": path.name, "mode": "draft",
                "profiles": [slot], "assignments": [{"file": "article.txt", "profile_slot": slot}],
            }
        return plans

    def test_profile_count_overrides_versions_and_preserves_order(self) -> None:
        for phrase, slots in (
            ("프로필 2,1 작업 99개", [2, 1]),
            ("프로필 3개 작업", [1, 2, 3]),
            ("프로필3 작업", [3]),
            ("프로필1 프로필2 작업", [1, 2]),
            ("프로필2,2,1 작업", [2, 1]),
        ):
            for url in ("https://myrealt.rip/iZRp3d", "https://naver.me/FDc4FI1x"):
                with self.subTest(phrase=phrase, url=url):
                    request = parse_request.parse_request(url + " " + phrase)
                    self.assertEqual(request["profiles"], slots)
                    self.assertEqual(request["versions"], len(slots))

    def test_prepare_binds_one_child_to_each_profile(self) -> None:
        batch = self.prepare()
        self.assertEqual(batch["count"], 2)
        self.assertEqual([row["profile_slot"] for row in batch["children"]], [2, 1])
        parent = history.load_run(batch["run_dir"])
        self.assertEqual(parent["request"]["versions"], 2)
        for row in batch["children"]:
            child = history.load_run(row["run_dir"])
            request = child["request"]
            self.assertEqual(request["versions"], 1)
            self.assertEqual(request["profiles"], [row["profile_slot"]])
            self.assertEqual(request["product_url"], "https://myrealt.rip/iZRp3d")
            self.assertEqual(request["mode"], "draft")
            self.assertTrue(request["upload_requested"])
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.prepare()

    def test_prepare_preserves_generation_only_and_upload_rejects_it(self) -> None:
        batch = self.prepare("프로필1,2 업로드 하지마")
        with self.assertRaisesRegex(ValueError, "did not request upload"):
            product_batch.upload_batch(batch["run_dir"])

    def test_assignment_tampering_is_rejected(self) -> None:
        batch = self.prepare()
        child_path = batch["children"][0]["run_dir"]
        request = dict(history.load_run(child_path)["request"], profiles=[99])
        history.update_run(child_path, {"request": request})
        with self.assertRaisesRegex(ValueError, "profile/count"):
            product_batch.batch_status(batch["run_dir"])

    def test_preflight_has_no_external_writes_and_execution_is_serial(self) -> None:
        batch = self.prepare()
        plans = self.plans(batch)
        calls = []
        def build(path, slot, mode):
            calls.append(("plan", int(slot)))
            return plans[str(path)]
        def execute(plan, *, confirm):
            self.assertEqual(confirm, plan["run_id"])
            calls.append(("execute", plan["profiles"][0]))
            return {"ok": True}
        with patch.object(product_batch.upload, "build_upload_plan", side_effect=build), \
             patch.object(product_batch.upload, "save_upload_plan") as save, \
             patch.object(product_batch.upload, "execute_upload", side_effect=execute):
            result = product_batch.upload_batch(batch["run_dir"])
            self.assertEqual(len(result["plans"]), 2)
            save.assert_not_called()
            self.assertEqual(calls, [("plan", 2), ("plan", 1)])
            calls.clear()
            product_batch.upload_batch(batch["run_dir"], execute=True, confirm=batch["run_id"])
        self.assertEqual(calls, [("plan", 2), ("plan", 1), ("execute", 2), ("execute", 1)])
        self.assertEqual(history.load_run(batch["run_dir"])["status"], "completed")

    def test_failure_stops_before_next_profile_and_retains_children(self) -> None:
        batch = self.prepare()
        plans = self.plans(batch)
        with patch.object(product_batch.upload, "build_upload_plan", side_effect=lambda path, *_: plans[str(path)]), \
             patch.object(product_batch.upload, "save_upload_plan"), \
             patch.object(product_batch.upload, "execute_upload", side_effect=ValueError("login expired")) as execute:
            with self.assertRaisesRegex(ValueError, "login expired"):
                product_batch.upload_batch(batch["run_dir"], execute=True, confirm=batch["run_id"])
            self.assertEqual(execute.call_count, 1)
        self.assertEqual(history.load_run(batch["run_dir"])["status"], "failed")
        self.assertEqual(product_batch.batch_status(batch["run_dir"])["count"], 2)

    def test_duplicate_manuscripts_and_missing_preflight_stop_all_uploads(self) -> None:
        batch = self.prepare()
        plans = self.plans(batch, duplicate=True)
        with patch.object(product_batch.upload, "build_upload_plan", side_effect=lambda path, *_: plans[str(path)]), \
             patch.object(product_batch.upload, "execute_upload") as execute:
            with self.assertRaisesRegex(ValueError, "duplicate"):
                product_batch.upload_batch(batch["run_dir"], execute=True, confirm=batch["run_id"])
            execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
