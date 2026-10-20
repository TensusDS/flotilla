"""Which worktrees organize may enter (spec, section 3).

A worktree lock does not say a tree is idle: Claude Code locks only its agent, background and `-p` trees, not an
interactive session's, and other tools lock nothing. So a tree other than the organizing one is in use when it is
locked, when it is a flotilla seat's, or when a live process has its working directory inside it - read from procfs,
as retire reads it for leftovers. Where procfs cannot be read, whether anyone works there cannot be told, and the
tree is treated as in use. A free tree is still entered only on the person's yes for that tree.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Tree:
    path: Path
    branch: str
    locked: bool


def worktrees(root: Path, run=subprocess.run) -> list[Tree]:
    done = run(["git", "-C", str(root), "worktree", "list", "--porcelain"], capture_output=True, text=True,
               check=False)
    found, path, branch, locked = [], None, "", False
    for line in (done.stdout if done.returncode == 0 else "").splitlines() + [""]:
        if line.startswith("worktree "):
            path, branch, locked = Path(line[len("worktree "):]).resolve(), "", False
        elif line.startswith("branch refs/heads/"):
            branch = line[len("branch refs/heads/"):]
        elif line.startswith("locked"):
            locked = True
        elif not line and path is not None:
            found.append(Tree(path=path, branch=branch, locked=locked))
            path = None
    return found


def in_use(tree: Tree, *, seats: list[Path], me: Path, proc_root: Path | None = None) -> str:
    """"" when the tree is free to ask about; otherwise why it is not touched."""
    from flotilla.fleet import leftovers
    if tree.path.resolve() == Path(me).resolve():
        return ""
    if tree.locked:
        return "locked"
    if any(tree.path.resolve() == Path(seat).resolve() for seat in seats):
        return "a flotilla seat"
    root = leftovers.PROC_ROOT if proc_root is None else Path(proc_root)
    try:
        table = leftovers._table(root)
    except OSError:
        return "whether a process works in it cannot be told (procfs cannot be read)"
    for pid, proc in sorted(table.items()):
        if leftovers._inside(proc.cwd, tree.path.resolve()):
            return f"process {pid} works in it"
    return ""
