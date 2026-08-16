"""Deterministic Naver title planning for MyRealTrip product posts.

The public helpers deliberately separate hard keyword validation from scoring:
every candidate must contain every main-keyword term, one or two selected
subkeywords, and the hook. History similarity can change the winner, but can
never make an invalid title eligible.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Mapping, Sequence


DEFAULT_HOOK = "솔직후기"
CANDIDATE_COUNT = 5
NAVER_TITLE_MAX_CHARS = 40

# Canonical city -> (country, aliases). Countries are checked separately so a
# city-level mismatch is not hidden merely because both products are in Vietnam.
_REGION_CITIES: dict[str, tuple[str, tuple[str, ...]]] = {
    "나트랑": ("베트남", ("나트랑", "냐짱", "nha trang")),
    "다낭": ("베트남", ("다낭", "da nang", "danang")),
    "호이안": ("베트남", ("호이안", "hoi an")),
    "하노이": ("베트남", ("하노이", "hanoi")),
    "호치민": ("베트남", ("호치민", "사이공", "ho chi minh", "saigon")),
    "푸꾸옥": ("베트남", ("푸꾸옥", "phu quoc")),
    "달랫": ("베트남", ("달랫", "da lat", "dalat")),
    "방콕": ("태국", ("방콕", "bangkok")),
    "파타야": ("태국", ("파타야", "pattaya")),
    "푸켓": ("태국", ("푸켓", "phuket")),
    "치앙마이": ("태국", ("치앙마이", "chiang mai")),
    "도쿄": ("일본", ("도쿄", "동경", "tokyo")),
    "오사카": ("일본", ("오사카", "osaka")),
    "교토": ("일본", ("교토", "kyoto")),
    "후쿠오카": ("일본", ("후쿠오카", "fukuoka")),
    "삿포로": ("일본", ("삿포로", "sapporo")),
    "오키나와": ("일본", ("오키나와", "okinawa")),
    "세부": ("필리핀", ("세부", "cebu")),
    "보홀": ("필리핀", ("보홀", "bohol")),
    "보라카이": ("필리핀", ("보라카이", "boracay")),
    "발리": ("인도네시아", ("발리", "bali")),
    "코타키나발루": ("말레이시아", ("코타키나발루", "kota kinabalu")),
    "제주": ("한국", ("제주도", "제주", "jeju")),
    "부산": ("한국", ("부산", "busan")),
    "서울": ("한국", ("서울", "seoul")),
}
_REGION_COUNTRIES: dict[str, tuple[str, ...]] = {
    "베트남": ("베트남", "vietnam"),
    "태국": ("태국", "thailand"),
    "일본": ("일본", "japan"),
    "필리핀": ("필리핀", "philippines"),
    "인도네시아": ("인도네시아", "indonesia"),
    "말레이시아": ("말레이시아", "malaysia"),
    "한국": ("대한민국", "한국", "korea"),
}


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value or ""))).strip()


def _compact(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", _text(value).lower())


def _unique_texts(values: Sequence[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = _text(value)
        key = _compact(cleaned)
        if cleaned and key and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result


def main_keyword_terms(main_keyword: str) -> list[str]:
    """Split a main keyword into its required, de-duplicated lexical terms."""

    return _unique_texts(re.findall(r"[0-9A-Za-z가-힣]+", _text(main_keyword)))


def title_validation_details(
    title: str,
    main_keyword: str,
    subkeywords: Sequence[str],
    hook: str,
) -> dict[str, Any]:
    """Return the hard-validation evidence used by title generation."""

    compact_title = _compact(title)
    main_terms = main_keyword_terms(main_keyword)
    selected_subkeywords = _unique_texts(subkeywords)
    cleaned_hook = _text(hook)
    missing_main = [term for term in main_terms if _compact(term) not in compact_title]
    missing_sub = [term for term in selected_subkeywords if _compact(term) not in compact_title]
    hook_present = bool(cleaned_hook and _compact(cleaned_hook) in compact_title)
    valid_sub_count = 1 <= len(selected_subkeywords) <= 2
    valid = bool(
        compact_title
        and main_terms
        and not missing_main
        and valid_sub_count
        and not missing_sub
        and hook_present
        and len(_text(title)) <= NAVER_TITLE_MAX_CHARS
    )
    return {
        "valid": valid,
        "main_terms": main_terms,
        "selected_subkeywords": selected_subkeywords,
        "missing_main_terms": missing_main,
        "missing_subkeywords": missing_sub,
        "valid_subkeyword_count": valid_sub_count,
        "hook_present": hook_present,
        "within_naver_limit": len(_text(title)) <= NAVER_TITLE_MAX_CHARS,
    }


def validate_title(
    title: str,
    main_keyword: str,
    subkeywords: Sequence[str],
    hook: str,
) -> bool:
    """Return whether a title satisfies every non-negotiable keyword rule."""

    return bool(title_validation_details(title, main_keyword, subkeywords, hook)["valid"])


def _main_orders(main_keyword: str) -> tuple[list[str], list[str]]:
    terms = main_keyword_terms(main_keyword)
    if not terms:
        raise ValueError("메인 키워드가 필요합니다.")
    suffixes = {"투어", "여행", "맛집", "호텔", "리조트", "코스"}
    # Country/city/neighbourhood order is semantic Korean, not a bag of words.
    # Keep that prefix intact and vary only where the user-supplied subkeyword
    # sits relative to the category noun (투어/맛집/호텔...).
    alternate = list(terms)
    return terms, alternate


def _candidate_specs(
    main_keyword: str,
    subkeywords: Sequence[str],
    hook: str,
) -> list[tuple[str, list[str]]]:
    original, alternate = _main_orders(main_keyword)
    pool = _unique_texts(subkeywords)
    if not pool:
        raise ValueError("서브키워드가 1개 이상 필요합니다.")
    cleaned_hook = _text(hook) or DEFAULT_HOOK

    suffixes = {"투어", "여행", "맛집", "호텔", "리조트", "코스"}
    has_category_suffix = original[-1] in suffixes
    prefix = original[:-1] if has_category_suffix else original
    category = original[-1:] if has_category_suffix else []

    def interleave(selected: list[str]) -> list[str]:
        if category and any(_compact(value).endswith(_compact(category[0])) for value in selected):
            return prefix + selected
        return prefix + selected + category

    if len(pool) >= 3:
        selections = [
            [pool[0], pool[1]],
            [pool[1]],
            [pool[0], pool[2]],
            [pool[0]],
            [pool[1], pool[2]],
        ]
        token_rows = [
            original + selections[0] + [cleaned_hook],
            interleave(selections[1]) + [cleaned_hook],
            original + selections[2] + [cleaned_hook],
            interleave(selections[3]) + [cleaned_hook],
            original + selections[4] + [cleaned_hook],
        ]
    elif len(pool) == 2:
        selections = [[pool[0], pool[1]], [pool[0]], [pool[1]], [pool[1], pool[0]], [pool[1], pool[0]]]
        token_rows = [
            original + selections[0] + [cleaned_hook],
            interleave(selections[1]) + [cleaned_hook],
            original + selections[2] + [cleaned_hook],
            interleave(selections[3]) + [cleaned_hook],
            original + selections[4] + [cleaned_hook],
        ]
    else:
        selections = [[pool[0]] for _ in range(CANDIDATE_COUNT)]
        token_rows = [
            original + selections[0] + [cleaned_hook],
            interleave(selections[1]) + [cleaned_hook],
            original + selections[2] + ["정보", cleaned_hook],
            interleave(selections[3]) + ["정리", cleaned_hook],
            original + selections[4] + ["가이드", cleaned_hook],
        ]
    specs = [(" ".join(row), selections[index]) for index, row in enumerate(token_rows)]
    if len({title for title, _selected in specs}) != CANDIDATE_COUNT:
        raise RuntimeError("서로 다른 제목 후보 5개를 만들지 못했습니다.")
    return specs


def generate_title_candidates(
    main_keyword: str,
    subkeywords: Sequence[str],
    hook: str = DEFAULT_HOOK,
) -> list[str]:
    """Generate exactly five deterministic, order-varied Naver title candidates."""

    specs = _candidate_specs(main_keyword, subkeywords, hook)
    cleaned_hook = _text(hook) or DEFAULT_HOOK
    for title, selected in specs:
        if not validate_title(title, main_keyword, selected, cleaned_hook):
            raise RuntimeError("제목 후보가 필수 키워드 규칙을 충족하지 못했습니다.")
    return [title for title, _selected in specs]


def _history_titles(values: Sequence[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        candidate = value.get("title") if isinstance(value, Mapping) else value
        cleaned = _text(candidate)
        if cleaned:
            result.append(cleaned)
    return result


def history_duplicate_penalty(title: str, prior_titles: Sequence[Any]) -> int:
    """Return a deterministic non-positive penalty for title-history overlap."""

    normalized = _compact(title)
    if not normalized:
        return -10_000
    worst = 0
    title_tokens = set(main_keyword_terms(title))
    for prior in _history_titles(prior_titles):
        previous = _compact(prior)
        if previous == normalized:
            return -1_000
        ratio = SequenceMatcher(None, normalized, previous).ratio()
        prior_tokens = set(main_keyword_terms(prior))
        union = title_tokens | prior_tokens
        token_overlap = len(title_tokens & prior_tokens) / len(union) if union else 0.0
        if ratio >= 0.9:
            penalty = -int(round(300 * ratio))
        elif ratio >= 0.75:
            penalty = -int(round(120 * ratio))
        elif token_overlap >= 0.8:
            penalty = -70
        elif token_overlap >= 0.6:
            penalty = -35
        else:
            penalty = 0
        worst = min(worst, penalty)
    return worst


def _find_regions(value: str) -> tuple[set[str], set[str]]:
    normalized = _text(value).lower()
    cities: set[str] = set()
    countries: set[str] = set()
    for city, (country, aliases) in _REGION_CITIES.items():
        if any(_text(alias).lower() in normalized for alias in aliases):
            cities.add(city)
            countries.add(country)
    for country, aliases in _REGION_COUNTRIES.items():
        if any(_text(alias).lower() in normalized for alias in aliases):
            countries.add(country)
    return cities, countries


def region_mismatch_details(main_keyword: str, product_text: str) -> dict[str, Any]:
    """Explain only provable city/country mismatches; unknown regions pass."""

    requested_cities, requested_countries = _find_regions(main_keyword)
    product_cities, product_countries = _find_regions(product_text)
    city_mismatch = bool(
        requested_cities and product_cities and requested_cities.isdisjoint(product_cities)
    )
    country_mismatch = bool(
        requested_countries
        and product_countries
        and requested_countries.isdisjoint(product_countries)
    )
    return {
        "mismatch": city_mismatch or country_mismatch,
        "requested_cities": sorted(requested_cities),
        "product_cities": sorted(product_cities),
        "requested_countries": sorted(requested_countries),
        "product_countries": sorted(product_countries),
        "reason": "city" if city_mismatch else ("country" if country_mismatch else ""),
    }


def detect_region_mismatch(main_keyword: str, product_text: str) -> bool:
    """Return True only when recognized request/product regions conflict."""

    return bool(region_mismatch_details(main_keyword, product_text)["mismatch"])


def build_title_plan(
    main_keyword: str,
    subkeywords: Sequence[str],
    hook: str = DEFAULT_HOOK,
    *,
    prior_titles: Sequence[Any] = (),
    product_text: str = "",
    strict_region: bool = True,
) -> dict[str, Any]:
    """Score five valid candidates and choose the deterministic best title."""

    region_check = region_mismatch_details(main_keyword, product_text)
    if strict_region and product_text and region_check["mismatch"]:
        raise ValueError(
            f"요청 지역과 상품 지역이 다릅니다: "
            f"{region_check['requested_cities'] or region_check['requested_countries']} -> "
            f"{region_check['product_cities'] or region_check['product_countries']}"
        )
    specs = _candidate_specs(main_keyword, subkeywords, hook)
    cleaned_hook = _text(hook) or DEFAULT_HOOK
    original_main = " ".join(main_keyword_terms(main_keyword))
    rows: list[dict[str, Any]] = []
    for index, (title, selected) in enumerate(specs, start=1):
        validation = title_validation_details(title, main_keyword, selected, cleaned_hook)
        if not validation["valid"]:
            raise RuntimeError(f"제목 후보 {index}가 hard validation에 실패했습니다.")
        duplicate_penalty = history_duplicate_penalty(title, prior_titles)
        product_compact = _compact(product_text)
        product_match_count = sum(
            1 for value in selected if _compact(value) and _compact(value) in product_compact
        )
        relevance_score = 20 + (4 if len(selected) == 2 else 2) + 5 * product_match_count
        naturalness_score = 14 if title.startswith(original_main) else 12
        if re.search(r"(?:투어|여행|맛집)\s+(?:베트남|나트랑|다낭|호이안|부산|서울)", title):
            naturalness_score -= 8
        conciseness_score = max(0, 12 - max(0, len(title) - 24))
        score = 70 + relevance_score + naturalness_score + conciseness_score + duplicate_penalty
        rows.append(
            {
                "index": index,
                "title": title,
                "subkeywords": list(selected),
                "hook": cleaned_hook,
                "valid": True,
                "history_penalty": duplicate_penalty,
                "relevance_score": relevance_score,
                "product_match_count": product_match_count,
                "naturalness_score": naturalness_score,
                "conciseness_score": conciseness_score,
                "score": score,
            }
        )
    selected = max(rows, key=lambda row: (int(row["score"]), -int(row["index"])))
    return {
        "selected_title": selected["title"],
        "selected_index": selected["index"],
        "candidates": rows,
        "region_check": region_check,
    }


def select_best_title(
    main_keyword: str,
    subkeywords: Sequence[str],
    hook: str = DEFAULT_HOOK,
    *,
    prior_titles: Sequence[Any] = (),
    product_text: str = "",
    strict_region: bool = True,
) -> str:
    """Return only the chosen title for simple callers."""

    return str(
        build_title_plan(
            main_keyword,
            subkeywords,
            hook,
            prior_titles=prior_titles,
            product_text=product_text,
            strict_region=strict_region,
        )["selected_title"]
    )
