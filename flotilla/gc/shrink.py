"""Shrinking idle trees, gc design section 8. With a low disk, a tree nobody works in gives up the entries git ignores
whose names an install or a build makes again (`node_modules`, `.venv`, `dist`); the tree itself stays. A tree a live
session holds, a process works in, or git touched in the last day is never shrunk."""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from flotilla.core.config import whole_number
from flotilla.core.text import visible
from flotilla.fleet import leftovers
from flotilla.gc import ignored, sweep, trees
from flotilla.gc.twins import main_checkout
from flotilla.ledger.letters import Letter

IDLE_HOURS = 24
OUTPUTS_MB = 1000
LARGEST = 5
GIT_FILES = ("index", "HEAD", "logs/HEAD")


@dataclass
class Shrunk:
    removed: list[tuple[str, str, int]] = field(default_factory=list)   # (tree, entry, bytes)
    kept: list[str] = field(default_factory=list)
    freed: int = 0


def _gitdir(ledger, tree: Path) -> str:
    done = ledger.run(["git", "-C", str(tree), "rev-parse", "--absolute-git-dir"], capture_output=True, text=True,
                      check=False)
    return done.stdout.strip() if done.returncode == 0 else ""


def busy(ledger, tree: Path, *, sessions, now: float, proc_root=None, git_activity: bool = True) -> str:
    """Why `tree` is not idle, or "". Without `git_activity` the git files' times are not read: the collector's own
    `git status` refreshes the index, so the look after the lock asks only what can come in between."""
    tree = Path(tree).resolve()
    gitdir = _gitdir(ledger, tree)
    alive = trees._alive(ledger, tree, sessions, (), None, gitdir)
    if alive:
        return alive
    working = leftovers.working_in(tree, proc_root=proc_root)
    if working is None:
        return "its processes could not be read (no /proc on this machine)"
    if working:
        pid, command = working[0]
        return f"process {pid} works in it ({visible(command[:80])})"
    if not git_activity:
        return ""
    if not gitdir:
        return "git could not be asked about it"
    times = []
    for name in GIT_FILES:
        try:
            times.append((Path(gitdir) / name).stat().st_mtime)
        except OSError:
            continue
    if times and now - max(times) < IDLE_HOURS * 3600:
        return f"git activity {max(0, int((now - max(times)) // 3600))} h ago"
    return ""


def _size(path: Path) -> int:
    """Bytes on disk under `path`, links not followed: a link counts as itself."""
    try:
        if path.is_symlink() or not path.is_dir():
            return path.lstat().st_size
    except OSError:
        return 0
    total = 0
    for top, dirs, files in os.walk(path, followlinks=False):
        for name in files + [d for d in dirs if os.path.islink(os.path.join(top, d))]:
            try:
                total += os.lstat(os.path.join(top, name)).st_size
            except OSError:
                continue
    return total


def _remove(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        path.unlink()
    else:
        shutil.rmtree(path)


def _candidates(ledger, tree: Path) -> list[str]:
    entries = ignored._entries(tree, ledger.run) or []
    return [entry.rstrip("/") for entry in entries
            if ignored.regenerable_name(entry.rstrip("/").rsplit("/", 1)[-1], ledger.profile)]


def shrink(ledger, *, sessions, act: bool, now: float | None = None, proc_root=None) -> Shrunk:
    now = time.time() if now is None else now
    found = Shrunk()
    made, _ = sweep.made_by_flotilla(ledger)
    held = trees.locks(ledger)
    for tree, _, missing in sweep.worktrees(ledger):
        here = tree.resolve()
        if missing or here not in made:
            continue
        lock = held.get(here)
        if lock is not None and lock != trees.GC_LOCK:
            found.kept.append(f"{tree.name}: locked ({visible(lock or 'no reason given')})")
            continue
        why = busy(ledger, here, sessions=sessions, now=now, proc_root=proc_root)
        if why:
            found.kept.append(f"{tree.name}: {why}")
            continue
        entries = _candidates(ledger, here)
        if not entries:
            continue
        if not act:
            found.removed += [(tree.name, entry, _size(here / entry)) for entry in entries]
            continue
        if lock is None and ledger.run(["git", "-C", str(ledger.root), "worktree", "lock", "--reason",
                                        trees.GC_LOCK, str(here)], capture_output=True, text=True,
                                       check=False).returncode != 0:
            found.kept.append(f"{tree.name}: git would not lock it")
            continue
        try:
            why = busy(ledger, here, sessions=sessions, now=now, proc_root=proc_root, git_activity=False)
            if why:   # something came in between the look and the lock
                found.kept.append(f"{tree.name}: {why}")
                continue
            for entry in entries:
                target = here / entry
                parent = target.parent.resolve()
                if not os.path.lexists(target) or (parent != here and here not in parent.parents):
                    continue   # gone since the look, or reached through a link out of the tree
                size = _size(target)
                try:
                    _remove(target)
                except OSError as err:
                    found.kept.append(f"{tree.name}: {entry} could not be removed ({err})")
                    continue
                found.removed.append((tree.name, entry, size))
                found.freed += size
        finally:
            ledger.run(["git", "-C", str(ledger.root), "worktree", "unlock", str(here)], capture_output=True,
                       text=True, check=False)
    return found


def lines(shrunk: Shrunk) -> list[str]:
    if not shrunk.removed:
        return []
    count = len({tree for tree, _, _ in shrunk.removed})
    entries = len(shrunk.removed)
    out = [f"shrank {count} idle tree(s): removed {entries} regenerable entr{'y' if entries == 1 else 'ies'}, "
           f"freed {sweep.size_text(shrunk.freed or sum(size for _, _, size in shrunk.removed))}"]
    out += [f"  {tree}: {visible(entry)} ({sweep.size_text(size)})"
            for tree, entry, size in shrunk.removed[:sweep.SHOWN]]
    if entries > sweep.SHOWN:
        out.append(f"  and {entries - sweep.SHOWN} more")
    return out


def _holder(ledger, here: Path, sessions) -> str:
    """The live session whose tree this is: an open row of a live owner on it, or a live session working in it."""
    live = {item.name for item in sessions if item.name}
    for row in ledger.rows().values():
        if row.tree and row.is_open and row.owner in live and Path(row.tree).resolve() == here:
            return row.owner
    for item in sessions:
        if item.name and item.cwd and (Path(item.cwd).resolve() == here or here in Path(item.cwd).resolve().parents):
            return item.name
    return ""


def piling(ledger, *, sessions, data: dict, today: str) -> list[Letter]:
    """One letter a day to a live session whose tree holds more ignored outputs than `[gc] outputs_mb`: files no
    install makes again, which are the session's to keep or remove (gc design 8). Records the day in `data`."""
    limit, _ = whole_number((ledger.profile.get("gc") or {}).get("outputs_mb", OUTPUTS_MB), OUTPUTS_MB, "gc.outputs_mb")
    sent = data.setdefault("letters", {})
    made, _ = sweep.made_by_flotilla(ledger)
    main = main_checkout(ledger)
    found = []
    for tree, branch, missing in sweep.worktrees(ledger):
        here = tree.resolve()
        if missing or here not in made or sent.get(str(here)) == today:
            continue
        owner = _holder(ledger, here, sessions)
        if not owner:
            continue
        classified = ignored.classify(here, main=main, profile=ledger.profile, run=ledger.run)
        if classified is None:
            continue
        files = sorted(classified.unique, key=lambda item: -item[1])
        total = sum(size for _, size, _ in files)
        if not files or total <= limit * sweep.MB:
            continue
        largest = ", ".join(f"{visible(path)} ({sweep.size_text(size)})" for path, size, _ in files[:LARGEST])
        found.append(Letter(mover=owner, to=(owner,), branch=tree.name,
                            text=f"{tree.name} holds {sweep.size_text(total)} of ignored outputs no install makes "
                                 f"again, largest: {largest}. They are yours: commit what the work keeps, remove what "
                                 "it does not, or move them out of the tree."))
        sent[str(here)] = today
    return found
