"""What the repository says about itself: trunk, remote, releases and commit style."""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from pathlib import Path

CONVENTIONAL = re.compile(r"^(feat|fix|docs|chore|ci|test|refactor|build|perf|style|revert)(\([^)]*\))?!?: ")


def _git(root: Path, *args: str, run=subprocess.run) -> subprocess.CompletedProcess:
    return run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)


def has_remote(root: Path, run=subprocess.run) -> bool:
    return _git(root, "remote", "get-url", "origin", run=run).returncode == 0


def trunk_branch(root: Path, run=subprocess.run) -> str:
    head = _git(root, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD", run=run)
    if head.returncode == 0 and head.stdout.strip():
        return head.stdout.strip().split("/", 1)[-1]
    for name in ("main", "master"):
        if _git(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{name}", run=run).returncode == 0:
            return name
    current = _git(root, "symbolic-ref", "--quiet", "--short", "HEAD", run=run)
    if current.returncode == 0 and current.stdout.strip():
        return current.stdout.strip()
    return "main"


def _toml_version(path: Path, table: str) -> bool:
    try:
        return bool(tomllib.loads(path.read_text(encoding="utf-8")).get(table, {}).get("version"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError):
        return False


def _json_version(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError, OSError):
        return False
    return isinstance(data, dict) and bool(data.get("version"))


def release_info(root: Path, run=subprocess.run) -> dict:
    files = []
    if (root / "pyproject.toml").is_file() and _toml_version(root / "pyproject.toml", "project"):
        files.append("pyproject.toml")
    if (root / "package.json").is_file() and _json_version(root / "package.json"):
        files.append("package.json")
    if (root / "Cargo.toml").is_file() and _toml_version(root / "Cargo.toml", "package"):
        files.append("Cargo.toml")
    info: dict = {"version_files": files}
    tags = _git(root, "tag", "--list", "v[0-9]*", "--sort=-v:refname", run=run)
    latest = tags.stdout.split()[0] if tags.returncode == 0 and tags.stdout.split() else None
    if latest:
        info["latest_tag"] = latest
    return info


def commit_convention(root: Path, run=subprocess.run) -> str:
    log = _git(root, "log", "-50", "--format=%s", run=run)
    subjects = [s for s in log.stdout.splitlines() if s.strip()] if log.returncode == 0 else []
    if not subjects:
        return "unknown"
    share = sum(1 for s in subjects if CONVENTIONAL.match(s)) / len(subjects)
    return "conventional" if share >= 0.6 else "free"
