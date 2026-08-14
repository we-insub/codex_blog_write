from __future__ import annotations

import io
import json
import re
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import quote

from _support import IsolatedMatoEnvironment, SCRIPTS_DIR, write_json

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import myrealtrip_product


CANONICAL_URL = "https://experiences.myrealtrip.com/products/3795277"
SELLER_BASE = "https://dry7pvlp22cox.cloudfront.net/mrt-images-prod/2024/10/25"
COURSE_BASE = "https://d2ur7st6jjikze.cloudfront.net/offer_courses/29534"


def rendered_fixture() -> str:
    return f"""
    <div id="__next"><main>
      <section class="e1pdsqzp0">
        <div><span class="e1qp43hc2">베트남</span><span class="e1qp43hc2">다낭</span></div>
        <h1 class="e2io25d1">[특가] 호이안 풀코스 단독투어</h1>
        <span class="e10qflf62">4.8 ·</span><span class="e10qflf63">후기 181개</span>
        <div class="swiper-wrapper">
          <div class="e1k35iz0"><div class="e1k35iz1">
            <img class="css-1idxmew" src="{SELLER_BASE}/a.jpg">
            <div hidden><img class="css-1ydfimi" src="https://dffoxz5he03rp.cloudfront.net/etc/img-placeholder.svg"></div>
          </div></div>
          <div class="e1k35iz0"><div class="e1k35iz1">
            <img class="css-1idxmew" src="https://d6bztw1vgnv55.cloudfront.net/1/review/2026/01/01/review.jpeg">
          </div><span class="e1k35iz2">후기사진</span></div>
        </div>
        <div class="e16emf0h0">
          <div class="eg5633s0"><span class="e1xhc0zq1">최소 인원 2명</span></div>
          <div class="eg5633s0"><span class="e1xhc0zq1">6시간 소요</span></div>
          <div class="eg5633s0"><span class="e1xhc0zq1">차량이동</span></div>
          <div class="eg5633s0"><span class="e1xhc0zq1">한국어, 영어</span></div>
        </div>
      </section>
      <section id="PARTNER"><span class="ek8rnbz4">힘투어</span>
        <img src="https://d2ur7st6jjikze.cloudfront.net/profile_images/1/profile.png">
      </section>
      <section id="INTRODUCTION"><div class="e1kcu58w2">
        <img class="css-1idxmew" src="{SELLER_BASE}/intro.png">
      </div><div class="e1kcu58w2"><img class="css-1idxmew"></div></section>
      <section id="ITINERARIES">
        <div class="e7xe4ph0"><div class="e7xe4ph5"><span>바구니배 투어</span>
          <span class="e7xe4ph7">1시간 소요</span></div>
          <div class="e7xe4ph12">코코넛마을 체험</div>
          <img class="e7xe4ph14" src="{COURSE_BASE}/course_original.jpg?123">
        </div>
      </section>
      <section id="INCLUDE_EXCLUDE">
        <div><h3>포함되어 있어요</h3><p>⭐ 전용차량\n⭐ 한국어 가이드</p></div>
        <div><h3>불포함되어 있어요</h3><p>• 개인경비\n• 매너팁</p></div>
      </section>
      <section id="USAGE">
        <div><h3>만나는시간</h3><p>15:00</p></div>
        <div><h3>만나는장소</h3><p>리조트 로비</p></div>
      </section>
      <section id="ESSENTIALS"><p>상품 번호 : 3795277</p></section>
      <section id="REFUND"><p>여행일 기준 취소 수수료가 적용됩니다.</p></section>
      <section id="REVIEW"><div class="e15aqqks0">
        <span class="e1look9g2">1달 전</span>
        <span class="ebhs27u1">부모님과 아이들 모두 만족했어요.</span>
        <span class="e1ao7r1t0">친절해요 · 안전해요</span>
        <div class="ebhs27u3"><img class="css-y5m0bt" src="https://d6bztw1vgnv55.cloudfront.net/1/review/x.jpeg"></div>
      </div></section>
      <section id="RECOMMENDATION"><div class="e1hlbvyh2">
        <img class="css-y5m0bt" src="{SELLER_BASE}/other-product.jpg">
      </div></section>
      <div class="en857rz0"><span class="esxxdu66">67,000원~</span></div>
    </main></div>
    """


def without_section(rendered_html: str, section_id: str) -> str:
    return re.sub(
        rf'<section id="{re.escape(section_id)}"[^>]*>.*?</section>',
        "",
        rendered_html,
        count=1,
        flags=re.DOTALL,
    )


class FakeResponse:
    def __init__(
        self,
        *,
        content: bytes = b"",
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.content = content
        self.status_code = status_code
        self.headers = headers or {}
        self.error = error

    def raise_for_status(self) -> None:
        if self.error:
            raise self.error
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, responses: dict[str, FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def get(self, url: str, **_kwargs: object) -> FakeResponse:
        self.calls.append(url)
        return self.responses[url]


def image_bytes(
    image_format: str,
    size: tuple[int, int],
    color: tuple[int, int, int, int] = (20, 40, 60, 255),
) -> bytes:
    from PIL import Image

    image = Image.new("RGBA", size, color)
    if image_format.upper() in {"JPEG", "JPG", "BMP"}:
        image = image.convert("RGB")
    output = io.BytesIO()
    image.save(output, format=image_format)
    return output.getvalue()


def candidate(index: int, url: str, role: str = "gallery") -> myrealtrip_product.ImageCandidate:
    return myrealtrip_product.ImageCandidate(
        candidate_id=f"{role}:{index}",
        dom_index=index,
        classification="product",
        source_role=role,
        source_url=url,
        allowed=True,
        rejection_reason=None,
        requires_hydration=False,
    )


class ProductURLTests(unittest.TestCase):
    def test_validates_canonical_short_and_bridge_urls(self) -> None:
        canonical = myrealtrip_product.validate_product_url(
            CANONICAL_URL + "?mylink_id=3198615"
        )
        self.assertEqual(canonical.canonical_url, CANONICAL_URL)
        self.assertEqual(canonical.product_id, "3795277")

        short = myrealtrip_product.validate_product_url("https://myrealt.rip/iZRp3d")
        self.assertEqual(short.kind, "short")
        self.assertEqual(short.short_code, "iZRp3d")
        self.assertIsNone(short.product_id)

        return_url = CANONICAL_URL + "?mylink_id=3198615&utm_source=mktpartner"
        bridge_url = (
            "https://www.myrealtrip.com/main/bridge/marketing?return_url="
            + quote(return_url, safe="")
        )
        bridge = myrealtrip_product.validate_product_url(bridge_url)
        self.assertEqual(bridge.kind, "bridge")
        self.assertEqual(bridge.canonical_url, CANONICAL_URL)

    def test_rejects_non_product_and_unsafe_urls(self) -> None:
        invalid = (
            "http://experiences.myrealtrip.com/products/1",
            "https://example.com/products/1",
            "https://experiences.myrealtrip.com/search/1",
            "https://myrealt.rip/a?redirect=https://example.com",
            "https://user:pass@experiences.myrealtrip.com/products/1",
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(
                myrealtrip_product.ProductURLValidationError
            ):
                myrealtrip_product.validate_product_url(value)

    def test_resolves_short_url_from_mocked_marketing_bridge(self) -> None:
        return_url = CANONICAL_URL + "?mylink_id=3198615&utm_source=mktpartner"
        location = (
            "https://www.myrealtrip.com/main/bridge/marketing?return_url="
            + quote(return_url, safe="")
        )
        session = FakeSession(
            {
                "https://myrealt.rip/iZRp3d": FakeResponse(
                    status_code=301, headers={"Location": location}
                )
            }
        )

        result = myrealtrip_product.resolve_product_url(
            "https://myrealt.rip/iZRp3d", session=session
        )

        self.assertEqual(result.canonical_url, CANONICAL_URL)
        self.assertEqual(result.product_id, "3795277")
        self.assertEqual(session.calls, ["https://myrealt.rip/iZRp3d"])


class ProductHTMLTests(unittest.TestCase):
    def test_parses_facts_from_rendered_dom(self) -> None:
        facts = myrealtrip_product.parse_product_html(
            rendered_fixture(), source_url=CANONICAL_URL
        )

        self.assertEqual(facts["title"], "[특가] 호이안 풀코스 단독투어")
        self.assertEqual(facts["rating"], 4.8)
        self.assertEqual(facts["review_count"], 181)
        self.assertEqual(facts["price_text"], "67,000원~")
        self.assertEqual(facts["price_krw"], 67000)
        self.assertTrue(facts["price"]["from"])
        self.assertEqual(facts["tour_duration_minutes"], 360)
        self.assertEqual(facts["tour_info"]["meeting_time"], "15:00")
        self.assertEqual(facts["tour_info"]["meeting_place"], "리조트 로비")
        self.assertEqual(facts["seller_name"], "힘투어")
        self.assertEqual(facts["product_id"], "3795277")
        self.assertEqual(facts["location"], ["베트남", "다낭"])
        self.assertEqual(facts["itineraries"][0]["duration_minutes"], 60)
        self.assertEqual(facts["included"], ["전용차량", "한국어 가이드"])
        self.assertEqual(facts["excluded"], ["개인경비", "매너팁"])
        self.assertEqual(facts["prices"]["currency"], "KRW")
        self.assertIn("상품 번호", facts["essentials"][0])
        self.assertIn("취소 수수료", facts["refund_policy"][0])
        self.assertIn("부모님", facts["reviews"][0]["text"])
        self.assertEqual(facts["reviews"][0]["tags"], ["친절해요", "안전해요"])

    def test_requires_a_product_title(self) -> None:
        with self.assertRaises(myrealtrip_product.ProductParseError):
            myrealtrip_product.parse_product_html("<main></main>")

    def test_rejects_a_url_and_html_product_number_mismatch(self) -> None:
        with self.assertRaisesRegex(myrealtrip_product.ProductParseError, "상품 번호가 다릅니다"):
            myrealtrip_product.parse_product_html(
                rendered_fixture().replace("상품 번호 : 3795277", "상품 번호 : 3510284"),
                source_url=CANONICAL_URL,
            )

    def test_itinerary_is_optional_but_core_product_sections_are_required(self) -> None:
        without_itinerary = without_section(rendered_fixture(), "ITINERARIES")
        facts = myrealtrip_product.parse_product_html(
            without_itinerary, source_url=CANONICAL_URL
        )
        self.assertNotIn("ITINERARIES", facts["available_sections"])
        self.assertEqual(facts["itineraries"], [])

        for section_id in (
            "INTRODUCTION",
            "INCLUDE_EXCLUDE",
            "USAGE",
            "ESSENTIALS",
            "REFUND",
            "REVIEW",
        ):
            with self.subTest(section_id=section_id), self.assertRaisesRegex(
                myrealtrip_product.ProductParseError, "필수 상품 섹션"
            ):
                myrealtrip_product.parse_product_html(
                    without_section(rendered_fixture(), section_id),
                    source_url=CANONICAL_URL,
                )

    def test_present_but_unexpanded_itinerary_is_rejected(self) -> None:
        unexpanded = rendered_fixture().replace(
            'class="e7xe4ph0"', 'class="itinerary-not-expanded"', 1
        )
        with self.assertRaisesRegex(
            myrealtrip_product.ProductParseError, "ITINERARIES"
        ):
            myrealtrip_product.parse_product_html(unexpanded, source_url=CANONICAL_URL)

    def test_present_but_empty_introduction_is_rejected(self) -> None:
        empty_intro = re.sub(
            r'<section id="INTRODUCTION">.*?</section>',
            '<section id="INTRODUCTION"><div class="e1kcu58w2"></div></section>',
            rendered_fixture(),
            count=1,
            flags=re.DOTALL,
        )
        with self.assertRaisesRegex(
            myrealtrip_product.ProductParseError, "INTRODUCTION"
        ):
            myrealtrip_product.parse_product_html(
                empty_intro, source_url=CANONICAL_URL
            )

    def test_present_include_and_usage_headings_require_their_values(self) -> None:
        mutations = (
            re.sub(
                r"(<h3>불포함되어 있어요</h3>)<p>.*?</p>",
                r"\1<p></p>",
                rendered_fixture(),
                count=1,
                flags=re.DOTALL,
            ),
            re.sub(
                r"(<h3>만나는장소</h3>)<p>.*?</p>",
                r"\1<p></p>",
                rendered_fixture(),
                count=1,
                flags=re.DOTALL,
            ),
        )
        for mutated in mutations:
            with self.subTest(), self.assertRaisesRegex(
                myrealtrip_product.ProductParseError,
                "INCLUDE_EXCLUDE|USAGE",
            ):
                myrealtrip_product.parse_product_html(
                    mutated, source_url=CANONICAL_URL
                )

    def test_classifies_seller_images_and_denies_review_and_recommendations(self) -> None:
        result = myrealtrip_product.classify_image_candidates(
            rendered_fixture(), page_url=CANONICAL_URL
        )

        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(result["requires_hydration_count"], 0)
        self.assertEqual(
            [row["source_role"] for row in result["allowed"]],
            ["gallery"],
        )
        self.assertTrue(all(row["classification"] == "product" for row in result["allowed"]))
        self.assertTrue(any(row["classification"] == "review" for row in result["rejected"]))
        self.assertTrue(
            any(row["classification"] == "recommendation" for row in result["rejected"])
        )
        self.assertTrue(any(row["classification"] == "placeholder" for row in result["rejected"]))
        self.assertFalse(any("other-product" in str(row["source_url"]) for row in result["allowed"]))
        self.assertTrue(
            any(
                row["rejection_reason"] == "seller_introduction_graphic"
                for row in result["rejected"]
            )
        )
        self.assertTrue(
            any(
                row["rejection_reason"] == "seller_itinerary_graphic"
                for row in result["rejected"]
            )
        )

        for unsafe_html in (
            rendered_fixture().replace(
                f"{SELLER_BASE}/a.jpg",
                f"{SELLER_BASE}/reviews/customer.jpg",
                1,
            ),
            rendered_fixture().replace(
                '<img class="css-1idxmew" src="',
                '<img class="css-1idxmew" data-role="traveler" src="',
                1,
            ),
        ):
            unsafe = myrealtrip_product.classify_image_candidates(
                unsafe_html, page_url=CANONICAL_URL
            )
            self.assertFalse(
                any(row["candidate_id"] == "gallery:1" for row in unsafe["allowed"])
            )

    def test_inert_noscript_and_template_images_do_not_shift_browser_indexes(self) -> None:
        inert = (
            '<noscript><img src="https://example.com/fallback.jpg"></noscript>'
            '<template><img src="https://example.com/template.jpg"></template>'
        )
        rendered = rendered_fixture().replace("<main>", f"<main>{inert}", 1)
        classified = myrealtrip_product.classify_image_candidates(
            rendered, page_url=CANONICAL_URL
        )
        gallery = next(
            row for row in classified["allowed"] if row["candidate_id"] == "gallery:1"
        )
        self.assertEqual(gallery["dom_index"], 1)
        self.assertFalse(
            any("example.com" in str(row["source_url"]) for row in classified["rejected"])
        )

    def test_hydration_uses_only_gallery_photos_and_rejects_review_urls(self) -> None:
        classified = myrealtrip_product.classify_image_candidates(
            rendered_fixture(), page_url=CANONICAL_URL
        )
        hydration_rows = []
        for row in classified["allowed"]:
            hydration_rows.append(
                {
                    "candidate_id": row["candidate_id"],
                    "dom_index": row["dom_index"],
                    "document_image_index": row["dom_index"] - 1,
                    "source_role": row["source_role"],
                    "current_src": row["source_url"] or f"{SELLER_BASE}/lazy.jpg",
                    "natural_width": 1200,
                    "natural_height": 800,
                    "complete": True,
                }
            )
        hydrated = myrealtrip_product.validate_hydrated_candidates(
            classified,
            hydration_rows,
            page_url=CANONICAL_URL,
        )
        gallery = next(row for row in hydrated if row.candidate_id == "gallery:1")
        self.assertEqual(gallery.source_url, f"{SELLER_BASE}/a.jpg")
        self.assertEqual((gallery.natural_width, gallery.natural_height), (1200, 800))

        # A static src is not proof that the browser decoded the image.
        with self.assertRaises(myrealtrip_product.ProductImageError):
            myrealtrip_product.validate_hydrated_candidates(
                classified,
                [row for row in hydration_rows if row["candidate_id"] != "gallery:1"],
                page_url=CANONICAL_URL,
            )

        with self.assertRaises(myrealtrip_product.ProductImageError):
            unsafe_rows = [dict(row) for row in hydration_rows]
            unsafe = next(row for row in unsafe_rows if row["candidate_id"] == "gallery:1")
            unsafe["current_src"] = "https://d6bztw1vgnv55.cloudfront.net/1/review/x.jpeg"
            myrealtrip_product.validate_hydrated_candidates(
                classified,
                unsafe_rows,
                page_url=CANONICAL_URL,
            )
        with self.assertRaisesRegex(myrealtrip_product.ProductImageError, "URL"):
            substituted_rows = [dict(row) for row in hydration_rows]
            substituted_rows[0]["current_src"] = f"{SELLER_BASE}/other-product.jpg"
            myrealtrip_product.validate_hydrated_candidates(
                classified, substituted_rows, page_url=CANONICAL_URL
            )
        with self.assertRaisesRegex(myrealtrip_product.ProductImageError, "인덱스"):
            shifted_rows = [dict(row) for row in hydration_rows]
            shifted_rows[0]["document_image_index"] += 1
            myrealtrip_product.validate_hydrated_candidates(
                classified, shifted_rows, page_url=CANONICAL_URL
            )
        with self.assertRaises(myrealtrip_product.ProductImageError):
            myrealtrip_product.validate_hydrated_candidates(
                classified,
                [{"candidate_id": "review:1", "complete": True}],
                page_url=CANONICAL_URL,
            )

    def test_collection_proof_requires_expansion_end_scroll_and_stability(self) -> None:
        classified = myrealtrip_product.classify_image_candidates(
            rendered_fixture(), page_url=CANONICAL_URL
        )
        hydrated_rows = [
            {
                "candidate_id": row["candidate_id"],
                "dom_index": row["dom_index"],
                "document_image_index": row["dom_index"] - 1,
                "source_role": row["source_role"],
                "current_src": row["source_url"] or f"{SELLER_BASE}/lazy.jpg",
                "natural_width": 1200,
                "natural_height": 800,
                "complete": True,
            }
            for row in classified["allowed"]
        ]
        count = classified["candidate_count"]
        proof = {
            "page_url": CANONICAL_URL,
            "document_ready_state": "complete",
            "available_sections": classified["available_sections"],
            "expanded_sections": ["INTRODUCTION", "ITINERARIES", "ESSENTIALS", "REVIEW"],
            "review_all_opened": True,
            "page_end_reached": True,
            "scroll_height": 5000,
            "max_scroll_y": 4200,
            "viewport_height": 800,
            "seller_candidate_count": count,
            "hydrated_candidate_count": count,
            "gallery_visited_count": 1,
            "stable_candidate_counts": [count, count],
        }
        normalized = myrealtrip_product.validate_collection_proof(
            proof, classified, hydrated_rows, page_url=CANONICAL_URL
        )
        self.assertEqual(normalized["candidate_count"], count)
        self.assertEqual(normalized["available_sections"], classified["available_sections"])

        for mutation in ("expanded", "scroll", "stable", "gallery"):
            invalid = dict(proof)
            if mutation == "expanded":
                invalid["expanded_sections"] = ["INTRODUCTION"]
            elif mutation == "scroll":
                invalid["max_scroll_y"] = 2000
            elif mutation == "stable":
                invalid["stable_candidate_counts"] = [count - 1, count]
            else:
                invalid["gallery_visited_count"] = 0
            with self.subTest(mutation=mutation), self.assertRaises(
                myrealtrip_product.ProductImageError
            ):
                myrealtrip_product.validate_collection_proof(
                    invalid, classified, hydrated_rows, page_url=CANONICAL_URL
                )

    def test_collection_proof_requires_only_expandable_sections_that_exist(self) -> None:
        rendered = without_section(rendered_fixture(), "ITINERARIES")
        classified = myrealtrip_product.classify_image_candidates(
            rendered, page_url=CANONICAL_URL
        )
        hydrated_rows = [
            {
                "candidate_id": row["candidate_id"],
                "dom_index": row["dom_index"],
                "document_image_index": row["dom_index"] - 1,
                "source_role": row["source_role"],
                "current_src": row["source_url"] or f"{SELLER_BASE}/lazy.jpg",
                "natural_width": 1200,
                "natural_height": 800,
                "complete": True,
            }
            for row in classified["allowed"]
        ]
        count = classified["candidate_count"]
        proof = {
            "page_url": CANONICAL_URL,
            "document_ready_state": "complete",
            "available_sections": classified["available_sections"],
            "expanded_sections": ["INTRODUCTION", "ESSENTIALS", "REVIEW"],
            "review_all_opened": True,
            "page_end_reached": True,
            "scroll_height": 5000,
            "max_scroll_y": 4200,
            "viewport_height": 800,
            "seller_candidate_count": count,
            "hydrated_candidate_count": count,
            "gallery_visited_count": 1,
            "stable_candidate_counts": [count, count],
        }
        normalized = myrealtrip_product.validate_collection_proof(
            proof, classified, hydrated_rows, page_url=CANONICAL_URL
        )
        self.assertNotIn("ITINERARIES", normalized["available_sections"])
        self.assertEqual(
            normalized["expanded_sections"], ["ESSENTIALS", "INTRODUCTION", "REVIEW"]
        )

        invalid = dict(proof)
        invalid["expanded_sections"] = [
            "INTRODUCTION",
            "ITINERARIES",
            "ESSENTIALS",
            "REVIEW",
        ]
        with self.assertRaises(myrealtrip_product.ProductImageError):
            myrealtrip_product.validate_collection_proof(
                invalid, classified, hydrated_rows, page_url=CANONICAL_URL
            )


class ProductImageBundleTests(unittest.TestCase):
    def test_eighty_one_duplicate_candidates_reduce_to_one_before_unique_cap(self) -> None:
        url = f"{SELLER_BASE}/same.jpg"
        rows = [candidate(index, url) for index in range(1, 82)]
        hydrated_rows = [
            {
                "candidate_id": row.candidate_id,
                "dom_index": row.dom_index,
                "document_image_index": row.dom_index - 1,
                "source_role": row.source_role,
                "current_src": url,
                "complete": True,
                "natural_width": 40,
                "natural_height": 20,
            }
            for row in rows
        ]
        hydrated = myrealtrip_product.validate_hydrated_candidates(
            rows, hydrated_rows, page_url=CANONICAL_URL
        )
        session = FakeSession(
            {url: FakeResponse(content=image_bytes("JPEG", (40, 20)), headers={"Content-Type": "image/jpeg"})}
        )
        with TemporaryDirectory() as temporary:
            result = myrealtrip_product.download_product_images(
                hydrated,
                Path(temporary) / "deduplicated",
                source_url=CANONICAL_URL,
                session=session,
            )
        self.assertEqual(result["image_count"], 1)
        self.assertEqual(len(result["duplicates"]), 80)
        self.assertEqual(session.calls, [url])

    def test_eighty_one_genuinely_unique_images_fail_atomically_after_dedup(self) -> None:
        urls = [f"{SELLER_BASE}/unique-{index}.png" for index in range(1, 82)]
        rows = [candidate(index, url) for index, url in enumerate(urls, start=1)]
        session = FakeSession(
            {
                url: FakeResponse(
                    content=image_bytes(
                        "PNG",
                        (3 + index % 4, 3 + index % 5),
                        (index, (index * 3) % 256, (index * 7) % 256, 255),
                    ),
                    headers={"Content-Type": "image/png"},
                )
                for index, url in enumerate(urls, start=1)
            }
        )
        with TemporaryDirectory() as temporary:
            output = Path(temporary) / "over-unique-cap"
            with self.assertRaisesRegex(
                myrealtrip_product.ProductImageError, "중복 제거 후 고유 업체 이미지"
            ):
                myrealtrip_product.download_product_images(
                    rows,
                    output,
                    source_url=CANONICAL_URL,
                    session=session,
                )
            self.assertFalse(output.exists())

    def test_preserves_originals_prepares_jpegs_and_deduplicates_three_ways(self) -> None:
        first = image_bytes("PNG", (40, 20))
        pixel_duplicate = image_bytes("BMP", (40, 20))
        large = image_bytes("PNG", (200, 100), (200, 30, 80, 255))
        urls = [
            f"{SELLER_BASE}/first.png?111",
            f"{SELLER_BASE}/first.png?111",
            f"{SELLER_BASE}/same-content.png",
            f"{SELLER_BASE}/same-pixels.bmp",
            f"{SELLER_BASE}/large.png",
        ]
        session = FakeSession(
            {
                urls[0]: FakeResponse(content=first, headers={"Content-Type": "image/png"}),
                urls[2]: FakeResponse(content=first, headers={"Content-Type": "image/png"}),
                urls[3]: FakeResponse(content=pixel_duplicate, headers={"Content-Type": "image/bmp"}),
                urls[4]: FakeResponse(content=large, headers={"Content-Type": "image/png; charset=binary"}),
            }
        )
        rows = [candidate(index, url) for index, url in enumerate(urls, start=1)]

        with TemporaryDirectory() as temporary:
            output = Path(temporary) / "product-images"
            result = myrealtrip_product.download_product_images(
                rows,
                output,
                source_url="https://myrealt.rip/iZRp3d",
                canonical_url=CANONICAL_URL,
                product_id="3795277",
                session=session,
                prepared_max_side=100,
                facts={
                    "title": "호이안 투어",
                    "reviews": [
                        {"text": "후기 원문은 저장하지 않음", "tags": ["친절해요"]}
                    ],
                },
            )

            self.assertEqual(result["image_count"], 2)
            self.assertEqual(
                [row["reason"] for row in result["duplicates"]],
                ["url_duplicate", "content_duplicate", "pixel_duplicate"],
            )
            self.assertEqual([row["file"] for row in result["accepted"]], ["prepared/image_1.jpg", "prepared/image_2.jpg"])
            self.assertTrue(all(row["classification"] == "product" for row in result["accepted"]))
            self.assertEqual(result["accepted"][0]["source_role"], "gallery")
            self.assertEqual(result["accepted"][0]["role"], "gallery")
            self.assertEqual((result["accepted"][0]["width"], result["accepted"][0]["height"]), (40, 20))
            self.assertFalse(result["accepted"][0]["resized"])
            self.assertEqual((result["accepted"][1]["width"], result["accepted"][1]["height"]), (200, 100))
            self.assertFalse(result["accepted"][1]["resized"])
            self.assertEqual(result["accepted"][0]["source_filename"], "first.png")
            self.assertEqual(result["accepted"][0]["original_extension"], ".png")
            self.assertEqual(result["accepted"][0]["content_type"], "image/png")
            self.assertEqual((output / "originals/image_1.png").read_bytes(), first)
            self.assertEqual((output / "originals/image_2.png").read_bytes(), large)
            self.assertTrue((output / "prepared/image_1.jpg").is_file())
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["product_id"], "3795277")
            self.assertEqual(manifest["facts"]["title"], "호이안 투어")
            self.assertNotIn("reviews", manifest["facts"])
            self.assertEqual(manifest["facts"]["review_count_sampled"], 1)
            self.assertEqual(manifest["facts"]["review_tag_counts"], {"친절해요": 1})
            self.assertNotIn("후기 원문", (output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["accepted"], result["accepted"])

    def test_enforces_hard_cap_without_truncating_or_calling_network(self) -> None:
        rows = [
            candidate(index, f"{SELLER_BASE}/image-{index}.jpg")
            for index in range(1, myrealtrip_product.HARD_IMAGE_CAP * 5 + 2)
        ]
        session = FakeSession({})
        with TemporaryDirectory() as temporary:
            output = Path(temporary) / "too-many"
            with self.assertRaises(myrealtrip_product.ProductImageError):
                myrealtrip_product.download_product_images(
                    rows,
                    output,
                    source_url=CANONICAL_URL,
                    session=session,
                )
            self.assertFalse(output.exists())
            self.assertEqual(session.calls, [])

        with TemporaryDirectory() as temporary:
            with self.assertRaises(myrealtrip_product.ProductImageError):
                myrealtrip_product.download_product_images(
                    [candidate(1, f"{SELLER_BASE}/one.jpg")],
                    Path(temporary) / "zero-cap",
                    source_url=CANONICAL_URL,
                    session=session,
                    max_images=0,
                )

    def test_download_failure_leaves_no_partial_output(self) -> None:
        url = f"{SELLER_BASE}/broken.jpg"
        session = FakeSession(
            {url: FakeResponse(content=b"<html>login</html>", headers={"Content-Type": "text/html"})}
        )
        with TemporaryDirectory() as temporary:
            output = Path(temporary) / "atomic-output"
            with self.assertRaises(myrealtrip_product.ProductImageError):
                myrealtrip_product.download_product_images(
                    [candidate(1, url)],
                    output,
                    source_url=CANONICAL_URL,
                    session=session,
                )
            self.assertFalse(output.exists())
            self.assertEqual(list(Path(temporary).glob(".atomic-output-*")), [])


class ProductCLITests(unittest.TestCase):
    def test_classify_mode_emits_exact_zero_based_browser_indexes(self) -> None:
        with IsolatedMatoEnvironment() as environment:
            staging = (
                environment.googleblog_home / "mato-blog-codex" / "staging" / "run"
            )
            source = staging / "browser-html.json"
            output_path = staging / "hydration-candidates.json"
            write_json(
                source,
                {
                    "product_url": CANONICAL_URL,
                    "rendered_html": rendered_fixture().replace(
                        "<main>",
                        '<main><noscript><img src="https://example.com/fallback.jpg"></noscript>',
                        1,
                    ),
                },
            )
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = myrealtrip_product.main(
                    [
                        "--input",
                        str(source),
                        "--classify-output",
                        str(output_path),
                    ]
                )
            self.assertEqual(code, 0, stdout.getvalue())
            result = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(result["kind"], "myrealtrip_browser_hydration_candidates")
            self.assertEqual(result["candidate_count"], 1)
            self.assertIn("ITINERARIES", result["available_sections"])
            self.assertEqual(result["candidates"][0]["candidate_id"], "gallery:1")
            self.assertEqual(result["candidates"][0]["document_image_index"], 0)
            for row in result["candidates"]:
                self.assertEqual(row["document_image_index"], row["dom_index"] - 1)
                self.assertNotIn("/review/", row["current_src"])

    def test_cli_reads_staging_input_and_prints_summary_without_raw_text(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_path = root / "browser-product.json"
            input_path.write_text(
                json.dumps(
                    {
                        "product_url": "https://myrealt.rip/iZRp3d",
                        "rendered_html": "<html>RAW-REVIEW-SECRET</html>",
                        "hydrated_rows": [],
                        "collection_proof": {},
                    }
                ),
                encoding="utf-8",
            )
            fake_result = {
                "product_id": "3795277",
                "image_count": 3,
                "parsed_facts": {
                    "title": "호이안 투어",
                    "rating": 4.8,
                    "review_count": 181,
                    "price_text": "67,000원~",
                    "reviews": [{"text": "RAW-REVIEW-SECRET"}],
                },
            }

            def fake_build(
                rendered_html: str,
                product_url: str,
                output_dir: str | Path,
                **_kwargs: object,
            ) -> dict[str, object]:
                del rendered_html, product_url
                staged_bundle = Path(output_dir)
                staged_bundle.mkdir(parents=True)
                manifest = staged_bundle / "manifest.json"
                manifest.write_text("{}", encoding="utf-8")
                return {**fake_result, "manifest": str(manifest)}

            output = io.StringIO()
            with patch.object(
                myrealtrip_product, "build_product_bundle", side_effect=fake_build
            ) as mocked:
                with redirect_stdout(output):
                    code = myrealtrip_product.main(
                        [
                            "--input",
                            str(input_path),
                            "--output-dir",
                            str(root / "bundle"),
                        ]
                    )
            self.assertEqual(code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["image_count"], 3)
            self.assertEqual(payload["title"], "호이안 투어")
            self.assertNotIn("RAW-REVIEW-SECRET", output.getvalue())
            self.assertTrue(input_path.exists())
            self.assertIn("RAW-REVIEW-SECRET", mocked.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
