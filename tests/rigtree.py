"""A committed git tree for rig tests, with no global git config and a fixed author."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ENV = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "t",
       "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}


def git(tree: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(tree), *args], env=ENV, check=True, capture_output=True,
                          text=True).stdout


def git_tree(path: Path, files: dict) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    for name, text in files.items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(text)
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "init")
    return path


def commit(tree: Path, name: str, text: str) -> str:
    (tree / name).parent.mkdir(parents=True, exist_ok=True)
    (tree / name).write_text(text)
    git(tree, "add", name)
    git(tree, "commit", "-q", "-m", f"change {name}")
    return git(tree, "rev-parse", "HEAD").strip()
