from __future__ import annotations

import sys
import unittest

from _support import SCRIPTS_DIR

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import product_title


class ProductTitleTests(unittest.TestCase):
    def test_generates_five_order_varied_hard_valid_candidates(self) -> None:
        main_keyword = "베트남 나트랑 투어"
        subkeywords = ["종류", "아이랑", "비용"]
        candidates = product_title.generate_title_candidates(
            main_keyword, subkeywords, "솔직후기"
        )
        self.assertEqual(len(candidates), 5)
        self.assertEqual(len(set(candidates)), 5)

        plan = product_title.build_title_plan(
            main_keyword,
            subkeywords,
            "솔직후기",
            product_text="나트랑 가족 호핑투어",
        )
        self.assertEqual([row["title"] for row in plan["candidates"]], candidates)
        for row in plan["candidates"]:
            self.assertIn(len(row["subkeywords"]), (1, 2))
            self.assertTrue(
                product_title.validate_title(
                    row["title"], main_keyword, row["subkeywords"], "솔직후기"
                )
            )
            for term in ("베트남", "나트랑", "투어"):
                self.assertIn(term, row["title"])

    def test_validation_rejects_missing_main_sub_or_hook(self) -> None:
        self.assertFalse(
            product_title.validate_title(
                "나트랑 투어 아이랑 솔직후기",
                "베트남 나트랑 투어",
                ["아이랑"],
                "솔직후기",
            )
        )
        self.assertFalse(
            product_title.validate_title(
                "베트남 나트랑 투어 솔직후기",
                "베트남 나트랑 투어",
                ["아이랑"],
                "솔직후기",
            )
        )
        self.assertFalse(
            product_title.validate_title(
                "베트남 나트랑 투어 아이랑",
                "베트남 나트랑 투어",
                ["아이랑"],
                "솔직후기",
            )
        )
        self.assertFalse(
            product_title.validate_title(
                "베트남 나트랑 투어 종류 비용 아이랑 솔직후기",
                "베트남 나트랑 투어",
                ["종류", "비용", "아이랑"],
                "솔직후기",
            )
        )

    def test_history_exact_duplicate_is_penalized_and_not_selected(self) -> None:
        initial = product_title.build_title_plan(
            "베트남 나트랑 투어", ["종류", "아이랑", "비용"], "솔직후기"
        )
        first_title = initial["selected_title"]
        repeated = product_title.build_title_plan(
            "베트남 나트랑 투어",
            ["종류", "아이랑", "비용"],
            "솔직후기",
            prior_titles=[first_title],
        )
        exact_row = next(row for row in repeated["candidates"] if row["title"] == first_title)
        self.assertEqual(exact_row["history_penalty"], -1_000)
        self.assertNotEqual(repeated["selected_title"], first_title)
        self.assertEqual(
            repeated,
            product_title.build_title_plan(
                "베트남 나트랑 투어",
                ["종류", "아이랑", "비용"],
                "솔직후기",
                prior_titles=[first_title],
            ),
        )

    def test_region_mismatch_helper_checks_city_before_shared_country(self) -> None:
        mismatch = product_title.region_mismatch_details(
            "베트남 나트랑 투어", "베트남 다낭 바나힐 투어"
        )
        self.assertTrue(mismatch["mismatch"])
        self.assertEqual(mismatch["reason"], "city")
        self.assertFalse(
            product_title.detect_region_mismatch(
                "베트남 나트랑 투어", "냐짱 가족 호핑 투어"
            )
        )
        with self.assertRaisesRegex(ValueError, "지역"):
            product_title.build_title_plan(
                "베트남 나트랑 투어",
                ["아이랑"],
                product_text="다낭 근교 일일 투어",
            )

    def test_default_hook_and_single_subkeyword_still_create_five(self) -> None:
        candidates = product_title.generate_title_candidates("나트랑 투어", ["비용"], "")
        self.assertEqual(len(candidates), 5)
        self.assertEqual(len(set(candidates)), 5)
        self.assertTrue(all("솔직후기" in title for title in candidates))


if __name__ == "__main__":
    unittest.main()
