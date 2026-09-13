"""One independent, validated product run per requested profile.

The existing one-post fact/image/finalization contract stays unchanged in each
child run. This module owns only batch assignment and serial upload/resume.
Generation is performed by Codex using each child's common prompt and overlay.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

try:
    from . import history, parse_request, upload
except ImportError:
    import history
    import parse_request
    import upload


def prepare_batch(command: str, run_dir: str | Path | None = None) -> dict[str, Any]:
    request = parse_request.parse_request(command)
    if request.get("source_type") not in history.PRODUCT_SOURCE_TYPES:
        raise ValueError("product batch requires a supported product URL")
    slots = request.get("profiles", [])
    if not slots:
        raise ValueError("product batch requires explicit profile slots")
    if run_dir is not None and Path(run_dir).expanduser().exists():
        raise ValueError("batch directory already exists; use status/upload to resume")
    directory, _state = history.create_run(
        request["keyword"], "product", len(slots), command,
        run_dir=run_dir, request_fields=request,
    )
    directory = directory.resolve()
    children = []
    for index, slot in enumerate(slots, start=1):
        child_request = dict(request, profiles=[slot], versions=1)
        relative = f"profiles/{index:02d}_profile_{slot}"
        child_dir, _child = history.create_run(
            request["keyword"], "product", 1, command,
            run_dir=directory / relative, request_fields=child_request,
        )
        history.update_run(child_dir, {"batch_parent": str(directory), "batch_index": index})
        children.append({"index": index, "profile_slot": slot, "run_dir": relative})
    history.append_event(
        directory, "product_batch_prepared", status="collecting",
        message=f"프로필별 상품 원고 {len(children)}개를 준비했습니다.",
        run_updates={"product_batch": {"children": children, "count": len(children)}},
    )
    return batch_status(directory)


def _children(directory: Path) -> tuple[dict[str, Any], list[tuple[dict[str, Any], Path, dict[str, Any]]]]:
    parent = history.load_run(directory)
    batch = parent.get("product_batch", {})
    request = parent.get("request", {})
    rows = batch.get("children")
    slots = request.get("profiles")
    if not isinstance(rows, list) or not rows or not isinstance(slots, list) or len(rows) != len(slots):
        raise ValueError("invalid product batch assignments")
    children = []
    for index, (row, slot) in enumerate(zip(rows, slots), start=1):
        expected = f"profiles/{index:02d}_profile_{slot}"
        if row != {"index": index, "profile_slot": slot, "run_dir": expected}:
            raise ValueError("product batch assignment changed")
        path = (directory / expected).resolve()
        if not path.is_relative_to(directory) or any(part.is_symlink() for part in [directory / "profiles", directory / expected]):
            raise ValueError("unsafe product batch child path")
        child = history.load_run(path)
        child_request = child.get("request", {})
        if child_request.get("profiles") != [slot] or child_request.get("versions") != 1:
            raise ValueError("child profile/count differs from batch assignment")
        for key in ("product_url", "source_type", "mode", "upload_requested", "experience_notes"):
            if child_request.get(key) != request.get(key):
                raise ValueError(f"child {key} differs from batch request")
        children.append((row, path, child))
    return parent, children


def batch_status(run_dir: str | Path) -> dict[str, Any]:
    directory = Path(run_dir).expanduser().resolve()
    parent, children = _children(directory)
    return {
        "ok": True, "run_id": parent["run_id"], "run_dir": str(directory),
        "count": len(children), "mode": parent["request"].get("mode", "draft"),
        "children": [
            {**row, "run_dir": str(path), "status": child.get("status"),
             "posts": child.get("generation", {}).get("posts", []),
             "uploads": child.get("uploads", {})}
            for row, path, child in children
        ],
    }


def upload_batch(run_dir: str | Path, *, execute: bool = False, confirm: str = "") -> dict[str, Any]:
    directory = Path(run_dir).expanduser().resolve()
    parent, children = _children(directory)
    request = parent["request"]
    if request.get("upload_requested") is not True:
        raise ValueError("this batch did not request upload")
    if execute and confirm != parent["run_id"]:
        raise ValueError("batch confirmation must match its run ID")
    mode = request.get("mode", "draft")
    # Preflight every target and file before writing to any external editor.
    plans = []
    bodies: set[str] = set()
    for row, path, _child in children:
        plan = upload.build_upload_plan(path, str(row["profile_slot"]), mode)
        if len(plan["assignments"]) != 1 or plan["assignments"][0]["profile_slot"] != row["profile_slot"]:
            raise ValueError("each batch child must upload exactly one post to its assigned profile")
        file = (path / plan["assignments"][0]["file"]).resolve()
        if not file.is_relative_to(path):
            raise ValueError("unsafe batch manuscript path")
        body = file.read_text(encoding="utf-8-sig").split("본문2:", 1)[-1]
        digest = hashlib.sha256("".join(body.split()).encode("utf-8")).hexdigest()
        if digest in bodies:
            raise ValueError("profile manuscripts must not duplicate the same body")
        bodies.add(digest)
        plans.append(plan)
    if not execute:
        return {"ok": True, "run_id": parent["run_id"], "mode": mode, "plans": plans}
    completed = []
    for plan in plans:
        slot = plan["profiles"][0]
        try:
            upload.save_upload_plan(plan)
            # The normal uploader checks validation/signatures again, keeps the
            # profile browser open, and skips matching verified successes.
            result = upload.execute_upload(plan, confirm=plan["run_id"])
            if result.get("ok") is not True:
                raise ValueError(f"profile {slot} upload did not return verified success")
        except Exception as exc:
            history.append_event(directory, "product_batch_upload_failed", status="failed",
                                 message=str(exc), details={"profile_slot": slot, "completed_profiles": completed})
            raise
        completed.append(slot)
        history.append_event(directory, "product_batch_profile_completed", status="uploading",
                             details={"profile_slot": slot})
    history.append_event(directory, "product_batch_completed", status="completed",
                         details={"profiles": completed, "count": len(completed), "mode": mode})
    return batch_status(directory)


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare and upload per-profile product jobs.")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--command-file", required=True)
    prepare.add_argument("--run-dir")
    for name in ("status", "upload"):
        sub = commands.add_parser(name)
        sub.add_argument("--run-dir", required=True)
        if name == "upload":
            sub.add_argument("--execute", action="store_true")
            sub.add_argument("--confirm", default="")
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare_batch(Path(args.command_file).expanduser().read_text(encoding="utf-8-sig"), args.run_dir)
    elif args.command == "status":
        result = batch_status(args.run_dir)
    else:
        result = upload_batch(args.run_dir, execute=args.execute, confirm=args.confirm)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
