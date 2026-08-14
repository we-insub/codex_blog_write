from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from _support import IsolatedMatoEnvironment, write_json

import history
import product_writing
import write_posts


class ProductWritingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = IsolatedMatoEnvironment()
        self.env = self.environment.__enter__()

    def tearDown(self) -> None:
        self.environment.__exit__(None, None, None)

    def _run(
        self,
        *,
        main_keyword: str = "다낭 호이안 투어",
        permission: bool = True,
    ) -> Path:
        run_dir = self.env.root / f"run-{len(list(self.env.root.glob('run-*')))}"
        history.create_run(
            main_keyword,
            "product",
            1,
            "마이리얼트립 상품 원고",
            run_dir=run_dir,
            request_fields={
                "source_type": "myrealtrip_product",
                "channel": "naver",
                "product_url": "https://myrealt.rip/iZRp3d",
                "main_keyword": main_keyword,
                "subkeywords": ["가족여행", "단독투어", "코스"],
                "hook": "솔직후기",
                "companions": ["시부모님", "아이 둘"],
                "experience_notes": ["차량 이동 중 아이가 잠들었을 때 편했다"],
                "image_policy": {
                    "mode": "all_unique_seller_product_images",
                    "permission_confirmed": permission,
                    "max_images": 80,
                },
                "link_wait_ms": 2_000,
            },
        )
        return run_dir

    def _manifest(self, run_dir: Path, roles: list[str]) -> tuple[Path, dict[str, object]]:
        path = run_dir / "sources" / "myrealtrip-product" / "manifest.json"
        accepted = [
            {
                "index": index,
                "classification": "product",
                "source_role": role,
                "role": role,
                "source_url": (
                    f"https://dry7pvlp22cox.cloudfront.net/mrt-images-prod/x/{index}.jpg"
                ),
                "file": f"prepared/image_{index}.jpg",
                "sha256": f"{index:064x}",
                "width": 1200,
                "height": 800,
            }
            for index, role in enumerate(roles, start=1)
        ]
        manifest: dict[str, object] = {
            "schema_version": 1,
            "kind": "myrealtrip_product_images",
            "source_url": "https://myrealt.rip/iZRp3d",
            "canonical_url": "https://experiences.myrealtrip.com/products/3795277",
            "product_id": "3795277",
            "image_policy": {
                "seller_only": True,
                "review_images_excluded": True,
                "recommendation_images_excluded": True,
            },
            "collection_proof": {
                "page_url": "https://experiences.myrealtrip.com/products/3795277",
                "candidate_count": len(accepted),
                "gallery_visited_count": sum(
                    1 for item in accepted if item["source_role"] == "gallery"
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
                "stable_candidate_counts": [len(accepted), len(accepted)],
            },
            "candidate_count": len(accepted),
            "unique_url_count": len(accepted),
            "image_count": len(accepted),
            "accepted": accepted,
            "duplicates": [],
        }
        write_json(path, manifest)
        return path, manifest

    @staticmethod
    def _facts() -> dict[str, object]:
        return {
            "kind": "myrealtrip_product_facts",
            "canonical_url": "https://experiences.myrealtrip.com/products/3795277",
            "product_id": "3795277",
            "title": "호이안 풀코스 단독투어 바구니배 올드타운",
            "location": ["베트남", "다낭", "호이안"],
            "rating": 4.8,
            "review_count": 181,
            "price_text": "67,000원~",
            "tour_duration": "6시간 소요",
            "itineraries": [{"title": "코코넛마을"}, {"title": "호이안 올드타운"}],
            "included": ["전용 차량", "한국어 가이드"],
            "excluded": ["사공 팁 1달러"],
            "reviews": [
                {
                    "text": "부모님 걸음에 맞춰 주고 차량 이동이 편했습니다.",
                    "tags": ["가족과", "부모님과"],
                },
                {
                    "text": "가족과 함께 갔는데 한국어 가이드가 친절했고 사진도 잘 찍어 줬어요.",
                    "tags": ["가족과"],
                },
            ],
        }

    @staticmethod
    def _visuals(count: int) -> dict[str, object]:
        return {
            "images": [
                {"index": index, "visual_summary": f"업체 제공 상품 장면 {index}"}
                for index in range(1, count + 1)
            ]
        }

    @staticmethod
    def _post(paragraphs_per_section: int = 2) -> dict[str, object]:
        roles = (
            ("family_benefits", "가족과 선택한 이유"),
            ("product_details", "포함 구성과 이용 팁"),
            ("itinerary", "코스와 일정"),
            ("booking_checks", "예약 전 체크"),
        )
        return {
            "title": "모델이 임의로 쓴 제목",
            "intro": [
                "평점은 4.8점이며 후기 수는 181개이고 가격은 67,000원~, 일정은 6시간 소요로 안내됩니다."
            ],
            "sections": [
                {
                    "role": role,
                    "heading": heading,
                    "paragraphs": [
                        (
                            "포함 사항에는 전용 차량이 있습니다."
                            if role == "product_details" and index == 1
                            else "코스에는 코코넛마을이 포함됩니다."
                            if role == "itinerary" and index == 1
                            else "예약 전 불포함 사공 팁 1달러를 확인합니다."
                            if role == "booking_checks" and index == 1
                            else f"{heading}에 관한 확인된 내용을 정리합니다 {index}."
                        )
                        for index in range(1, paragraphs_per_section + 1)
                    ],
                }
                for role, heading in roles
            ],
            "fact_claims": [
                {
                    "field": "rating",
                    "claim": "평점은 4.8점이며 후기 수는 181개이고 가격은 67,000원~, 일정은 6시간 소요로 안내됩니다.",
                    "evidence_values": ["4.8"],
                    "section_role": "intro",
                },
                {
                    "field": "review_count",
                    "claim": "평점은 4.8점이며 후기 수는 181개이고 가격은 67,000원~, 일정은 6시간 소요로 안내됩니다.",
                    "evidence_values": ["181"],
                    "section_role": "intro",
                },
                {
                    "field": "price",
                    "claim": "평점은 4.8점이며 후기 수는 181개이고 가격은 67,000원~, 일정은 6시간 소요로 안내됩니다.",
                    "evidence_values": ["67,000원~"],
                    "section_role": "intro",
                },
                {
                    "field": "duration",
                    "claim": "평점은 4.8점이며 후기 수는 181개이고 가격은 67,000원~, 일정은 6시간 소요로 안내됩니다.",
                    "evidence_values": ["6시간 소요"],
                    "section_role": "intro",
                },
                {
                    "field": "included",
                    "claim": "포함 사항에는 전용 차량이 있습니다.",
                    "evidence_values": ["전용 차량"],
                    "section_role": "product_details",
                },
                {
                    "field": "itinerary",
                    "claim": "코스에는 코코넛마을이 포함됩니다.",
                    "evidence_values": ["코코넛마을"],
                    "section_role": "itinerary",
                },
                {
                    "field": "excluded",
                    "claim": "예약 전 불포함 사공 팁 1달러를 확인합니다.",
                    "evidence_values": ["사공 팁 1달러"],
                    "section_role": "booking_checks",
                },
            ],
        }

    def test_review_aggregation_retains_counts_and_hashes_not_sentences(self) -> None:
        reviews = self._facts()["reviews"]
        result = product_writing.aggregate_review_themes(reviews)
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertEqual(result["sampled_review_count"], 2)
        self.assertEqual(len(result["review_hashes"]), 2)
        self.assertNotIn("부모님 걸음에 맞춰", serialized)
        self.assertNotIn("한국어 가이드가 친절", serialized)
        self.assertTrue(any(row["theme"] == "가족 동행" for row in result["themes"]))

    def test_booking_evidence_uses_substantive_clauses_not_policy_headers(self) -> None:
        facts = self._facts()
        facts["essentials"] = [
            "📌 [추가금 안내] ★ 외곽 숙소 픽업은 추가금 $20/팀이 발생합니다. "
            "✅ 우천시 투어 안내 ✅ 비가 아주 많이 오면 안전상 배 탑승이 중단될 수 있습니다. ",
            "해양 동·식물에 의해 다칠 수 있으니 아쿠아슈즈를 준비하고 "
            "개별 여행자 보험 가입을 권장합니다.",
        ]
        facts["refund_policy"] = [
            "취소 규정 안내 - 여행시작 3일 전부터 당일까지는 취소/환불 불가"
        ]
        evidence = product_writing._fact_evidence({"product": facts})
        self.assertNotIn("[추가금 안내]", evidence["additional_conditions"])
        self.assertNotIn("우천시 투어 안내", evidence["safety_conditions"])
        self.assertTrue(
            any("$20/팀" in value for value in evidence["additional_conditions"])
        )
        self.assertTrue(any("안전상" in value for value in evidence["safety_conditions"]))
        self.assertTrue(
            any("아쿠아슈즈" in value for value in evidence["safety_conditions"])
        )
        self.assertTrue(any("환불 불가" in value for value in evidence["refund_policy"]))

    def test_fact_and_experience_claims_reject_new_numbers_people_and_actions(self) -> None:
        brief = {
            "product": self._facts(),
            "experience": {"notes": ["차량 이동 중 아이가 잠들었을 때 편했다"]},
        }
        factual = self._post(2)
        wrong = (
            "평점 4.8, 실제 1점이고 후기 181개, 가격 67,000원~, 일정은 6시간 소요입니다."
        )
        factual["intro"] = [wrong]
        for claim in factual["fact_claims"][:4]:  # type: ignore[index]
            claim["claim"] = wrong
        with self.assertRaisesRegex(ValueError, "unsupported or negated value"):
            product_writing._validate_fact_claims(factual, brief)

        experienced = self._post(2)
        fabricated = "차량 이동 중 시부모님이 춤추며 너무 만족했다."
        experienced["intro"].append(fabricated)  # type: ignore[union-attr]
        experienced["experience_claims"] = [
            {"claim": fabricated, "evidence_note_indexes": [1]}
        ]
        with self.assertRaisesRegex(ValueError, "adds a person, action, or detail"):
            product_writing._validate_experience_claims(experienced, brief)

    def test_claims_reject_negated_inclusions_and_appended_fake_benefits(self) -> None:
        brief = {
            "product": self._facts(),
            "experience": {"notes": ["차량 이동 중 아이가 잠들었을 때 편했다"]},
        }
        factual = self._post(2)
        fake_fact = "전용 차량은 포함되지 않으며 숙소 무료 업그레이드도 제공합니다."
        factual["sections"][1]["paragraphs"][0] = fake_fact  # type: ignore[index]
        included_claim = next(  # type: ignore[arg-type]
            row for row in factual["fact_claims"] if row["field"] == "included"
        )
        included_claim["claim"] = fake_fact
        with self.assertRaisesRegex(ValueError, "polarity|unsupported benefit"):
            product_writing._validate_fact_claims(factual, brief)

        numeric = self._post(2)
        fake_numeric = (
            "평점은 4.8점, 후기 수 181개, 가격 67,000원~, "
            "일정 6시간 소요이고 무료 마사지도 제공됩니다."
        )
        numeric["intro"] = [fake_numeric]
        for row in numeric["fact_claims"][:4]:  # type: ignore[index]
            row["claim"] = fake_numeric
        with self.assertRaisesRegex(ValueError, "unsupported product details"):
            product_writing._validate_fact_claims(numeric, brief)

        for fabricated in (
            "저희는 차량 이동 중 아이가 잠들었을 때 편했고 무료 마사지까지 받았어요.",
            "저희는 차량 이동 중 아이가 잠들었을 때 편했고 호텔 객실도 무료 업그레이드받았어요.",
        ):
            experienced = self._post(2)
            experienced["intro"].append(fabricated)  # type: ignore[union-attr]
            experienced["experience_claims"] = [
                {"claim": fabricated, "evidence_note_indexes": [1]}
            ]
            with self.assertRaisesRegex(ValueError, "unsupported clause|adds a person"):
                product_writing._validate_experience_claims(experienced, brief)

        for fabricated in (
            "저희는 차량 이동 중 아이가 잠들었을 때 편했고 물도 마셨어요.",
            "저희는 차량 이동 중 아이가 잠들었을 때 편했고 기념품도 샀어요.",
            "저희는 차량 이동 중 아이가 잠들었을 때 편했고 돌고래도 봤어요.",
        ):
            experienced = self._post(2)
            experienced["intro"].append(fabricated)  # type: ignore[union-attr]
            experienced["experience_claims"] = [
                {"claim": fabricated, "evidence_note_indexes": [1]}
            ]
            with self.assertRaisesRegex(ValueError, "unsupported clause"):
                product_writing._validate_experience_claims(experienced, brief)

    def test_unmapped_article_sentences_cannot_bypass_truthful_claims(self) -> None:
        no_notes_brief = {"experience": {"notes": []}}
        for fabricated in (
            "저희는 현장에서 돌고래를 봤어요.",
            "제가 기념품을 샀어요.",
            "저는 숙소 수영장에서 놀았어요.",
        ):
            post = self._post(2)
            post["intro"].append(fabricated)  # type: ignore[union-attr]
            post["experience_claims"] = []
            with self.assertRaisesRegex(ValueError, "without user notes"):
                product_writing._validate_experience_claims(post, no_notes_brief)

        supported_brief = {
            "experience": {"notes": ["차량 이동 중 아이가 잠들었을 때 편했다"]}
        }
        partial = self._post(2)
        full_sentence = (
            "저희는 차량 이동 중 아이가 잠들었을 때 편했고 돌고래도 봤어요."
        )
        partial["intro"].append(full_sentence)  # type: ignore[union-attr]
        partial["experience_claims"] = [
            {
                "claim": "저희는 차량 이동 중 아이가 잠들었을 때 편했고",
                "evidence_note_indexes": [1],
            }
        ]
        with self.assertRaisesRegex(ValueError, "every experience claim"):
            product_writing._validate_experience_claims(partial, supported_brief)

        fact_brief = {"product": self._facts()}
        omitted_fact = self._post(2)
        omitted_fact["intro"].append(  # type: ignore[union-attr]
            "이 상품은 무료 마사지와 호텔 객실 업그레이드도 제공됩니다."
        )
        with self.assertRaisesRegex(ValueError, "no exact fact_claim"):
            product_writing._validate_fact_claims(omitted_fact, fact_brief)

        review_brief = {
            "review_summary": product_writing.aggregate_review_themes(
                self._facts()["reviews"]
            )
        }
        valid_review = self._post(2)
        review_sentence = "표본 후기에서는 가족 동행 장점이 반복됐습니다."
        valid_review["intro"].append(review_sentence)  # type: ignore[union-attr]
        valid_review["review_claims"] = [
            {"claim": review_sentence, "theme": "가족 동행", "mentions": 2}
        ]
        self.assertEqual(
            product_writing._validate_review_claims(valid_review, review_brief)[0][
                "theme"
            ],
            "가족 동행",
        )
        fake_review = self._post(2)
        fake_review["intro"].append(  # type: ignore[union-attr]
            "표본 후기에서는 직원이 선물을 줬다는 이야기가 반복됐습니다."
        )
        fake_review["review_claims"] = []
        with self.assertRaisesRegex(ValueError, "exact review_claim"):
            product_writing._validate_review_claims(fake_review, review_brief)

    def test_brief_selects_one_title_persona_and_requires_every_visual(self) -> None:
        run_dir = self._run()
        manifest_path, manifest = self._manifest(
            run_dir, ["gallery", "gallery", "introduction", "itinerary"]
        )
        brief = product_writing.build_product_writing_brief(
            run_dir,
            self._facts(),
            manifest,
            self._visuals(4),
            image_manifest_path=manifest_path,
        )
        self.assertEqual(len(brief["title_plan"]["candidates"]), 5)
        self.assertIn("3대 가족 엄마", brief["persona"]["label"])
        self.assertEqual(brief["image_count"], 4)
        self.assertEqual(brief["images"][2]["source_role"], "introduction")
        self.assertNotIn("reviews", brief["product"])
        with self.assertRaisesRegex(ValueError, "every seller image"):
            product_writing.build_product_writing_brief(
                run_dir,
                self._facts(),
                manifest,
                self._visuals(3),
                image_manifest_path=manifest_path,
            )

    def test_manifest_proof_allows_absent_itinerary_but_binds_image_roles(self) -> None:
        run_dir = self._run()
        _manifest_path, manifest = self._manifest(
            run_dir, ["gallery", "introduction"]
        )
        proof = manifest["collection_proof"]
        self.assertIsInstance(proof, dict)
        proof["available_sections"].remove("ITINERARIES")  # type: ignore[union-attr]
        proof["expanded_sections"].remove("ITINERARIES")  # type: ignore[union-attr]
        rows = product_writing._manifest_rows(manifest)
        self.assertEqual(
            [row["source_role"] for row in rows], ["gallery", "introduction"]
        )

        manifest["accepted"][1]["source_role"] = "itinerary"  # type: ignore[index]
        manifest["accepted"][1]["role"] = "itinerary"  # type: ignore[index]
        with self.assertRaisesRegex(ValueError, "no matching available"):
            product_writing._manifest_rows(manifest)

    def test_fact_evidence_omits_optional_fields_that_are_not_available(self) -> None:
        product = self._facts()
        product["itineraries"] = []
        product["included"] = []
        product["excluded"] = []
        evidence = product_writing._fact_evidence({"product": product})
        self.assertNotIn("itinerary", evidence)
        self.assertNotIn("included", evidence)
        self.assertNotIn("excluded", evidence)
        self.assertEqual(evidence["rating"], ["4.8"])

    def test_brief_allows_standing_approval_and_rejects_region_conflict(self) -> None:
        no_permission_run = self._run(permission=False)
        manifest_path, manifest = self._manifest(no_permission_run, ["gallery"])
        brief = product_writing.build_product_writing_brief(
            no_permission_run,
            self._facts(),
            manifest,
            self._visuals(1),
            image_manifest_path=manifest_path,
        )
        self.assertEqual(brief["product"]["title"], self._facts()["title"])

        mismatch_run = self._run(main_keyword="나트랑 투어")
        mismatch_manifest_path, mismatch_manifest = self._manifest(mismatch_run, ["gallery"])
        with self.assertRaisesRegex(ValueError, "요청 지역과 상품 지역"):
            product_writing.build_product_writing_brief(
                mismatch_run,
                self._facts(),
                mismatch_manifest,
                self._visuals(1),
                image_manifest_path=mismatch_manifest_path,
            )

    def test_brief_automatically_penalizes_titles_from_sibling_run_history(self) -> None:
        run_dir = self._run()
        manifest_path, manifest = self._manifest(run_dir, ["gallery"])
        first = product_writing.build_product_writing_brief(
            run_dir,
            self._facts(),
            manifest,
            self._visuals(1),
            image_manifest_path=manifest_path,
        )
        prior_dir = run_dir.parent / "prior-run"
        write_json(
            prior_dir / "run.json",
            {
                "generation": {
                    "posts": [{"title": first["title_plan"]["exact_title"]}]
                }
            },
        )
        repeated = product_writing.build_product_writing_brief(
            run_dir,
            self._facts(),
            manifest,
            self._visuals(1),
            image_manifest_path=manifest_path,
        )
        self.assertEqual(repeated["title_plan"]["prior_title_count"], 1)
        self.assertNotEqual(
            repeated["title_plan"]["exact_title"], first["title_plan"]["exact_title"]
        )

    def test_deterministic_placements_use_every_image_once_in_groups_of_two(self) -> None:
        images = [
            {"index": 1, "source_role": "gallery"},
            {"index": 2, "source_role": "gallery"},
            {"index": 3, "source_role": "gallery"},
            {"index": 4, "source_role": "gallery"},
            {"index": 5, "source_role": "introduction"},
            {"index": 6, "source_role": "introduction"},
            {"index": 7, "source_role": "itinerary"},
        ]
        placements = product_writing.deterministic_image_placements(self._post(2), images)
        self.assertEqual(
            [item["manifest_index"] for item in placements], list(range(1, 8))
        )
        self.assertEqual([item["region"] for item in placements[:2]], ["intro", "intro"])
        for key in {
            (item["region"], item.get("section_index"), item["after_paragraph"])
            for item in placements
        }:
            self.assertLessEqual(
                sum(
                    1
                    for item in placements
                    if (item["region"], item.get("section_index"), item["after_paragraph"])
                    == key
                ),
                2,
            )

    def test_visual_relevance_can_promote_later_manifest_image_without_breaking_tags(self) -> None:
        images = [
            {
                "index": 1,
                "source_role": "gallery",
                "representative": True,
                "representative_rank": 2,
            },
            {"index": 2, "source_role": "gallery", "representative": False},
            {
                "index": 3,
                "source_role": "gallery",
                "representative": True,
                "representative_rank": 1,
            },
            {"index": 4, "source_role": "introduction", "representative": False},
            {"index": 5, "source_role": "itinerary", "representative": False},
        ]
        placements = product_writing.deterministic_image_placements(self._post(2), images)
        self.assertEqual(
            [row["manifest_index"] for row in placements], [3, 1, 2, 4, 5]
        )
        self.assertEqual(
            [row["file"] for row in placements],
            [f"image_{index}.jpg" for index in range(1, 6)],
        )
        self.assertTrue(all(row["region"] == "intro" for row in placements[:2]))

    def test_deterministic_placements_require_text_after_each_image_group(self) -> None:
        images = [
            {"index": index, "source_role": "gallery"}
            for index in range(1, 8)
        ]
        with self.assertRaisesRegex(ValueError, "needs at least 3 paragraphs"):
            product_writing.deterministic_image_placements(self._post(2), images)

    def test_finalize_and_writer_render_exact_url_and_sequential_tags(self) -> None:
        run_dir = self._run()
        roles = ["gallery", "gallery", "gallery", "introduction", "itinerary"]
        manifest_path, manifest = self._manifest(run_dir, roles)
        visuals = self._visuals(len(roles))
        visuals["images"][2]["visual_summary"] = "다낭 호이안 투어 가족여행 대표 장면"  # type: ignore[index]
        brief = product_writing.build_product_writing_brief(
            run_dir,
            self._facts(),
            manifest,
            visuals,
            image_manifest_path=manifest_path,
        )
        brief_path = run_dir / "analysis" / "product-writing-brief.json"
        write_json(brief_path, brief)
        history.update_run(
            run_dir,
            {
                "product_writing": {
                    "status": "briefed",
                    "brief_file": str(brief_path.relative_to(run_dir)),
                    "brief_file_sha256": product_writing._sha256_file(brief_path),
                    "brief_sha256": product_writing._mapping_sha256(brief),
                    "image_manifest": str(brief["image_manifest"]),
                    "image_manifest_sha256": str(brief["image_manifest_sha256"]),
                }
            },
        )
        finalized = product_writing.finalize_product_payload(
            run_dir,
            {"analysis": {}, "posts": [self._post(2)]},
            brief,
        )
        finalized_provenance = finalized["product_writing_provenance"]
        finalized_state = dict(history.load_run(run_dir)["product_writing"])
        finalized_state.update(
            {
                "status": "finalized",
                "finalized_post_sha256": finalized_provenance[
                    "finalized_post_sha256"
                ],
            }
        )
        history.update_run(run_dir, {"product_writing": finalized_state})
        generated_path = self.env.root / "generated.json"
        write_json(generated_path, finalized)
        result = write_posts.write_posts(run_dir, generated_path)
        text_path = run_dir / result["posts"][0]["file"]
        text = text_path.read_text(encoding="utf-8")
        body = text.split("본문2:\n", 1)[1]
        meaningful = [line.strip() for line in body.splitlines() if line.strip()]
        self.assertEqual(meaningful[0], "https://myrealt.rip/iZRp3d")
        self.assertEqual(meaningful[-1], "https://myrealt.rip/iZRp3d")
        self.assertEqual(body.count("https://myrealt.rip/iZRp3d"), 2)
        self.assertEqual(
            re.findall(r"\[(image_[1-9]\d*\.jpg)\]", body),
            [f"image_{index}.jpg" for index in range(1, len(roles) + 1)],
        )
        generation = history.load_run(run_dir)["generation"]["posts"][0]
        self.assertEqual(
            [row["manifest_index"] for row in generation["image_placements"]],
            [3, 1, 2, 4, 5],
        )
        self.assertNotRegex(body, r"(?:\[image_[1-9]\d*\.jpg\]\n){3}")


if __name__ == "__main__":
    unittest.main()
