#!/usr/bin/env python3
"""Refuse a release whose code changed while its version did not move.

Claude Code caches an installed plugin by its version, so two revisions under one version share one
cache directory and a project cannot stay on the revision it was set up with (field test
2026-09-29, T2). The version lives in `.claude-plugin/plugin.json`; `pyproject.toml` and
`flotilla/__init__.py` carry the same string (tests/test_version.py pins that). This check fails
when the tag `v<version>` does not exist, or when the plugin's code changed since that tag.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

CODE_PATHS = ("flotilla", "templates", "skills", "hooks", "scripts", ".claude-plugin")   # what a hook runs


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)


def main(root: Path | None = None) -> int:
    try:
        return _check(root)
    except OSError as error:   # git not installed, or not runnable here
        print(f"git could not be run ({error}): this check compares the plugin with its release tag and needs git",
              file=sys.stderr)
        return 1


def _check(root: Path | None) -> int:
    root = Path.cwd() if root is None else Path(root)
    try:
        version = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"cannot read the version from .claude-plugin/plugin.json: {error}", file=sys.stderr)
        return 1
    tag = f"v{version}"
    if _git(root, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}").returncode != 0:
        print(f"no tag {tag}: tag {tag} on the release commit (or bump the version if this is a new release)",
              file=sys.stderr)
        return 1
    changed = _git(root, "diff", "--name-only", tag, "--", *CODE_PATHS)
    if changed.returncode != 0:
        print(f"cannot compare with {tag}: {changed.stderr.strip()}", file=sys.stderr)
        return 1
    files = [line for line in changed.stdout.splitlines() if line]
    if files:
        shown = ", ".join(files[:10]) + (f" and {len(files) - 10} more" if len(files) > 10 else "")
        print(f"the plugin changed since {tag} ({shown}): bump the version in .claude-plugin/plugin.json, "
              f"pyproject.toml and flotilla/__init__.py, and tag the release", file=sys.stderr)
        return 1
    print(f"version {version}: the plugin is unchanged since {tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
