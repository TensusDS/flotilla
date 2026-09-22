#!/usr/bin/env python3
"""Refuse Cyrillic characters in the files of this repository.

The repository is English-only (design spec, section 12). The check reads the files git knows
about, tracked and untracked-but-not-ignored, so a new file is caught before its first commit.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

CYRILLIC = re.compile("[\u0400-\u04ff]")


def find_cyrillic(root: Path, paths: list[str]) -> list[str]:
    """Every line containing Cyrillic, as `<path>:<line>: <text>`. Unreadable files are skipped."""
    hits: list[str] = []
    for rel in paths:
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if CYRILLIC.search(line):
                hits.append(f"{rel}:{number}: {line.strip()[:120]}")
    return hits


def repository_files(root: Path) -> list[str]:
    done = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True, text=True, check=True)
    return [path for path in done.stdout.split("\0") if path]


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    hits = find_cyrillic(root, repository_files(root))
    for hit in hits:
        print(hit)
    if hits:
        print(f"{len(hits)} line(s) with Cyrillic; this repository is English-only.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
