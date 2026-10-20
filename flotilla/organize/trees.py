"""Which worktrees organize may enter (spec, section 3).

A worktree lock does not say a tree is idle: Claude Code locks only its agent, background and `-p` trees, not an
interactive session's, and other tools lock nothing. So a tree other than the organizing one is in use when it is
locked, when it is a flotilla seat's, or when a live process has its working directory inside it - read from procfs,
as retire reads it for leftovers. Where procfs cannot be read, whether anyone works there cannot be told, and the
tree is treated as in use. A free tree is still entered only on the person's yes for that tree.
"""

from __future__ import annotations

import os
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


def _zombie(entry: Path) -> bool:
    try:
        stat = (entry / "stat").read_text(encoding="utf-8", errors="replace")
        return stat[stat.rindex(")") + 2:].split()[0] == "Z"
    except (OSError, ValueError, IndexError):
        return False


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
        entries = sorted((entry for entry in root.iterdir() if entry.name.isdigit()), key=lambda e: int(e.name))
    except OSError:
        return "whether a process works in it cannot be told (procfs cannot be read)"
    target = tree.path.resolve()
    for entry in entries:
        try:
            cwd = os.readlink(entry / "cwd")
        except OSError:
            # gone since the listing, or another user's: neither works in this tree. One's own process that cannot
            # be read is "cannot tell", never "nobody" (security review of 4ec6c7c)
            try:
                mine = entry.stat().st_uid == os.getuid()
            except OSError:
                continue
            if mine and not _zombie(entry):   # a zombie has no directory and works nowhere
                return f"whether process {entry.name} works in it cannot be told (its directory cannot be read)"
            continue
        if leftovers._inside(cwd, target):
            return f"process {entry.name} works in it"
    return ""
