"""Shared fixtures for guard tests: repositories, segments, receipts."""

import subprocess
from pathlib import Path

from flotilla.guards import shell

IDENTITY = ("-c", "user.email=t@example.invalid", "-c", "user.name=t")


def git(cwd, *args) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return done.stdout.strip()


def plain_repo(tmp_path) -> Path:
    root = Path(tmp_path) / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "f.txt").write_text("base\n", encoding="utf-8")
    git(root, "add", "f.txt")
    git(root, *IDENTITY, "commit", "-q", "-m", "base")
    return root


def first(command, cwd):
    return next(s for s in shell.segments(command, cwd) if s.program in ("git", "sed", "gsed", "gh"))
