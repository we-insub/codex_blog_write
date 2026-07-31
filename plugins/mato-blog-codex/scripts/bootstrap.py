"""Create or inspect the per-machine Mato Blog Codex Python runtime."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path
from typing import Sequence


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = PLUGIN_ROOT / "requirements.txt"
MINIMUM_PYTHON = (3, 10)


def is_windows() -> bool:
    """Return whether the current host uses Windows runtime layout."""

    return os.name == "nt"


def runtime_root() -> Path:
    override = os.environ.get("MATO_BLOG_RUNTIME_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".googleblog" / "mato-blog-codex"


def runtime_python() -> Path:
    root = runtime_root() / ".venv"
    if is_windows():
        return root / "Scripts" / "python.exe"
    return root / "bin" / "python"


def bootstrap_python_candidates() -> list[list[str]]:
    """Return possible supported interpreters for relaunching this script."""

    candidates: list[list[str]] = []
    existing_runtime = runtime_python()
    if existing_runtime.is_file():
        candidates.append([str(existing_runtime)])
    if is_windows():
        if shutil.which("py"):
            candidates.extend([["py", f"-{version}"] for version in ("3.14", "3.13", "3.12", "3.11", "3.10")])
        for name in ("python", "python3"):
            executable = shutil.which(name)
            if executable:
                candidates.append([executable])
    else:
        names = ("python3.14", "python3.13", "python3.12", "python3.11", "python3.10", "python3", "python")
        search_roots = (Path("/opt/homebrew/bin"), Path("/usr/local/bin"))
        for name in names:
            executable = shutil.which(name)
            if executable:
                candidates.append([executable])
        for root in search_roots:
            for name in names[:5]:
                executable = root / name
                if executable.is_file():
                    candidates.append([str(executable)])
        if sys.platform == "darwin":
            for version in ("3.14", "3.13", "3.12", "3.11", "3.10"):
                executable = Path("/Library/Frameworks/Python.framework/Versions") / version / "bin" / "python3"
                if executable.is_file():
                    candidates.append([str(executable)])
    unique: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for command in candidates:
        key = tuple(command)
        if key not in seen:
            unique.append(command)
            seen.add(key)
    return unique


def relaunch_with_supported_python(argv: Sequence[str]) -> int:
    """Relaunch through an installed Python 3.10+ when invoked by an older one."""

    version_probe = "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
    for command in bootstrap_python_candidates():
        try:
            probe = subprocess.run(
                [*command, "-c", version_probe],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode != 0:
            continue
        completed = subprocess.run([*command, str(Path(__file__).resolve()), *argv])
        return int(completed.returncode)
    raise RuntimeError("Python 3.10 or newer was not found. Install Python and try again.")


def chrome_candidates() -> list[Path]:
    if sys.platform == "darwin":
        return [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        ]
    if not is_windows():
        return [
            Path("/usr/bin/google-chrome"),
            Path("/usr/bin/google-chrome-stable"),
            Path("/usr/bin/chromium"),
            Path("/usr/bin/chromium-browser"),
        ]
    candidates: list[Path] = []
    for variable, suffix in (
        ("PROGRAMFILES", "Google/Chrome/Application/chrome.exe"),
        ("PROGRAMFILES(X86)", "Google/Chrome/Application/chrome.exe"),
        ("LOCALAPPDATA", "Google/Chrome/Application/chrome.exe"),
    ):
        base = os.environ.get(variable)
        if base:
            candidates.append(Path(base) / Path(suffix))
    return candidates


def find_chrome() -> Path | None:
    return next((path for path in chrome_candidates() if path.is_file()), None)


def dependency_check(python: Path) -> tuple[bool, str]:
    if not python.is_file():
        return False, "local virtual environment is missing"
    command = [
        str(python),
        "-c",
        "from scrapling.fetchers import DynamicSession; "
        "from playwright.sync_api import sync_playwright; "
        "import requests; import bs4; from PIL import Image; print('ready')",
    ]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if completed.returncode == 0:
        return True, "ready"
    message = (completed.stderr or completed.stdout or "dependency import failed").strip()
    return False, message.splitlines()[-1][:300]


def status_payload() -> dict[str, object]:
    python = runtime_python()
    ready, detail = dependency_check(python)
    chrome = find_chrome()
    return {
        "ready": ready and chrome is not None,
        "python": str(python),
        "dependencies_ready": ready,
        "dependency_detail": detail,
        "chrome": str(chrome) if chrome else None,
        "chrome_ready": chrome is not None,
        "requirements": str(REQUIREMENTS),
    }


def install(*, reinstall: bool = False) -> dict[str, object]:
    if sys.version_info < MINIMUM_PYTHON:
        raise RuntimeError("Python 3.10 or newer is required.")
    if not REQUIREMENTS.is_file():
        raise FileNotFoundError(f"requirements file not found: {REQUIREMENTS}")

    python = runtime_python()
    if not python.is_file():
        runtime_root().mkdir(parents=True, exist_ok=True)
        venv.EnvBuilder(with_pip=True, clear=False).create(python.parents[1])

    command = [
        str(python),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--requirement",
        str(REQUIREMENTS),
    ]
    if reinstall:
        command.insert(4, "--force-reinstall")
    subprocess.run(command, check=True)
    payload = status_payload()
    if not payload["dependencies_ready"]:
        raise RuntimeError(str(payload["dependency_detail"]))
    if not payload["chrome_ready"]:
        raise RuntimeError("Google Chrome was not found. Install Chrome and run bootstrap again.")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare the local Scrapling/Playwright runtime for Mato Blog Codex."
    )
    parser.add_argument("--check", action="store_true", help="inspect without installing")
    parser.add_argument("--reinstall", action="store_true", help="force dependency reinstall")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    if sys.version_info < MINIMUM_PYTHON:
        try:
            return relaunch_with_supported_python(raw_argv)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            print(json.dumps({"ready": False, "error": str(exc)}, ensure_ascii=False, indent=2))
            return 2
    args = build_parser().parse_args(raw_argv)
    try:
        payload = status_payload() if args.check else install(reinstall=args.reinstall)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload.get("ready") else 1
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(json.dumps({"ready": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
