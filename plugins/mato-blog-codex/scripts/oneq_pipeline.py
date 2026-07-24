"""Standalone OneQ platform pipeline: optional WordPress, Blogspot and Naver.

This module owns the orchestration state for a user-owned source URL.  It does
not import, spawn, inspect, or require Google Blog Auto.  Codex supplies the
fresh per-platform drafts; the module validates, stores, and publishes them in
the deterministic order required for a public WordPress backlink.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import quote, urlparse

try:
    from . import upload, validate_posts, write_posts
    from .blogspot_client import BlogspotClient
    from .history import append_event, update_run
    from .integrations import (
        BLOGSPOT_SECRET_KEY,
        WORDPRESS_SECRET_KEY,
        get_secret,
        integration_status,
        load_settings,
    )
    from .mato_common import atomic_write_json, atomic_write_text, load_run, parse_positive_slots, read_json
    from .owned_web_post import prepare_owned_web_post
    from .platform_prompts import get_prompt, init_prompt
    from .wordpress_client import WordPressClient, append_source_link, replace_image_markers
except ImportError:  # Direct execution: python scripts/oneq_pipeline.py
    import upload  # type: ignore[no-redef]
    import validate_posts  # type: ignore[no-redef]
    import write_posts  # type: ignore[no-redef]
    from blogspot_client import BlogspotClient  # type: ignore[no-redef]
    from history import append_event, update_run  # type: ignore[no-redef]
    from integrations import (  # type: ignore[no-redef]
        BLOGSPOT_SECRET_KEY,
        WORDPRESS_SECRET_KEY,
        get_secret,
        integration_status,
        load_settings,
    )
    from mato_common import atomic_write_json, atomic_write_text, load_run, parse_positive_slots, read_json  # type: ignore[no-redef]
    from owned_web_post import prepare_owned_web_post  # type: ignore[no-redef]
    from platform_prompts import get_prompt, init_prompt  # type: ignore[no-redef]
    from wordpress_client import WordPressClient, append_source_link, replace_image_markers  # type: ignore[no-redef]


TARGETS = ("wordpress", "blogspot", "naver")
IMAGE_TOKEN_RE = re.compile(r"\[(image_[1-9]\d*\.jpg)\]", re.IGNORECASE)
TEXT_LINK_RE = re.compile(r"^\[\[MATO_TEXT_LINK\|[^|]*\|[^|]*\]\]$", re.MULTILINE)


def _dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _target_list(values: Iterable[str] | str | None) -> list[str]:
    raw = values.split(",") if isinstance(values, str) else (values or [])
    result: list[str] = []
    for value in raw:
        target = str(value or "").strip().lower()
        if not target:
            continue
        if target == "nb":
            target = "naver"
        if target not in TARGETS:
            raise ValueError(f"unsupported target: {target}")
        if target not in result:
            result.append(target)
    return result or ["naver"]


def _oneq_state(run: Mapping[str, Any]) -> dict[str, Any]:
    state = run.get("oneq")
    if not isinstance(state, Mapping):
        raise ValueError("this run was not prepared by oneq_pipeline.py")
    targets = _target_list(state.get("targets") if isinstance(state.get("targets"), list) else [])
    result = dict(state)
    result["targets"] = targets
    return result


def _run_dir(value: str | Path) -> Path:
    directory = Path(value).expanduser().resolve()
    if not load_run(directory):
        raise FileNotFoundError(f"run.json not found: {directory}")
    return directory


def _safe_inside(path: Path, root: Path) -> bool:
    candidate = path.resolve(strict=False)
    base = root.resolve(strict=False)
    return candidate == base or base in candidate.parents


def _source_folder(directory: Path, run: Mapping[str, Any]) -> Path:
    sources = _dict(run.get("sources"))
    folder = str(sources.get("owned_source_folder") or "").strip()
    source = (directory / "sources" / "items" / folder).resolve()
    root = (directory / "sources" / "items").resolve()
    if not folder or not _safe_inside(source, root) or not source.is_dir():
        raise ValueError("the run does not have a safe owned source folder")
    return source


def _source_image_path(folder: Path, name: str) -> Path:
    direct = folder / name
    if direct.is_file():
        return direct
    for item in folder.iterdir():
        if item.is_file() and item.name.casefold() == name.casefold():
            return item
    raise ValueError(f"referenced owned image is missing: {name}")


def _artifact_path(directory: Path, platform: str, filename: str) -> Path:
    numbers = {"wordpress": "01_wordpress", "blogspot": "02_blogspot", "naver": "03_naver"}
    if platform not in numbers:
        raise ValueError(f"unsupported platform artifact: {platform}")
    path = directory / "platforms" / numbers[platform] / filename
    if not _safe_inside(path, directory / "platforms"):
        raise ValueError("unsafe platform artifact path")
    return path


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require_platform_config(platform: str) -> dict[str, Any]:
    status = integration_status()
    entry = _dict(status.get(platform))
    if not entry.get("configured"):
        raise ValueError(f"{platform} is not configured locally; configure it before publishing")
    return _dict(load_settings().get(platform))


def _platform_payload(value: object, platform: str) -> dict[str, str]:
    data = _dict(value)
    title = re.sub(r"\s+", " ", str(data.get("title") or "")).strip()
    html = str(data.get("html") or "").strip()
    if not title or not html:
        raise ValueError(f"the render input needs {platform}.title and {platform}.html")
    if "<html" in html.casefold() or "<body" in html.casefold():
        # The clients accept a fragment.  Keeping whole documents out avoids
        # imported theme markup and unexpected Blogger body nesting.
        body = re.search(r"<body[^>]*>(.*?)</body>", html, re.IGNORECASE | re.DOTALL)
        html = body.group(1).strip() if body else html
    return {"title": title, "html": html}


def _write_html_artifact(directory: Path, platform: str, payload: Mapping[str, str]) -> dict[str, Any]:
    html_path = _artifact_path(directory, platform, "post.html")
    meta_path = _artifact_path(directory, platform, "post.json")
    atomic_write_text(html_path, str(payload["html"]).rstrip() + "\n")
    atomic_write_json(meta_path, {"title": payload["title"], "html_sha256": _sha_file(html_path)})
    return {"html": str(html_path.relative_to(directory)), "meta": str(meta_path.relative_to(directory)), "title": payload["title"]}


def _load_html_artifact(directory: Path, platform: str) -> tuple[dict[str, Any], Path]:
    meta_path = _artifact_path(directory, platform, "post.json")
    html_path = _artifact_path(directory, platform, "post.html")
    meta = read_json(meta_path, {})
    if not isinstance(meta, Mapping) or not html_path.is_file():
        raise ValueError(f"{platform} draft has not been rendered")
    actual = _sha_file(html_path)
    if str(meta.get("html_sha256") or "") != actual:
        raise ValueError(f"{platform} draft changed after rendering; render it again")
    return dict(meta), html_path


def prepare_oneq_run(
    source_url: str,
    *,
    targets: Iterable[str] | str | None,
    naver_versions: int,
    profiles: Iterable[int | str] | str | None,
    mode: str,
    command: str,
    link_text: str = "더 자세한 정보 보러가기",
    include_images: bool = False,
    images_authorized: bool = False,
    run_dir: str | Path | None = None,
) -> dict[str, Any]:
    selected = _target_list(targets)
    if naver_versions <= 0:
        raise ValueError("naver versions must be positive")
    slots = parse_positive_slots(profiles or "1") if "naver" in selected else []
    versions = naver_versions if "naver" in selected else 1
    prepared = prepare_owned_web_post(
        source_url,
        versions=versions,
        command=command or source_url,
        include_images=include_images,
        images_authorized=images_authorized,
        run_dir=run_dir,
    )
    directory = Path(str(prepared["run_dir"])).resolve()
    prompt_keys = [target for target in selected if target in {"wordpress", "blogspot"}]
    if "naver" in selected:
        prompt_keys.append("naver-from-wordpress" if "wordpress" in selected else "naver-from-wordpress")
    prompts = {key: init_prompt(key) for key in prompt_keys}
    normalized_mode = "publish" if str(mode).lower() == "publish" else "draft"

    def update(state: dict[str, Any]) -> None:
        state["oneq"] = {
            "schema_version": 1,
            "targets": selected,
            "source_url": str(source_url).strip(),
            "naver_versions": int(naver_versions) if "naver" in selected else 0,
            "profiles": slots,
            "mode": normalized_mode,
            "link_text": str(link_text or "더 자세한 정보 보러가기").strip() or "더 자세한 정보 보러가기",
            "prompts": {key: {"path": value["path"], "sha256": value["sha256"]} for key, value in prompts.items()},
            "stages": {"render": "pending", "wordpress": "pending" if "wordpress" in selected else "skipped", "blogspot": "pending" if "blogspot" in selected else "skipped", "naver": "pending" if "naver" in selected else "skipped"},
        }

    update_run(directory, update)
    append_event(
        directory,
        "oneq_prepared",
        status="collected",
        message="독립형 OneQ 플랫폼 작업을 준비했습니다.",
        details={"targets": selected, "naver_versions": int(naver_versions) if "naver" in selected else 0, "profiles": slots, "mode": normalized_mode},
    )
    return {"ok": True, "run_dir": str(directory), "targets": selected, "profiles": slots, "mode": normalized_mode, "prompts": prompts}


def render_oneq_payload(run_dir: str | Path, input_path: str | Path) -> dict[str, Any]:
    """Store Codex's original platform drafts and render selected Naver versions."""

    directory = _run_dir(run_dir)
    run = load_run(directory)
    oneq = _oneq_state(run)
    raw = json.loads(Path(input_path).read_text(encoding="utf-8-sig"))
    if not isinstance(raw, Mapping):
        raise ValueError("OneQ render input must be a JSON object")
    rendered: dict[str, Any] = {}
    if "wordpress" in oneq["targets"]:
        rendered["wordpress"] = _write_html_artifact(directory, "wordpress", _platform_payload(raw.get("wordpress"), "wordpress"))
    if "blogspot" in oneq["targets"]:
        rendered["blogspot"] = _write_html_artifact(directory, "blogspot", _platform_payload(raw.get("blogspot"), "blogspot"))
    if "naver" in oneq["targets"]:
        posts = raw.get("naver_posts")
        if not isinstance(posts, list):
            raise ValueError("the render input needs a naver_posts array")
        payload = {"analysis": _dict(raw.get("analysis")), "posts": posts}
        staging = _artifact_path(directory, "naver", "generated-posts.json")
        atomic_write_json(staging, payload)
        naver_result = write_posts.write_posts(directory, staging, remove_raw=False)
        rendered["naver"] = {"generated_count": naver_result["generated_count"], "posts": naver_result["posts"]}

    def update(state: dict[str, Any]) -> None:
        nested = _dict(state.get("oneq"))
        stages = _dict(nested.get("stages"))
        stages["render"] = "completed"
        nested["stages"] = stages
        nested["rendered"] = rendered
        state["oneq"] = nested

    update_run(directory, update)
    append_event(directory, "oneq_render_completed", status="generated", message="플랫폼별 독립 원고를 저장했습니다.", details={"targets": oneq["targets"]})
    return {"ok": True, "run_dir": str(directory), "rendered": rendered}


def publish_wordpress(run_dir: str | Path, *, status: str | None = None) -> dict[str, Any]:
    directory = _run_dir(run_dir)
    run = load_run(directory)
    oneq = _oneq_state(run)
    if "wordpress" not in oneq["targets"]:
        return {"ok": True, "skipped": True, "reason": "wordpress was not selected"}
    settings = _require_platform_config("wordpress")
    meta, html_path = _load_html_artifact(directory, "wordpress")
    html = html_path.read_text(encoding="utf-8-sig")
    title = str(meta.get("title") or "")
    source_folder = _source_folder(directory, run)
    client = WordPressClient(str(settings.get("site_url") or ""), str(settings.get("username") or ""), get_secret(WORDPRESS_SECRET_KEY))
    uploaded_urls: dict[str, str] = {}
    featured_media: int | None = None
    for index, name in enumerate(dict.fromkeys(match.group(1).lower() for match in IMAGE_TOKEN_RE.finditer(html)), start=1):
        media = client.upload_image(_source_image_path(source_folder, name), title=title, index=index)
        uploaded_urls[name] = str(media["source_url"])
        if featured_media is None and int(media.get("id") or 0):
            featured_media = int(media["id"])
    html = replace_image_markers(html, image_urls=uploaded_urls) if uploaded_urls else html
    downstream = any(target in oneq["targets"] for target in ("blogspot", "naver"))
    effective_status = "publish" if downstream else ("publish" if str(status).lower() == "publish" else str(settings.get("default_status") or "draft"))
    post = client.create_post(
        title=title,
        body_html=html,
        status=effective_status,
        categories=settings.get("categories") if isinstance(settings.get("categories"), list) else [],
        tags=settings.get("tags") if isinstance(settings.get("tags"), list) else [],
        featured_media=featured_media,
    )
    url = str(post.get("link") or _dict(post.get("guid")).get("rendered") or "").strip()
    if downstream and not url.startswith(("http://", "https://")):
        raise RuntimeError("WordPress publish returned no public URL; downstream stages were stopped")
    receipt_path = _artifact_path(directory, "wordpress", "published.json")
    atomic_write_json(receipt_path, {"post_id": post.get("id"), "url": url, "status": effective_status, "title": title})

    def update(state: dict[str, Any]) -> None:
        nested = _dict(state.get("oneq"))
        stages = _dict(nested.get("stages"))
        stages["wordpress"] = "published" if effective_status == "publish" else "draft"
        nested["stages"] = stages
        nested["wordpress"] = {"url": url, "post_id": post.get("id"), "status": effective_status, "receipt": str(receipt_path.relative_to(directory))}
        state["oneq"] = nested

    update_run(directory, update)
    append_event(directory, "oneq_wordpress_completed", status="generated", message="WordPress 업로드를 확인했습니다.", details={"status": effective_status, "url": url})
    return {"ok": True, "platform": "wordpress", "url": url, "status": effective_status}


def _naver_text_link_marker(url: str, label: str) -> str:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    safe_label = str(label or "더 자세한 정보 보러가기").strip() or "더 자세한 정보 보러가기"
    return f"[[MATO_TEXT_LINK|{quote(safe_label, safe='')}|{quote(str(url).strip(), safe='')}]]"


def finalize_downstream(run_dir: str | Path) -> dict[str, Any]:
    """Attach the WordPress public URL after a verified WordPress publish."""

    directory = _run_dir(run_dir)
    run = load_run(directory)
    oneq = _oneq_state(run)
    if not any(target in oneq["targets"] for target in ("blogspot", "naver")):
        return {"ok": True, "skipped": True, "reason": "no downstream platform was selected"}
    wordpress = _dict(oneq.get("wordpress"))
    wordpress_url = str(wordpress.get("url") or "").strip()
    if "wordpress" in oneq["targets"] and not wordpress_url:
        raise ValueError("publish WordPress first; downstream content needs its verified public URL")
    if not wordpress_url:
        wordpress_url = str(oneq.get("source_url") or "").strip()
    completed: list[str] = []
    if "blogspot" in oneq["targets"]:
        meta, html_path = _load_html_artifact(directory, "blogspot")
        html = html_path.read_text(encoding="utf-8-sig")
        if wordpress_url and wordpress_url not in html:
            html = append_source_link(html, wordpress_url)
            atomic_write_text(html_path, html.rstrip() + "\n")
            atomic_write_json(_artifact_path(directory, "blogspot", "post.json"), {"title": meta["title"], "html_sha256": _sha_file(html_path)})
        completed.append("blogspot")
    if "naver" in oneq["targets"]:
        marker = _naver_text_link_marker(wordpress_url, str(oneq.get("link_text") or ""))
        if marker:
            generation = _dict(run.get("generation"))
            records = generation.get("posts")
            if not isinstance(records, list):
                raise ValueError("Naver posts have not been rendered")
            for record in records:
                path = (directory / str(_dict(record).get("file") or "")).resolve()
                if not _safe_inside(path, directory / "posts") or not path.is_file():
                    raise ValueError("unsafe or missing Naver draft")
                content = path.read_text(encoding="utf-8-sig").rstrip()
                if not TEXT_LINK_RE.search(content):
                    atomic_write_text(path, content + "\n\n" + marker + "\n")
            validation = validate_posts.validate_run(directory, int(oneq["naver_versions"]))
            if not validation.get("ok"):
                raise ValueError("Naver validation failed after WordPress-link insertion")
        completed.append("naver")

    def update(state: dict[str, Any]) -> None:
        nested = _dict(state.get("oneq"))
        stages = _dict(nested.get("stages"))
        for platform in completed:
            stages[platform] = "ready_to_publish"
        nested["stages"] = stages
        nested["downstream_url"] = wordpress_url
        state["oneq"] = nested

    update_run(directory, update)
    append_event(directory, "oneq_downstream_finalized", status="generated", message="하위 플랫폼 원고에 원문 링크를 연결했습니다.", details={"platforms": completed, "wordpress_url": wordpress_url})
    return {"ok": True, "run_dir": str(directory), "platforms": completed, "wordpress_url": wordpress_url}


def publish_blogspot(run_dir: str | Path, *, status: str | None = None) -> dict[str, Any]:
    directory = _run_dir(run_dir)
    run = load_run(directory)
    oneq = _oneq_state(run)
    if "blogspot" not in oneq["targets"]:
        return {"ok": True, "skipped": True, "reason": "blogspot was not selected"}
    settings = _require_platform_config("blogspot")
    meta, html_path = _load_html_artifact(directory, "blogspot")
    html = html_path.read_text(encoding="utf-8-sig")
    tokens = list(dict.fromkeys(match.group(1).lower() for match in IMAGE_TOKEN_RE.finditer(html)))
    if tokens and not bool(settings.get("drive_public_image_sharing_acknowledged")):
        raise ValueError("Blogspot source-image upload requires acknowledge-public-drive-images during setup")
    client = BlogspotClient(str(settings.get("client_id") or ""), get_secret(BLOGSPOT_SECRET_KEY))
    if tokens:
        source_folder = _source_folder(directory, run)
        urls = {name: client.upload_image_to_drive(_source_image_path(source_folder, name)) for name in tokens}
        html = replace_image_markers(html, image_urls=urls)
    effective_status = "publish" if str(status).lower() == "publish" else str(settings.get("default_status") or "draft")
    post = client.create_post(
        blog_id=str(settings.get("blog_id") or ""),
        title=str(meta.get("title") or ""),
        body_html=html,
        labels=settings.get("labels") if isinstance(settings.get("labels"), list) else [],
        status=effective_status,
    )
    url = str(post.get("url") or post.get("selfLink") or "")
    receipt = _artifact_path(directory, "blogspot", "published.json")
    atomic_write_json(receipt, {"post_id": post.get("id"), "url": url, "status": effective_status, "title": meta.get("title")})

    def update(state: dict[str, Any]) -> None:
        nested = _dict(state.get("oneq"))
        stages = _dict(nested.get("stages"))
        stages["blogspot"] = "published" if effective_status == "publish" else "draft"
        nested["stages"] = stages
        nested["blogspot"] = {"url": url, "post_id": post.get("id"), "status": effective_status, "receipt": str(receipt.relative_to(directory))}
        state["oneq"] = nested

    update_run(directory, update)
    append_event(directory, "oneq_blogspot_completed", status="generated", message="Blogspot 업로드를 확인했습니다.", details={"status": effective_status, "url": url})
    return {"ok": True, "platform": "blogspot", "url": url, "status": effective_status}


def publish_naver(run_dir: str | Path, *, mode: str | None = None, confirm: str) -> dict[str, Any]:
    directory = _run_dir(run_dir)
    run = load_run(directory)
    oneq = _oneq_state(run)
    if "naver" not in oneq["targets"]:
        return {"ok": True, "skipped": True, "reason": "naver was not selected"}
    selected_mode = "publish" if str(mode or oneq.get("mode")).lower() == "publish" else "draft"
    plan = upload.build_upload_plan(directory, ",".join(str(slot) for slot in oneq["profiles"]), selected_mode)
    upload.save_upload_plan(plan)
    result = upload.execute_upload(plan, confirm=confirm)

    def update(state: dict[str, Any]) -> None:
        nested = _dict(state.get("oneq"))
        stages = _dict(nested.get("stages"))
        stages["naver"] = "published" if selected_mode == "publish" else "draft"
        nested["stages"] = stages
        nested["naver"] = {"mode": selected_mode, "success_count": result.get("success_count")}
        state["oneq"] = nested

    update_run(directory, update)
    append_event(directory, "oneq_naver_completed", status="completed", message="번호 프로필 네이버 업로드를 확인했습니다.", details={"mode": selected_mode, "success_count": result.get("success_count")})
    return result


def workflow_status(run_dir: str | Path) -> dict[str, Any]:
    directory = _run_dir(run_dir)
    run = load_run(directory)
    return {"ok": True, "run_dir": str(directory), "oneq": _oneq_state(run), "integration": integration_status()}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the standalone optional WordPress/Blogspot/Naver OneQ workflow.")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="download a user-owned source and create a OneQ run")
    prepare.add_argument("--url", required=True)
    prepare.add_argument("--targets", default="naver", help="comma-separated: wordpress,blogspot,naver; default naver")
    prepare.add_argument("--naver-versions", type=int, default=1)
    prepare.add_argument("--profiles", default="1")
    prepare.add_argument("--mode", choices=("draft", "publish"), default="draft")
    prepare.add_argument("--link-text", default="더 자세한 정보 보러가기")
    prepare.add_argument("--command", default="")
    prepare.add_argument("--run-dir", default="")
    prepare.add_argument("--include-images", action="store_true")
    prepare.add_argument("--images-authorized", action="store_true")
    render = commands.add_parser("render", help="store Codex-generated platform drafts")
    render.add_argument("--run-dir", required=True)
    render.add_argument("--input", required=True)
    wp = commands.add_parser("publish-wordpress", help="upload the rendered WordPress draft")
    wp.add_argument("--run-dir", required=True)
    wp.add_argument("--status", choices=("draft", "publish"), default=None)
    downstream = commands.add_parser("finalize-downstream", help="insert the verified WordPress link")
    downstream.add_argument("--run-dir", required=True)
    bs = commands.add_parser("publish-blogspot", help="upload the rendered Blogspot draft")
    bs.add_argument("--run-dir", required=True)
    bs.add_argument("--status", choices=("draft", "publish"), default=None)
    naver = commands.add_parser("publish-naver", help="execute the existing Mato Naver uploader")
    naver.add_argument("--run-dir", required=True)
    naver.add_argument("--mode", choices=("draft", "publish"), default=None)
    naver.add_argument("--confirm", required=True)
    status = commands.add_parser("status", help="show OneQ and local integration status without secrets")
    status.add_argument("--run-dir", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare_oneq_run(
                args.url,
                targets=args.targets,
                naver_versions=args.naver_versions,
                profiles=args.profiles,
                mode=args.mode,
                command=args.command,
                link_text=args.link_text,
                include_images=args.include_images,
                images_authorized=args.images_authorized,
                run_dir=args.run_dir or None,
            )
        elif args.command == "render":
            result = render_oneq_payload(args.run_dir, args.input)
        elif args.command == "publish-wordpress":
            result = publish_wordpress(args.run_dir, status=args.status)
        elif args.command == "finalize-downstream":
            result = finalize_downstream(args.run_dir)
        elif args.command == "publish-blogspot":
            result = publish_blogspot(args.run_dir, status=args.status)
        elif args.command == "publish-naver":
            result = publish_naver(args.run_dir, mode=args.mode, confirm=args.confirm)
        else:
            result = workflow_status(args.run_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
