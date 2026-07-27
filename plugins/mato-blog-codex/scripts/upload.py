"""Plan and execute Mato-compatible Naver uploads with local profiles."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import subprocess
import sys
import time
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from .collect import normalize_naver_post_url
    from .history import append_event, update_run
    from .mato_common import load_run, now_iso, parse_positive_slots
    from .naver_session import (
        ensure_keep_login_checked,
        is_naver_login_required,
        persistent_login_ready,
    )
    from .profiles import get_profile, round_robin_assign
    from .naver_profile_runtime import launch_mato_profile_context
    from .validate_posts import parse_mato_text, verify_validation_manifest
except ImportError:
    from collect import normalize_naver_post_url  # type: ignore[no-redef]
    from history import append_event, update_run  # type: ignore[no-redef]
    from mato_common import load_run, now_iso, parse_positive_slots  # type: ignore[no-redef]
    from naver_session import (  # type: ignore[no-redef]
        ensure_keep_login_checked,
        is_naver_login_required,
        persistent_login_ready,
    )
    from profiles import get_profile, round_robin_assign  # type: ignore[no-redef]
    from naver_profile_runtime import launch_mato_profile_context  # type: ignore[no-redef]
    from validate_posts import parse_mato_text, verify_validation_manifest  # type: ignore[no-redef]


MODES = {"draft", "publish"}
MANUAL_RESOLUTIONS = {"success", "not-uploaded"}
FATAL_CODES = {
    "login_required",
    "captcha_or_access_restricted",
    "profile_in_use",
    "owner_mismatch",
    "editor_structure_changed",
    "save_unverified",
    "publish_unverified",
    "image_upload_failed",
    "template_not_found",
}
IMAGE_TAG_RE = re.compile(
    r"^\s*\[([^\]]+\.(jpg|jpeg|png|gif|webp))\]\s*$", re.IGNORECASE
)
TABLE_START_RE = re.compile(r"^\s*표\s+(\d+)\s*[xX×]\s*(\d+)\s+시작\s*$")
TABLE_CELL_RE = re.compile(r"^\s*\((\d+)\s*,\s*(\d+)\)\s*(.*)$")
TABLE_END_RE = re.compile(r"^\s*표\s+\d+\s*[xX×]\s*\d+\s+끝\s*$")
NAVER_TEMPLATE_NAME = "제목을입력해주세요1:"
NAVER_TITLE_PLACEHOLDER = "제목을입력해주세요1"
NAVER_BODY1_PLACEHOLDER = "본문1:"
NAVER_INTRO_PLACEHOLDER = "인트로1:"
NAVER_BODY_PLACEHOLDER = "본문2:"
NAVER_FALLBACK_PLACEHOLDER = "글감과 함께 나의 일상을 기록해보세요!"
TITLE_TYPING_DELAY_MS = 50
BODY_TYPING_DELAY_MS = 20
PLACEHOLDER_TYPING_DELAY_MS = 30
TABLE_DELAY_MS = 1_500
TABLE_SELECT_DELAY_MS = 2_500
TABLE_DELETE_DELAY_MS = 2_500
RESTRICTION_TEXT = ("비정상적인 접근이 감지", "접근이 제한되었습니다", "자동입력 방지문자를 입력")
DRAFT_SUCCESS_SELECTORS = (
    "[role='alert']:has-text('임시저장이 완료되었습니다')",
    "[role='status']:has-text('임시저장이 완료되었습니다')",
    "[role='alert']:has-text('임시 저장이 완료되었습니다')",
    "[role='status']:has-text('임시 저장이 완료되었습니다')",
    "[role='alert']:has-text('저장이 완료되었습니다')",
    "[role='status']:has-text('저장이 완료되었습니다')",
    "[role='alert']:has-text('저장되었습니다')",
    "[role='status']:has-text('저장되었습니다')",
    ".se-toast:has-text('임시저장')",
    ".se-toast-message:has-text('임시저장')",
    ".toast:has-text('임시저장')",
    ".se-notification:has-text('임시저장')",
    ".se-toast:has-text('저장')",
    ".se-toast-message:has-text('저장')",
    ".toast:has-text('저장')",
    ".se-notification:has-text('저장')",
)
PUBLISH_SUCCESS_SELECTORS = (
    "[role='alert']:has-text('발행되었습니다')",
    "[role='status']:has-text('발행되었습니다')",
    ".se-toast:has-text('발행되었습니다')",
    ".se-toast-message:has-text('발행되었습니다')",
    ".toast:has-text('발행되었습니다')",
    ".se-notification:has-text('발행되었습니다')",
)


class UploadError(RuntimeError):
    def __init__(self, message: str, *, code: str = "upload_failed") -> None:
        super().__init__(message)
        self.code = code


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _editor_body(parsed: Mapping[str, Any]) -> str:
    lines: list[str] = []
    for line in str(parsed.get("body") or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("ㅂㅂㅂ"):
            lines.append(stripped[3:].strip())
        else:
            lines.append(line.rstrip())
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines).strip()


def _editor_lines(parsed: Mapping[str, Any]) -> list[str]:
    """Return Mato editor instructions without flattening special blocks.

    The uploader consumes ``ㅂㅂㅂ`` headings, image tags, and table markers as
    Smart Editor actions.  They must remain intact until the typing loop.
    """

    source = parsed.get("body2_lines")
    if isinstance(source, list):
        lines = [str(line).rstrip() for line in source]
    else:
        lines = [line.rstrip() for line in str(parsed.get("body") or "").splitlines()]
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def _section_lines(parsed: Mapping[str, Any], key: str) -> list[str]:
    source = parsed.get(key)
    if not isinstance(source, list):
        return []
    lines = [str(line).rstrip() for line in source]
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def _editor_sections(parsed: Mapping[str, Any]) -> list[tuple[str, list[str]]]:
    """Return the three template regions in Mato Helper typing order."""

    return [
        (NAVER_BODY1_PLACEHOLDER, _section_lines(parsed, "body1_lines")),
        (NAVER_INTRO_PLACEHOLDER, _section_lines(parsed, "intro_lines")),
        (NAVER_BODY_PLACEHOLDER, _editor_lines(parsed)),
    ]


def _copy_to_clipboard(value: str) -> bool:
    """Copy one URL through the same native clipboard path as Mato Helper."""

    try:
        system = platform.system()
        if system == "Darwin":
            subprocess.run(["pbcopy"], input=value.encode("utf-8"), check=True)
            return True
        if system == "Windows":
            subprocess.run(["clip"], input=value.encode("utf-16le"), check=True)
            return True
        for command in (["xclip", "-selection", "clipboard"], ["xsel", "-b", "-i"]):
            try:
                subprocess.run(command, input=value.encode("utf-8"), check=True)
                return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def _resolve_image_path(asset_dir: Path, image_name: str) -> Path | None:
    """Match Mato image tags across brackets, case, and Unicode normalization."""

    direct = asset_dir / image_name
    if direct.is_file():
        return direct
    bracketed = asset_dir / f"[{image_name}]"
    if bracketed.is_file():
        return bracketed

    target = image_name.casefold()
    target_nfc = unicodedata.normalize("NFC", target)
    target_nfd = unicodedata.normalize("NFD", target)
    try:
        for candidate in asset_dir.iterdir():
            if not candidate.is_file():
                continue
            folded = candidate.name.casefold()
            stripped = folded.strip("[]")
            if (
                folded == target
                or stripped == target
                or unicodedata.normalize("NFC", folded) == target_nfc
                or unicodedata.normalize("NFD", folded) == target_nfd
                or unicodedata.normalize("NFC", stripped) == target_nfc
                or unicodedata.normalize("NFD", stripped) == target_nfd
            ):
                return candidate
    except OSError:
        return None
    return None


def _is_repeated_separator(value: str) -> bool:
    return len(value) >= 3 and len(set(value)) == 1 and not value[0].isalnum()


def _normalize_naver_write_url(value: str) -> str:
    """Accept the legacy and ``/postwrite`` URL forms supported by Mato Helper."""

    write_url = str(value or "").strip()
    legacy = re.search(
        r"(?:https?://)?blog\.naver\.com/PostWriteForm\.naver\?(.*?)$",
        write_url,
        re.IGNORECASE,
    )
    postwrite = re.search(
        r"(?:https?://)?blog\.naver\.com/([^/?\s]+)/postwrite/?(?:\?(.*))?$",
        write_url,
        re.IGNORECASE,
    )
    if legacy:
        query = legacy.group(1)
        blog_id = re.search(r"(?:^|&)blogId=([^&\s]+)", query, re.IGNORECASE)
        if blog_id:
            other = re.sub(r"(?:^|&)blogId=[^&]+", "", query, flags=re.IGNORECASE).strip("&")
            suffix = f"&{other}" if other else ""
            return f"https://blog.naver.com/{blog_id.group(1)}?Redirect=Write{suffix}"
    if postwrite:
        suffix = f"&{postwrite.group(2)}" if postwrite.group(2) else ""
        return f"https://blog.naver.com/{postwrite.group(1)}?Redirect=Write{suffix}"
    return write_url


def _load_post_items(run_dir: Path, run: Mapping[str, Any]) -> list[dict[str, Any]]:
    files = verify_validation_manifest(run_dir, run)
    items: list[dict[str, Any]] = []
    for index, path in enumerate(files, start=1):
        if not path.is_file():
            raise FileNotFoundError(f"generated post is missing: {path}")
        parsed = parse_mato_text(path.read_text(encoding="utf-8-sig"))
        if not parsed["title"] or not parsed["body"]:
            raise ValueError(f"invalid Mato post file: {path}")
        items.append(
            {
                "index": index,
                "title": parsed["title"],
                "file": str(path.relative_to(run_dir)),
                "file_sha256": _file_sha256(path),
                "body": _editor_body(parsed),
            }
        )
    return items


def _plan_signature(run_id: str, mode: str, slots: Sequence[int], assignments: Sequence[Mapping[str, Any]]) -> str:
    compact = {
        "run_id": run_id,
        "mode": mode,
        "profiles": list(slots),
        "assignments": [
            {
                "file": item["file"],
                "file_sha256": item["file_sha256"],
                "profile_slot": item["profile_slot"],
                "account_alias": item.get("account_alias"),
                "blog_url": item.get("blog_url"),
                "write_url": item.get("write_url"),
            }
            for item in assignments
        ],
    }
    data = json.dumps(compact, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def build_upload_plan(run_dir: str | Path, profiles: str, mode: str) -> dict[str, Any]:
    directory = Path(run_dir).expanduser().resolve()
    run = load_run(directory)
    if not run:
        raise FileNotFoundError(f"run.json not found: {directory}")
    validation = run.get("validation")
    if not isinstance(validation, Mapping) or validation.get("status") != "passed":
        raise ValueError("drafts must pass validate_posts.py before upload planning")
    normalized_mode = str(mode).lower()
    if normalized_mode not in MODES:
        raise ValueError("mode must be draft or publish")
    slots = parse_positive_slots(profiles)
    post_items = _load_post_items(directory, run)
    assignments_raw = round_robin_assign(post_items, slots)
    assignments: list[dict[str, Any]] = []
    for row in assignments_raw:
        item = dict(row["item"])
        profile = get_profile(int(row["profile_slot"]))
        if (
            not profile.get("account_alias")
            or not profile.get("blog_url")
            or not profile.get("write_url")
        ):
            raise ValueError(f"profile {profile['slot']} needs an alias/blog URL before upload")
        profile_path = Path(str(profile["profile_path"]))
        if not profile.get("profile_exists"):
            raise ValueError(f"profile directory is missing or unsafe: {profile_path}")
        assignments.append(
            {
                "index": item["index"],
                "title": item["title"],
                "file": item["file"],
                "file_sha256": item["file_sha256"],
                "profile_slot": int(profile["slot"]),
                "profile_name": profile["name"],
                "account_alias": profile.get("account_alias") or "(별칭 없음)",
                "blog_url": profile["blog_url"],
                "write_url": profile["write_url"],
            }
        )
    run_id = str(run.get("run_id") or directory.name)
    signature = _plan_signature(run_id, normalized_mode, slots, assignments)
    return {
        "run_id": run_id,
        "run_dir": str(directory),
        "mode": normalized_mode,
        "profiles": slots,
        "planned_at": now_iso(),
        "signature": signature,
        "assignments": assignments,
    }


def save_upload_plan(plan: Mapping[str, Any]) -> None:
    directory = Path(str(plan["run_dir"]))
    update_run(directory, {"status": "upload_planned", "upload_plan": dict(plan)})
    append_event(
        directory,
        "upload_planned",
        status="upload_planned",
        message=f"{len(plan['assignments'])}개 원고의 {plan['mode']} 업로드 배정을 준비했습니다.",
        details={
            "mode": plan["mode"],
            "profiles": plan["profiles"],
            "assignment_count": len(plan["assignments"]),
            "confirmation_run_id": plan["run_id"],
            "assignments": [
                {
                    "index": item["index"],
                    "title": item["title"],
                    "profile_slot": item["profile_slot"],
                }
                for item in plan["assignments"]
            ],
        },
    )


def _render_plan(plan: Mapping[str, Any]) -> str:
    lines = [
        f"# Naver upload plan — {plan['run_id']}",
        "",
        f"Mode: **{plan['mode']}**",
        "",
        "| # | Title | Profile | Alias | Blog |",
        "|---:|---|---:|---|---|",
    ]
    for row in plan["assignments"]:
        title = str(row["title"]).replace("|", "\\|")
        alias = str(row["account_alias"]).replace("|", "\\|")
        lines.append(
            f"| {row['index']} | {title} | {row['profile_slot']} | {alias} | {row['blog_url']} |"
        )
    lines.extend(
        [
            "",
            "No browser action has been performed.",
            f"Confirm with this exact run ID: `{plan['run_id']}`",
        ]
    )
    return "\n".join(lines)


def _is_login_required(page: Any) -> bool:
    try:
        context_attr = getattr(page, "context", None)
        context = context_attr() if callable(context_attr) else context_attr
    except Exception:
        context = None
    if context is not None:
        return is_naver_login_required(page, context)
    current = str(getattr(page, "url", "")).lower()
    if "nid.naver.com" in current or "nidlogin" in current:
        return True
    try:
        return page.locator("input#id, input[name='id'], input[name='pw']").count() > 0
    except Exception:
        return False


def _page_context(page: Any) -> Any | None:
    try:
        context_attr = getattr(page, "context", None)
        return context_attr() if callable(context_attr) else context_attr
    except Exception:
        return None


def _page_text(page: Any) -> str:
    try:
        return str(page.locator("body").inner_text(timeout=2_000))[:20_000]
    except Exception:
        return ""


def _check_restriction(page: Any) -> None:
    if _is_login_required(page):
        raise UploadError("Naver login is required for this profile.", code="login_required")
    lowered = _page_text(page).lower()
    if any(marker.lower() in lowered for marker in RESTRICTION_TEXT):
        raise UploadError(
            "Naver displayed a CAPTCHA or access restriction; upload stopped.",
            code="captcha_or_access_restricted",
        )


def _scopes(page: Any) -> list[Any]:
    scopes: list[Any] = []
    try:
        if page.locator("iframe#mainFrame").count() > 0:
            scopes.append(page.frame_locator("iframe#mainFrame"))
    except Exception:
        pass
    scopes.append(page)
    return scopes


def _find_visible(scopes: Sequence[Any], selectors: Sequence[str], timeout: int = 1_500) -> Any | None:
    for scope in scopes:
        for selector in selectors:
            try:
                locator = scope.locator(selector)
                count = min(locator.count(), 10)
                for index in range(count):
                    candidate = locator.nth(index)
                    if candidate.is_visible(timeout=timeout):
                        return candidate
            except Exception:
                continue
    return None


def _dismiss_unfinished_draft_prompt(page: Any) -> bool:
    """Cancel Naver's restore-draft dialog before starting a new manuscript.

    Naver can show this dialog either above the editor iframe or inside it.
    The user asked to create a new local draft, so selecting the dialog's
    explicit ``취소`` action is the safe, deterministic equivalent of the
    Mato Helper behavior.  Do not resume, edit, or delete the old draft.
    """

    prompt_selectors = (
        # The current Smart Editor alert has no ``role=dialog`` and its
        # visible Korean text is not reliably matched by Playwright's
        # ``:has-text`` engine.  Its popup-layer attributes and cancel-button
        # class are stable, structural hooks.
        "[data-group='popupLayer'][data-name*='se-popup-alert-confirm']",
        "[role='dialog']:has-text('이어 작성')",
        ".se-popup-container:has-text('이어 작성')",
        "[role='dialog']:has-text('작성 중인 글')",
    )
    prompt = _find_visible(
        _scopes(page),
        prompt_selectors,
        timeout=500,
    )
    if prompt is None:
        return False
    cancel = _find_visible(
        _scopes(page),
        (
            "button.se-popup-button-cancel",
            "[role='dialog'] button:has-text('취소')",
            ".se-popup-container button:has-text('취소')",
            "button[class*='btn_cancel']",
            "button[class*='cancel']",
        ),
        timeout=1_500,
    )
    if cancel is None:
        raise UploadError(
            "The editor has an unfinished-draft prompt. Resolve it manually and retry.",
            code="editor_prompt",
        )
    try:
        cancel.click(timeout=5_000)
        page.wait_for_timeout(1_000)
    except Exception as exc:
        raise UploadError(
            "Could not cancel Naver's unfinished-draft prompt.", code="editor_prompt"
        ) from exc
    if _find_visible(_scopes(page), prompt_selectors, timeout=500) is not None:
        raise UploadError(
            "Naver's unfinished-draft prompt remained open after cancel.", code="editor_prompt"
        )
    return True


def _verify_input(locator: Any, expected: str) -> bool:
    needle = re.sub(r"\s+", "", expected)[:20]
    try:
        actual = re.sub(r"\s+", "", locator.inner_text(timeout=2_000))
        return bool(needle and needle in actual)
    except Exception:
        return False


def _wait_for_visible_feedback(page: Any, selectors: Sequence[str], timeout_ms: int) -> bool:
    """Wait for a dedicated visible toast/status element, never editor body text."""

    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        if _find_visible(_scopes(page), selectors, timeout=100) is not None:
            return True
        try:
            page.wait_for_timeout(250)
        except Exception:
            break
    return False


class NaverTextUploader:
    def __init__(
        self,
        page: Any,
        *,
        login_timeout_seconds: int = 300,
        admin_subtitle_style: bool = False,
    ) -> None:
        self.page = page
        self.login_timeout_seconds = max(30, min(int(login_timeout_seconds), 900))
        self.admin_subtitle_style = bool(admin_subtitle_style)

    def _wait_for_manual_login(self) -> None:
        """Keep the visible profile window open until its user logs in normally."""

        context = _page_context(self.page)
        if not _is_login_required(self.page):
            if context is None or persistent_login_ready(context, self.page):
                return
        ensure_keep_login_checked(self.page)
        deadline = time.monotonic() + self.login_timeout_seconds
        while time.monotonic() < deadline:
            lowered = _page_text(self.page).lower()
            if any(marker.lower() in lowered for marker in RESTRICTION_TEXT):
                raise UploadError(
                    "Naver displayed a CAPTCHA or access restriction; upload stopped.",
                    code="captcha_or_access_restricted",
                )
            if not _is_login_required(self.page):
                self.page.wait_for_timeout(2_000)
                if context is None or persistent_login_ready(context, self.page):
                    return
            else:
                ensure_keep_login_checked(self.page)
            self.page.wait_for_timeout(1_000)
        raise UploadError(
            "Naver login was not completed in the visible profile window.", code="login_required"
        )

    def _editor_frame(self) -> Any:
        if self.page.locator("iframe#mainFrame").count() < 1:
            raise UploadError(
                "Naver editor iframe was not found.", code="editor_structure_changed"
            )
        return self.page.frame_locator("iframe#mainFrame")

    @staticmethod
    def _exact_text(scope: Any, value: str, *, timeout: int = 2_000) -> Any | None:
        try:
            locator = scope.get_by_text(value, exact=True).last
            if locator.is_visible(timeout=timeout):
                return locator
        except Exception:
            pass
        return None

    def _apply_mato_template(self, frame: Any) -> bool:
        """Load Mato's own template, returning False for original-style fallback."""

        button = _find_visible(
            (frame,),
            (
                "button[data-name='template']",
                "button[aria-label*='템플릿']",
            ),
            timeout=3_000,
        )
        if button is None:
            return False
        try:
            button.click(timeout=5_000)
        except Exception:
            return False
        mine = _find_visible(
            (frame,),
            (
                "button.se-tab-button[value='my']",
                "button:has-text('내 템플릿')",
            ),
            timeout=3_000,
        )
        if mine is None:
            return False
        try:
            mine.click(timeout=5_000)
        except Exception:
            return False
        self.page.wait_for_timeout(1_000)
        target = None
        cards = frame.locator(
            ".se-tab-content-my-template.se-is-on "
            ".se-doc-template[data-name='template-my']"
        )
        try:
            for index in range(min(cards.count(), 100)):
                card = cards.nth(index)
                card_title = card.locator(".se-doc-template-title").first
                visible_title = card_title.inner_text(timeout=1_000).strip()
                if (
                    card.is_visible(timeout=500)
                    and visible_title.rstrip(":：").strip()
                    == NAVER_TEMPLATE_NAME.rstrip(":：").strip()
                ):
                    target = card
                    break
        except Exception:
            target = None
        if target is None:
            for label in (
                NAVER_TEMPLATE_NAME,
                NAVER_TEMPLATE_NAME.rstrip(":：").strip(),
            ):
                target = self._exact_text(frame, label, timeout=1_000)
                if target is not None:
                    break
        if target is None:
            close_sidebar = _find_visible(
                (frame,), ("button.se-sidebar-close-button",), timeout=1_000
            )
            if close_sidebar is not None:
                close_sidebar.click(timeout=5_000)
            return False
        try:
            target.click(timeout=5_000)
        except Exception:
            return False
        self.page.wait_for_timeout(2_000)
        close_sidebar = _find_visible(
            (frame,), ("button.se-sidebar-close-button",), timeout=1_000
        )
        if close_sidebar is not None:
            close_sidebar.click(timeout=5_000)
        return True

    def open_editor(self, write_url: str) -> list[Any]:
        target_url = _normalize_naver_write_url(write_url)
        self.page.goto(target_url, wait_until="domcontentloaded", timeout=60_000)
        self.page.wait_for_timeout(4_000)
        self._wait_for_manual_login()
        _check_restriction(self.page)
        current = str(self.page.url)
        if "BlogHome.naver" in current or "section.blog.naver.com" in current:
            raise UploadError(
                "The logged-in account does not own the configured blog.", code="owner_mismatch"
            )
        _dismiss_unfinished_draft_prompt(self.page)
        frame = self._editor_frame()
        close_help = _find_visible(
            (frame,), ("button.se-help-panel-close-button",), timeout=1_000
        )
        if close_help is not None:
            close_help.click(timeout=5_000)
        template_found = self._apply_mato_template(frame)
        scopes = (frame,)
        title = _find_visible(
            scopes,
            (
                ".se-section-documentTitle .se-text-paragraph",
                ".se-title-text",
                "[contenteditable='true'][data-placeholder*='제목']",
                "[contenteditable='true'][aria-label*='제목']",
            ),
            timeout=3_000,
        )
        if title is None:
            raise UploadError(
                "Naver title editor was not found.", code="editor_structure_changed"
            )
        return [frame, title, template_found]

    def _upload_image(self, image_path: Path) -> None:
        button = _find_visible(
            _scopes(self.page),
            (
                "button[data-name='image']",
                "button[aria-label='사진']",
            ),
            timeout=3_000,
        )
        if button is None:
            raise UploadError("Image upload button was not found.", code="image_upload_failed")
        try:
            with self.page.expect_file_chooser(timeout=15_000) as chooser_info:
                button.click(force=True, timeout=5_000)
            chooser_info.value.set_files(str(image_path))
            self.page.wait_for_timeout(4_000)
        except Exception as exc:
            raise UploadError(
                f"Could not upload image: {image_path.name}", code="image_upload_failed"
            ) from exc

    @staticmethod
    def _replace_line(locator: Any, value: str, *, delay: int, backspace: bool) -> None:
        locator.click(timeout=5_000)
        locator.press("End")
        locator.press("Shift+Home")
        if backspace:
            locator.press("Backspace")
        locator.type(value, delay=delay)

    @staticmethod
    def _clean_title(title: str) -> str:
        value = str(title or "").strip()
        match = re.match(r"^제목을입력해주세요1\s*:\s*(.*)$", value)
        if match:
            value = match.group(1).strip()
        return value if len(value) <= 40 else value[:38] + ".."

    def _insert_table(
        self,
        frame: Any,
        rows: int,
        cols: int,
        cells: Mapping[tuple[int, int], str],
    ) -> None:
        if rows < 1 or cols < 1 or rows > 20 or cols > 10:
            raise UploadError("Unsupported Mato table size.", code="editor_structure_changed")
        table_button = _find_visible((frame,), ("button[data-name='table']",), timeout=3_000)
        if table_button is None:
            raise UploadError("Naver table button was not found.", code="editor_structure_changed")
        table_button.click(timeout=5_000)
        self.page.wait_for_timeout(TABLE_DELAY_MS)

        def section() -> Any:
            return frame.locator("div.se-section-table").last

        def table() -> Any:
            return frame.locator("table.se-table-content").last

        def shape() -> tuple[int, int]:
            current = table()
            row_count = current.locator("tr.se-tr").count()
            col_count = (
                current.locator("tr.se-tr").first.locator("td.se-cell").count()
                if row_count
                else 0
            )
            return row_count, col_count

        def reactivate() -> None:
            table().locator("td.se-cell").first.click(force=True, timeout=5_000)
            self.page.wait_for_timeout(TABLE_DELAY_MS)

        def add_rows() -> None:
            attempts = 0
            limit = max(6, max(0, rows - shape()[0]) * 4)
            while shape()[0] < rows and attempts < limit:
                attempts += 1
                before = shape()[0]
                reactivate()
                section().locator(
                    "ul.se-cell-controlbar-row li:last-child button.se-cell-add-button"
                ).first.click(force=True, timeout=5_000)
                deadline = time.monotonic() + 3
                while shape()[0] <= before and time.monotonic() < deadline:
                    self.page.wait_for_timeout(200)

        def delete_rows() -> None:
            attempts = 0
            limit = max(6, max(0, shape()[0] - rows) * 4)
            while shape()[0] > rows and attempts < limit:
                attempts += 1
                before = shape()[0]
                last_row = table().locator("tr.se-tr").last
                first_cell = last_row.locator("td.se-cell").first
                last_cell = last_row.locator("td.se-cell").last
                first_box = first_cell.bounding_box()
                last_box = last_cell.bounding_box()
                if not first_box or not last_box:
                    break
                self.page.wait_for_timeout(TABLE_SELECT_DELAY_MS)
                self.page.mouse.move(
                    first_box["x"] + first_box["width"] / 2,
                    first_box["y"] + first_box["height"] / 2,
                )
                self.page.mouse.down()
                self.page.mouse.move(
                    last_box["x"] + last_box["width"] / 2,
                    last_box["y"] + last_box["height"] / 2,
                    steps=20,
                )
                self.page.mouse.up()
                self.page.wait_for_timeout(TABLE_DELETE_DELAY_MS)
                self.page.keyboard.press("Delete")
                self.page.wait_for_timeout(TABLE_DELAY_MS)
                if shape()[0] >= before:
                    break
                if shape()[0] > rows:
                    reactivate()

        def add_columns() -> None:
            attempts = 0
            limit = max(6, max(0, cols - shape()[1]) * 4)
            while shape()[1] < cols and attempts < limit:
                attempts += 1
                before = shape()[1]
                reactivate()
                section().locator(
                    "ul.se-cell-controlbar-column li:last-child button.se-cell-add-button"
                ).first.click(force=True, timeout=5_000)
                deadline = time.monotonic() + 3
                while shape()[1] <= before and time.monotonic() < deadline:
                    self.page.wait_for_timeout(200)

        def delete_columns() -> None:
            attempts = 0
            limit = max(6, max(0, shape()[1] - cols) * 5)
            while shape()[1] > cols and attempts < limit:
                attempts += 1
                before = shape()[1]
                all_rows = table().locator("tr.se-tr")
                first_cell = all_rows.first.locator("td.se-cell").last
                last_cell = all_rows.last.locator("td.se-cell").last
                first_box = first_cell.bounding_box()
                last_box = last_cell.bounding_box()
                if not first_box or not last_box:
                    break
                edge = 4
                self.page.wait_for_timeout(TABLE_SELECT_DELAY_MS)
                self.page.mouse.move(first_box["x"] + edge, first_box["y"] + edge)
                self.page.mouse.down()
                self.page.mouse.move(
                    last_box["x"] + last_box["width"] - edge,
                    last_box["y"] + last_box["height"] - edge,
                    steps=25,
                )
                self.page.mouse.up()
                self.page.wait_for_timeout(TABLE_DELETE_DELAY_MS)
                self.page.keyboard.press("Delete")
                self.page.wait_for_timeout(500)
                if shape()[1] >= before:
                    self.page.wait_for_timeout(TABLE_SELECT_DELAY_MS)
                    first_cell.click(force=True, timeout=5_000)
                    self.page.wait_for_timeout(250)
                    last_cell.click(modifiers=["Shift"], force=True, timeout=5_000)
                    self.page.wait_for_timeout(TABLE_DELETE_DELAY_MS)
                    self.page.keyboard.press("Delete")
                    self.page.wait_for_timeout(500)
                self.page.wait_for_timeout(TABLE_DELAY_MS)
                if shape()[1] > cols:
                    reactivate()

        reactivate()
        add_rows()
        delete_rows()
        add_columns()
        delete_columns()

        # A dragged last-column selection can also remove a row.  Mato Helper
        # rechecks and restores the target shape before typing cell contents.
        add_rows()
        delete_rows()

        if shape() != (rows, cols):
            raise UploadError(
                f"Naver table shape mismatch: expected {rows}x{cols}, got {shape()[0]}x{shape()[1]}.",
                code="editor_structure_changed",
            )
        for row in range(rows):
            for col in range(cols):
                value = str(cells.get((row, col), "")).strip()
                if not value:
                    continue
                paragraph = table().locator(
                    f"tbody tr.se-tr:nth-child({row + 1}) "
                    f"td.se-cell:nth-child({col + 1}) p.se-text-paragraph"
                ).first
                if paragraph.count() < 1:
                    raise UploadError(
                        f"Naver table cell is missing: ({row},{col}).",
                        code="editor_structure_changed",
                    )
                paragraph.click(force=True, timeout=8_000)
                self.page.keyboard.type(value, delay=BODY_TYPING_DELAY_MS)
                self.page.wait_for_timeout(TABLE_DELAY_MS)

        box = section().bounding_box()
        if box:
            self.page.mouse.click(
                box["x"] + min(max(box["width"] / 2, 24), max(24, box["width"] - 24)),
                box["y"] + box["height"] + 16,
            )
            self.page.wait_for_timeout(400)
        else:
            self.page.keyboard.press("ArrowDown")
            self.page.wait_for_timeout(1_000)
        self.page.keyboard.press("Enter")
        self.page.wait_for_timeout(300)

    def _editor_contains(self, frame: Any, expected: str) -> bool:
        needle = re.sub(r"\s+", "", expected)[:20]
        paragraphs = frame.locator(".se-section-text .se-text-paragraph")
        try:
            for index in range(min(paragraphs.count(), 250)):
                actual = re.sub(r"\s+", "", paragraphs.nth(index).inner_text(timeout=1_000))
                if needle and needle in actual:
                    return True
        except Exception:
            pass
        return False

    def _clear_region_placeholder(self, locator: Any) -> None:
        """Clear a Mato body marker with the helper's click-selection sequence."""

        locator.click(timeout=5_000)
        box = locator.bounding_box()
        if box:
            self.page.mouse.dblclick(box["x"] + 5, box["y"] + 5)
            self.page.keyboard.press("Backspace")
            return
        locator.press("End")
        locator.press("Shift+Home")
        locator.press("Backspace")

    def _select_naver_paragraph_style(self, frame: Any, style_name: str) -> bool:
        """Select the optional Smart Editor paragraph style used by Mato admin runs."""

        style = str(style_name or "").strip()
        if not style:
            return False

        def exact(selector: str, text: str) -> Any:
            return frame.locator(selector).filter(
                has_text=re.compile(rf"^\s*{re.escape(text)}\s*$")
            ).first

        opened = False
        for label in ("본문", "소제목", "제목"):
            for locator in (
                exact("span.se-toolbar-label[data-role='label']", label),
                frame.locator(
                    f"button:has(span.se-toolbar-label[data-role='label']:text-is('{label}'))"
                ).first,
            ):
                try:
                    if locator.count() > 0 and locator.is_visible(timeout=1_000):
                        locator.click(timeout=2_500)
                        self.page.wait_for_timeout(400)
                        opened = True
                        break
                except Exception:
                    continue
            if opened:
                break
        if not opened:
            return False

        for locator in (
            exact("span.se-toolbar-option-label", style),
            frame.locator(
                f"button:has(span.se-toolbar-option-label:text-is('{style}'))"
            ).first,
        ):
            try:
                if locator.count() > 0 and locator.is_visible(timeout=2_000):
                    locator.click(timeout=2_500)
                    self.page.wait_for_timeout(400)
                    return True
            except Exception:
                continue
        return False

    def _apply_toolbar_token(self, frame: Any, token: str, content: str) -> bool:
        """Apply Mato's ``!!``, ``ㅂㅂㅂ``, and ``소제목`` Smart Editor actions."""

        if token in {"ㅂㅂㅂ", "소제목"} and self.admin_subtitle_style:
            if self._select_naver_paragraph_style(frame, "소제목"):
                if content:
                    self.page.keyboard.type(content, delay=BODY_TYPING_DELAY_MS)
                self.page.keyboard.press("Enter")
                self.page.wait_for_timeout(400)
                return True
            return False

        name = "horizontal-line" if token == "!!" else "quotation"
        button = _find_visible(
            (frame,),
            (f"button[data-name='{name}'][data-value='default']",),
            timeout=2_000,
        )
        if button is None:
            return False
        try:
            button.click(timeout=5_000)
            self.page.wait_for_timeout(1_000)
            if name == "quotation" and content:
                self.page.keyboard.type(content, delay=BODY_TYPING_DELAY_MS)
                self.page.wait_for_timeout(1_000)
                self.page.keyboard.press("ArrowDown")
                self.page.wait_for_timeout(1_000)
                self.page.keyboard.press("ArrowDown")
                self.page.wait_for_timeout(1_000)
                self.page.keyboard.press("Enter")
                self.page.wait_for_timeout(300)
            return True
        except Exception:
            return False

    def _process_lines(
        self,
        frame: Any,
        lines: Sequence[str],
        start_placeholder: str,
        asset_dir: Path,
    ) -> str:
        if not lines:
            return ""
        start = self._exact_text(frame, start_placeholder, timeout=3_000)
        if start is None:
            raise UploadError(
                f"Naver editor placeholder was not found: {start_placeholder}",
                code="template_not_found",
            )
        self._clear_region_placeholder(start)

        first_meaningful = next((str(line).strip() for line in lines if str(line).strip()), "")
        if (
            self.admin_subtitle_style
            and start_placeholder.startswith("본문2")
            and first_meaningful.startswith(("ㅂㅂㅂ", "소제목"))
        ):
            self.page.wait_for_timeout(1_000)

        first_text = ""
        table_state: dict[str, Any] | None = None
        for raw_line in lines:
            line = str(raw_line).strip()
            if table_state is None:
                table_start = TABLE_START_RE.match(line)
                if table_start:
                    table_state = {
                        "rows": int(table_start.group(1)),
                        "cols": int(table_start.group(2)),
                        "cells": {},
                    }
                    continue
            else:
                if TABLE_END_RE.match(line):
                    self._insert_table(
                        frame,
                        int(table_state["rows"]),
                        int(table_state["cols"]),
                        table_state["cells"],
                    )
                    table_state = None
                    continue
                table_cell = TABLE_CELL_RE.match(line)
                if table_cell:
                    table_state["cells"][(int(table_cell.group(1)), int(table_cell.group(2)))] = (
                        table_cell.group(3).strip()
                    )
                continue

            if not line:
                self.page.keyboard.press("Enter")
                self.page.wait_for_timeout(100)
                continue

            matched_token = next(
                (token for token in ("!!", "ㅂㅂㅂ", "소제목") if line.startswith(token)),
                None,
            )
            if matched_token is not None:
                content = line[len(matched_token):].strip()
                if self._apply_toolbar_token(frame, matched_token, content):
                    if content and not first_text:
                        first_text = content
                    continue

            if _is_repeated_separator(line):
                continue

            image_match = IMAGE_TAG_RE.fullmatch(line)
            if image_match:
                image_name = image_match.group(1)
                image_path = _resolve_image_path(asset_dir, image_name)
                if image_path is None:
                    raise UploadError(
                        f"Referenced image is missing: {image_name}",
                        code="image_upload_failed",
                    )
                self._upload_image(image_path)
                continue

            if line.casefold().startswith(("http://", "https://")):
                pasted = _copy_to_clipboard(line)
                if pasted:
                    paste_key = "Meta+V" if platform.system() == "Darwin" else "Control+V"
                    try:
                        self.page.keyboard.press(paste_key)
                    except Exception:
                        pasted = False
                if not pasted:
                    self.page.keyboard.type(line, delay=BODY_TYPING_DELAY_MS)
                self.page.keyboard.press("Enter")
                self.page.wait_for_timeout(4_000)
                continue

            self.page.keyboard.type(line, delay=BODY_TYPING_DELAY_MS)
            if not first_text:
                first_text = line
            self.page.keyboard.press("Enter")
            self.page.wait_for_timeout(200)

        if table_state is not None:
            raise UploadError("Mato table block was not closed.", code="editor_structure_changed")
        return first_text

    def write(self, title: str, parsed: Mapping[str, Any], asset_dir: Path) -> None:
        frame, title_box, template_found = self.open_editor_fields
        clean_title = self._clean_title(title)
        self._replace_line(
            title_box, clean_title, delay=TITLE_TYPING_DELAY_MS, backspace=False
        )
        title_placeholder = self._exact_text(
            frame, NAVER_TITLE_PLACEHOLDER, timeout=2_000
        )
        if title_placeholder is not None:
            self._replace_line(
                title_placeholder,
                clean_title,
                delay=PLACEHOLDER_TYPING_DELAY_MS,
                backspace=True,
            )
        first_text = ""
        sections = _editor_sections(parsed)
        if template_found:
            for placeholder, lines in sections:
                typed = self._process_lines(frame, lines, placeholder, asset_dir)
                if typed and not first_text:
                    first_text = typed
        else:
            fallback_lines = [line for _placeholder, lines in sections for line in lines]
            first_text = self._process_lines(
                frame,
                fallback_lines,
                NAVER_FALLBACK_PLACEHOLDER,
                asset_dir,
            )

        if not _verify_input(title_box, clean_title) or (
            first_text and not self._editor_contains(frame, first_text)
        ):
            raise UploadError(
                "Could not verify typed text in the editor.",
                code="editor_structure_changed",
            )

    def prepare(self, write_url: str, title: str, parsed: Mapping[str, Any], asset_dir: Path) -> None:
        self.open_editor_fields = self.open_editor(write_url)
        self.write(title, parsed, asset_dir)

    def save_draft(self) -> dict[str, Any]:
        scopes = _scopes(self.page)
        selectors = (
            "button[data-click-area='tpb.save']",
            "button:has(span:text-is('저장'))",
            "button:has-text('저장')",
            "a:has-text('임시저장')",
            "button:has-text('임시저장')",
        )
        clicked = False
        for attempt in range(1, 4):
            if attempt > 1:
                self.page.wait_for_timeout(3_000)
            button = _find_visible(scopes, selectors, timeout=2_500)
            if button is None:
                continue
            try:
                button.click(timeout=3_500)
            except Exception:
                try:
                    button.click(timeout=3_500, force=True)
                except Exception:
                    continue
            clicked = True
            self.page.wait_for_timeout(750)
            dialog_button = _find_visible(
                scopes,
                (
                    "[role='dialog'] button:has-text('저장')",
                    "[role='dialog'] button:has-text('확인')",
                    ".se-popup-container button:has-text('저장')",
                    ".se-popup-container button:has-text('확인')",
                ),
                timeout=2_000,
            )
            if dialog_button is not None:
                dialog_button.click(timeout=5_000)
            if _wait_for_visible_feedback(self.page, DRAFT_SUCCESS_SELECTORS, 5_000):
                self.page.wait_for_timeout(3_000)
                return {"status": "success", "verified_url": None}
        if not clicked:
            raise UploadError("Draft save button was not found.", code="editor_structure_changed")
        raise UploadError(
            "The draft button was clicked three times, but completion could not be verified. Check Naver manually.",
            code="save_unverified",
        )

    def publish(self) -> dict[str, Any]:
        scopes = _scopes(self.page)
        first = _find_visible(
            scopes,
            (
                "button[data-click-area='tpb.publish']",
                "button:has(span:text-is('발행'))",
            ),
            timeout=2_000,
        )
        if first is None:
            raise UploadError("Publish button was not found.", code="editor_structure_changed")
        before = str(self.page.url)
        first.click(timeout=5_000)
        self.page.wait_for_timeout(1_000)
        confirm = _find_visible(
            scopes,
            (
                "button[data-testid='seOnePublishBtn']",
                "button[data-click-area='tpb*i.publish']",
                "[role='dialog'] button:has-text('발행')",
                ".layer_popup button:has-text('발행')",
            ),
            timeout=2_000,
        )
        if confirm is None:
            raise UploadError("Publish confirmation button was not found.", code="publish_unverified")
        confirm.click(timeout=5_000)
        deadline = time.monotonic() + 15
        verified_url: str | None = None
        verified = False
        while time.monotonic() < deadline:
            current = str(self.page.url)
            verified_url = normalize_naver_post_url(current)
            if verified_url and current != before:
                verified = True
                break
            if _wait_for_visible_feedback(self.page, PUBLISH_SUCCESS_SELECTORS, 250):
                verified = True
                break
            self.page.wait_for_timeout(500)
        if not verified:
            raise UploadError(
                "Publish was clicked, but completion could not be verified. Check Naver manually.",
                code="publish_unverified",
            )
        return {"status": "success", "verified_url": verified_url}

    def upload(
        self,
        write_url: str,
        title: str,
        parsed: Mapping[str, Any],
        mode: str,
        asset_dir: Path,
    ) -> dict[str, Any]:
        self.prepare(write_url, title, parsed, asset_dir)
        return self.publish() if mode == "publish" else self.save_draft()


def _existing_upload_entries(run: Mapping[str, Any]) -> list[dict[str, Any]]:
    uploads = run.get("uploads")
    if isinstance(uploads, Mapping) and isinstance(uploads.get("entries"), list):
        return [dict(item) for item in uploads["entries"] if isinstance(item, Mapping)]
    return []


def _entry_key(row: Mapping[str, Any], mode: str) -> str:
    return f"{row['file']}|{row['file_sha256']}|{row['profile_slot']}|{mode}"


def _save_upload_state(run_dir: Path, mode: str, entries: Sequence[Mapping[str, Any]], status: str) -> None:
    success_count = sum(1 for item in entries if item.get("status") == "success")
    update_run(
        run_dir,
        {
            "status": status,
            "uploads": {
                "mode": mode,
                "status": status,
                "success_count": success_count,
                "entries": [dict(item) for item in entries],
            },
        },
    )


def resolve_uncertain_upload(
    run_dir: str | Path,
    *,
    index: int,
    entry_key: str,
    resolution: str,
    confirm: str,
    verified_url: str | None = None,
) -> dict[str, Any]:
    """Resolve one uncertain result after the user checks Naver manually."""

    directory = Path(run_dir).expanduser().resolve()
    run = load_run(directory)
    if not run:
        raise FileNotFoundError(f"run.json not found: {directory}")
    run_id = str(run.get("run_id") or directory.name)
    if confirm != run_id:
        raise ValueError("--confirm must exactly match the displayed run ID")
    normalized_resolution = str(resolution).lower()
    if normalized_resolution not in MANUAL_RESOLUTIONS:
        raise ValueError("resolution must be success or not-uploaded")

    uploads = run.get("uploads")
    if not isinstance(uploads, Mapping):
        raise ValueError("no upload state exists for this run")
    mode = str(uploads.get("mode") or "")
    if mode not in MODES:
        raise ValueError("upload state has no valid mode")
    entries = _existing_upload_entries(run)
    matches = [
        item
        for item in entries
        if int(item.get("index", -1)) == int(index) and str(item.get("key") or "") == entry_key
    ]
    if len(matches) != 1:
        raise ValueError("the exact uncertain index and entry key were not found")
    entry = matches[0]
    if entry.get("status") != "uncertain":
        raise ValueError("the selected upload entry is not uncertain")

    saved_plan = run.get("upload_plan")
    if not isinstance(saved_plan, Mapping) or str(saved_plan.get("mode") or "") != mode:
        raise ValueError("the saved upload plan does not match the uncertain entry")
    assignments = saved_plan.get("assignments")
    if not isinstance(assignments, list):
        raise ValueError("the saved upload plan is incomplete")
    expected_keys = {
        _entry_key(item, mode)
        for item in assignments
        if isinstance(item, Mapping)
    }
    if entry_key not in expected_keys:
        raise ValueError("the uncertain entry is not part of the confirmed upload plan")

    canonical_url = normalize_naver_post_url(str(verified_url or "")) if verified_url else None
    if verified_url and not canonical_url:
        raise ValueError("--verified-url must be a direct Naver blog post URL")
    if normalized_resolution == "success" and mode == "publish" and not canonical_url:
        raise ValueError("a manually confirmed publish requires --verified-url")
    if normalized_resolution == "not-uploaded" and verified_url:
        raise ValueError("--verified-url cannot be used with not-uploaded")

    resolved_at = now_iso()
    if normalized_resolution == "success":
        entry.update(
            {
                "status": "success",
                "completed_at": resolved_at,
                "verified_url": canonical_url,
                "manual_resolution": "success",
                "resolved_at": resolved_at,
            }
        )
    else:
        entry.update(
            {
                "status": "pending",
                "manual_resolution": "not-uploaded",
                "resolved_at": resolved_at,
            }
        )
        entry.pop("verified_url", None)

    expected_success = {
        str(item.get("key") or "")
        for item in entries
        if item.get("status") == "success" and str(item.get("mode") or "") == mode
    }
    next_status = "completed" if expected_keys and expected_keys <= expected_success else "upload_planned"
    _save_upload_state(directory, mode, entries, next_status)
    append_event(
        directory,
        "upload_uncertain_resolved",
        status=next_status,
        message=(
            f"원고 {index}번의 불확실한 {mode} 결과를 "
            f"{normalized_resolution} 상태로 수동 확인했습니다."
        ),
        details={
            "index": index,
            "entry_key": entry_key,
            "mode": mode,
            "resolution": normalized_resolution,
            "verified_url": canonical_url,
        },
    )
    return {
        "ok": True,
        "run_id": run_id,
        "run_dir": str(directory),
        "index": index,
        "entry_key": entry_key,
        "mode": mode,
        "resolution": normalized_resolution,
        "status": entry["status"],
        "verified_url": canonical_url,
        "run_status": next_status,
    }


def _launch_context(playwright: Any, profile_path: str, *, headless: bool) -> Any:
    try:
        return launch_mato_profile_context(playwright, profile_path, headless=headless)
    except Exception as exc:
        message = str(exc)
        lowered = message.lower()
        code = "profile_in_use" if any(
            marker in lowered for marker in ("processsingleton", "user data directory is already in use", "profile in use")
        ) else "browser_launch_failed"
        raise UploadError(f"Could not open the Chrome profile: {message[:300]}", code=code) from exc


def _open_profile_context(
    playwright: Any,
    profile_path: str,
    *,
    headless: bool,
) -> tuple[Any, bool, Any | None]:
    """Use the same persistent-context lifecycle as the profile login flow."""

    return _launch_context(playwright, profile_path, headless=headless), True, None


def _record_upload_failure(run_dir: Path, exc: BaseException) -> None:
    code = exc.code if isinstance(exc, UploadError) else type(exc).__name__
    append_event(
        run_dir,
        "upload_failed",
        status="failed",
        message=str(exc) or "Upload failed before completion.",
        details={"error_code": code},
    )


def execute_upload(
    current_plan: Mapping[str, Any],
    *,
    confirm: str,
    headless: bool = False,
    delay: float = 3.0,
    login_timeout: int = 300,
) -> dict[str, Any]:
    directory = Path(str(current_plan["run_dir"]))
    run = load_run(directory)
    run_id = str(run.get("run_id") or directory.name)
    if confirm != run_id:
        raise ValueError("--confirm must exactly match the displayed run ID")
    saved_plan = run.get("upload_plan")
    if not isinstance(saved_plan, Mapping):
        raise ValueError("no saved upload plan exists; run upload.py without --execute first")
    if saved_plan.get("signature") != current_plan.get("signature"):
        raise ValueError("the upload plan changed; display and confirm a new plan")
    try:
        fresh_plan = build_upload_plan(
            directory,
            ",".join(str(slot) for slot in current_plan["profiles"]),
            str(current_plan["mode"]),
        )
    except Exception as exc:
        _record_upload_failure(directory, exc)
        raise
    if fresh_plan.get("signature") != current_plan.get("signature"):
        error = ValueError(
            "a validated file or profile target changed after confirmation; display a new plan"
        )
        _record_upload_failure(directory, error)
        raise error

    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as exc:
        error = UploadError("Playwright is missing; run bootstrap.py first.", code="dependency_missing")
        _record_upload_failure(directory, error)
        raise error from exc

    mode = str(current_plan["mode"])
    assignments = [dict(item) for item in current_plan["assignments"]]
    uploads_state = run.get("uploads")
    prior_mode = str(uploads_state.get("mode") or "") if isinstance(uploads_state, Mapping) else ""
    entries = _existing_upload_entries(run) if prior_mode == mode else []
    by_key = {_entry_key(item, mode): item for item in entries if all(
        key in item for key in ("file", "file_sha256", "profile_slot")
    )}
    uncertain = [item for item in by_key.values() if item.get("status") == "uncertain"]
    if uncertain:
        raise ValueError("a prior upload is unverified; inspect Naver manually before retrying")

    append_event(
        directory,
        "upload_started",
        status="uploading",
        message=f"확인된 {mode} 업로드를 시작했습니다.",
        details={"profiles": current_plan["profiles"], "count": len(assignments)},
    )
    _save_upload_state(directory, mode, entries, "uploading")
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in assignments:
        grouped[int(row["profile_slot"])].append(row)

    try:
        with sync_playwright() as playwright:
            for slot in current_plan["profiles"]:
                profile_rows = grouped[int(slot)]
                if not profile_rows:
                    continue
                profile = get_profile(int(slot))
                planned_target = profile_rows[0]
                if (
                    str(profile.get("blog_url") or "") != str(planned_target.get("blog_url") or "")
                    or str(profile.get("write_url") or "") != str(planned_target.get("write_url") or "")
                ):
                    raise ValueError(
                        f"profile {slot} changed after confirmation; display and confirm a new plan"
                    )
                context, owns_context, connector_browser = _open_profile_context(
                    playwright,
                    str(profile["profile_path"]),
                    headless=headless,
                )
                try:
                    # The local name intentionally keeps a live reference to
                    # a reused connector for the whole profile assignment.
                    _ = connector_browser
                    page = context.pages[0] if context.pages else context.new_page()
                    uploader = NaverTextUploader(page, login_timeout_seconds=login_timeout)
                    for position, row in enumerate(profile_rows):
                        key = _entry_key(row, mode)
                        previous = by_key.get(key)
                        if previous and previous.get("status") == "success":
                            continue
                        post_path = directory / str(row["file"])
                        if _file_sha256(post_path) != str(row["file_sha256"]):
                            raise ValueError(
                                f"post {row['index']} changed after confirmation; stop and validate again"
                            )
                        parsed = parse_mato_text(post_path.read_text(encoding="utf-8-sig"))
                        entry = {
                            "key": key,
                            "index": row["index"],
                            "title": row["title"],
                            "file": row["file"],
                            "file_sha256": row["file_sha256"],
                            "profile_slot": int(slot),
                            "mode": mode,
                            "started_at": now_iso(),
                            "status": "uploading",
                        }
                        entries = [item for item in entries if item.get("key") != key]
                        entries.append(entry)
                        _save_upload_state(directory, mode, entries, "uploading")
                        try:
                            result = uploader.upload(
                                str(profile["write_url"]),
                                str(parsed["title"]),
                                parsed,
                                mode,
                                post_path.parent,
                            )
                            entry.update(
                                {
                                    "status": "success",
                                    "completed_at": now_iso(),
                                    "verified_url": result.get("verified_url"),
                                }
                            )
                            append_event(
                                directory,
                                "upload_item_completed",
                                status="uploading",
                                message=f"원고 {row['index']}번의 {mode} 업로드를 확인했습니다.",
                                details={
                                    "index": row["index"],
                                    "title": row["title"],
                                    "profile_slot": slot,
                                    "verified_url": result.get("verified_url"),
                                },
                            )
                        except UploadError as exc:
                            entry.update(
                                {
                                    "status": "uncertain" if exc.code in {"save_unverified", "publish_unverified"} else "failed",
                                    "completed_at": now_iso(),
                                    "error_code": exc.code,
                                    "error": str(exc),
                                }
                            )
                            entries = [item for item in entries if item.get("key") != key] + [entry]
                            _save_upload_state(directory, mode, entries, "failed")
                            append_event(
                                directory,
                                "upload_item_failed",
                                status="failed",
                                message=str(exc),
                                details={"index": row["index"], "profile_slot": slot, "error_code": exc.code},
                            )
                            setattr(exc, "_mato_failure_event_recorded", True)
                            raise
                        except Exception as exc:
                            error_code = type(exc).__name__
                            entry.update(
                                {
                                    "status": "failed",
                                    "completed_at": now_iso(),
                                    "error_code": error_code,
                                    "error": str(exc) or error_code,
                                }
                            )
                            entries = [item for item in entries if item.get("key") != key] + [entry]
                            _save_upload_state(directory, mode, entries, "failed")
                            append_event(
                                directory,
                                "upload_item_failed",
                                status="failed",
                                message=str(exc) or error_code,
                                details={
                                    "index": row["index"],
                                    "profile_slot": slot,
                                    "error_code": error_code,
                                },
                            )
                            setattr(exc, "_mato_failure_event_recorded", True)
                            raise
                        entries = [item for item in entries if item.get("key") != key] + [entry]
                        by_key[key] = entry
                        _save_upload_state(directory, mode, entries, "uploading")
                        if position + 1 < len(profile_rows):
                            time.sleep(max(2.0, min(float(delay), 15.0)))
                finally:
                    if owns_context:
                        context.close()
    except Exception as exc:
        _save_upload_state(directory, mode, entries, "failed")
        if not getattr(exc, "_mato_failure_event_recorded", False):
            _record_upload_failure(directory, exc)
        raise

    _save_upload_state(directory, mode, entries, "completed")
    append_event(
        directory,
        "upload_completed",
        status="completed",
        message=f"{mode} 업로드 {len(assignments)}개를 완료했습니다.",
        details={"mode": mode, "success_count": len(assignments)},
    )
    return {
        "ok": True,
        "run_id": run_id,
        "run_dir": str(directory),
        "mode": mode,
        "success_count": len(assignments),
        "entries": sorted(entries, key=lambda item: int(item.get("index", 0))),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan or execute confirmed Naver text uploads.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--profiles", help="comma-separated numbered profile slots")
    parser.add_argument("--mode", choices=sorted(MODES))
    parser.add_argument("--execute", action="store_true", help="perform browser writes after confirmation")
    parser.add_argument("--confirm", help="exact run ID displayed by the saved plan")
    parser.add_argument("--headless", action="store_true", help="hide Chrome (not recommended)")
    parser.add_argument("--delay", type=float, default=3.0, help="seconds between posts for one profile")
    parser.add_argument("--login-timeout", type=int, default=300, help="seconds to wait for normal visible login")
    parser.add_argument("--resolve-uncertain", type=int, metavar="INDEX")
    parser.add_argument("--entry-key", help="exact uncertain entry key shown in run history")
    parser.add_argument("--resolution", choices=sorted(MANUAL_RESOLUTIONS))
    parser.add_argument("--verified-url", help="direct Naver post URL for a confirmed publish")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.resolve_uncertain is not None:
            if not args.entry_key or not args.resolution or not args.confirm:
                parser.error(
                    "--resolve-uncertain requires --entry-key, --resolution, and --confirm"
                )
            result = resolve_uncertain_upload(
                args.run_dir,
                index=args.resolve_uncertain,
                entry_key=args.entry_key,
                resolution=args.resolution,
                confirm=args.confirm,
                verified_url=args.verified_url,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if not args.profiles or not args.mode:
            parser.error("upload planning requires --profiles and --mode")
        if not 2.0 <= args.delay <= 15.0:
            parser.error("--delay must be between 2 and 15 seconds")
        plan = build_upload_plan(args.run_dir, args.profiles, args.mode)
        if not args.execute:
            save_upload_plan(plan)
            if args.json:
                print(json.dumps({"ok": True, "executed": False, **plan}, ensure_ascii=False, indent=2))
            else:
                print(_render_plan(plan))
            return 0
        if not args.confirm:
            parser.error("--execute requires --confirm <run-id>")
        result = execute_upload(
            plan,
            confirm=args.confirm,
            headless=args.headless,
            delay=args.delay,
            login_timeout=args.login_timeout,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, UploadError) as exc:
        code = exc.code if isinstance(exc, UploadError) else type(exc).__name__
        print(json.dumps({"ok": False, "error_code": code, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
