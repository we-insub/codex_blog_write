"""Local Naver profile, source-download, and draft workflow helpers.

The source downloader is bundled with this plugin and does not require the
old Mato Helper checkout.  Profile and editor migration remains isolated from
the downloader so a content-only run never needs browser credentials.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import parse_qs, urlparse, urlunparse

try:
    from . import naver_url_download as local_url_download
except ImportError:
    import naver_url_download as local_url_download  # type: ignore[no-redef]


DEFAULT_MATO_HELPER_ROOT = Path.home() / "Desktop" / "google-blog-auto"
DEFAULT_PROFILE_ROOT = Path.home() / ".googleblog" / "browser_profiles"
DEFAULT_MANAGED_PROMPT_ROOT = Path.home() / ".googleblog" / "mato-blog-codex" / "prompts"


class BridgeError(RuntimeError):
    """Raised when the existing Mato Helper functions cannot be used."""


def helper_root(value: str = "") -> Path:
    """Resolve and validate the existing Mato Helper source root."""

    raw = value or os.environ.get("MATO_HELPER_ROOT", "")
    root = Path(raw).expanduser() if raw else DEFAULT_MATO_HELPER_ROOT
    root = root.resolve()
    required = (
        root / "naver_playwright.py",
        root / "local_agent" / "local_store.py",
        root / "local_agent" / "naver_draft.py",
        root / "local_agent" / "naver_url_download.py",
    )
    missing = [path.name for path in required if not path.is_file()]
    if missing:
        raise BridgeError(f"Mato Helper 연결 파일을 찾지 못했습니다: {', '.join(missing)}")
    return root


def _add_helper_import_path(root: Path) -> None:
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)


def profile_name(slot: int) -> str:
    if slot <= 0:
        raise ValueError("프로필 번호는 1 이상이어야 합니다.")
    return f"naver_{slot}"


def profile_check_payload(slot: int, write_url: str, *, interactive_login: bool) -> dict[str, Any]:
    """Build a compatibility payload for callers that inspect the command."""

    payload: dict[str, Any] = {
        "profile_name": profile_name(slot),
        "write_url": str(write_url or "").strip(),
    }
    if interactive_login:
        payload.update({"mode": "create_login", "interactive_login": True, "ensure_profile": True})
    return payload


def draft_payload(
    slot: int,
    root_dir: str,
    write_url: str,
    *,
    max_count: int = 1,
    typing_delay: int = 20,
    publish: bool = False,
) -> dict[str, Any]:
    work_dir = Path(root_dir).expanduser().resolve()
    if not work_dir.is_dir():
        raise ValueError(f"원고 작업 폴더를 찾을 수 없습니다: {work_dir}")
    if not str(write_url or "").strip():
        raise ValueError("네이버 글쓰기 URL이 필요합니다.")
    return {
        "root_dir": str(work_dir),
        "write_url": str(write_url).strip(),
        "template_name": "제목을입력해주세요1",
        "typing_delay": max(0, min(500, int(typing_delay))),
        "max_count": max(1, min(50, int(max_count))),
        "profile_name": profile_name(slot),
        "publish_mode": "publish" if publish else "draft",
        "is_draft": not publish,
    }


def _load_store(root: Path) -> Any:
    _add_helper_import_path(root)
    try:
        return importlib.import_module("local_agent.local_store")
    except Exception as exc:
        raise BridgeError(f"Mato Helper 프로필 저장소를 불러오지 못했습니다: {exc}") from exc


def resolve_profile_path(root: Path, slot: int, *, create: bool) -> Path:
    """Use Mato Helper's registered or legacy persistent profile for a slot.

    Older Mato Helper installations stored the first profile as
    ``~/.googleblog/naver_browser`` and later slots as ``naver_browser_N``.
    Adopt those folders before creating the newer ``browser_profiles/naver_N``
    layout so an existing user session is never replaced by an empty profile.
    """

    name = profile_name(slot)
    store = _load_store(root)
    existing = str(store.get_playwright_profile(name) or "").strip()
    if existing:
        path = Path(existing).expanduser()
    else:
        legacy_name = "naver_browser" if slot == 1 else f"naver_browser_{slot}"
        legacy_path = Path.home() / ".googleblog" / legacy_name
        if legacy_path.is_dir():
            row = store.ensure_playwright_profile(name, str(legacy_path))
            path = Path(str(row.get("path") or legacy_path)).expanduser()
        else:
            path = DEFAULT_PROFILE_ROOT / name
    if create:
        row = store.ensure_playwright_profile(name, str(path))
        path = Path(str(row.get("path") or path)).expanduser()
    return path.resolve()


def _safe_url(url: str) -> str:
    value = str(url or "").strip()
    if not value:
        return ""
    if not value.lower().startswith(("https://blog.naver.com/", "http://blog.naver.com/")):
        raise BridgeError("글쓰기 주소는 blog.naver.com 주소여야 합니다.")
    return value


def _record_profile_status(slot: int, ready: bool) -> None:
    """Mirror a bridge check into the plugin's credential-free profile catalog."""

    try:
        profiles_module = importlib.import_module("profiles")
        profiles_module._update_login_status(slot, "ready" if ready else "needs_login")
    except Exception:
        # The existing Mato Helper profile is authoritative. A stale display
        # status must not turn a successful browser check into a failure.
        pass


def run_direct_profile_check(
    root: Path,
    slot: int,
    write_url: str,
    *,
    timeout_seconds: int = 300,
    status_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Open the verified Mato Helper profile login/check flow in visible Chrome."""

    profile = resolve_profile_path(root, slot, create=True)
    target = _safe_url(write_url)
    _add_helper_import_path(root)
    try:
        module = importlib.import_module("naver_playwright")
        writer_class = module.NaverPlaywright
    except Exception as exc:
        raise BridgeError(f"Mato Helper 네이버 브라우저 기능을 불러오지 못했습니다: {exc}") from exc

    def report(message: object, _color: str = "black") -> None:
        if status_callback:
            status_callback(str(message))

    writer = writer_class(update_status_func=report, profile_dir=str(profile))
    ready = False
    try:
        ready = bool(
            writer.login(
                "",
                "",
                target_url=target or None,
                login_timeout_sec=max(60, min(900, int(timeout_seconds))),
            )
        )
    finally:
        try:
            writer.stop()
        except Exception:
            pass

    _record_profile_status(slot, ready)

    return {
        "ok": ready,
        "kind": "naver_profile_check",
        "profile_name": profile_name(slot),
        "profile_path": str(profile),
        "profile_exists": profile.is_dir(),
        "write_url": target,
        "login_ready": ready,
    }


def _safe_draft_result(result: Mapping[str, Any], profile: Path, slot: int) -> dict[str, Any]:
    """Keep useful writer results while omitting all browser/session data."""

    entries: list[dict[str, Any]] = []
    raw_entries = result.get("entries")
    if isinstance(raw_entries, list):
        for raw in raw_entries[:50]:
            if not isinstance(raw, Mapping):
                continue
            entries.append(
                {
                    key: raw.get(key)
                    for key in ("folder", "title", "write_url", "ok", "skipped", "save_ok", "completed", "reason")
                    if key in raw
                }
            )
    return {
        "ok": int(result.get("succeeded") or 0) > 0 and int(result.get("failed") or 0) == 0,
        "kind": "naver_draft_upload",
        "profile_name": profile_name(slot),
        "profile_path": str(profile),
        "mode": result.get("mode"),
        "processed": result.get("processed"),
        "succeeded": result.get("succeeded"),
        "failed": result.get("failed"),
        "skipped": result.get("skipped"),
        "elapsed_sec": result.get("elapsed_sec"),
        "entries": entries,
    }


def run_direct_draft(
    root: Path,
    slot: int,
    root_dir: str,
    write_url: str,
    *,
    max_count: int = 1,
    typing_delay: int = 20,
    publish: bool = False,
    status_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Run Mato Helper's existing Naver draft writer with its numbered profile."""

    payload = draft_payload(
        slot,
        root_dir,
        _safe_url(write_url),
        max_count=max_count,
        typing_delay=typing_delay,
        publish=publish,
    )
    profile = resolve_profile_path(root, slot, create=False)
    if not profile.is_dir():
        raise BridgeError(f"{profile_name(slot)} 프로필이 없습니다. 먼저 profile-check를 실행하세요.")
    _add_helper_import_path(root)
    try:
        module = importlib.import_module("local_agent.naver_draft")
    except Exception as exc:
        raise BridgeError(f"Mato Helper 네이버 글쓰기 기능을 불러오지 못했습니다: {exc}") from exc

    def report(_progress: int, message: str) -> None:
        if status_callback:
            status_callback(str(message))

    result = module.run_naver_draft_upload(
        profile_path=str(profile),
        root_dir=payload["root_dir"],
        write_url=payload["write_url"],
        template_name=payload["template_name"],
        typing_delay=payload["typing_delay"],
        max_count=payload["max_count"],
        publish_mode=bool(publish),
        headless=False,
        status_callback=report,
    )
    if not isinstance(result, Mapping):
        raise BridgeError("Mato Helper 글쓰기 결과 형식이 올바르지 않습니다.")
    return _safe_draft_result(result, profile, slot)


def _safe_local_title(value: object, fallback: str) -> str:
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(value or "").strip()).strip(" .")
    return title[:100] or fallback


def _naver_postview_url(url: str) -> str:
    """Resolve a public Naver post URL to the document served inside mainFrame.

    Mato Helper's downloader intentionally stays request-based.  Naver's short
    ``/{blogId}/{logNo}`` form now returns only an iframe shell, so handing the
    equivalent public PostView URL to the existing function preserves the
    verified downloader while allowing it to see the article and images.
    """

    parsed = urlparse(str(url or "").strip())
    if parsed.netloc.lower() not in {"blog.naver.com", "m.blog.naver.com"}:
        return str(url or "").strip()
    query = parse_qs(parsed.query)
    blog_id = str((query.get("blogId") or [""])[0]).strip()
    log_no = str((query.get("logNo") or [""])[0]).strip()
    if not (blog_id and log_no):
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[1].isdigit():
            blog_id, log_no = parts[0], parts[1]
    if not (blog_id and log_no):
        return str(url or "").strip()
    return f"https://blog.naver.com/PostView.naver?blogId={blog_id}&logNo={log_no}"


def _normalize_downloaded_images(folder: Path) -> tuple[list[str], dict[str, str]]:
    """Normalize Mato Helper downloads to real ``image_N.jpg`` files."""

    try:
        from PIL import Image
    except Exception as exc:
        raise BridgeError(f"이미지 JPG 정규화 기능을 불러오지 못했습니다: {exc}") from exc

    candidates_by_index: dict[int, list[Path]] = {}
    for path in folder.iterdir():
        match = re.fullmatch(r"image_(\d+)\.[A-Za-z0-9]+", path.name, re.IGNORECASE)
        if path.is_file() and match:
            index = int(match.group(1))
            candidates_by_index.setdefault(index, []).append(path)
    replacements: dict[str, str] = {}
    filenames: list[str] = []
    for index, candidates in sorted(candidates_by_index.items()):
        source = next((path for path in candidates if path.suffix.lower() == ".jpg"), candidates[0])
        target = folder / f"image_{index}.jpg"
        old_name = source.name
        source_is_jpeg = False
        temporary: Path | None = None
        with Image.open(source) as image:
            source_format = str(image.format or "").upper()
            if source_format in {"JPEG", "JPG"}:
                image.verify()
                source_is_jpeg = True
            else:
                image.load()
                converted = image.convert("RGB")
                save_options: dict[str, Any] = {"format": "JPEG", "quality": 95}
                exif = image.info.get("exif")
                icc_profile = image.info.get("icc_profile")
                if exif:
                    save_options["exif"] = exif
                if icc_profile:
                    save_options["icc_profile"] = icc_profile
                temporary = folder / f".image_{index}.jpg.tmp"
                converted.save(temporary, **save_options)
        if source_is_jpeg:
            if source != target:
                if target.exists():
                    target.unlink()
                source.replace(target)
        else:
            if temporary is None:
                raise BridgeError(f"이미지 변환 임시 파일을 만들지 못했습니다: {source.name}")
            os.replace(temporary, target)
            if source != target:
                source.unlink(missing_ok=True)
        replacements[old_name] = target.name
        filenames.append(target.name)
        # A re-download may leave a newly fetched PNG beside a prior JPEG
        # having the same image index.  The normalized JPEG is authoritative;
        # remove only those duplicate representations of this exact index.
        for duplicate in candidates:
            if duplicate != source and duplicate.exists():
                duplicate.unlink()
    return filenames, replacements


def _original_image_url(value: str) -> str:
    """Keep Naver's displayed source URL, including its required size token.

    On ``postfiles.pstatic.net`` removing ``?type=w966`` does not reveal an
    original file.  It can instead return a tiny fallback thumbnail, which
    makes otherwise valid article images fail the pixel-size filter.
    """

    return str(value or "").strip()


def _filter_local_images_by_size(
    folder: Path,
    *,
    min_width: int,
    min_height: int,
) -> dict[str, Any]:
    """Fallback size filter when the article HTML cannot be fetched again."""

    from PIL import Image

    candidates = sorted(
        (
            (int(match.group(1)), path)
            for path in folder.iterdir()
            if path.is_file()
            and (match := re.fullmatch(r"image_(\d+)\.jpg", path.name, re.IGNORECASE))
        ),
        key=lambda item: item[0],
    )
    accepted: list[tuple[int, Path, int, int]] = []
    for old_index, path in candidates:
        try:
            with Image.open(path) as image:
                image.load()
                width, height = image.size
            if width >= min_width and height >= min_height:
                accepted.append((old_index, path, width, height))
        except Exception:
            continue

    staged: list[tuple[int, Path, int, int]] = []
    for new_index, (old_index, source, width, height) in enumerate(accepted, start=1):
        temporary = folder / f".mato-filter-{new_index}.jpg"
        shutil.copy2(source, temporary)
        staged.append((old_index, temporary, width, height))
    for _old_index, path in candidates:
        path.unlink(missing_ok=True)

    old_to_new = {f"image_{old_index}.jpg": "" for old_index, _path in candidates}
    details: list[dict[str, Any]] = []
    for new_index, (old_index, temporary, width, height) in enumerate(staged, start=1):
        target = folder / f"image_{new_index}.jpg"
        os.replace(temporary, target)
        old_to_new[f"image_{old_index}.jpg"] = target.name
        details.append(
            {"source_index": old_index, "file": target.name, "width": width, "height": height}
        )
    return {
        "image_files": [item["file"] for item in details],
        "old_to_new": old_to_new,
        "accepted": details,
        "dropped": len(candidates) - len(details),
        "used_original_urls": False,
    }


def _download_original_images_by_size(
    url: str,
    folder: Path,
    *,
    old_count: int,
    max_images: int,
    min_width: int,
    min_height: int,
) -> dict[str, Any]:
    """Download original article images, filter by pixels, and renumber them.

    Candidate images are considered solely by their decoded original pixel
    dimensions.  The accepted sequence is compacted to ``image_1.jpg`` onward,
    and a tag replacement map is returned for the Mato source text.
    """

    import requests
    from bs4 import BeautifulSoup
    from PIL import Image

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
    response = requests.get(url, headers=headers, timeout=20)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    main = soup.select_one(".se-main-container") or soup.select_one("#postViewArea")
    if main is None:
        return 0
    image_urls: list[str] = []
    for tag in main.select("img"):
        value = str(tag.get("data-lazy-src") or tag.get("data-src") or tag.get("src") or "").strip()
        if value.startswith(("http://", "https://")):
            image_urls.append(_original_image_url(value))
        if int(max_images) > 0 and len(image_urls) >= int(max_images):
            break

    accepted: list[tuple[int, Path, int, int, str]] = []
    for old_index, image_url in enumerate(image_urls, start=1):
        if int(max_images) > 0 and len(accepted) >= int(max_images):
            break
        temporary = folder / f".mato-original-{old_index}.tmp"
        try:
            image_response = requests.get(image_url, headers={**headers, "Referer": url}, timeout=20)
            image_response.raise_for_status()
            temporary.write_bytes(image_response.content)
            with Image.open(temporary) as image:
                image.load()
                width, height = image.size
                if width < min_width or height < min_height:
                    continue
                converted = image.convert("RGB")
                staged = folder / f".mato-filter-{len(accepted) + 1}.jpg"
                converted.save(staged, format="JPEG", quality=95)
            accepted.append((old_index, staged, width, height, image_url))
        except Exception:
            pass
        finally:
            temporary.unlink(missing_ok=True)

    for path in folder.iterdir():
        if path.is_file() and re.fullmatch(r"image_\d+\.jpg", path.name, re.IGNORECASE):
            path.unlink()
    candidate_count = len(image_urls)
    old_to_new = {
        f"image_{index}.jpg": ""
        for index in range(1, max(int(old_count), candidate_count) + 1)
    }
    details: list[dict[str, Any]] = []
    for new_index, (old_index, staged, width, height, image_url) in enumerate(accepted, start=1):
        target = folder / f"image_{new_index}.jpg"
        os.replace(staged, target)
        old_to_new[f"image_{old_index}.jpg"] = target.name
        details.append(
            {
                "source_index": old_index,
                "file": target.name,
                "width": width,
                "height": height,
                "source_url": image_url,
            }
        )
    return {
        "image_files": [item["file"] for item in details],
        "old_to_new": old_to_new,
        "accepted": details,
        "dropped": max(0, candidate_count - len(details)),
        "used_original_urls": True,
    }


def _write_downloaded_hamchuk(
    folder: Path,
    *,
    title: str,
    text_file: str,
    replacements: Mapping[str, str],
) -> Path:
    source = folder / text_file
    if not source.is_file():
        raise BridgeError(f"다운로드 본문 파일을 찾지 못했습니다: {source}")
    content = source.read_text(encoding="utf-8-sig")
    for old_name, new_name in replacements.items():
        replacement = f"[{new_name}]" if new_name else ""
        content = content.replace(f"[{old_name}]", replacement)
    if not any(line.startswith("제목을입력해주세요1:") for line in content.splitlines()[:5]):
        content = f"제목을입력해주세요1: {title}\n\n본문2:\n{content}"
    output = folder / f"{_safe_local_title(title, folder.name)}_함축.txt"
    output.write_text(content.rstrip() + "\n", encoding="utf-8", newline="\n")
    return output


def _clean_naver_text(value: object) -> str:
    """Collapse Naver editor line fragments without losing paragraph breaks."""

    return re.sub(r"[\u200b\ufeff\xa0]", "", str(value or "")).strip()


def _image_lookup_key(value: str) -> str:
    """Normalize an image URL for matching a downloaded local filename."""

    parsed = urlparse(str(value or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return str(value or "").strip()
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def _matched_image_name(image_names_by_url: Mapping[str, str], raw_url: str) -> str:
    normalized = _original_image_url(raw_url)
    return (
        image_names_by_url.get(normalized)
        or image_names_by_url.get(raw_url)
        or image_names_by_url.get(_image_lookup_key(normalized))
        or image_names_by_url.get(_image_lookup_key(raw_url))
        or ""
    )


def _component_paragraphs(component: Any) -> list[str]:
    """Recover editor paragraphs from Naver's visual line-by-line markup."""

    paragraphs: list[str] = []
    pending: list[str] = []
    nodes = component.select(".se-text-paragraph")
    if not nodes:
        text = _clean_naver_text(component.get_text(" ", strip=True))
        return [text] if text else []
    for node in nodes:
        # ``stripped_strings`` keeps the spaces written inside a visual line;
        # adjacent visual lines are deliberately concatenated to rebuild one
        # Korean sentence rather than making a word break at every line wrap.
        text = _clean_naver_text("".join(node.stripped_strings))
        if not text:
            if pending:
                paragraphs.append(re.sub(r"\s+([,.!?…])", r"\1", " ".join(pending)).strip())
                pending = []
            continue
        pending.append(text)
    if pending:
        paragraphs.append(re.sub(r"\s+([,.!?…])", r"\1", " ".join(pending)).strip())
    return [paragraph for paragraph in paragraphs if paragraph]


def _table_marker_lines(table: Any) -> list[str]:
    """Convert a visible Naver table to the writer's lossless table markers."""

    rows = table.find_all("tr")
    if not rows:
        return []
    width = max((sum(max(1, int(cell.get("colspan") or 1)) for cell in row.find_all(["th", "td"], recursive=False)) for row in rows), default=0)
    if width <= 0:
        return []
    lines = [f"표 {len(rows)} x {width} 시작"]
    for row_index, row in enumerate(rows):
        column = 0
        for cell in row.find_all(["th", "td"], recursive=False):
            text = " ".join(_component_paragraphs(cell)) or _clean_naver_text(cell.get_text(" ", strip=True))
            if text:
                lines.append(f"({row_index},{column}) {text}")
            column += max(1, int(cell.get("colspan") or 1))
    lines.append(f"표 {len(rows)} x {width} 끝")
    return lines


def _structured_original_hamchuk(
    page_html: str,
    *,
    source_url: str,
    fallback_title: str,
    image_names_by_url: Mapping[str, str],
) -> tuple[str, str]:
    """Render a downloaded post into the Mato source ``_원본_함축`` format.

    The result preserves source-component order in the body: quotation blocks
    become ``ㅂㅂㅂ`` headings, tables become Naver writer table markers, and
    retained images become their matching ``[image_N.jpg]`` tag.  The first
    ordinary editor text block is also exposed through ``인트로1``.
    """

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(page_html, "html.parser")
    title_node = soup.select_one(".se-title-text") or soup.select_one(".pcol1 .se-title-text")
    title = _clean_naver_text(title_node.get_text(" ", strip=True) if title_node else "") or fallback_title
    main = soup.select_one(".se-main-container") or soup.select_one("#postViewArea")
    if main is None:
        raise BridgeError("원문 본문 컨테이너를 찾지 못했습니다.")

    intro: list[str] = []
    body: list[str] = []
    for component in main.find_all("div", class_="se-component", recursive=False):
        classes = set(component.get("class") or [])
        if "se-table" in classes:
            for table in component.select("table"):
                body.extend(_table_marker_lines(table))
            continue
        if "se-image" in classes:
            image = component.select_one("img")
            if image is not None:
                raw_url = str(image.get("data-lazy-src") or image.get("data-src") or image.get("src") or "")
                image_name = _matched_image_name(image_names_by_url, raw_url)
                if image_name:
                    body.append(f"[{image_name}]")
            continue
        if "se-quotation" in classes:
            heading = " ".join(_component_paragraphs(component))
            if heading:
                body.append(f"ㅂㅂㅂ {heading}")
            continue
        if "se-shopping-connect" in classes:
            body.extend(_component_paragraphs(component))
            image = component.select_one("img")
            if image is not None:
                raw_url = str(image.get("data-lazy-src") or image.get("data-src") or image.get("src") or "")
                image_name = _matched_image_name(image_names_by_url, raw_url)
                if image_name:
                    body.append(f"[{image_name}]")
            continue
        paragraphs = _component_paragraphs(component)
        if not paragraphs:
            continue
        # The first normal text component is the original article opening.
        # Earlier shopping cards, quotation blocks, and tables stay in body.
        if not intro and "se-text" in classes:
            intro.extend(paragraphs)
        else:
            body.extend(paragraphs)

    lines = [f"제목을입력해주세요1: {title}", "", "인트로1:"]
    lines.extend(intro)
    lines.extend(["", "본문2:"])
    lines.extend(body)
    lines.extend(["", f"출처: {source_url}"])
    return title, "\n".join(lines).rstrip() + "\n"


def _write_structured_original_hamchuk(
    folder: Path,
    *,
    page_url: str,
    fallback_title: str,
    accepted_images: list[Mapping[str, Any]],
) -> tuple[Path, str]:
    """Fetch one public post and save its title/text/table/image structure."""

    import requests

    response = requests.get(
        page_url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"},
        timeout=20,
    )
    response.raise_for_status()
    image_names_by_url: dict[str, str] = {}
    for item in accepted_images:
        source = str(item.get("source_url") or "")
        filename = str(item.get("file") or "")
        if source and filename:
            image_names_by_url[_original_image_url(source)] = filename
            image_names_by_url[_image_lookup_key(source)] = filename
    title, content = _structured_original_hamchuk(
        response.text,
        source_url=page_url,
        fallback_title=fallback_title,
        image_names_by_url=image_names_by_url,
    )
    output = folder / f"{_safe_local_title(title, folder.name)}_원본_함축.txt"
    output.write_text(content, encoding="utf-8", newline="\n")
    return output, title


def run_direct_url_download(
    root: Path | None,
    run_dir: str,
    *,
    max_images_per_page: int = 0,
    min_image_width: int = 1,
    min_image_height: int = 1,
    sanitize_images: bool = True,
    status_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Download with the bundled module, then keep adequate original images.

    ``root`` is retained temporarily for call compatibility with earlier
    runs.  It is deliberately unused: downloading no longer imports from or
    reads the Mato Helper checkout.
    """

    min_image_width = max(1, min(10000, int(min_image_width)))
    min_image_height = max(1, min(10000, int(min_image_height)))
    max_images_per_page = max(0, min(10000, int(max_images_per_page)))

    directory = Path(run_dir).expanduser().resolve()
    metadata_path = directory / "sources" / "sources.json"
    if not metadata_path.is_file():
        raise BridgeError(f"수집 출처 목록을 찾지 못했습니다: {metadata_path}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
    sources = metadata.get("sources") if isinstance(metadata, Mapping) else None
    if not isinstance(sources, list) or not sources:
        raise BridgeError("다운로드할 네이버 출처가 없습니다.")

    pairs: list[tuple[str, str, str]] = []
    source_by_folder: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(sources[:5], start=1):
        if not isinstance(raw, Mapping):
            continue
        url = str(raw.get("url") or "").strip()
        title = str(raw.get("title") or "").strip()
        note_file = Path(str(raw.get("note_file") or ""))
        folder_name = note_file.parent.name if note_file.parent.name else f"{index:02d}_{_safe_local_title(title, 'source')}"
        parsed_url = urlparse(url)
        if (
            parsed_url.scheme.lower() not in {"http", "https"}
            or parsed_url.netloc.lower() not in {"blog.naver.com", "m.blog.naver.com"}
        ):
            raise BridgeError(f"지원하지 않는 출처 URL입니다: {url}")
        pairs.append((_naver_postview_url(url), folder_name, title))
        source_by_folder[folder_name] = dict(raw)
    if not pairs:
        raise BridgeError("다운로드할 유효한 네이버 출처가 없습니다.")

    output_root = directory / "sources" / "items"
    output_root.mkdir(parents=True, exist_ok=True)
    try:
        plugin_history = importlib.import_module("history")
    except Exception:
        plugin_history = None
    def report(_progress: int, message: str) -> None:
        if status_callback:
            status_callback(str(message))

    with tempfile.TemporaryDirectory(prefix=".mato-url-download-", dir=directory) as temporary:
        pair_file = Path(temporary) / "url-title-pairs.txt"
        pair_file.write_text(
            "\n".join(value for url, folder, _title in pairs for value in (url, folder)) + "\n",
            encoding="utf-8",
        )
        result = local_url_download.run_naver_url_download(
            txt_path=str(pair_file),
            output_dir=str(output_root),
            max_items=len(pairs),
            max_images_per_page=max_images_per_page,
            status_callback=report,
        )
    if not isinstance(result, Mapping):
        raise BridgeError("Mato Helper 이미지 다운로드 결과 형식이 올바르지 않습니다.")

    safe_entries: list[dict[str, Any]] = []
    for raw in result.get("entries", []):
        if not isinstance(raw, Mapping):
            continue
        folder_name = str(raw.get("folder") or "")
        title = str(source_by_folder.get(folder_name, {}).get("title") or folder_name)
        safe_entry: dict[str, Any] = {
            "folder": folder_name,
            "title": title,
            "url": raw.get("url"),
            "ok": bool(raw.get("ok")),
            "reason": raw.get("reason"),
            "images": 0,
            "image_files": [],
        }
        if safe_entry["ok"]:
            folder = output_root / folder_name
            try:
                _normalized_files, normalize_replacements = _normalize_downloaded_images(folder)
                try:
                    filtered = _download_original_images_by_size(
                        str(raw.get("url") or ""),
                        folder,
                        old_count=len(_normalized_files),
                        max_images=max_images_per_page,
                        min_width=min_image_width,
                        min_height=min_image_height,
                    )
                except Exception:
                    filtered = _filter_local_images_by_size(
                        folder,
                        min_width=min_image_width,
                        min_height=min_image_height,
                    )
                replacements: dict[str, str] = {}
                filter_replacements = filtered["old_to_new"]
                for old_name, normalized_name in normalize_replacements.items():
                    replacements[old_name] = str(filter_replacements.get(normalized_name, ""))
                for old_name, new_name in filter_replacements.items():
                    replacements.setdefault(str(old_name), str(new_name))
                image_files = list(filtered["image_files"])
                image_sanitization: dict[str, Any] | None = None
                if sanitize_images and image_files:
                    sanitizer = importlib.import_module("sanitize_images")
                    image_sanitization = sanitizer.sanitize_path(
                        folder,
                        recursive=False,
                        manifest_path=folder / "image-processing.json",
                    )
                    if not image_sanitization.get("ok"):
                        raise BridgeError(
                            f"이미지 메타데이터 정리에 실패했습니다: {folder_name}"
                        )
                hamchuk, extracted_title = _write_structured_original_hamchuk(
                    folder,
                    page_url=str(raw.get("url") or ""),
                    fallback_title=title,
                    accepted_images=list(filtered["accepted"]),
                )
                safe_entry["title"] = extracted_title
                safe_entry.update(
                    {
                        "images": len(image_files),
                        "image_files": image_files,
                        "image_min_width": min_image_width,
                        "image_min_height": min_image_height,
                        "dropped_small_images": int(filtered["dropped"]),
                        "used_original_image_urls": bool(filtered["used_original_urls"]),
                        "image_dimensions": filtered["accepted"],
                        "image_sanitization": image_sanitization,
                        "original_hamchuk_file": str(hamchuk.resolve()),
                    }
                )
            except Exception as exc:
                safe_entry.update({"ok": False, "reason": str(exc)[:240]})
        safe_entries.append(safe_entry)

    succeeded = sum(1 for item in safe_entries if item.get("ok"))
    failed = len(safe_entries) - succeeded
    summary = {
        "ok": succeeded > 0 and failed == 0,
        "kind": "naver_url_download",
        "run_dir": str(directory),
        "output_dir": str(output_root),
        "processed": len(safe_entries),
        "succeeded": succeeded,
        "failed": failed,
        "entries": safe_entries,
    }
    if plugin_history is not None:
        try:
            plugin_history.append_event(
                directory,
                "source_images_downloaded",
                status="collected" if failed == 0 else "failed",
                message=f"내장 URL 다운로드로 출처 이미지 {succeeded}개 글 성공, {failed}개 글 실패.",
                details={
                    "processed": len(safe_entries),
                    "succeeded": succeeded,
                    "failed": failed,
                    "images": sum(int(item.get("images") or 0) for item in safe_entries),
                    "image_sanitization": bool(sanitize_images),
                },
                run_updates={"source_download": summary},
            )
        except Exception:
            pass
    return summary


def _helper_generation_prompt(root: Path, prompt_key: str) -> tuple[str, str]:
    """Load one Naver prompt through Mato Helper's own prompt loader."""
    _add_helper_import_path(root)
    try:
        module = importlib.import_module("local_agent.html_card")
        prompts = module.load_naver_prompts(root)
    except Exception as exc:
        raise BridgeError(f"Mato Helper 프롬프트를 불러오지 못했습니다: {exc}") from exc
    key = str(prompt_key or "공통").strip() or "공통"
    body = str(prompts.get(key) or "").strip()
    if not body:
        raise BridgeError(f"Mato Helper 프롬프트가 비어 있습니다: {key}")
    return key, body


def managed_prompt_path(prompt_key: str) -> Path:
    """Return the per-PC editable prompt file without allowing path traversal."""

    key = str(prompt_key or "공통").strip() or "공통"
    safe_key = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", key).strip("._") or "공통"
    return (DEFAULT_MANAGED_PROMPT_ROOT / f"{safe_key}.txt").resolve()


def init_managed_prompt(root: Path, prompt_key: str, *, overwrite: bool = False) -> dict[str, Any]:
    """Create or explicitly refresh a locally editable prompt from Mato Helper."""

    key, helper_body = _helper_generation_prompt(root, prompt_key)
    destination = managed_prompt_path(key)
    existed = destination.is_file()
    if overwrite or not existed:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(helper_body.rstrip() + "\n", encoding="utf-8", newline="\n")
    body = destination.read_text(encoding="utf-8-sig")
    return {
        "ok": True,
        "kind": "naver_managed_prompt",
        "prompt_key": key,
        "path": str(destination),
        "created": not existed,
        "overwritten": bool(overwrite and existed),
        "source": str((root / "1_프로그램" / "app.py").resolve()),
        "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "character_count": len(body),
    }


def prompt_status(root: Path, prompt_key: str) -> dict[str, Any]:
    """Report the managed file and whether it differs from Mato Helper."""

    key, helper_body = _helper_generation_prompt(root, prompt_key)
    path = managed_prompt_path(key)
    local_body = path.read_text(encoding="utf-8-sig") if path.is_file() else ""
    helper_text = helper_body.rstrip() + "\n"
    return {
        "ok": True,
        "kind": "naver_managed_prompt_status",
        "prompt_key": key,
        "path": str(path),
        "exists": path.is_file(),
        "modified_from_helper": bool(path.is_file() and local_body != helper_text),
        "local_sha256": hashlib.sha256(local_body.encode("utf-8")).hexdigest() if path.is_file() else "",
        "helper_sha256": hashlib.sha256(helper_text.encode("utf-8")).hexdigest(),
    }


def export_generation_prompt(root: Path, prompt_key: str, output_path: str) -> dict[str, Any]:
    """Export the editable managed prompt plus Mato Helper's live date context."""

    managed = init_managed_prompt(root, prompt_key, overwrite=False)
    key = str(managed["prompt_key"])
    managed_path = Path(str(managed["path"]))
    body = managed_path.read_text(encoding="utf-8-sig").strip()
    today = dt.date.today()
    date_start = today - dt.timedelta(days=30)
    date_end = today - dt.timedelta(days=1)
    weekday = "월화수목금토일"[today.weekday()]
    date_context = (
        "# ⏰ 현재 시점 정보 (필수 참고 — 학습 데이터 시점 X, 실제 오늘 기준)\n"
        f"오늘 날짜: {today:%Y-%m-%d} ({weekday}요일)\n"
        f"방문일자 자동 생성 시 사용할 범위: {date_start:%Y-%m-%d} ~ {date_end:%Y-%m-%d} 사이 임의 날짜\n"
        f"※ 위 범위 밖의 날짜 (특히 {today.year - 1}년 이전) 절대 사용 금지\n"
        "※ 본문 도입부에 'YYYY.MM.DD 방문 기준' 형식으로 명시\n\n"
    )
    effective = date_context + body.rstrip() + "\n"
    destination = Path(output_path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(effective, encoding="utf-8", newline="\n")
    return {
        "ok": True,
        "kind": "naver_generation_prompt",
        "prompt_key": key,
        "source": str((root / "1_프로그램" / "app.py").resolve()),
        "managed_prompt": str(managed_path),
        "output": str(destination),
        "sha256": hashlib.sha256(effective.encode("utf-8")).hexdigest(),
        "character_count": len(effective),
    }


def doctor(root: Path) -> dict[str, Any]:
    """Check source functions and discover existing naver_N folders."""

    store = _load_store(root)
    profiles: dict[str, dict[str, Any]] = {}
    try:
        for item in store.list_playwright_profiles():
            name = str(item.get("name") or "")
            if name.startswith("naver_"):
                path = Path(str(item.get("path") or DEFAULT_PROFILE_ROOT / name)).expanduser()
                profiles[name] = {"name": name, "path": str(path), "exists": path.is_dir()}
    except Exception as exc:
        raise BridgeError(f"Mato Helper 프로필 목록을 확인하지 못했습니다: {exc}") from exc
    if DEFAULT_PROFILE_ROOT.is_dir():
        for path in DEFAULT_PROFILE_ROOT.glob("naver_*"):
            if path.is_dir():
                profiles.setdefault(path.name, {"name": path.name, "path": str(path), "exists": True})
    return {
        "ok": True,
        "helper_root": str(root),
        "transport": "direct-local",
        "profile_check_available": True,
        "draft_writer_available": True,
        "url_image_downloader_available": True,
        "naver_profiles": [profiles[name] for name in sorted(profiles)],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run local Naver profile, draft, and source-download workflows.")
    parser.add_argument("--helper-root", default="", help="Existing google-blog-auto source root.")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("doctor", help="Check Mato Helper functions and numbered Naver profiles.")

    check = commands.add_parser("profile-check", help="Open/check a numbered Mato Helper Naver profile.")
    check.add_argument("--slot", type=int, required=True)
    check.add_argument("--write-url", default="")
    check.add_argument("--interactive-login", action="store_true", help="Compatibility flag; Chrome is always visible.")
    check.add_argument("--timeout", type=int, default=300)

    draft = commands.add_parser("draft", help="Upload local Mato posts through the existing Mato Helper writer.")
    draft.add_argument("--slot", type=int, required=True)
    draft.add_argument("--root-dir", required=True)
    draft.add_argument("--write-url", required=True)
    draft.add_argument("--max-count", type=int, default=1)
    draft.add_argument("--typing-delay", type=int, default=20)
    draft.add_argument("--publish", action="store_true")
    draft.add_argument("--confirm-publish", default="")

    download = commands.add_parser("url-download", help="Download source text and images with the bundled downloader.")
    download.add_argument("--run-dir", required=True)
    download.add_argument("--max-images-per-page", type=int, default=0, help="0이면 본문 이미지를 모두 받습니다.")
    download.add_argument("--min-image-width", type=int, default=1)
    download.add_argument("--min-image-height", type=int, default=1)
    download.add_argument(
        "--skip-image-sanitize",
        action="store_true",
        help="Keep downloaded metadata instead of applying the default privacy cleanup.",
    )

    prompt = commands.add_parser("prompt-export", help="Export Mato Helper's effective Naver prompt.")
    prompt.add_argument("--prompt-key", default="공통")
    prompt.add_argument("--output", required=True)

    prompt_init = commands.add_parser("prompt-init", help="Create the editable per-PC Naver prompt file.")
    prompt_init.add_argument("--prompt-key", default="공통")

    prompt_status_cmd = commands.add_parser("prompt-status", help="Show editable prompt path and hashes.")
    prompt_status_cmd.add_argument("--prompt-key", default="공통")

    prompt_sync = commands.add_parser("prompt-sync", help="Replace the editable prompt with Mato Helper's current prompt.")
    prompt_sync.add_argument("--prompt-key", default="공통")
    prompt_sync.add_argument("--confirm-overwrite", default="")

    args = parser.parse_args(argv)
    try:
        if args.command == "url-download":
            result = run_direct_url_download(
                None,
                args.run_dir,
                max_images_per_page=args.max_images_per_page,
                min_image_width=args.min_image_width,
                min_image_height=args.min_image_height,
                sanitize_images=not args.skip_image_sanitize,
                status_callback=lambda message: print(message, file=sys.stderr, flush=True),
            )
        else:
            root = helper_root(args.helper_root)
            if args.command == "doctor":
                result = doctor(root)
            elif args.command == "profile-check":
                result = run_direct_profile_check(
                    root,
                    args.slot,
                    args.write_url,
                    timeout_seconds=args.timeout,
                    status_callback=lambda message: print(message, file=sys.stderr, flush=True),
                )
            elif args.command == "draft":
                if args.publish and args.confirm_publish != "PUBLISH":
                    raise BridgeError("공개 발행은 --confirm-publish PUBLISH 확인이 필요합니다.")
                result = run_direct_draft(
                    root,
                    args.slot,
                    args.root_dir,
                    args.write_url,
                    max_count=args.max_count,
                    typing_delay=args.typing_delay,
                    publish=args.publish,
                    status_callback=lambda message: print(message, file=sys.stderr, flush=True),
                )
            elif args.command == "prompt-export":
                result = export_generation_prompt(root, args.prompt_key, args.output)
            elif args.command == "prompt-init":
                result = init_managed_prompt(root, args.prompt_key, overwrite=False)
            elif args.command == "prompt-status":
                result = prompt_status(root, args.prompt_key)
            else:
                if args.confirm_overwrite != "SYNC":
                    raise BridgeError("프롬프트 덮어쓰기는 --confirm-overwrite SYNC 확인이 필요합니다.")
                result = init_managed_prompt(root, args.prompt_key, overwrite=True)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
