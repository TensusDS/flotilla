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
    if not cwd:
        return False
    here = Path(cwd).resolve()
    return any(here == top or top in here.parents for top in roots)


def members(sessions: list, rows: dict, roots: list[Path]) -> list:
    named = {name for row in rows.values() if row.is_open for name in (row.owner, row.reader) if name}
    return [session for session in sessions if session.name in named or _inside(session.cwd, roots)]
