"""Keeping what could be lost before anything moves (spec, sections 6, 7 and 10).

Kept commits live under `refs/flotilla/<kind>/<sha>`, outside `refs/heads`, so no later walk finds them as work; each
ref is named by the commit it keeps and written create-only, so no run ever overwrites another's.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ZERO = "0" * 40


def create_ref(root: Path, kind: str, sha: str, run=subprocess.run) -> str:
    ref = f"refs/flotilla/{kind}/{sha}"
    done = run(["git", "-C", str(root), "update-ref", ref, sha, ZERO], capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise RuntimeError(f"git would not write {ref} (it may exist already): {done.stderr.strip()[:200]}")
    return ref
