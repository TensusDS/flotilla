"""Which live sessions belong to this project (field test F9, F20).

The census is machine-wide: a judge of another repository matches this project's judge post by name. A session is
of this project when this ledger names it — an owner or reader of an open row, post rows included — or when its
working directory is inside this repository's main checkout or one of its worktrees. The second holds before any
row exists; the first holds for a person's own session started anywhere.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def roots(root: Path, run=subprocess.run) -> list[Path]:
    done = run(["git", "-C", str(root), "worktree", "list", "--porcelain"], capture_output=True, text=True,
               check=False)
    found = [Path(line[len("worktree "):]).resolve() for line in (done.stdout or "").splitlines()
             if done.returncode == 0 and line.startswith("worktree ")]
    return found or [Path(root).resolve()]


def _inside(cwd: str, roots: list[Path]) -> bool:
    """Whether the repository `cwd` works in is this one: the nearest directory holding `.git` must be a root, so a
    repository nested inside the checkout is not taken for it. A relative path names no place and is not inside."""
    if not cwd or not Path(cwd).is_absolute():
        return False
    here = Path(cwd).resolve()
    top = next((place for place in (here, *here.parents) if (place / ".git").exists()), None)
    return top is not None and top in roots


def members(sessions: list, rows: dict, roots: list[Path]) -> list:
    named = {name for row in rows.values() if row.is_open for name in (row.owner, row.reader) if name}
    return [session for session in sessions if session.name in named or _inside(session.cwd, roots)]
