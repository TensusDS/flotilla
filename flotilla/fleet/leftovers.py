"""Background processes a retired seat left in its tree (field test H50).

A session that starts a dev server or a browser detached (`nohup ... &`, `setsid`) leaves it running after the
session stops: the process is re-parented to pid 1 and holds memory until someone finds it. In the twosuns run 27
`vite` servers of finished sessions were alive at once. Retire looks for them by two facts procfs gives: the
process's working directory is inside the seat's tree, and its parent is pid 1 — an orphan, not something a live
shell or session still owns. Each such orphan is stopped with everything under it, unless its subtree holds a
process with a terminal (a person's `tmux` server started in the tree) or a live census session, or the process
running this command.

Linux only: where there is no procfs the leftovers are not looked for, and the caller says so.
"""

from __future__ import annotations

import os
import signal
from dataclasses import dataclass
from pathlib import Path

PROC_ROOT = Path("/proc")


@dataclass(frozen=True)
class Leftover:
    pid: int
    command: str


@dataclass(frozen=True)
class _Proc:
    ppid: int
    tty: int
    cwd: str
    command: str


def _table(proc_root: Path) -> dict[int, _Proc]:
    table = {}
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text(encoding="utf-8", errors="replace")
            fields = stat[stat.rindex(")") + 2:].split()
            cwd = os.readlink(entry / "cwd")
            command = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace").strip()
            table[int(entry.name)] = _Proc(int(fields[1]), int(fields[4]), cwd, command)
        except (OSError, ValueError, IndexError):
            continue   # gone since the listing, or not ours to read
    return table


def _inside(path: str, tree: Path) -> bool:
    where = Path(path)
    return where == tree or tree in where.parents


def _subtree(root: int, children: dict[int, list[int]]) -> list[int]:
    found, todo = [], [root]
    while todo:
        pid = todo.pop(0)
        found.append(pid)
        todo += sorted(children.get(pid, []))
    return found


def in_tree(tree, *, keep: set[int], proc_root: Path | None = None, me: int | None = None) -> list[Leftover] | None:
    """The orphans working in `tree`, each followed by what runs under it; None where procfs is absent."""
    proc_root = PROC_ROOT if proc_root is None else Path(proc_root)
    if not proc_root.is_dir():
        return None
    table = _table(proc_root)
    tree = Path(os.path.realpath(tree))
    children: dict[int, list[int]] = {}
    for pid, proc in table.items():
        children.setdefault(proc.ppid, []).append(pid)
    protected = set(keep)
    pid = os.getpid() if me is None else me
    while pid in table and pid not in protected:   # this command and every process above it
        protected.add(pid)
        pid = table[pid].ppid
    found = []
    for root in sorted(table):
        if table[root].ppid != 1 or not _inside(table[root].cwd, tree):
            continue
        subtree = _subtree(root, children)
        if protected & set(subtree) or any(table[pid].tty for pid in subtree if pid in table):
            continue
        found += [Leftover(pid, table[pid].command) for pid in subtree if pid in table]
    return found


def stop(found: list[Leftover], *, kill=None) -> int:
    """SIGTERM each; how many were still there to receive it."""
    kill = os.kill if kill is None else kill
    reached = 0
    for item in found:
        try:
            kill(item.pid, signal.SIGTERM)
            reached += 1
        except (ProcessLookupError, PermissionError):
            continue
    return reached
